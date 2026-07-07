"""Hermes Hook 入口 — 01 账号采集入库（stdin JSON）。"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional

from collect_01.config import hermes_home
from collect_01.db import DbError
from collect_01.normalizers.base import infer_mcp_server
from collect_01.normalizers.registry import dispatch
from collect_01.phases import (
    APIFY_POST_TOOLS,
    TOOL_POST_PLATFORM,
    TOOL_PRIMARY_STEP,
    post_step_key,
)
from collect_01.task_store import TaskStore, is_collect_intent

_LOG_DIR = hermes_home() / "logs"
_LOG_FILE = _LOG_DIR / "collect_01_sink.log"
logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [collect_01] %(message)s")
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

# 记录最近一次 Apify Actor，供 get_dataset_items 推断平台
_LAST_APIFY_HINT: Dict[str, str] = {}
# 不参与业务步骤推进的工具
_SKIP_STEP_TOOLS = frozenset({"skill_view", "clarify", "tool_search", "describe_tool"})
# 每个任务已处理的工具调用（防重）
_SEEN_TOOL_CALLS: set = set()


def handle_event(payload: Dict[str, Any]) -> None:
    event = payload.get("hook_event_name") or ""
    if event == "pre_llm_call":
        _on_pre_llm(payload)
    elif event == "post_tool_call":
        _on_post_tool(payload)
    elif event in {"post_llm_call", "on_session_end"}:
        _on_turn_end(payload)
    else:
        return


def _extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    ex = payload.get("extra")
    return ex if isinstance(ex, dict) else {}


def _store() -> TaskStore:
    return TaskStore()


def _resolve_task_id(payload: Dict[str, Any], user_message: str = "") -> Optional[str]:
    ex = _extra(payload)
    tid = str(ex.get("task_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    store = _store()
    if tid:
        row = store.get_task(tid)
        if row:
            return row["task_id"]
    if session_id:
        row = store.get_task_by_session(session_id)
        if row:
            return row["task_id"]
    msg = (user_message or str(ex.get("user_message") or "")).strip()
    if msg and is_collect_intent(msg):
        return store.ensure_task(session_id=session_id, user_message=msg)
    # post_tool 兜底：pre_llm 失败时，仅从 MCP 参数推断种子（禁止「自动补建」脏任务）
    tool_name = str(payload.get("tool_name") or "")
    if session_id and tool_name.startswith("mcp_"):
        tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        handle = _extract_account_id(tool_args) or ""
        if handle:
            seed_msg = f"account-intelligence-collect 采集 @{handle}"
            return store.ensure_task(session_id=session_id, user_message=seed_msg)
    return None


def _on_pre_llm(payload: Dict[str, Any]) -> None:
    ex = _extra(payload)
    user_message = str(ex.get("user_message") or "").strip()
    if not user_message:
        return
    if not is_collect_intent(user_message):
        return
    try:
        task_id = _resolve_task_id(payload, user_message=user_message)
        if not task_id:
            return
        store = _store()
        store.set_step_status(task_id, "step1_seed", "running", message="等待种子账号资料采集…")
        # 仅首轮写入用户原话，避免每轮 pre_llm 重复插 dialogue
        if ex.get("is_first_turn"):
            store.save_dialogue(
                task_id,
                payload.get("session_id"),
                "user",
                user_message,
                "user_input",
            )
    except DbError as exc:
        logger.warning("%s", exc)


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

    store = _store()
    if tool_name in _SKIP_STEP_TOOLS:
        if tool_name == "clarify":
            logger.warning("采集任务禁止 clarify，模型仍调用了 clarify session=%s", payload.get("session_id"))
        return

    step_key = TOOL_PRIMARY_STEP.get(tool_name)
    if step_key:
        store.set_step_status(task_id, step_key, "running", message=f"执行 {tool_name}")

    tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    result = ex.get("result")
    if isinstance(result, (dict, list)):
        tool_output = json.dumps(result, ensure_ascii=False, default=str)
    else:
        tool_output = str(result or "")

    status = "success" if ex.get("status") == "ok" else "error"
    phase = store.get_task(task_id)
    current_phase = (phase or {}).get("current_phase")

    try:
        tool_output_id = store.save_tool_output(
            task_id=task_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_output=tool_output,
            tool_call_id=tool_call_id or f"anon-{tool_name}-{len(_SEEN_TOOL_CALLS)}",
            duration_ms=ex.get("duration_ms"),
            status=status,
            phase=current_phase,
            mcp_server=infer_mcp_server(tool_name),
        )
    except DbError as exc:
        logger.warning("%s", exc)
        return

    if tool_call_id:
        _SEEN_TOOL_CALLS.add(tool_call_id)

    # Apify Actor 上下文
    if tool_name in APIFY_POST_TOOLS:
        hint = tool_name.replace("mcp_apify_", "")
        _LAST_APIFY_HINT[task_id] = hint

    ctx: Dict[str, Any] = {
        "task_id": task_id,
        "tool_output_id": tool_output_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "account_id": _extract_account_id(tool_args),
        "platform_hint": _LAST_APIFY_HINT.get(task_id, ""),
    }

    if status != "success":
        if step_key:
            store.set_step_status(task_id, step_key, "failed", message=ex.get("error_message") or "工具失败")
        return

    result_data = dispatch(tool_name, tool_output, ctx)
    _persist_normalized(store, task_id, result_data)

    _update_steps_after_tool(store, task_id, tool_name, step_key, result_data)

    # 子步骤：分平台发文（步骤三 profile 阶段的 dataset 不得误标 step6 完成）
    post_platform = TOOL_POST_PLATFORM.get(tool_name)
    if not post_platform and tool_name == "mcp_apify_get_dataset_items":
        plats = result_data.get("platforms") or []
        post_platform = plats[0] if plats else None
    post_count = len(result_data.get("posts") or [])
    if post_platform and (tool_name in TOOL_POST_PLATFORM or post_count > 0):
        child = post_step_key(post_platform)
        store.ensure_post_steps(task_id, [post_platform])
        child_status = "completed" if post_count > 0 else "pending"
        store.set_step_status(
            task_id,
            child,
            child_status,
            message=f"已采集 {post_platform} 发文 {post_count} 条",
            payload={"post_count": post_count},
        )

    # 步骤三～五：profile / 候选入库后触发规则流水线
    if result_data.get("profiles"):
        for prof in result_data["profiles"]:
            store.build_streams_from_profile(task_id, prof)
    if tool_name == "mcp_maigret_collect_accounts":
        store.set_step_status(task_id, "step2_cross_platform", "completed", message="Maigret 跨平台扫描完成")
    if tool_name in {"mcp_ocr_perform_ocr", "mcp_vision_analyze"}:
        store.set_step_status(task_id, "step4_image_compare", "running")
        store.set_step_status(task_id, "step4_image_compare", "completed", message="图片流 OCR/Vision 完成")

    _maybe_advance_pipeline(store, task_id)


def _update_steps_after_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    step_key: Optional[str],
    result_data: Dict[str, Any],
) -> None:
    if tool_name == "mcp_twitter_get_user_info":
        store.set_step_status(task_id, "step1_seed", "completed", message="种子 Twitter 资料已入库")
        return
    if tool_name == "mcp_maigret_collect_accounts":
        return
    if step_key == "step3_profiles":
        n_prof = len(result_data.get("profiles") or [])
        n_post = len(result_data.get("posts") or [])
        if n_prof or n_post:
            store.set_step_status(
                task_id,
                "step3_profiles",
                "completed",
                message=f"{tool_name} 完成，profile {n_prof} 条",
            )
        elif tool_name == "mcp_apify_get_dataset_items":
            store.set_step_status(
                task_id,
                "step3_profiles",
                "running",
                message=f"{tool_name} 无 profile 数据，已记录",
            )
        else:
            store.set_step_status(task_id, step_key, "completed", message=f"{tool_name} 完成")
        return
    if step_key:
        store.set_step_status(task_id, step_key, "completed", message=f"{tool_name} 完成")


def _persist_normalized(store: TaskStore, task_id: str, data: Dict[str, Any]) -> None:
    for row in data.get("profiles") or []:
        store.save_profile_row(row)
    for row in data.get("candidates") or []:
        store.save_candidate_rows([row])
    posts = data.get("posts") or []
    if posts:
        store.save_post_rows(posts)
    plats = data.get("platforms") or []
    if plats:
        store.ensure_post_steps(task_id, plats)


def _maybe_advance_pipeline(store: TaskStore, task_id: str) -> None:
    """在有关键数据后自动推进文本比对与可信账号收敛。"""
    from collect_01 import db

    task = store.get_task(task_id) or {}
    seed = {}
    try:
        import json as _json

        seed = _json.loads(task.get("seed_json") or "{}")
    except Exception:
        pass
    seed_platform = seed.get("platform", "twitter")
    seed_profile = db.fetch_one(
        "SELECT 1 AS ok FROM collect_profiles WHERE task_id=%s AND platform=%s LIMIT 1",
        (task_id, seed_platform),
    )
    if not seed_profile:
        logger.info("跳过流水线推进：种子平台 %s 尚无 profile task=%s", seed_platform, task_id)
        return
    n = db.fetch_one("SELECT COUNT(*) AS c FROM collect_profiles WHERE task_id=%s", (task_id,))
    if int((n or {}).get("c") or 0) < 1:
        return
    store.run_text_compare(task_id)
    store.run_validated_accounts(task_id)


def _on_turn_end(payload: Dict[str, Any]) -> None:
    task_id = _resolve_task_id(payload)
    if not task_id:
        return
    try:
        _store().finalize_task(task_id)
    except DbError as exc:
        logger.warning("%s", exc)


def _extract_account_id(tool_args: Dict[str, Any]) -> Optional[str]:
    for key in ("user_id", "username", "screen_name", "channelId", "channel_id", "uid"):
        val = tool_args.get(key)
        if val:
            return str(val)
    return None


def _read_stdin() -> str:
    """读取 Hermes Hook stdin。Windows 下父进程 subprocess text=True 常为 GBK。"""
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
        payload = json.loads(raw)
        if isinstance(payload, dict):
            handle_event(payload)
    except DbError as exc:
        logger.warning("%s", exc)
    except Exception as exc:
        logger.exception("collect_01 sink 异常: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
