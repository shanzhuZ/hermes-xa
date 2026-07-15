"""Hermes Hook 入口 — 04 账号画像写报入库（stdin JSON）。

冲突约定（方案 C，见 docs/思考流与步骤树同步落库实施方案.md）：
- Java 中继可粗写 pending/running→running；Vision/OCR/Apify 禁止粗 completed；
- Hook 为细状态真相源。
"""

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
from collect_01.seed_platforms import (
    APIFY_SEED_PLATFORM_TOOLS,
    REPORT_SEED_PROFILE_TOOLS as SEED_PROFILE_TOOLS,
    TOOL_TO_SEED_PLATFORM,
    apify_seed_empty_ok,
    is_apify_seed_platform,
)
from report_04.account_parser import looks_like_seed_done, parse_seed_from_assistant
from report_04.candidate_parser import (
    looks_like_step3_summary,
    parse_web_search_candidates,
    parse_web_search_candidates_from_tool,
)
from report_04.gates import (
    analysis_steps_terminal,
    can_advance_to_analysis,
    can_advance_to_step5,
    can_advance_to_step7,
    can_complete_step11,
    can_run_step7_collect,
    can_run_step3_web_search,
    can_update_step4_children,
    can_update_step7_children,
    discovery_steps_terminal,
    get_step_status,
    is_stream_compare_ready,
    step7_collect_active,
)
from report_04.phases import (
    ANALYSIS_STEP_KEYS,
    APIFY_TOOL_PLATFORM,
    POST_TOOLS,
    PROFILE_TOOLS,
    STEP4_COLLECT_TOOLS,
    TOOL_PLATFORM,
    TOOL_PRIMARY_STEP,
    WEB_SEARCH_TOOLS,
    post_platform_step_key,
    profile_platform_step_key,
    tool_collect_step_key,
    tool_step_key,
)
from report_04.report_parser import (
    backfill_analysis_from_report,
    detect_skip_steps,
    is_final_report,
    is_progress_only,
    mentions_analysis_steps,
    parse_standalone_analysis_blocks,
)
from report_04.task_store import TaskStore, is_report_intent

_LOG_DIR = hermes_home() / "logs"
_LOG_FILE = _LOG_DIR / "report_04_sink.log"
logger = logging.getLogger(__name__)

_SKIP_STEP_TOOLS = frozenset({
    "skill_view", "clarify", "tool_search", "describe_tool", "todo", "terminal",
    "mcp_firecrawl_firecrawl_search", "mcp_firecrawl_firecrawl_scrape",
})
_STEP5_STREAM_TOOLS = frozenset({"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"})
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
    fmt = logging.Formatter("%(asctime)s [report_04] %(message)s")
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
        if row and str(row.get("task_type") or "") == "account_report":
            if session_id:
                store.bind_session(java_tid, session_id)
            return java_tid
        msg = (user_message or str(ex.get("user_message") or "")).strip()
        if msg and is_report_intent(msg):
            return store.ensure_task(session_id=session_id, user_message=msg, task_id=java_tid)
        if row and str(row.get("task_type") or "") == "account_report":
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
        if row and str(row.get("task_type") or "") == "account_report":
            if session_id and row.get("session_id") != session_id:
                store.bind_session(tid, session_id)
            return tid

    msg = (user_message or str(ex.get("user_message") or "")).strip()
    if msg and is_report_intent(msg):
        return store.ensure_task(session_id=session_id, user_message=msg)
    return None


def _prepare_step4_collect(store: TaskStore, task_id: str) -> bool:
    """步骤二、三均结束后，按候选去重生成步骤四子节点。"""
    if not can_update_step4_children(task_id):
        return False
    store.materialize_step4_from_candidates(task_id)
    return True


def _resolve_collect_phase(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    platform: Optional[str],
    *,
    seed_collect: bool,
) -> tuple[Optional[str], str]:
    if seed_collect:
        return None, "step1_seed"
    if tool_name in WEB_SEARCH_TOOLS:
        return None, "step3_web_search"
    if tool_name == "mcp_maigret_collect_accounts":
        return None, "step2_maigret"
    if tool_name.startswith("mcp_maigret_"):
        return None, "step2_maigret"
    if tool_name in _STEP5_STREAM_TOOLS:
        sk = tool_step_key(tool_name)
        return None, sk
    if tool_name in STEP4_COLLECT_TOOLS or (tool_name == "mcp_apify_get_dataset_items" and platform):
        _prepare_step4_collect(store, task_id)
        if platform and can_update_step4_children(task_id):
            if tool_name == "mcp_apify_get_dataset_items":
                cs = _resolve_dataset_collect_phase(task_id, platform)
            else:
                cs = tool_collect_step_key(tool_name, platform)
            return cs, cs
        return None, "step4_profiles"
    if tool_name in POST_TOOLS or (
        tool_name == "mcp_apify_get_dataset_items"
        and platform
        and step7_collect_active(task_id)
    ):
        if not can_run_step7_collect(task_id):
            # 步骤六完成前：仅记录 tool_outputs，不推进步骤七状态
            return None, post_platform_step_key(platform) if platform else "step4_profiles"
        store.materialize_step7_from_validated(task_id)
        if platform:
            cs = post_platform_step_key(platform)
            return cs, cs
    if platform:
        plat_step = tool_collect_step_key(tool_name, platform)
        if plat_step.startswith("step4_profile_") and not can_update_step4_children(task_id):
            return None, tool_step_key(tool_name)
        if plat_step.startswith("step7_post_") and not can_update_step7_children(task_id):
            return None, tool_step_key(tool_name)
        return plat_step, plat_step
    return None, tool_step_key(tool_name)


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
    if tool_name not in SEED_PROFILE_TOOLS:
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
    """生成种子失败文案，便于前端提示用户检查账号名。"""
    text = (tool_output or "").lower()
    if empty:
        # Apify 常返回空壳 profile（cleanItemCount=0），未必是账号不存在
        if "mcp_apify" in (tool_name or "") or "dataset" in text or '"type": "profile"' in text:
            return "种子主页 Apify 未返回有效资料（空结果），请稍后重试或核对账号名"
        return "种子主页采集无结果，请检查账号名是否正确后重试"
    if "does not exist" in text or "user not found" in text or "not found" in text:
        return "种子账号不存在（平台未找到），请检查账号名后重试"
    if "error" in text:
        # 截取可读片段
        snippet = (tool_output or "").replace("\n", " ").strip()
        if len(snippet) > 180:
            snippet = snippet[:180] + "…"
        return f"种子主页采集失败：{snippet}"
    return f"种子主页采集失败（{tool_name}），请检查账号名后重试"


def _task_is_terminal(store: TaskStore, task_id: str) -> bool:
    task = store.get_task(task_id) or {}
    return str(task.get("status") or "") in {"failed", "completed"}


def _maybe_stale_step5(store: TaskStore, task_id: str) -> None:
    """步骤五空等 Vision 超时则 fail-forward（会话中途也能解开）。"""
    try:
        from report_04.step_reconcile import maybe_fail_forward_stale_step5

        n = maybe_fail_forward_stale_step5(store, task_id, min_wait_seconds=90)
        if n:
            _maybe_advance_step67(store, task_id)
    except Exception as exc:
        logger.warning("step5 超时兜底失败 task=%s: %s", task_id, exc)


def _on_pre_llm(payload: Dict[str, Any]) -> None:
    ex = _extra(payload)
    user_message = str(ex.get("user_message") or "").strip()
    try:
        task_id = _resolve_task_id(payload, user_message=user_message)
        if not task_id:
            return
        store = _store()
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            store.bind_session(task_id, session_id)
        # 每轮 LLM 前检查：步骤五空等 Vision 超时则收口
        _maybe_stale_step5(store, task_id)
        if user_message and is_report_intent(user_message):
            _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id)
            if get_step_status(task_id, "step1_seed") == "pending":
                store.set_step_status(task_id, "step1_seed", "running", message="等待 Agent 确认种子账号…")
            if ex.get("is_first_turn") and not store.has_user_dialogue(task_id, "user_input"):
                store.save_dialogue(task_id, session_id, "user", user_message, "user_input")
    except DbError as exc:
        logger.warning("%s", exc)


def _enter_step5(store: TaskStore, task_id: str) -> None:
    gate = can_advance_to_step5(task_id)
    if not gate.get("ok"):
        logger.info("进入 step5 跳过 task=%s: %s", task_id, gate.get("message"))
        return
    if get_step_status(task_id, "step5_streams") == "pending":
        store.set_step_status(task_id, "step5_streams", "running", message="文本/图片流核查中")


def _maybe_advance_step67(store: TaskStore, task_id: str) -> None:
    gate5 = can_advance_to_step5(task_id)
    if not gate5.get("ok"):
        return
    from report_04.gates import count_image_streams

    if get_step_status(task_id, "step5_streams") != "completed":
        # 文本已比对方：只做轻量收口，避免反复整段文本比对导致 message 闪烁
        store.kickoff_step5_if_ready(task_id)
    n_img = count_image_streams(task_id)
    if is_stream_compare_ready(task_id) and get_step_status(task_id, "step5_streams") != "completed":
        msg = "无头像图片流，跳过图片比对" if n_img == 0 else "图片流 Vision 完成"
        store.set_step_status(task_id, "step5_streams", "completed", message=msg)
    if (
        get_step_status(task_id, "step5_streams") == "completed"
        and get_step_status(task_id, "step6_validated") != "completed"
    ):
        store.run_validated_accounts(task_id)
    if can_advance_to_step7(task_id).get("ok"):
        store.materialize_step7_from_validated(task_id)
        store.reconcile_collect_child_steps(task_id)


def _on_step5_tool_after(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    success: bool,
) -> None:
    # vision 可提前回写图片流进度，但不得在未满足门禁时推进步骤五状态
    matched = False
    if tool_name in _STEP5_STREAM_TOOLS:
        matched = bool(store.mark_image_stream_progress(task_id, tool_name, tool_args, success=success))
        # OCR 失败不阻塞：提示继续 vision；vision 未命中库内 URL 记日志
        if tool_name == "mcp_ocr_perform_ocr" and not success:
            logger.info("step5 OCR 失败/无字，继续 vision task=%s matched=%s", task_id, matched)
        elif tool_name in {"mcp_vision_analyze", "vision_analyze"} and not matched:
            logger.warning(
                "step5 vision 未匹配库内图片流，不计入进度 task=%s",
                task_id,
            )

    gate = can_advance_to_step5(task_id)
    if not gate.get("ok"):
        logger.info("step5 工具跳过状态推进 task=%s: %s", task_id, gate.get("message"))
        return
    from report_04.gates import count_image_streams, count_image_streams_processed

    n_img = count_image_streams(task_id)
    n_done = count_image_streams_processed(task_id)
    payload = store._step5_payload(task_id)
    wait_since = payload.get("wait_images_since")
    patch = {
        "image_pending": max(n_img - n_done, 0),
        "text_compare_done": bool(payload.get("text_compare_done")),
        "wait_images_since": wait_since,
    }
    if get_step_status(task_id, "step5_streams") == "pending":
        store.set_step_status(
            task_id,
            "step5_streams",
            "running",
            message=f"图片流比对中 {n_done}/{n_img}",
            payload=patch,
        )
    if not is_stream_compare_ready(task_id):
        if get_step_status(task_id, "step5_streams") not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                "step5_streams",
                "running",
                message=f"图片流比对中 {n_done}/{n_img}",
                payload=patch,
                touch_updated_at=False,
            )
        return
    if get_step_status(task_id, "step5_streams") != "completed":
        msg = "无头像图片流，跳过图片比对" if n_img == 0 else f"图片流 Vision 完成 ({n_done}/{n_img})"
        store.set_step_status(task_id, "step5_streams", "completed", message=msg)
    # 图片流齐后推进步骤六（不必等步骤七门禁）
    _maybe_advance_step67(store, task_id)


def _try_parse_seed(store: TaskStore, task_id: str, assistant: str, user_message: str) -> None:
    if get_step_status(task_id, "step1_seed") == "completed":
        return
    if not looks_like_seed_done(assistant) and not assistant.strip():
        return
    accounts = parse_seed_from_assistant(user_message=user_message, assistant_text=assistant)
    if not accounts:
        return
    store.mark_seed_completed(task_id, accounts, source="agent")
    logger.info("步骤1 解析账号 task=%s count=%d", task_id, len(accounts))


def _ensure_seed_from_dialogue(
    store: TaskStore,
    task_id: str,
    *,
    user_message: str = "",
    session_id: Optional[str] = None,
) -> None:
    """post_llm 未触发时：从 Agent 表格或用户原始输入兜底完成步骤一。"""
    if get_step_status(task_id, "step1_seed") == "completed":
        return
    assistant = _load_assistant_for_seed(session_id or "")
    if assistant and looks_like_seed_done(assistant):
        accounts = parse_seed_from_assistant(user_message=user_message, assistant_text=assistant)
        if accounts:
            store.mark_seed_completed(task_id, accounts, source="agent")
            logger.info("步骤1 兜底(assistant) task=%s count=%d", task_id, len(accounts))
            return
    msg = user_message.strip() or store.load_user_input_message(task_id)
    accounts = parse_seed_from_assistant(user_message=msg, assistant_text="")
    if accounts:
        store.mark_seed_completed(task_id, accounts, source="user_input")
        logger.info("步骤1 兜底(user_input) task=%s count=%d", task_id, len(accounts))


def _load_assistant_for_seed(session_id: str) -> str:
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
        if looks_like_seed_done(text):
            return text
    return ""


def _apply_skip_steps(store: TaskStore, task_id: str, text: str) -> None:
    for step_key in detect_skip_steps(text):
        if get_step_status(task_id, step_key) not in {"completed", "skipped"}:
            store.set_step_status(task_id, step_key, "skipped", message="单平台路径跳过")


def _mark_analysis_running(store: TaskStore, task_id: str) -> None:
    if not can_advance_to_analysis(task_id).get("ok"):
        return
    for step_key in ANALYSIS_STEP_KEYS:
        if get_step_status(task_id, step_key) == "pending":
            store.set_step_status(task_id, step_key, "running", message="分析进行中…")


def _maybe_complete_step3_after_web_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
) -> None:
    """步骤三：web 工具成功后解析候选，并在检索阶段结束时收口为 completed。"""
    if tool_name not in WEB_SEARCH_TOOLS:
        return
    if not can_run_step3_web_search(task_id):
        return
    if get_step_status(task_id, "step3_web_search") in {"completed", "skipped"}:
        return

    from collect_01 import db as _db

    row = _db.fetch_one(
        """
        SELECT
          SUM(tool_name='web_search' AND status='success') AS n_search,
          SUM(tool_name='web_extract' AND status='success') AS n_extract,
          SUM(tool_name LIKE 'browser_%%' AND status='success') AS n_browser
        FROM hermes_tool_outputs
        WHERE task_id=%s AND phase='step3_web_search'
        """,
        (task_id,),
    )
    n_search = int((row or {}).get("n_search") or 0)
    n_extract = int((row or {}).get("n_extract") or 0)
    n_browser = int((row or {}).get("n_browser") or 0)

    should_complete = False
    if tool_name == "web_extract" and n_extract >= 1:
        should_complete = True
    elif tool_name.startswith("browser_") and n_browser >= 1 and n_search >= 1:
        should_complete = True
    elif tool_name == "web_search" and n_search >= 3:
        should_complete = True

    if not should_complete:
        return

    n_web_cands = _db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM cross_platform_candidates
        WHERE task_id=%s AND match_strategy='web_search'
        """,
        (task_id,),
    )
    n_web = int((n_web_cands or {}).get("c") or 0)
    store.set_step_status(
        task_id,
        "step3_web_search",
        "completed",
        message=f"网页检索完成（search={n_search} extract={n_extract} 候选={n_web}）",
    )
    store.materialize_step4_from_candidates(task_id)


def _save_step3_candidates_from_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    raw_output: Any,
) -> None:
    """从 web 工具原始输出解析并保存步骤三候选。"""
    if tool_name not in WEB_SEARCH_TOOLS:
        return
    if not can_run_step3_web_search(task_id):
        return
    candidates = parse_web_search_candidates_from_tool(tool_name, raw_output)
    if not candidates:
        return
    for row in candidates:
        row["task_id"] = task_id
        row.setdefault("match_strategy", "web_search")
    store.save_candidate_rows(candidates, step_key="step3_web_search")


def _try_step3_web_candidates(store: TaskStore, task_id: str, assistant: str) -> None:
    text = assistant or ""
    if not text:
        return
    _apply_skip_steps(store, task_id, text)
    if is_progress_only(text) and mentions_analysis_steps(text):
        _mark_analysis_running(store, task_id)
        return
    if not can_run_step3_web_search(task_id):
        return
    if looks_like_step3_summary(text):
        candidates = parse_web_search_candidates(text)
        for row in candidates:
            row["task_id"] = task_id
            row["match_strategy"] = row.get("discovery_source") or "web_search"
        if candidates:
            store.save_candidate_rows(candidates, step_key="step3_web_search")
        if get_step_status(task_id, "step3_web_search") not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                "step3_web_search",
                "completed",
                message=f"网页检索候选 {len(candidates)} 个",
            )
            store.materialize_step4_from_candidates(task_id)
    if is_progress_only(text):
        return
    blocks = parse_standalone_analysis_blocks(text)
    for step_key, content in blocks.items():
        if get_step_status(task_id, step_key) not in {"completed", "skipped"}:
            store.save_analysis_display(task_id, step_key, content, source="standalone")
    if mentions_analysis_steps(text):
        _mark_analysis_running(store, task_id)


def _complete_step11_from_report(
    store: TaskStore,
    task_id: str,
    assistant: str,
    session_id: Optional[str],
) -> None:
    if get_step_status(task_id, "step11_report") == "completed":
        return
    backfill = backfill_analysis_from_report(assistant)
    for step_key, content in backfill.items():
        if get_step_status(task_id, step_key) not in {"completed", "skipped"}:
            store.save_analysis_display(task_id, step_key, content, source="backfill_from_step11")
    for step_key in ANALYSIS_STEP_KEYS:
        if get_step_status(task_id, step_key) not in {"completed", "skipped"}:
            store.set_step_status(task_id, step_key, "skipped", message="终稿已出，未单独输出")
    if not can_complete_step11(task_id).get("ok"):
        logger.info("终稿已到但步骤8～10未收口 task=%s", task_id)
        return
    store.save_assistant_output(task_id, session_id, assistant)
    store.set_step_status(task_id, "step11_report", "completed", message="画像报告已生成")
    from report_04.phases import PHASE_DONE

    store.set_task_phase(task_id, PHASE_DONE)


def _infer_platform(
    tool_name: str,
    tool_args: Dict[str, Any],
    accounts: List[Dict[str, Any]],
    platform_hint: str = "",
) -> Optional[str]:
    if tool_name in WEB_SEARCH_TOOLS or tool_name == "mcp_maigret_collect_accounts":
        return None
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


def _resolve_dataset_collect_phase(task_id: str, platform: str) -> str:
    """步骤四拉主页 dataset vs 步骤七拉发文 dataset。"""
    prof_key = profile_platform_step_key(platform)
    if not can_run_step7_collect(task_id):
        return prof_key
    prof_st = get_step_status(task_id, prof_key)
    if prof_st in {"running", "pending"}:
        return prof_key
    return post_platform_step_key(platform)


def _sync_platform_collect_steps(
    store: TaskStore,
    task_id: str,
    platform: str,
    result_data: Dict[str, Any],
    *,
    tool_name: str,
    tool_ok: bool,
) -> None:
    if not platform:
        return
    if not can_update_step4_children(task_id):
        return
    prof_key = profile_platform_step_key(platform)
    post_key = post_platform_step_key(platform)
    store.ensure_step_row(task_id, prof_key)

    n_prof = len(result_data.get("profiles") or [])
    n_post = len(result_data.get("posts") or [])
    if n_post == 0 and tool_name in POST_TOOLS and tool_ok and can_run_step7_collect(task_id):
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
        elif cur not in {"completed", "skipped"}:
            # 首次失败（如 YouTube @handle）保持 running，等待 channelId 重试
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
    elif tool_name == "mcp_apify_get_dataset_items" and tool_ok and not can_run_step7_collect(task_id):
        cur = get_step_status(task_id, prof_key)
        if n_prof > 0 and cur != "completed":
            store.set_step_status(task_id, prof_key, "completed", message=f"已入库主页 {n_prof} 条")
        elif n_prof == 0 and cur == "running":
            from report_04.step_reconcile import _dataset_success_for_profile

            if _dataset_success_for_profile(task_id, platform):
                store.set_step_status(
                    task_id,
                    prof_key,
                    "skipped",
                    message=f"{platform} Apify 已拉取 dataset 但未入库主页",
                )

    if can_run_step7_collect(task_id):
        store.ensure_step_row(task_id, post_key)
        if n_post > 0:
            store.set_step_status(task_id, post_key, "completed", message=f"已入库发文 {n_post} 条")
        elif tool_name in POST_TOOLS and tool_ok:
            cur = get_step_status(task_id, post_key)
            if n_post == 0 and tool_name == "mcp_apify_get_dataset_items":
                if cur not in {"completed", "skipped"}:
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

    store.reconcile_collect_child_steps(task_id)


def _try_complete_profiles(store: TaskStore, task_id: str) -> None:
    store.reconcile_collect_child_steps(task_id)
    if get_step_status(task_id, "step4_profiles") in {"completed", "skipped"}:
        store.kickoff_step5_if_ready(task_id)


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

    # 任务已失败/完成：仅保留工具审计，不再推进任何步骤
    if _task_is_terminal(store, task_id):
        result = ex.get("result")
        if isinstance(result, (dict, list)):
            tool_output = json.dumps(result, ensure_ascii=False, default=str)
        else:
            tool_output = str(result or "")
        status = "success" if ex.get("status") == "ok" else "error"
        tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
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

    _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id)

    tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    input_accounts = store.get_seed_accounts(task_id)
    platform_hint_early = _LAST_APIFY_HINT.get(task_id, "")
    platform = _infer_platform(tool_name, tool_args, input_accounts, platform_hint_early)
    seed_collect = _is_seed_profile_tool(tool_name, task_id)
    collect_step, output_step_key = _resolve_collect_phase(
        store, task_id, tool_name, platform, seed_collect=seed_collect
    )

    if seed_collect:
        if get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}:
            store.set_step_status(task_id, "step1_seed", "running", message=f"种子采集中 ({tool_name})")
    else:
        primary_step = TOOL_PRIMARY_STEP.get(tool_name)
        if primary_step and tool_name not in _STEP5_STREAM_TOOLS:
            if primary_step == "step4_profiles" and not discovery_steps_terminal(task_id):
                pass
            elif primary_step == "step3_web_search" and not can_run_step3_web_search(task_id):
                pass
            elif primary_step == "step7_posts" and not can_run_step7_collect(task_id):
                pass
            else:
                cur = get_step_status(task_id, primary_step)
                if cur not in {"completed", "failed", "skipped"}:
                    store.set_step_status(task_id, primary_step, "running", message=f"执行 {tool_name}")
    if collect_step and not seed_collect:
        blocked = (
            (collect_step.startswith("step4_profile_") and not can_update_step4_children(task_id))
            or (collect_step.startswith("step7_post_") and not can_update_step7_children(task_id))
        )
        if not blocked:
            child_cur = get_step_status(task_id, collect_step)
            if child_cur not in {"completed", "failed", "skipped"}:
                kind = "发文" if collect_step.startswith("step7_post_") else "主页"
                store.set_step_status(task_id, collect_step, "running", message=f"{platform} {kind}采集中…")

    if tool_name in _STEP5_STREAM_TOOLS:
        _enter_step5(store, task_id)

    result = ex.get("result")
    if isinstance(result, (dict, list)):
        tool_output = json.dumps(result, ensure_ascii=False, default=str)
    else:
        tool_output = str(result or "")

    status = "success" if ex.get("status") == "ok" else "error"

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

    # OCR/Vision 快路径：无 profile/post 产物，必须先写图片流再退出。
    # GPT 并行多工具时 db_sink 易在尾部超时，导致 tool_outputs 已成功、步骤五永远 pending。
    if tool_name in _STEP5_STREAM_TOOLS:
        _enter_step5(store, task_id)
        try:
            _on_step5_tool_after(
                store,
                task_id,
                tool_name,
                tool_args,
                success=(status == "success"),
            )
            if status == "success" and is_stream_compare_ready(task_id):
                _maybe_advance_step67(store, task_id)
        except Exception as exc:
            logger.exception("step5 快路径失败 task=%s tool=%s: %s", task_id, tool_name, exc)
        return

    apify_platform = apify_platform_from_actor_tool(tool_name)
    if apify_platform:
        _LAST_APIFY_HINT[task_id] = tool_name.replace("mcp_apify_", "")

    # 方案 A：种子工具失败或空结果 → 任务硬失败，拦住后续步骤
    # Apify Actor / get_actor_run 成功时通常尚无 profile，不算失败
    if seed_collect:
        if status != "success":
            fail_msg = _seed_fail_message(tool_name, tool_output, empty=False)
            store.fail_seed_and_abort(task_id, fail_msg)
            logger.warning("种子采集失败(工具error) task=%s tool=%s: %s", task_id, tool_name, fail_msg)
            return

    if status != "success":
        if platform and can_update_step4_children(task_id):
            _prepare_step4_collect(store, task_id)
            _sync_platform_collect_steps(
                store, task_id, platform, {}, tool_name=tool_name, tool_ok=False
            )
        return

    platform_hint = _LAST_APIFY_HINT.get(task_id, "")
    if tool_name == "mcp_apify_get_dataset_items":
        ds_id = str(tool_args.get("datasetId") or tool_args.get("dataset_id") or "").strip()
        platform_hint = resolve_apify_platform_hint(
            task_id, tool_output_id, dataset_id=ds_id or None
        )
        platform = _infer_platform(tool_name, tool_args, store.get_seed_accounts(task_id), platform_hint) or platform
        if seed_collect:
            output_step_key = "step1_seed"
            collect_step = None
            store.update_tool_output_phase(tool_output_id, "step1_seed")
        elif platform:
            ds_phase = _resolve_dataset_collect_phase(task_id, platform)
            output_step_key = ds_phase
            collect_step = ds_phase
            store.update_tool_output_phase(tool_output_id, ds_phase)

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
        if seed_collect:
            fail_msg = _seed_fail_message(tool_name, str(exc), empty=True)
            store.fail_seed_and_abort(task_id, fail_msg)
            return
        if platform:
            _prepare_step4_collect(store, task_id)
            _sync_platform_collect_steps(
                store, task_id, platform, {}, tool_name=tool_name, tool_ok=False
            )
        store.reconcile_collect_child_steps(task_id)
        return

    if seed_collect:
        profs = result_data.get("profiles") or []
        if not profs:
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
        if tool_name == "mcp_apify_get_dataset_items":
            n_drop = len(result_data.get("posts") or [])
            result_data = _strip_apify_posts_for_seed_phase(result_data)
            if n_drop:
                logger.info("Apify 种子轮丢弃 posts=%d task=%s", n_drop, task_id)

    _persist_normalized(store, task_id, result_data, platform=platform, tool_name=tool_name)

    seed_plat = _task_seed_platform(task_id)
    is_mcp_seed_tool = tool_name in TOOL_TO_SEED_PLATFORM and not tool_name.startswith("mcp_apify_")
    is_apify_seed_dataset = (
        tool_name == "mcp_apify_get_dataset_items"
        and is_apify_seed_platform(seed_plat)
        and get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}
    )
    if (
        (is_mcp_seed_tool or is_apify_seed_dataset)
        and (result_data.get("profiles") or [])
        and get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}
        and not _task_is_terminal(store, task_id)
        and not apify_seed_empty_ok(tool_name)
    ):
        store.mark_seed_completed(task_id, result_data.get("profiles") or [], source="tool")

    if tool_name == "mcp_maigret_collect_accounts":
        cands = result_data.get("candidates") or []
        store.set_step_status(
            task_id,
            "step2_maigret",
            "completed",
            message=f"Maigret 发现 {len(cands)} 个候选",
        )
    elif tool_name.startswith("mcp_maigret_"):
        cur2 = get_step_status(task_id, "step2_maigret")
        if cur2 == "pending":
            store.set_step_status(task_id, "step2_maigret", "running", message=f"Maigret 执行中 ({tool_name})")
        cands = result_data.get("candidates") or []
        if cands and get_step_status(task_id, "step2_maigret") not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                "step2_maigret",
                "completed",
                message=f"Maigret 发现 {len(cands)} 个候选",
            )

    if tool_name in WEB_SEARCH_TOOLS:
        if get_step_status(task_id, "step3_web_search") not in {"completed", "skipped"}:
            if can_run_step3_web_search(task_id):
                store.set_step_status(task_id, "step3_web_search", "running", message=f"网页检索中 ({tool_name})")
            else:
                logger.info(
                    "step3 跳过 tool=%s task=%s: step2_maigret=%s",
                    tool_name,
                    task_id,
                    get_step_status(task_id, "step2_maigret"),
                )
        _save_step3_candidates_from_tool(store, task_id, tool_name, result)
        _maybe_complete_step3_after_web_tool(store, task_id, tool_name)

    for prof in result_data.get("profiles") or []:
        store.build_streams_from_profile(task_id, prof)

    if seed_collect:
        return

    if platform and can_update_step4_children(task_id):
        _sync_platform_collect_steps(
            store, task_id, platform, result_data, tool_name=tool_name, tool_ok=True
        )
    _try_complete_profiles(store, task_id)
    _maybe_stale_step5(store, task_id)


def _persist_normalized(
    store: TaskStore,
    task_id: str,
    data: Dict[str, Any],
    *,
    platform: Optional[str] = None,
    tool_name: str = "",
) -> None:
    seed_plat = _task_seed_platform(task_id)
    is_seed_persist = (
        get_step_status(task_id, "step1_seed") not in {"completed", "skipped", "failed"}
        and (
            (tool_name in TOOL_TO_SEED_PLATFORM and not tool_name.startswith("mcp_apify_"))
            or (
                is_apify_seed_platform(seed_plat)
                and tool_name
                in {
                    APIFY_SEED_PLATFORM_TOOLS.get(seed_plat),
                    "mcp_apify_get_actor_run",
                    "mcp_apify_get_dataset_items",
                }
            )
        )
    )
    prof_step = "step1_seed" if is_seed_persist else (
        profile_platform_step_key(platform) if platform else "step4_profiles"
    )
    post_step = post_platform_step_key(platform) if platform else "step4_profiles"
    for row in data.get("profiles") or []:
        store.save_profile_row(row, step_key=prof_step)
    for row in data.get("candidates") or []:
        row["task_id"] = task_id
        if tool_name.startswith("mcp_maigret_"):
            step_key = "step2_maigret"
            match_strategy = "maigret"
        elif tool_name in WEB_SEARCH_TOOLS:
            step_key = "step3_web_search"
            match_strategy = "web_search"
        else:
            step_key = "step3_web_search"
            match_strategy = row.get("discovery_source") or "web_search"
        row.setdefault("match_strategy", match_strategy)
        if step_key == "step3_web_search" and not can_run_step3_web_search(task_id):
            continue
        store.save_candidate_rows([row], step_key=step_key)
    posts = data.get("posts") or []
    if posts:
        # 发文数据始终入库（Agent 常提前调工具）；步骤七状态由门禁单独控制
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
        _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id)
        if not assistant or assistant == "(empty)":
            return
        _try_parse_seed(store, task_id, assistant, user_message)
        _try_step3_web_candidates(store, task_id, assistant)
        _try_complete_profiles(store, task_id)
        # 中途兜底：回放已成功的 vision；若长时间空等 Vision 则 fail-forward 解开 step5
        try:
            from report_04.step_reconcile import (
                _reconcile_vision_from_tools,
                maybe_fail_forward_stale_step5,
            )

            _reconcile_vision_from_tools(store, task_id)
            maybe_fail_forward_stale_step5(store, task_id, min_wait_seconds=90)
            _maybe_advance_step67(store, task_id)
        except Exception as exc:
            logger.warning("post_llm vision 回放失败 task=%s: %s", task_id, exc)
        if is_final_report(assistant):
            if get_step_status(task_id, "step6_validated") != "completed":
                _maybe_advance_step67(store, task_id)
            _complete_step11_from_report(store, task_id, assistant, session_id or payload.get("session_id"))
        elif not is_progress_only(assistant):
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
        if is_final_report(text):
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
        _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id or "")
        assistant = _load_assistant_output_from_state(session_id or "")
        if assistant:
            user_message = str(_extra(payload).get("user_message") or "")
            _try_parse_seed(store, task_id, assistant, user_message)
            _try_step3_web_candidates(store, task_id, assistant)
            if is_final_report(assistant):
                if get_step_status(task_id, "step6_validated") != "completed":
                    _maybe_advance_step67(store, task_id)
                _complete_step11_from_report(store, task_id, assistant, session_id)
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
