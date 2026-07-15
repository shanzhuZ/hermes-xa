"""Hermes Hook 入口 — 02 账号扩建入库（stdin JSON）。

冲突约定（方案 C，见 docs/思考流与步骤树同步落库实施方案.md）：
- Java 中继可粗写 pending/running→running；Vision/OCR/Apify 禁止粗 completed；
- Hook 为细状态真相源。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, Optional

from expand_02.config import hermes_home
from collect_01 import db
from collect_01.db import DbError
from expand_02.gates import get_step_status
from collect_01.normalizers.base import infer_mcp_server
from collect_01.normalizers.registry import dispatch
from expand_02.phases import (
    APIFY_POST_TOOLS,
    APIFY_TOOL_PLATFORM,
    PLATFORM_STEP_INDEX,
    TOOL_POST_PLATFORM,
    TOOL_PRIMARY_STEP,
    post_step_key,
    tool_step_key,
)
from collect_01.normalizers.apify import apify_platform_from_actor_tool, resolve_apify_platform_hint
from expand_02.task_store import TaskStore, is_expand_intent, is_four_section_report

_LOG_DIR = hermes_home() / "logs"
_LOG_FILE = _LOG_DIR / "expand_02_sink.log"
logger = logging.getLogger(__name__)

# 记录最近一次 Apify Actor，供 get_dataset_items 推断平台
_LAST_APIFY_HINT: Dict[str, str] = {}
# 不参与业务步骤推进的工具
_SKIP_STEP_TOOLS = frozenset({
    "skill_view", "clarify", "tool_search", "describe_tool", "todo", "terminal",
    "browser_navigate", "browser_vision", "browser_click", "browser_type",
})
# 步骤四入口工具（首次调用时收口步骤三）
_STEP4_TOOLS = frozenset({"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"})
# 02 种子主页：与 01 一致，含 Apify 三轮
from collect_01.seed_platforms import (
    APIFY_SEED_PLATFORM_TOOLS,
    EXPAND_SEED_PROFILE_TOOLS as _SEED_PROFILE_TOOLS,
    TOOL_TO_SEED_PLATFORM,
    apify_seed_empty_ok,
    clear_seed_soft_fails,
    is_apify_seed_platform,
    seed_step_completed_message,
    should_abort_after_seed_soft_fail,
)
# 每个任务已处理的工具调用（防重）
_SEEN_TOOL_CALLS: set = set()


def _task_seed_platform(task_id: str) -> str:
    row = _store().get_task(task_id) or {}
    try:
        seed = json.loads(row.get("seed_json") or "{}")
    except Exception:
        seed = {}
    return str(seed.get("platform") or "twitter").lower()


def _is_seed_profile_tool(tool_name: str, task_id: str) -> bool:
    if get_step_status(task_id, "step1_seed") in {"completed", "skipped", "failed"}:
        return False
    if tool_name not in _SEED_PROFILE_TOOLS:
        return False
    seed_plat = _task_seed_platform(task_id)
    # MCP 种子主页
    if tool_name in TOOL_TO_SEED_PLATFORM and not tool_name.startswith("mcp_apify_"):
        return True
    # Apify 种子：仅 seed_json.platform 为 Apify 平台时才绑定 step1
    if not is_apify_seed_platform(seed_plat):
        return False
    expected = APIFY_SEED_PLATFORM_TOOLS.get(seed_plat)
    if tool_name == expected:
        return True
    if tool_name in {"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}:
        return True
    return False


def _strip_apify_posts_for_seed_phase(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """Apify dataset 常夹带 posts；种子步只入库 profile。"""
    out = dict(result_data or {})
    out["posts"] = []
    return out


def _seed_fail_message(tool_name: str, tool_output: str, *, empty: bool = False) -> str:
    text = (tool_output or "").lower()
    if empty:
        return "种子主页采集无结果，请检查账号名是否正确后重试"
    if "does not exist" in text or "user not found" in text or "not found" in text:
        return "种子账号不存在（平台未找到），请检查账号名后重试"
    snippet = (tool_output or "").replace("\n", " ").strip()
    if len(snippet) > 180:
        snippet = snippet[:180] + "…"
    if snippet:
        return f"种子主页采集失败：{snippet}"
    return f"种子主页采集失败（{tool_name}），请检查账号名后重试"


def _task_is_terminal(store: TaskStore, task_id: str) -> bool:
    task = store.get_task(task_id) or {}
    return str(task.get("status") or "") in {"failed", "completed"}


def _setup_logging() -> None:
    if logger.handlers:
        return
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [expand_02] %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass


_setup_logging()


def handle_event(payload: Dict[str, Any]) -> None:
    event = payload.get("hook_event_name") or ""
    if event == "pre_llm_call":
        _on_pre_llm(payload)
    elif event == "post_tool_call":
        _on_post_tool(payload)
    elif event == "post_llm_call":
        _on_post_llm_call(payload)
    elif event == "on_session_end":
        _on_session_end(payload)
    else:
        return


def _extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    ex = payload.get("extra")
    return ex if isinstance(ex, dict) else {}


def _store() -> TaskStore:
    return TaskStore()


def _is_cross_platform_task(task_id: str) -> bool:
    row = _store().get_task(task_id) or {}
    return int(row.get("cross_platform") or 0) == 1


def _java_task_id_from_session_key(payload: Dict[str, Any]) -> Optional[str]:
    """解析 X-Hermes-Session-Key: task:{java_task_id}（不要求已落库）。"""
    ex = _extra(payload)
    for key in ("gateway_session_key", "session_key"):
        raw = str(ex.get(key) or "").strip()
        if raw.startswith("task:"):
            tid = raw[5:].strip()
            if tid:
                return tid
    return None


def _resolve_task_id(payload: Dict[str, Any], user_message: str = "") -> Optional[str]:
    ex = _extra(payload)
    session_id = str(payload.get("session_id") or "").strip()
    store = _store()

    java_tid = _java_task_id_from_session_key(payload)
    if java_tid:
        row = store.get_task(java_tid)
        if row:
            if session_id:
                store.bind_session(java_tid, session_id)
            return java_tid
        msg = (user_message or str(ex.get("user_message") or "")).strip()
        if msg and is_expand_intent(msg):
            return store.ensure_task(session_id=session_id, user_message=msg, task_id=java_tid)
        if session_id:
            store.bind_session(java_tid, session_id)
        return java_tid

    # Java 预插 pending/running 任务：同 session 多次采集靠此对齐 task_id
    if session_id:
        active = store.get_active_task_by_session(session_id)
        if active:
            return active["task_id"]

    tid = str(ex.get("task_id") or "").strip()
    if tid:
        row = store.get_task(tid)
        if row:
            if session_id and row.get("session_id") != session_id:
                store.bind_session(tid, session_id)
            return row["task_id"]
    if session_id:
        row = store.get_task_by_session(session_id)
        if row and str(row.get("status") or "") in {"pending", "running"}:
            return row["task_id"]
    msg = (user_message or str(ex.get("user_message") or "")).strip()
    if msg and is_expand_intent(msg):
        return store.ensure_task(session_id=session_id, user_message=msg)
    # 禁止仅凭 mcp_* 凭空建扩建任务（与 01 对称，避免跨类型污染）
    tool_name = str(payload.get("tool_name") or "")
    if tool_name.startswith("mcp_"):
        logger.warning(
            "expand 未解析到 task_id，忽略工具事件 tool=%s session=%s",
            tool_name,
            session_id or "-",
        )
    return None


def _on_pre_llm(payload: Dict[str, Any]) -> None:
    ex = _extra(payload)
    user_message = str(ex.get("user_message") or "").strip()
    if not user_message:
        return
    if not is_expand_intent(user_message):
        return
    try:
        task_id = _resolve_task_id(payload, user_message=user_message)
        if not task_id:
            return
        store = _store()
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            store.bind_session(task_id, session_id)
        if get_step_status(task_id, "step1_seed") not in {"completed", "running"}:
            store.set_step_status(task_id, "step1_seed", "running", message="等待种子账号资料采集…")
        task = store.get_task(task_id) or {}
        if int(task.get("cross_platform") or 0) == 1:
            if get_step_status(task_id, "step2_cross_platform") not in {"completed", "running", "skipped"}:
                store.set_step_status(task_id, "step2_cross_platform", "running", message="等待 Maigret 跨平台扫描…")
        if ex.get("is_first_turn") and not store.has_user_dialogue(task_id, "user_input"):
            store.save_dialogue(
                task_id,
                payload.get("session_id"),
                "user",
                user_message,
                "user_input",
            )
    except DbError as exc:
        logger.warning("%s", exc)


def _mark_step3_closed(store: TaskStore, task_id: str) -> None:
    """仅在步骤二下的发文子步骤全部终态后，才收口步骤二父节点。"""
    children = _step3_post_children(task_id)
    if not children:
        return
    statuses = [str(row.get("status") or "") for row in children]
    if any(s in {"pending", "running"} for s in statuses):
        if get_step_status(task_id, "step3_profiles") != "completed":
            store.set_step_status(task_id, "step3_profiles", "running", message="候选主页与发文采集中")
        return
    if get_step_status(task_id, "step3_profiles") != "completed":
        store.set_step_status(task_id, "step3_profiles", "completed", message="步骤二完成，候选主页与发文采集结束")
    # 历史任务若仍有遗留 step6_posts(2.9)，直接删除，避免拖住步骤二收口
    _drop_legacy_step6_posts(task_id)


def _drop_legacy_step6_posts(task_id: str) -> None:
    """02 不再使用 step6_posts 根节点；旧任务清掉以免 UI 显示 2.9。"""
    try:
        db.execute(
            "DELETE FROM collect_phase_steps WHERE task_id=%s AND step_key='step6_posts'",
            (task_id,),
        )
    except DbError as exc:
        logger.warning("删除遗留 step6_posts 失败 task=%s: %s", task_id, exc)


def _step3_post_children(task_id: str):
    return db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key='step3_profiles'
          AND step_key LIKE %s AND step_key <> 'step6_posts'
        ORDER BY step_order, step_key
        """,
        (task_id, "step6_post_%"),
    )


# 判断某平台是否已有采集工具调用（含 phase 挂在 step6_post_{platform}）
_POST_PLATFORM_TOOL_NAMES: Dict[str, frozenset] = {
    "twitter": frozenset({"mcp_twitter_get_user_tweets", "mcp_twitter_get_user_info"}),
    "youtube": frozenset({"mcp_youtube_get_channel_stats", "mcp_youtube_analyze_channel_videos"}),
    "weibo": frozenset({"mcp_weibo_get_profile", "mcp_weibo_get_feeds", "mcp_weibo_get_user_feeds"}),
    "instagram": frozenset({"mcp_apify_apify__instagram_scraper"}),
    "tiktok": frozenset({"mcp_apify_clockworks__tiktok_scraper"}),
    "telegram": frozenset({"mcp_apify_vujeen__telegram_channel_scraper"}),
    "facebook": frozenset({"mcp_apify_headlessagent__facebook_profile_post_scraper"}),
    "github": frozenset({"mcp_apify_knotless_cadence__github_profile_scraper"}),
}

# 仅主页 MCP（无发文）：成功后也要收口 step6_post_*，否则一直 pending
_PROFILE_ONLY_TO_PLATFORM: Dict[str, str] = {
    "mcp_youtube_get_channel_stats": "youtube",
    "mcp_weibo_get_profile": "weibo",
    "mcp_bilibili_get_user_info": "bilibili",
}


def _has_platform_collect_attempt(task_id: str, platform: str) -> bool:
    post_sk = post_step_key(platform)
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM hermes_tool_outputs WHERE task_id=%s AND phase=%s",
        (task_id, post_sk),
    )
    if int((row or {}).get("c") or 0) > 0:
        return True
    names = _POST_PLATFORM_TOOL_NAMES.get(platform)
    if not names:
        return False
    placeholders = ",".join(["%s"] * len(names))
    args: list = [task_id, *names]
    row2 = db.fetch_one(
        f"SELECT COUNT(*) AS c FROM hermes_tool_outputs WHERE task_id=%s AND tool_name IN ({placeholders})",
        tuple(args),
    )
    return int((row2 or {}).get("c") or 0) > 0


def _auto_skip_unattempted_post_steps(store: TaskStore, task_id: str) -> None:
    """Agent 跳过未采集平台即进入核查时，自动收口仍为 pending 且从未发起工具调用的子步骤。"""
    updated = 0
    for row in _step3_post_children(task_id):
        step_key = str(row.get("step_key") or "")
        status = str(row.get("status") or "")
        if status != "pending" or not step_key.startswith("step6_post_"):
            continue
        platform = step_key.replace("step6_post_", "", 1)
        if _has_platform_collect_attempt(task_id, platform):
            continue
        store.set_step_status(
            task_id,
            step_key,
            "skipped",
            message=f"{platform} 未发起采集，已自动跳过",
        )
        updated += 1
    if updated:
        logger.info("自动跳过未采集平台子步骤 task=%s count=%d", task_id, updated)
        _mark_step3_closed(store, task_id)


def _classify_collect_tool_failure(
    tool_name: str,
    tool_output: str,
    ex: Optional[Dict[str, Any]] = None,
) -> tuple:
    """平台采集失败 → (status, message)。用量不足等记 skipped，其它 failed。"""
    ex = ex or {}
    blob = f"{tool_output or ''} {ex.get('error_message') or ''} {ex.get('result') or ''}"
    low = blob.lower()
    if any(
        x in low
        for x in (
            "remaining usage",
            "exceed your remaining",
            "billing/subscription",
            "usage of $",
            "monthly usage",
        )
    ) or ("用量" in blob and ("不足" in blob or "超限" in blob or "限制" in blob)):
        return "skipped", "Apify 账户用量不足/额度超限，已跳过该平台"
    if "rate limit" in low or "too many requests" in low or "429" in low:
        return "skipped", f"Apify 限流，已跳过该平台（{tool_name}）"
    msg = str(ex.get("error_message") or "").strip()
    if not msg:
        # 截取错误正文前若干字
        compact = " ".join(str(tool_output or "").split())
        msg = compact[:220] if compact else f"{tool_name} 调用失败"
    return "failed", msg[:500]


def _finalize_failed_platform_attempts(store: TaskStore, task_id: str) -> int:
    """对仍为 running/pending、但工具已 error 的平台子步骤做收口（含历史卡住任务）。"""
    updated = 0
    for row in _step3_post_children(task_id):
        step_key = str(row.get("step_key") or "")
        cur = str(row.get("status") or "")
        if cur not in {"pending", "running"} or not step_key.startswith("step6_post_"):
            continue
        platform = step_key.replace("step6_post_", "", 1)
        names = _POST_PLATFORM_TOOL_NAMES.get(platform)
        if not names:
            continue
        placeholders = ",".join(["%s"] * len(names))
        err = db.fetch_one(
            f"""
            SELECT tool_name, tool_output, status FROM hermes_tool_outputs
            WHERE task_id=%s AND tool_name IN ({placeholders}) AND status='error'
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, *names),
        )
        if not err:
            continue
        st, msg = _classify_collect_tool_failure(
            str(err.get("tool_name") or ""),
            str(err.get("tool_output") or ""),
            {},
        )
        store.set_step_status(task_id, step_key, st, message=msg)
        updated += 1
    if updated:
        logger.info("收口失败平台子步骤 task=%s count=%d", task_id, updated)
        _mark_step3_closed(store, task_id)
    return updated


def _finalize_stale_post_children(store: TaskStore, task_id: str) -> int:
    """收口「有工具/有主页但仍 pending|running」的平台子步骤（如微博只调了 get_profile）。"""
    updated = 0
    post_counts = {
        str(r["platform"]): int(r["c"])
        for r in db.fetch_all(
            "SELECT platform, COUNT(*) AS c FROM collect_posts WHERE task_id=%s GROUP BY platform",
            (task_id,),
        )
    }
    prof_counts = {
        str(r["platform"]): int(r["c"])
        for r in db.fetch_all(
            "SELECT platform, COUNT(*) AS c FROM collect_profiles WHERE task_id=%s GROUP BY platform",
            (task_id,),
        )
    }
    for row in _step3_post_children(task_id):
        step_key = str(row.get("step_key") or "")
        cur = str(row.get("status") or "")
        if cur not in {"pending", "running"} or not step_key.startswith("step6_post_"):
            continue
        platform = step_key.replace("step6_post_", "", 1)
        n_post = post_counts.get(platform, 0)
        n_prof = prof_counts.get(platform, 0)
        if n_post > 0:
            store.set_step_status(
                task_id,
                step_key,
                "completed",
                message=f"已采集 {platform} 发文 {n_post} 条",
                payload={"post_count": n_post, "profile_count": n_prof},
            )
            updated += 1
            continue
        if n_prof > 0:
            store.set_step_status(
                task_id,
                step_key,
                "completed",
                message=f"已采集 {platform} profile {n_prof}",
                payload={"post_count": 0, "profile_count": n_prof},
            )
            updated += 1
            continue
        if _has_platform_collect_attempt(task_id, platform):
            store.set_step_status(
                task_id,
                step_key,
                "skipped",
                message=f"{platform} 已调用采集工具但未入库主页/发文",
            )
            updated += 1
    if updated:
        logger.info("收口滞留平台子步骤 task=%s count=%d", task_id, updated)
        _mark_step3_closed(store, task_id)
    return updated


def _seed_platform(task_id: str) -> str:
    task = _store().get_task(task_id) or {}
    try:
        seed = json.loads(task.get("seed_json") or "{}")
    except Exception:
        seed = {}
    return str(seed.get("platform") or "twitter")


def _platforms_from_maigret(task_id: str, result_data: Dict[str, Any]) -> list:
    plats = {_seed_platform(task_id)}
    for row in result_data.get("candidates") or []:
        platform = str(row.get("platform") or "").strip().lower()
        # 仅预建可采集平台，排除 imginn/picuki/wordpress 等 discovery 站点
        if platform and platform in PLATFORM_STEP_INDEX:
            plats.add(platform)
    return sorted(plats)


def _resolve_post_platform(
    tool_name: str,
    task_id: str,
    tool_output_id: int,
    result_data: Dict[str, Any],
    tool_args: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    post_platform = TOOL_POST_PLATFORM.get(tool_name)
    if post_platform:
        return post_platform
    if tool_name != "mcp_apify_get_dataset_items":
        return None
    plats = result_data.get("platforms") or []
    if plats:
        return str(plats[0])
    args = tool_args if isinstance(tool_args, dict) else {}
    ds_id = str(args.get("datasetId") or args.get("dataset_id") or "").strip()
    hint = resolve_apify_platform_hint(task_id, tool_output_id, dataset_id=ds_id or None)
    for actor_tool, platform in APIFY_TOOL_PLATFORM.items():
        actor_hint = actor_tool.replace("mcp_apify_", "")
        if hint == actor_hint or platform in hint:
            return platform
    return None


def _sync_platform_post_step(
    store: TaskStore,
    task_id: str,
    platform: str,
    result_data: Dict[str, Any],
    *,
    tool_output_id: Optional[int] = None,
) -> None:
    child = post_step_key(platform)
    store.ensure_post_steps(task_id, [platform])
    n_prof = len(result_data.get("profiles") or [])
    n_post = len(result_data.get("posts") or [])
    if n_prof or n_post:
        parts = []
        if n_prof:
            parts.append(f"profile {n_prof}")
        if n_post:
            parts.append(f"发文 {n_post}")
        store.set_step_status(
            task_id,
            child,
            "completed",
            message=f"已采集 {platform} " + "，".join(parts),
            payload={"post_count": n_post, "profile_count": n_prof},
        )
        if tool_output_id:
            store.update_tool_output_phase(tool_output_id, child)
    else:
        store.set_step_status(
            task_id,
            child,
            "skipped",
            message=f"{platform} Apify 未采集到数据",
        )


def _enter_step4(store: TaskStore, task_id: str) -> bool:
    """首次 OCR/Vision：仅在步骤二全部完成后，才启动步骤三（核查）子步骤。"""
    from collect_01.gates import can_advance_to_step45

    # 进入核查前先收口滞留/未开跑子步骤，避免微博 pending 堵死门禁却已开始 vision
    _finalize_failed_platform_attempts(store, task_id)
    _finalize_stale_post_children(store, task_id)
    _auto_skip_unattempted_post_steps(store, task_id)

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        logger.info("跳过进入步骤三核查 task=%s: %s", task_id, gate.get("message"))
        return False
    if get_step_status(task_id, "step3_streams") == "pending":
        store.set_step_status(task_id, "step3_streams", "running", message="文本/图片流拆分中")
    if get_step_status(task_id, "step4_image_compare") == "pending":
        store.set_step_status(task_id, "step4_image_compare", "running", message="图片流 OCR/Vision 比对中")
    if get_step_status(task_id, "step4_text_compare") == "pending":
        store.set_step_status(task_id, "step4_text_compare", "running", message="文本流规则比对中")
    _maybe_advance_step45(store, task_id)
    return True


def _maybe_advance_step45(store: TaskStore, task_id: str) -> None:
    """文本流可先行完成；步骤四父节点须等文本流与图片流均完成。"""
    from collect_01.gates import can_advance_to_step45

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        logger.info("跳过流水线推进 task=%s: %s", task_id, gate.get("message"))
        return
    if get_step_status(task_id, "step4_text_compare") != "completed":
        store.run_text_compare(task_id)
    _try_complete_step3_and_step5(store, task_id)


def _try_complete_step3_and_step5(store: TaskStore, task_id: str) -> None:
    if get_step_status(task_id, "step4_text_compare") != "completed":
        return
    if get_step_status(task_id, "step4_image_compare") != "completed":
        return
    if get_step_status(task_id, "step3_streams") != "completed":
        store.set_step_status(task_id, "step3_streams", "completed", message="文本/图片流拆分完成")
    if get_step_status(task_id, "step5_validated") != "completed":
        store.run_validated_accounts(task_id)


def _on_step4_tool_after(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    success: bool,
) -> None:
    """OCR/Vision 调用后按图片流粒度更新进度；全部就绪后再收口步骤四。"""
    from collect_01.gates import can_advance_to_step45, count_image_streams, count_image_streams_processed, is_image_compare_ready

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        logger.info("步骤三核查工具回调跳过 task=%s: %s", task_id, gate.get("message"))
        return

    if tool_name in _STEP4_TOOLS:
        store.mark_image_stream_progress(task_id, tool_name, tool_args, success=success)

    n_img = count_image_streams(task_id)
    n_done = count_image_streams_processed(task_id)
    if not is_image_compare_ready(task_id):
        store.set_step_status(
            task_id,
            "step4_image_compare",
            "running",
            message=f"图片流比对中 {n_done}/{n_img}",
        )
        return
    if get_step_status(task_id, "step4_image_compare") != "completed":
        msg = "无头像图片流，跳过图片比对" if n_img == 0 else f"图片流 Vision 完成 ({n_done}/{n_img})"
        store.set_step_status(task_id, "step4_image_compare", "completed", message=msg)
    _maybe_advance_step45(store, task_id)


def _advance_without_image_tools(store: TaskStore, task_id: str) -> None:
    """发文工具已调用但图片流未完成时的兜底：标记剩余图片流失败并尝试收口。"""
    from collect_01.gates import can_advance_to_step45, is_image_compare_ready

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        logger.info("跳过无图像工具兜底推进 task=%s: %s", task_id, gate.get("message"))
        return
    if get_step_status(task_id, "step3_streams") == "pending":
        store.set_step_status(task_id, "step3_streams", "running", message="文本/图片流拆分中")
    if get_step_status(task_id, "step4_image_compare") == "pending":
        store.set_step_status(task_id, "step4_image_compare", "running", message="等待图片流 OCR/Vision…")
    if get_step_status(task_id, "step4_text_compare") == "pending":
        store.set_step_status(task_id, "step4_text_compare", "running", message="文本流规则比对中")
    if not is_image_compare_ready(task_id):
        n_skip = store.mark_remaining_image_streams_failed(task_id, "发文阶段兜底：未完成 vision")
        if n_skip:
            store.set_step_status(
                task_id,
                "step4_image_compare",
                "completed",
                message=f"图片流未完成 vision，兜底跳过 {n_skip} 条",
            )
    _maybe_advance_step45(store, task_id)


def _maybe_advance_pipeline(store: TaskStore, task_id: str) -> None:
    """兼容旧调用。"""
    _maybe_advance_step45(store, task_id)


def _on_post_tool(payload: Dict[str, Any]) -> None:
    from collect_01.normalizers.base import normalize_mcp_tool_name

    tool_name = normalize_mcp_tool_name(str(payload.get("tool_name") or ""))
    if not tool_name:
        return
    ex = _extra(payload)
    tool_call_id = str(ex.get("tool_call_id") or "").strip()
    if tool_call_id and tool_call_id in _SEEN_TOOL_CALLS:
        return

    user_message = str(ex.get("user_message") or "").strip()
    task_id = _resolve_task_id(payload, user_message=user_message)
    if not task_id:
        logger.warning(
            "post_tool_call 跳过：无 task_id session=%s tool=%s",
            payload.get("session_id"),
            tool_name,
        )
        return

    if tool_name in _SKIP_STEP_TOOLS:
        return

    store = _store()
    seed_collect = _is_seed_profile_tool(tool_name, task_id)

    if _task_is_terminal(store, task_id):
        tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        result = ex.get("result")
        if isinstance(result, (dict, list)):
            tool_output = json.dumps(result, ensure_ascii=False, default=str)
        else:
            tool_output = str(result or "")
        status = "success" if ex.get("status") == "ok" else "error"
        try:
            store.save_tool_output(
                task_id=task_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_output=tool_output,
                tool_call_id=tool_call_id or f"anon-{tool_name}-{len(_SEEN_TOOL_CALLS)}",
                duration_ms=ex.get("duration_ms"),
                status=status,
                phase=tool_step_key(tool_name),
                mcp_server=infer_mcp_server(tool_name),
            )
        except DbError:
            pass
        if tool_call_id:
            _SEEN_TOOL_CALLS.add(tool_call_id)
        logger.info("任务已终态，忽略步骤推进 task=%s tool=%s", task_id, tool_name)
        return

    if tool_name in _STEP4_TOOLS and _is_cross_platform_task(task_id):
        entered = _enter_step4(store, task_id)
        if not entered:
            # 门禁未过但 Agent 已调 OCR/Vision：父 step3_streams 不得仍 pending
            if get_step_status(task_id, "step3_streams") == "pending":
                store.set_step_status(
                    task_id, "step3_streams", "running", message="文本/图片流拆分中"
                )

    if tool_name == "mcp_maigret_collect_accounts":
        logger.info("maigret post_tool task=%s call_id=%s", task_id, tool_call_id or "(none)")

    step_key = TOOL_PRIMARY_STEP.get(tool_name)
    if seed_collect:
        # 种子采集期间不误推进 step3 / 发文子步骤
        if get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}:
            store.set_step_status(task_id, "step1_seed", "running", message=f"种子采集中 ({tool_name})")
    elif step_key and tool_name not in _STEP4_TOOLS:
        cur = get_step_status(task_id, step_key)
        if cur not in {"completed", "failed", "skipped"}:
            label = "Maigret 跨平台扫描中" if tool_name == "mcp_maigret_collect_accounts" else f"执行 {tool_name}"
            store.set_step_status(task_id, step_key, "running", message=label)

    post_platform_early = TOOL_POST_PLATFORM.get(tool_name)
    if post_platform_early and not seed_collect:
        # 子节点 running 前先保证父 step3_profiles
        if get_step_status(task_id, "step3_profiles") == "pending":
            store.set_step_status(
                task_id, "step3_profiles", "running", message="候选主页与发文采集中"
            )
        child = post_step_key(post_platform_early)
        store.ensure_post_steps(task_id, [post_platform_early])
        child_cur = get_step_status(task_id, child)
        if child_cur not in {"completed", "failed", "skipped"}:
            store.set_step_status(task_id, child, "running", message=f"采集 {post_platform_early} 发文中…")

    apify_platform_early = apify_platform_from_actor_tool(tool_name)
    # 种子 Apify 归 step1；步骤三候选 Apify 才预建发文子节点
    if apify_platform_early and not seed_collect:
        if get_step_status(task_id, "step3_profiles") == "pending":
            store.set_step_status(
                task_id, "step3_profiles", "running", message="候选主页与发文采集中"
            )
        child = post_step_key(apify_platform_early)
        store.ensure_post_steps(task_id, [apify_platform_early])
        child_cur = get_step_status(task_id, child)
        if child_cur not in {"completed", "failed", "skipped"}:
            store.set_step_status(task_id, child, "running", message=f"Apify 采集 {apify_platform_early} 中…")

    tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    result = ex.get("result")
    if isinstance(result, (dict, list)):
        tool_output = json.dumps(result, ensure_ascii=False, default=str)
    else:
        tool_output = str(result or "")

    status = "success" if ex.get("status") == "ok" else "error"
    if seed_collect:
        output_step_key = "step1_seed"
    else:
        output_step_key = tool_step_key(tool_name)
        if post_platform_early:
            output_step_key = post_step_key(post_platform_early)
        elif apify_platform_early:
            output_step_key = post_step_key(apify_platform_early)
        elif tool_name == "mcp_youtube_get_channel_stats":
            output_step_key = post_step_key("youtube")
        elif tool_name == "mcp_weibo_get_profile":
            output_step_key = post_step_key("weibo")
        elif tool_name == "mcp_apify_get_dataset_items":
            ds_id = str(tool_args.get("datasetId") or tool_args.get("dataset_id") or "").strip()
            hint = resolve_apify_platform_hint(task_id, None, dataset_id=ds_id or None)
            for actor_tool, plat in APIFY_TOOL_PLATFORM.items():
                actor_hint = actor_tool.replace("mcp_apify_", "")
                if hint and (hint == actor_hint or plat in hint):
                    output_step_key = post_step_key(plat)
                    break

    try:
        tool_output_id = store.save_tool_output(
            task_id=task_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_output=tool_output,
            tool_call_id=tool_call_id or f"anon-{tool_name}-{len(_SEEN_TOOL_CALLS)}",
            duration_ms=ex.get("duration_ms"),
            status=status,
            phase=output_step_key,
            mcp_server=infer_mcp_server(tool_name),
        )
    except DbError as exc:
        logger.warning("写 tool_outputs 失败 tool=%s task=%s: %s", tool_name, task_id, exc)
        return
    except Exception as exc:
        logger.exception("写 tool_outputs 异常 tool=%s task=%s: %s", tool_name, task_id, exc)
        return

    if tool_call_id:
        _SEEN_TOOL_CALLS.add(tool_call_id)

    if tool_name in APIFY_POST_TOOLS:
        _LAST_APIFY_HINT[task_id] = tool_name.replace("mcp_apify_", "")

    platform_hint = _LAST_APIFY_HINT.get(task_id, "")
    if tool_name == "mcp_apify_get_dataset_items":
        ds_id = str(tool_args.get("datasetId") or tool_args.get("dataset_id") or "").strip()
        platform_hint = resolve_apify_platform_hint(
            task_id, tool_output_id, dataset_id=ds_id or None
        )

    ctx: Dict[str, Any] = {
        "task_id": task_id,
        "tool_output_id": tool_output_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "account_id": _extract_account_id(tool_args),
        "platform_hint": platform_hint,
    }

    if status != "success":
        if seed_collect:
            fail_msg = _seed_fail_message(tool_name, tool_output, empty=False)
            if should_abort_after_seed_soft_fail(task_id, tool_output or fail_msg):
                clear_seed_soft_fails(task_id)
                store.fail_seed_and_abort(task_id, fail_msg)
                logger.warning("种子采集失败(工具error) task=%s tool=%s: %s", task_id, tool_name, fail_msg)
            else:
                store.set_step_status(
                    task_id,
                    "step1_seed",
                    "running",
                    message="种子采集瞬态失败，等待自动重试…",
                )
                logger.warning(
                    "种子瞬态失败软重试 task=%s tool=%s: %s",
                    task_id,
                    tool_name,
                    fail_msg,
                )
            return
        if tool_name in _STEP4_TOOLS and _is_cross_platform_task(task_id):
            _enter_step4(store, task_id)
            _on_step4_tool_after(store, task_id, tool_name, tool_args, success=False)
            return
        # 收口平台子步骤（勿只失败父 step3_profiles，否则如 TikTok 用量不足会一直 running）
        fail_platform = apify_platform_early or post_platform_early
        if fail_platform:
            child = post_step_key(fail_platform)
            st, msg = _classify_collect_tool_failure(tool_name, tool_output, ex)
            child_cur = get_step_status(task_id, child)
            if child_cur not in {"completed", "skipped", "failed"}:
                store.set_step_status(task_id, child, st, message=msg)
                logger.warning(
                    "平台采集失败收口 task=%s platform=%s status=%s msg=%s",
                    task_id,
                    fail_platform,
                    st,
                    msg,
                )
            _mark_step3_closed(store, task_id)
        elif step_key and get_step_status(task_id, step_key) == "running":
            store.set_step_status(
                task_id, step_key, "failed", message=ex.get("error_message") or "工具失败"
            )
        return

    result_data: Dict[str, Any] = {"profiles": [], "posts": [], "candidates": [], "platforms": []}
    try:
        result_data = dispatch(tool_name, tool_output, ctx)
    except Exception as exc:
        logger.exception("normalizer 失败 tool=%s task=%s: %s", tool_name, task_id, exc)
        if seed_collect:
            fail_msg = _seed_fail_message(tool_name, str(exc), empty=True)
            store.fail_seed_and_abort(task_id, fail_msg)
            return
        if tool_name != "mcp_maigret_collect_accounts":
            return

    if seed_collect and not (result_data.get("profiles") or []):
        if apify_seed_empty_ok(tool_name):
            store.set_step_status(
                task_id,
                "step1_seed",
                "running",
                message=f"Apify 种子采集进行中 ({tool_name})",
            )
            logger.info("种子 Apify 中间步 task=%s tool=%s（等待 dataset）", task_id, tool_name)
            return
        fail_msg = _seed_fail_message(tool_name, tool_output, empty=True)
        store.fail_seed_and_abort(task_id, fail_msg)
        logger.warning("种子采集失败(空结果) task=%s tool=%s", task_id, tool_name)
        return

    # Apify 种子轮：丢掉夹带的 posts，避免提前挂发文子步骤
    if seed_collect and tool_name == "mcp_apify_get_dataset_items":
        n_drop = len(result_data.get("posts") or [])
        result_data = _strip_apify_posts_for_seed_phase(result_data)
        if n_drop:
            logger.info("Apify 种子轮丢弃 posts=%d task=%s", n_drop, task_id)

    _persist_normalized(store, task_id, result_data, display_step_key=output_step_key)
    _update_steps_after_tool(store, task_id, tool_name, result_data)

    if result_data.get("profiles"):
        for prof in result_data["profiles"]:
            store.build_streams_from_profile(task_id, prof)

    if tool_name == "mcp_maigret_collect_accounts":
        n_cand = len(result_data.get("candidates") or [])
        # 步骤二确定平台后，预先生成步骤二下的分平台发文子步骤，避免后续动态插入导致 UI 抖动。
        store.ensure_post_steps(task_id, _platforms_from_maigret(task_id, result_data))
        store.set_step_status(
            task_id,
            "step2_cross_platform",
            "completed",
            message=f"Maigret 跨平台扫描完成，候选 {n_cand} 条",
        )

    if (
        _is_cross_platform_task(task_id)
        and tool_name in TOOL_POST_PLATFORM
        and get_step_status(task_id, "step3_profiles") == "completed"
        and get_step_status(task_id, "step5_validated") != "completed"
    ):
        _advance_without_image_tools(store, task_id)

    post_platform = _resolve_post_platform(tool_name, task_id, tool_output_id, result_data, tool_args)
    if seed_collect:
        # 种子步只收口 step1，不写发文子步骤
        pass
    elif post_platform and tool_name in TOOL_POST_PLATFORM:
        n_post = len(result_data.get("posts") or [])
        if n_post > 0:
            _sync_platform_post_step(store, task_id, post_platform, result_data)
        else:
            child = post_step_key(post_platform)
            store.ensure_post_steps(task_id, [post_platform])
            store.set_step_status(
                task_id,
                child,
                "skipped",
                message=f"{post_platform} 未采集到发文",
            )
    elif tool_name in _PROFILE_ONLY_TO_PLATFORM and not seed_collect:
        # 微博/油管主页 MCP：入库 profile 后必须收口子步骤，否则一直 pending 堵死步骤二
        plat = _PROFILE_ONLY_TO_PLATFORM[tool_name]
        _sync_platform_post_step(
            store,
            task_id,
            plat,
            result_data,
            tool_output_id=tool_output_id,
        )
    elif post_platform and tool_name == "mcp_apify_get_dataset_items":
        _sync_platform_post_step(
            store,
            task_id,
            post_platform,
            result_data,
            tool_output_id=tool_output_id,
        )
    if not seed_collect:
        _mark_step3_closed(store, task_id)

    if tool_name in _STEP4_TOOLS and _is_cross_platform_task(task_id):
        _on_step4_tool_after(store, task_id, tool_name, tool_args, success=True)


def _update_steps_after_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    result_data: Dict[str, Any],
) -> None:
    seed_plat = _task_seed_platform(task_id)
    is_mcp_seed_tool = tool_name in TOOL_TO_SEED_PLATFORM and not tool_name.startswith("mcp_apify_")
    is_apify_seed_dataset = (
        tool_name == "mcp_apify_get_dataset_items"
        and is_apify_seed_platform(seed_plat)
        and get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}
    )
    profs = result_data.get("profiles") or []
    if (
        profs
        and (is_mcp_seed_tool or is_apify_seed_dataset)
        and get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}
        and not _task_is_terminal(store, task_id)
        and not apify_seed_empty_ok(tool_name)
    ):
        plat = TOOL_TO_SEED_PLATFORM.get(tool_name) or seed_plat
        if is_apify_seed_dataset:
            plat = seed_plat
        store.set_step_status(
            task_id,
            "step1_seed",
            "completed",
            message=seed_step_completed_message(plat),
        )
        clear_seed_soft_fails(task_id)
        return
    if tool_name == "mcp_maigret_collect_accounts":
        return
    step_key = TOOL_PRIMARY_STEP.get(tool_name)
    if step_key == "step3_profiles":
        n_prof = len(result_data.get("profiles") or [])
        msg = f"{tool_name} 执行中"
        if n_prof:
            msg = f"{tool_name} 已入库 profile {n_prof} 条"
        if get_step_status(task_id, "step3_profiles") != "completed":
            store.set_step_status(task_id, "step3_profiles", "running", message=msg)


def _persist_normalized(
    store: TaskStore,
    task_id: str,
    data: Dict[str, Any],
    *,
    display_step_key: str,
) -> None:
    from collect_01.phases import post_step_key

    profile_step = display_step_key if display_step_key in {"step1_seed", "step3_profiles"} else "step3_profiles"
    post_step = display_step_key if display_step_key.startswith("step6") else None

    for row in data.get("profiles") or []:
        store.save_profile_row(row, step_key=profile_step)
    for row in data.get("candidates") or []:
        store.save_candidate_rows([row], step_key="step2_cross_platform")
    posts = data.get("posts") or []
    if posts:
        for row in posts:
            sk = post_step or post_step_key(str(row.get("platform") or ""))
            store.save_post_rows([row], step_key=sk)


def _on_post_llm_call(payload: Dict[str, Any]) -> None:
    """每轮 LLM 结束后保存助手最终回复（含步骤七三节报告）。"""
    ex = _extra(payload)
    assistant = str(ex.get("assistant_response") or "").strip()
    session_id = payload.get("session_id")
    logger.info(
        "post_llm_call session=%s len=%d empty=%s",
        session_id,
        len(assistant),
        not bool(assistant) or assistant == "(empty)",
    )
    if not assistant or assistant == "(empty)":
        return
    user_message = str(ex.get("user_message") or "").strip()
    task_id = _resolve_task_id(payload, user_message=user_message)
    if not task_id:
        logger.info(
            "post_llm_call 跳过：无扩建 task session=%s len=%d",
            payload.get("session_id"),
            len(assistant),
        )
        return
    try:
        _store().save_assistant_output(
            task_id,
            payload.get("session_id"),
            assistant,
        )
    except DbError as exc:
        logger.warning("保存助手输出失败 task=%s: %s", task_id, exc)


def _load_assistant_output_from_state(session_id: str) -> Optional[str]:
    """从 Hermes state.db 读取本会话最后一条有效助手输出（post_llm_call 未触发时兜底）。"""
    if not session_id:
        return None
    db_path = hermes_home() / "state.db"
    if not db_path.is_file():
        return None
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT content FROM messages
            WHERE session_id=? AND role='assistant'
              AND content IS NOT NULL AND TRIM(content) != ''
              AND TRIM(content) != '(empty)'
            ORDER BY id DESC
            LIMIT 30
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    for row in rows:
        text = str(row[0] or "").strip()
        if is_four_section_report(text):
            return text
    for row in rows:
        text = str(row[0] or "").strip()
        if len(text) >= 200:
            return text
    return str(rows[0][0] or "").strip() or None


def _on_session_end(payload: Dict[str, Any]) -> None:
    """每轮结束时落库助手终稿（兜底）并刷新任务统计。"""
    task_id = _resolve_task_id(payload)
    if not task_id:
        return
    session_id = str(payload.get("session_id") or "").strip() or None
    store = _store()
    try:
        assistant = _load_assistant_output_from_state(session_id or "")
        if assistant:
            saved = store.save_assistant_output(task_id, session_id, assistant)
            if saved:
                logger.info(
                    "on_session_end 兜底入库 task=%s session=%s len=%d",
                    task_id,
                    session_id,
                    len(assistant),
                )
    except DbError as exc:
        logger.warning("on_session_end 保存助手输出失败 task=%s: %s", task_id, exc)
    except Exception as exc:
        logger.warning("on_session_end 读取 state.db 失败 task=%s: %s", task_id, exc)
    try:
        _auto_skip_unattempted_post_steps(store, task_id)
    except Exception as exc:
        logger.warning("on_session_end 收口未采集子步骤失败 task=%s: %s", task_id, exc)
    try:
        store.finalize_task(task_id)
    except DbError as exc:
        logger.warning("%s", exc)


def _extract_account_id(tool_args: Dict[str, Any]) -> Optional[str]:
    for key in ("user_id", "username", "screen_name", "channelId", "channel_id", "uid"):
        val = tool_args.get(key)
        if val:
            return str(val)
    return None


def _read_stdin() -> str:
    if hasattr(sys.stdin, "buffer"):
        data = sys.stdin.buffer.read()
    else:
        data = sys.stdin.read().encode("utf-8", errors="replace")
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def main() -> int:
    try:
        raw = _read_stdin()
        if not raw.strip():
            return 0
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("hook JSON 解析失败 len=%s err=%s", len(raw), exc)
            return 1
        if isinstance(payload, dict):
            handle_event(payload)
    except DbError as exc:
        logger.warning("%s", exc)
    except Exception as exc:
        logger.exception("expand_02 sink 异常: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
