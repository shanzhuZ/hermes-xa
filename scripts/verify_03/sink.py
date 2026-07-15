"""Hermes Hook 入口 — 03 账号核查入库（stdin JSON）。"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional

from collect_01.config import hermes_home
from collect_01.db import DbError
from collect_01.normalizers.base import infer_mcp_server
from collect_01.normalizers.registry import dispatch
from collect_01.normalizers.apify import apify_platform_from_actor_tool, resolve_apify_platform_hint
from verify_03.account_parser import looks_like_step1_confirmation, parse_input_accounts
from verify_03.gates import (
    all_input_profiles_attempted,
    can_advance_to_step4,
    can_advance_to_step45,
    get_step_status,
    is_image_compare_ready,
)
from verify_03.phases import (
    APIFY_TOOL_PLATFORM,
    POST_TOOLS,
    PROFILE_TOOLS,
    TOOL_PLATFORM,
    TOOL_PRIMARY_STEP,
    post_platform_step_key,
    profile_platform_step_key,
    tool_collect_step_key,
    tool_step_key,
)
from verify_03.task_store import TaskStore, is_three_section_report, is_verify_intent

_LOG_DIR = hermes_home() / "logs"
_LOG_FILE = _LOG_DIR / "verify_03_sink.log"
logger = logging.getLogger(__name__)

_SKIP_STEP_TOOLS = frozenset({
    "skill_view", "clarify", "tool_search", "describe_tool", "todo", "terminal",
    "browser_navigate", "browser_vision", "browser_click", "browser_type",
    "mcp_firecrawl_firecrawl_search", "mcp_firecrawl_firecrawl_scrape",
})
_STEP4_TOOLS = frozenset({"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"})
_SEEN_TOOL_CALLS: set = set()
_LAST_APIFY_HINT: Dict[str, str] = {}


def _setup_logging() -> None:
    if logger.handlers:
        return
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [verify_03] %(message)s")
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


def _extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    ex = payload.get("extra")
    return ex if isinstance(ex, dict) else {}


def _store() -> TaskStore:
    return TaskStore()


def _java_task_id_from_session_key(payload: Dict[str, Any]) -> Optional[str]:
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
        if row and str(row.get("task_type") or "") == "account_verify":
            if session_id:
                store.bind_session(java_tid, session_id)
            return java_tid
        msg = (user_message or str(ex.get("user_message") or "")).strip()
        if msg and is_verify_intent(msg):
            return store.ensure_task(session_id=session_id, user_message=msg, task_id=java_tid)
        if row and str(row.get("task_type") or "") == "account_verify":
            if session_id:
                store.bind_session(java_tid, session_id)
            return java_tid

    if session_id:
        active = store.get_active_task_by_session(session_id)
        if active:
            return active["task_id"]

    tid = str(ex.get("task_id") or "").strip()
    if tid:
        row = store.get_task(tid)
        if row and str(row.get("task_type") or "") == "account_verify":
            if session_id and row.get("session_id") != session_id:
                store.bind_session(tid, session_id)
            return tid

    msg = (user_message or str(ex.get("user_message") or "")).strip()
    if msg and is_verify_intent(msg):
        return store.ensure_task(session_id=session_id, user_message=msg)
    return None


def _on_pre_llm(payload: Dict[str, Any]) -> None:
    ex = _extra(payload)
    user_message = str(ex.get("user_message") or "").strip()
    if not user_message or not is_verify_intent(user_message):
        return
    try:
        task_id = _resolve_task_id(payload, user_message=user_message)
        if not task_id:
            return
        store = _store()
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            store.bind_session(task_id, session_id)
        _ensure_step1_completed(store, task_id, user_message=user_message, session_id=session_id)
        if get_step_status(task_id, "step1_input_accounts") == "pending":
            store.set_step_status(task_id, "step1_input_accounts", "running", message="等待 Agent 确认种子账号…")
        if ex.get("is_first_turn") and not store.has_user_dialogue(task_id, "user_input"):
            store.save_dialogue(task_id, session_id, "user", user_message, "user_input")
    except DbError as exc:
        logger.warning("%s", exc)


def _enter_step4(store: TaskStore, task_id: str) -> None:
    gate = can_advance_to_step4(task_id)
    if not gate.get("ok"):
        logger.info("进入 step4 跳过 task=%s: %s", task_id, gate.get("message"))
        return
    if get_step_status(task_id, "step3_streams") == "pending":
        store.set_step_status(task_id, "step3_streams", "running", message="文本/图片流分析中")
    if get_step_status(task_id, "step4_text_compare") == "pending":
        store.set_step_status(task_id, "step4_text_compare", "running", message="文本流比对中")
    if get_step_status(task_id, "step4_image_compare") == "pending":
        store.set_step_status(task_id, "step4_image_compare", "running", message="图片流比对中")


def _maybe_advance_step45(store: TaskStore, task_id: str) -> None:
    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        return
    if get_step_status(task_id, "step4_text_compare") != "completed":
        store.run_text_compare(task_id)
    n_img = 0
    from verify_03.gates import count_image_streams

    n_img = count_image_streams(task_id)
    if is_image_compare_ready(task_id) and get_step_status(task_id, "step4_image_compare") != "completed":
        msg = "无头像图片流，跳过图片比对" if n_img == 0 else "图片流 Vision 完成"
        store.set_step_status(task_id, "step4_image_compare", "completed", message=msg)
    if (
        get_step_status(task_id, "step4_text_compare") == "completed"
        and get_step_status(task_id, "step4_image_compare") == "completed"
        and get_step_status(task_id, "step5_validated") != "completed"
    ):
        store.run_validated_accounts(task_id)


def _on_step4_tool_after(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    success: bool,
) -> None:
    # vision 回写图片流不受 step3 门禁影响（Agent 常提前调 vision）
    if tool_name in _STEP4_TOOLS:
        store.mark_image_stream_progress(task_id, tool_name, tool_args, success=success)

    gate = can_advance_to_step4(task_id)
    if not gate.get("ok"):
        return
    from verify_03.gates import count_image_streams, count_image_streams_processed

    n_img = count_image_streams(task_id)
    n_done = count_image_streams_processed(task_id)
    if get_step_status(task_id, "step4_image_compare") == "pending":
        store.set_step_status(task_id, "step4_image_compare", "running", message=f"图片流比对中 {n_done}/{n_img}")
    if not is_image_compare_ready(task_id):
        if get_step_status(task_id, "step4_image_compare") not in {"completed", "skipped"}:
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
    if can_advance_to_step45(task_id).get("ok"):
        _maybe_advance_step45(store, task_id)


def _try_parse_step1(store: TaskStore, task_id: str, assistant: str, user_message: str) -> None:
    if get_step_status(task_id, "step1_input_accounts") == "completed":
        return
    if not looks_like_step1_confirmation(assistant) and not assistant.strip():
        return
    accounts = parse_input_accounts(user_message=user_message, assistant_text=assistant)
    if not accounts:
        return
    store.save_input_accounts(task_id, accounts, source="agent")
    logger.info("步骤1 解析账号 task=%s count=%d", task_id, len(accounts))


def _ensure_step1_completed(
    store: TaskStore,
    task_id: str,
    *,
    user_message: str = "",
    session_id: Optional[str] = None,
) -> None:
    """post_llm 未触发时：从 Agent 表格或用户原始输入兜底完成步骤一。"""
    if get_step_status(task_id, "step1_input_accounts") == "completed":
        return
    assistant = _load_assistant_for_step1(session_id or "")
    if assistant and looks_like_step1_confirmation(assistant):
        accounts = parse_input_accounts(user_message=user_message, assistant_text=assistant)
        if accounts:
            store.save_input_accounts(task_id, accounts, source="agent")
            logger.info("步骤1 兜底(assistant) task=%s count=%d", task_id, len(accounts))
            return
    msg = user_message.strip() or store.load_user_input_message(task_id)
    accounts = parse_input_accounts(user_message=msg, assistant_text="")
    if accounts:
        store.save_input_accounts(task_id, accounts, source="user_input")
        logger.info("步骤1 兜底(user_input) task=%s count=%d", task_id, len(accounts))


def _load_assistant_for_step1(session_id: str) -> str:
    if not session_id:
        return ""
    db_path = hermes_home() / "state.db"
    if not db_path.is_file():
        return ""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT content FROM messages
            WHERE session_id=? AND role='assistant'
              AND content IS NOT NULL AND TRIM(content) != ''
              AND TRIM(content) != '(empty)'
            ORDER BY id ASC
            LIMIT 20
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        text = str(row[0] or "").strip()
        if looks_like_step1_confirmation(text):
            return text
    return ""


def _try_style_analysis(store: TaskStore, task_id: str, assistant: str) -> None:
    if get_step_status(task_id, "step3_streams") == "completed":
        return
    text = assistant or ""
    if "步骤4" in text or "步骤四" in text or "发文观点" in text or "发文风格" in text:
        store.save_style_analysis(task_id, text)


def _infer_platform(
    tool_name: str,
    tool_args: Dict[str, Any],
    accounts: List[Dict[str, Any]],
    platform_hint: str = "",
) -> Optional[str]:
    plat = TOOL_PLATFORM.get(tool_name)
    if plat:
        return plat
    if tool_name == "mcp_apify_get_dataset_items" and platform_hint:
        for actor_tool, p in APIFY_TOOL_PLATFORM.items():
            actor_hint = actor_tool.replace("mcp_apify_", "")
            if platform_hint == actor_hint or p in platform_hint:
                return p
    blob = json.dumps(tool_args or {}, ensure_ascii=False).lower()
    for acc in accounts:
        handle = str(acc.get("account_handle") or acc.get("account_id") or "").lower()
        if handle and handle in blob:
            return str(acc.get("platform") or "")
    return None


def _sync_platform_collect_steps(
    store: TaskStore,
    task_id: str,
    platform: str,
    result_data: Dict[str, Any],
    *,
    tool_name: str,
    tool_ok: bool,
    tool_args: Optional[Dict[str, Any]] = None,
) -> None:
    if not platform:
        return
    tool_args = tool_args if isinstance(tool_args, dict) else {}
    prof_key = profile_platform_step_key(platform)
    post_key = post_platform_step_key(platform)
    store.ensure_step_row(task_id, prof_key)
    store.ensure_step_row(task_id, post_key)

    n_prof = len(result_data.get("profiles") or [])
    n_post = len(result_data.get("posts") or [])
    if n_post == 0 and tool_name in POST_TOOLS and tool_ok:
        from collect_01 import db as _db

        row = _db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
            (task_id, platform),
        )
        n_post = int((row or {}).get("c") or 0)

    if n_prof > 0:
        store.set_step_status(task_id, prof_key, "completed", message=f"已入库主页 {n_prof} 条")
    elif tool_name in PROFILE_TOOLS and not tool_ok:
        from collect_01 import db as _db

        row = _db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_profiles WHERE task_id=%s AND platform=%s",
            (task_id, platform),
        )
        has_prof = int((row or {}).get("c") or 0) > 0
        cur = get_step_status(task_id, prof_key)
        if has_prof:
            store.set_step_status(task_id, prof_key, "completed", message="已入库主页")
        elif tool_name == "mcp_youtube_get_channel_stats":
            # channelId 传 @handle 会失败；无成功记录则直接 failed，避免永久「等待重试」
            cid = str(tool_args.get("channelId") or tool_args.get("channel_id") or "").strip()
            looks_like_uc = cid.upper().startswith("UC") and len(cid) >= 20
            if not looks_like_uc and cur not in {"completed", "skipped", "failed"}:
                store.set_step_status(
                    task_id,
                    prof_key,
                    "failed",
                    message="YouTube 需 channelId（UC…），不能用 @handle",
                )
            elif cur not in {"completed", "skipped", "failed"}:
                store.set_step_status(
                    task_id, prof_key, "running", message=f"{platform} 主页采集中（等待重试）…"
                )
        elif cur not in {"completed", "skipped", "failed"}:
            # 首次失败保持 running，等待合法参数重试
            store.set_step_status(task_id, prof_key, "running", message=f"{platform} 主页采集中（等待重试）…")
    elif tool_name in PROFILE_TOOLS:
        cur = get_step_status(task_id, prof_key)
        if apify_platform_from_actor_tool(tool_name) and tool_ok:
            store.set_step_status(
                task_id,
                prof_key,
                "running",
                message=f"{platform} Actor 已完成，等待拉取 dataset…",
            )
        elif cur == "pending":
            store.set_step_status(task_id, prof_key, "running", message=f"{platform} 主页采集中…")

    if n_post > 0:
        store.set_step_status(task_id, post_key, "completed", message=f"已入库发文 {n_post} 条")
    elif tool_name in POST_TOOLS and tool_ok:
        cur = get_step_status(task_id, post_key)
        if n_post == 0 and tool_name == "mcp_apify_get_dataset_items":
            # 主页轮 dataset 常无 posts：勿提前 skipped，留给步骤三发文轮
            if n_prof > 0:
                if cur == "pending":
                    store.set_step_status(
                        task_id, post_key, "running", message=f"{platform} 等待发文采集…"
                    )
            elif cur not in {"completed", "skipped"}:
                store.set_step_status(task_id, post_key, "skipped", message=f"{platform} 未采集到发文")
        elif cur == "pending":
            store.set_step_status(task_id, post_key, "running", message=f"{platform} 发文采集中…")
    elif tool_name in POST_TOOLS and not tool_ok:
        from collect_01 import db as _db

        row = _db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
            (task_id, platform),
        )
        has_post = int((row or {}).get("c") or 0) > 0
        cur = get_step_status(task_id, post_key)
        if has_post:
            store.set_step_status(task_id, post_key, "completed", message=f"已入库发文 {int(row['c'])} 条")
        elif cur not in {"completed", "skipped"}:
            store.set_step_status(task_id, post_key, "running", message=f"{platform} 发文采集中（等待重试）…")
    elif tool_name in POST_TOOLS:
        if get_step_status(task_id, post_key) == "pending":
            store.set_step_status(task_id, post_key, "running", message=f"{platform} 发文采集中…")

    store.reconcile_profile_platform_steps(task_id)


def _try_complete_profiles(store: TaskStore, task_id: str) -> None:
    store.reconcile_profile_platform_steps(task_id)


def _on_post_tool(payload: Dict[str, Any]) -> None:
    from collect_01.normalizers.base import normalize_mcp_tool_name

    tool_name = normalize_mcp_tool_name(str(payload.get("tool_name") or ""))
    if not tool_name or tool_name in _SKIP_STEP_TOOLS:
        return
    ex = _extra(payload)
    tool_call_id = str(ex.get("tool_call_id") or "").strip()
    if tool_call_id and tool_call_id in _SEEN_TOOL_CALLS:
        return

    user_message = str(ex.get("user_message") or "").strip()
    task_id = _resolve_task_id(payload, user_message=user_message)
    if not task_id:
        return

    store = _store()
    session_id = str(payload.get("session_id") or "").strip()
    _ensure_step1_completed(store, task_id, user_message=user_message, session_id=session_id)

    tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    input_accounts = store.get_input_accounts(task_id)
    platform_hint_early = _LAST_APIFY_HINT.get(task_id, "")
    platform = _infer_platform(tool_name, tool_args, input_accounts, platform_hint_early)
    collect_step = tool_collect_step_key(tool_name, platform) if platform else None

    step_key = TOOL_PRIMARY_STEP.get(tool_name)
    if step_key and tool_name not in _STEP4_TOOLS:
        cur = get_step_status(task_id, step_key)
        if cur not in {"completed", "failed", "skipped"}:
            store.set_step_status(task_id, step_key, "running", message=f"执行 {tool_name}")
    if collect_step:
        child_cur = get_step_status(task_id, collect_step)
        if child_cur not in {"completed", "failed", "skipped"}:
            kind = "发文" if tool_name in POST_TOOLS else "主页"
            store.set_step_status(task_id, collect_step, "running", message=f"{platform} {kind}采集中…")

    if tool_name in _STEP4_TOOLS:
        _enter_step4(store, task_id)

    result = ex.get("result")
    if isinstance(result, (dict, list)):
        tool_output = json.dumps(result, ensure_ascii=False, default=str)
    else:
        tool_output = str(result or "")

    status = "success" if ex.get("status") == "ok" else "error"
    output_step_key = collect_step or tool_step_key(tool_name)

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

    if tool_call_id:
        _SEEN_TOOL_CALLS.add(tool_call_id)

    apify_platform = apify_platform_from_actor_tool(tool_name)
    if apify_platform:
        _LAST_APIFY_HINT[task_id] = tool_name.replace("mcp_apify_", "")

    if status != "success":
        if tool_name in _STEP4_TOOLS:
            _on_step4_tool_after(store, task_id, tool_name, tool_args, success=False)
        elif platform:
            _sync_platform_collect_steps(
                store,
                task_id,
                platform,
                {},
                tool_name=tool_name,
                tool_ok=False,
                tool_args=tool_args,
            )
        return

    platform_hint = _LAST_APIFY_HINT.get(task_id, "")
    if tool_name == "mcp_apify_get_dataset_items":
        ds_id = str(tool_args.get("datasetId") or tool_args.get("dataset_id") or "").strip()
        platform_hint = resolve_apify_platform_hint(
            task_id, tool_output_id, dataset_id=ds_id or None
        )
        platform = _infer_platform(tool_name, tool_args, store.get_input_accounts(task_id), platform_hint) or platform

    ctx: Dict[str, Any] = {
        "task_id": task_id,
        "tool_output_id": tool_output_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "account_id": _extract_account_id(tool_args),
        "platform_hint": platform_hint,
    }

    result_data: Dict[str, Any] = {"profiles": [], "posts": [], "candidates": [], "platforms": []}
    try:
        result_data = dispatch(tool_name, tool_output, ctx)
    except Exception as exc:
        logger.exception("normalizer 失败 tool=%s task=%s: %s", tool_name, task_id, exc)
        if platform:
            _sync_platform_collect_steps(
                store,
                task_id,
                platform,
                {},
                tool_name=tool_name,
                tool_ok=False,
                tool_args=tool_args,
            )
        store.reconcile_profile_platform_steps(task_id)
        return

    # Apify dataset：有主页归 profile 子步骤；仅发文归 post 子步骤
    if tool_name == "mcp_apify_get_dataset_items" and platform:
        n_prof = len(result_data.get("profiles") or [])
        n_post = len(result_data.get("posts") or [])
        if n_prof > 0:
            output_step_key = profile_platform_step_key(platform)
        elif n_post > 0:
            output_step_key = post_platform_step_key(platform)
        else:
            # 主页尚未完成时先记到主页子步骤，避免误判为发文轮
            prof_cur = get_step_status(task_id, profile_platform_step_key(platform))
            if prof_cur not in {"completed", "skipped"}:
                output_step_key = profile_platform_step_key(platform)
            else:
                output_step_key = post_platform_step_key(platform)
        store.update_tool_output_phase(tool_output_id, output_step_key)

    _persist_normalized(store, task_id, result_data, platform=platform, tool_name=tool_name)
    for prof in result_data.get("profiles") or []:
        store.build_streams_from_profile(task_id, prof)

    if platform:
        _sync_platform_collect_steps(
            store,
            task_id,
            platform,
            result_data,
            tool_name=tool_name,
            tool_ok=True,
            tool_args=tool_args,
        )
    _try_complete_profiles(store, task_id)

    if tool_name in _STEP4_TOOLS:
        _on_step4_tool_after(store, task_id, tool_name, tool_args, success=True)


def _persist_normalized(
    store: TaskStore,
    task_id: str,
    data: Dict[str, Any],
    *,
    platform: Optional[str] = None,
    tool_name: str = "",
) -> None:
    prof_step = profile_platform_step_key(platform) if platform else "step3_profiles"
    post_step = post_platform_step_key(platform) if platform else "step3_profiles"
    for row in data.get("profiles") or []:
        store.save_profile_row(row, step_key=prof_step)
    posts = data.get("posts") or []
    if posts:
        store.save_post_rows(posts, step_key=post_step)


def _on_post_llm_call(payload: Dict[str, Any]) -> None:
    ex = _extra(payload)
    assistant = str(ex.get("assistant_response") or "").strip()
    user_message = str(ex.get("user_message") or "").strip()
    task_id = _resolve_task_id(payload, user_message=user_message)
    if not task_id:
        return
    store = _store()
    session_id = str(payload.get("session_id") or "").strip()
    try:
        _ensure_step1_completed(store, task_id, user_message=user_message, session_id=session_id)
        if not assistant or assistant == "(empty)":
            return
        _try_parse_step1(store, task_id, assistant, user_message)
        _try_style_analysis(store, task_id, assistant)
        _try_complete_profiles(store, task_id)
        if is_three_section_report(assistant):
            store.save_assistant_output(task_id, payload.get("session_id"), assistant)
            if get_step_status(task_id, "step5_validated") != "completed":
                _maybe_advance_step45(store, task_id)
        else:
            store.save_assistant_output(task_id, payload.get("session_id"), assistant)
    except DbError as exc:
        logger.warning("post_llm_call 失败 task=%s: %s", task_id, exc)


def _load_assistant_output_from_state(session_id: str) -> Optional[str]:
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
        if is_three_section_report(text):
            return text
    return None


def _on_session_end(payload: Dict[str, Any]) -> None:
    task_id = _resolve_task_id(payload)
    if not task_id:
        return
    session_id = str(payload.get("session_id") or "").strip() or None
    store = _store()
    try:
        user_message = str(_extra(payload).get("user_message") or "")
        _ensure_step1_completed(store, task_id, user_message=user_message, session_id=session_id or "")
        assistant = _load_assistant_output_from_state(session_id or "")
        if assistant:
            user_message = str(_extra(payload).get("user_message") or "")
            _try_parse_step1(store, task_id, assistant, user_message)
            _try_style_analysis(store, task_id, assistant)
            if is_three_section_report(assistant):
                store.save_assistant_output(task_id, session_id, assistant)
    except Exception as exc:
        logger.warning("on_session_end 兜底失败 task=%s: %s", task_id, exc)
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
