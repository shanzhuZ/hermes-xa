"""Hermes Hook 入口 — 02 账号扩建入库（stdin JSON）。"""

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
# 每个任务已处理的工具调用（防重）
_SEEN_TOOL_CALLS: set = set()


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
    tool_name = str(payload.get("tool_name") or "")
    if session_id and tool_name.startswith("mcp_"):
        tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        handle = _extract_account_id(tool_args) or ""
        if handle:
            seed_msg = f"account-expansion 账号扩建 @{handle}"
            return store.ensure_task(session_id=session_id, user_message=seed_msg)
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
    legacy = get_step_status(task_id, "step6_posts")
    if legacy == "pending":
        store.set_step_status(
            task_id,
            "step6_posts",
            "skipped",
            message="02 扩建已改为在步骤二下直接展示分平台发文子步骤",
        )


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

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        logger.info("跳过进入步骤三核查 task=%s: %s", task_id, gate.get("message"))
        _auto_skip_unattempted_post_steps(store, task_id)
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
    tool_name = str(payload.get("tool_name") or "")
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

    if tool_name in _STEP4_TOOLS and _is_cross_platform_task(task_id):
        _enter_step4(store, task_id)

    if tool_name == "mcp_maigret_collect_accounts":
        logger.info("maigret post_tool task=%s call_id=%s", task_id, tool_call_id or "(none)")

    step_key = TOOL_PRIMARY_STEP.get(tool_name)
    # 步骤三核查工具须等步骤二完成；状态仅由 _enter_step4 推进，禁止此处提前标 running
    if step_key and tool_name not in _STEP4_TOOLS:
        cur = get_step_status(task_id, step_key)
        if cur not in {"completed", "failed", "skipped"}:
            label = "Maigret 跨平台扫描中" if tool_name == "mcp_maigret_collect_accounts" else f"执行 {tool_name}"
            store.set_step_status(task_id, step_key, "running", message=label)

    post_platform_early = TOOL_POST_PLATFORM.get(tool_name)
    if post_platform_early:
        child = post_step_key(post_platform_early)
        store.ensure_post_steps(task_id, [post_platform_early])
        child_cur = get_step_status(task_id, child)
        if child_cur not in {"completed", "failed", "skipped"}:
            store.set_step_status(task_id, child, "running", message=f"采集 {post_platform_early} 发文中…")

    apify_platform_early = apify_platform_from_actor_tool(tool_name)
    if apify_platform_early:
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
        if tool_name in _STEP4_TOOLS and _is_cross_platform_task(task_id):
            _enter_step4(store, task_id)
            _on_step4_tool_after(store, task_id, tool_name, tool_args, success=False)
        elif step_key and get_step_status(task_id, step_key) == "running":
            store.set_step_status(task_id, step_key, "failed", message=ex.get("error_message") or "工具失败")
        return

    result_data: Dict[str, Any] = {"profiles": [], "posts": [], "candidates": [], "platforms": []}
    try:
        result_data = dispatch(tool_name, tool_output, ctx)
    except Exception as exc:
        logger.exception("normalizer 失败 tool=%s task=%s: %s", tool_name, task_id, exc)
        if tool_name != "mcp_maigret_collect_accounts":
            return

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
    if post_platform and tool_name in TOOL_POST_PLATFORM:
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
    elif post_platform and tool_name == "mcp_apify_get_dataset_items":
        _sync_platform_post_step(
            store,
            task_id,
            post_platform,
            result_data,
            tool_output_id=tool_output_id,
        )
    _mark_step3_closed(store, task_id)

    if tool_name in _STEP4_TOOLS and _is_cross_platform_task(task_id):
        _on_step4_tool_after(store, task_id, tool_name, tool_args, success=True)


def _update_steps_after_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    result_data: Dict[str, Any],
) -> None:
    if tool_name == "mcp_twitter_get_user_info":
        store.set_step_status(task_id, "step1_seed", "completed", message="种子资料已入库")
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
