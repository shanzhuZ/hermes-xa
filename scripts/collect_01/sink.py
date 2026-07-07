"""Hermes Hook 入口 — 01 账号采集入库（stdin JSON）。"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, Optional

from collect_01.config import hermes_home
from collect_01.db import DbError
from collect_01.gates import get_step_status
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

# 记录最近一次 Apify Actor，供 get_dataset_items 推断平台
_LAST_APIFY_HINT: Dict[str, str] = {}
# 不参与业务步骤推进的工具
_SKIP_STEP_TOOLS = frozenset({"skill_view", "clarify", "tool_search", "describe_tool", "todo", "terminal"})
# 步骤四入口工具（首次调用时收口步骤三）
_STEP4_TOOLS = frozenset({"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"})
# 每个任务已处理的工具调用（防重）
_SEEN_TOOL_CALLS: set = set()


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
        if get_step_status(task_id, "step1_seed") not in {"completed", "running"}:
            store.set_step_status(task_id, "step1_seed", "running", message="等待种子账号资料采集…")
        task = store.get_task(task_id) or {}
        if int(task.get("cross_platform") or 0) == 1:
            if get_step_status(task_id, "step2_cross_platform") not in {"completed", "running", "skipped"}:
                store.set_step_status(task_id, "step2_cross_platform", "running", message="等待 Maigret 跨平台扫描…")
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


def _mark_step3_closed(store: TaskStore, task_id: str) -> None:
    """进入步骤四时收口步骤三（仅首次）。"""
    if get_step_status(task_id, "step3_profiles") != "completed":
        store.set_step_status(
            task_id,
            "step3_profiles",
            "completed",
            message="进入步骤四，候选主页采集结束",
        )


def _enter_step4(store: TaskStore, task_id: str) -> None:
    """首次 OCR/Vision：收口步骤三，启动步骤四子步骤（不提前 completed）。"""
    _mark_step3_closed(store, task_id)
    if get_step_status(task_id, "step3_streams") == "pending":
        store.set_step_status(task_id, "step3_streams", "running", message="文本/图片流拆分中")
    if get_step_status(task_id, "step4_image_compare") == "pending":
        store.set_step_status(task_id, "step4_image_compare", "running", message="图片流 OCR/Vision 比对中")


def _on_step4_tool_success(store: TaskStore, task_id: str) -> None:
    """OCR/Vision 成功后按头像进度更新图片比对；全部就绪后再推进文本比对与步骤五。"""
    from collect_01.gates import count_image_streams, count_step4_tool_success, is_image_compare_ready

    n_img = count_image_streams(task_id)
    n_done = count_step4_tool_success(task_id)
    if not is_image_compare_ready(task_id):
        store.set_step_status(
            task_id,
            "step4_image_compare",
            "running",
            message=f"图片流比对中 {n_done}/{n_img}",
        )
        return
    if get_step_status(task_id, "step4_image_compare") != "completed":
        msg = "无头像图片流，跳过图片比对" if n_img == 0 else f"图片流 OCR/Vision 完成 ({n_done}/{n_img})"
        store.set_step_status(task_id, "step4_image_compare", "completed", message=msg)
    _advance_text_and_validate(store, task_id)


def _advance_text_and_validate(store: TaskStore, task_id: str) -> None:
    """图片比对完成后：文本流比对 → 流拆分收口 → 可信账号收敛。"""
    from collect_01.gates import can_advance_to_step45

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        logger.info("跳过流水线推进 task=%s: %s", task_id, gate.get("message"))
        return
    if get_step_status(task_id, "step4_image_compare") != "completed":
        return
    if get_step_status(task_id, "step4_text_compare") != "completed":
        store.set_step_status(task_id, "step4_text_compare", "running", message="文本流规则比对中")
        store.run_text_compare(task_id)
    if get_step_status(task_id, "step3_streams") != "completed":
        store.set_step_status(task_id, "step3_streams", "completed", message="文本/图片流拆分完成")
    if get_step_status(task_id, "step5_validated") != "completed":
        store.run_validated_accounts(task_id)


def _advance_without_image_tools(store: TaskStore, task_id: str) -> None:
    """未调用 OCR/Vision 时的兜底：图片流保持 pending，仅推进文本比对与步骤五。"""
    from collect_01.gates import can_advance_to_step45

    _mark_step3_closed(store, task_id)
    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        logger.info("跳过无图像工具兜底推进 task=%s: %s", task_id, gate.get("message"))
        return
    if get_step_status(task_id, "step3_streams") == "pending":
        store.set_step_status(task_id, "step3_streams", "running", message="文本/图片流拆分中（未调用 OCR/Vision）")
    if get_step_status(task_id, "step4_image_compare") == "pending":
        store.set_step_status(task_id, "step4_image_compare", "pending", message="未调用 OCR/Vision，头像图片流保留 pending")
    if get_step_status(task_id, "step4_text_compare") != "completed":
        store.set_step_status(task_id, "step4_text_compare", "running", message="文本流规则比对中")
        store.run_text_compare(task_id)
    if get_step_status(task_id, "step3_streams") != "completed":
        store.set_step_status(task_id, "step3_streams", "completed", message="文本流拆分完成，图片流待补充 OCR/Vision")
    if get_step_status(task_id, "step5_validated") != "completed":
        store.run_validated_accounts(task_id)


def _maybe_advance_pipeline(store: TaskStore, task_id: str) -> None:
    """兼容旧调用；须 step4_image_compare 已 completed。"""
    _advance_text_and_validate(store, task_id)


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

    if tool_name in _STEP4_TOOLS:
        _enter_step4(store, task_id)

    if tool_name == "mcp_maigret_collect_accounts":
        logger.info("maigret post_tool task=%s call_id=%s", task_id, tool_call_id or "(none)")

    step_key = TOOL_PRIMARY_STEP.get(tool_name)
    if step_key:
        cur = get_step_status(task_id, step_key)
        if cur not in {"completed", "failed", "skipped"}:
            label = "Maigret 跨平台扫描中" if tool_name == "mcp_maigret_collect_accounts" else f"执行 {tool_name}"
            store.set_step_status(task_id, step_key, "running", message=label)

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
        logger.warning("写 tool_outputs 失败 tool=%s task=%s: %s", tool_name, task_id, exc)
        return
    except Exception as exc:
        logger.exception("写 tool_outputs 异常 tool=%s task=%s: %s", tool_name, task_id, exc)
        return

    if tool_call_id:
        _SEEN_TOOL_CALLS.add(tool_call_id)

    if tool_name in APIFY_POST_TOOLS:
        _LAST_APIFY_HINT[task_id] = tool_name.replace("mcp_apify_", "")

    ctx: Dict[str, Any] = {
        "task_id": task_id,
        "tool_output_id": tool_output_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "account_id": _extract_account_id(tool_args),
        "platform_hint": _LAST_APIFY_HINT.get(task_id, ""),
    }

    if status != "success":
        if tool_name in _STEP4_TOOLS:
            store.set_step_status(
                task_id,
                "step4_image_compare",
                "pending",
                message=str(ex.get("error_message") or "图片 OCR/Vision 失败")[:500],
            )
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

    _persist_normalized(store, task_id, result_data)
    _update_steps_after_tool(store, task_id, tool_name, result_data)

    if result_data.get("profiles"):
        for prof in result_data["profiles"]:
            store.build_streams_from_profile(task_id, prof)

    if tool_name == "mcp_maigret_collect_accounts":
        n_cand = len(result_data.get("candidates") or [])
        store.set_step_status(
            task_id,
            "step2_cross_platform",
            "completed",
            message=f"Maigret 跨平台扫描完成，候选 {n_cand} 条",
        )

    if tool_name in TOOL_POST_PLATFORM and get_step_status(task_id, "step5_validated") != "completed":
        _advance_without_image_tools(store, task_id)

    post_platform = TOOL_POST_PLATFORM.get(tool_name)
    if not post_platform and tool_name == "mcp_apify_get_dataset_items":
        plats = result_data.get("platforms") or []
        post_platform = plats[0] if plats else None
    post_count = len(result_data.get("posts") or [])
    if post_platform and tool_name in TOOL_POST_PLATFORM:
        child = post_step_key(post_platform)
        store.ensure_post_steps(task_id, [post_platform])
        if post_count > 0:
            store.set_step_status(
                task_id,
                child,
                "completed",
                message=f"已采集 {post_platform} 发文 {post_count} 条",
                payload={"post_count": post_count},
            )

    if tool_name in _STEP4_TOOLS:
        _on_step4_tool_success(store, task_id)


def _update_steps_after_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    result_data: Dict[str, Any],
) -> None:
    if tool_name == "mcp_twitter_get_user_info":
        store.set_step_status(task_id, "step1_seed", "completed", message="种子 Twitter 资料已入库")
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


def _persist_normalized(store: TaskStore, task_id: str, data: Dict[str, Any]) -> None:
    for row in data.get("profiles") or []:
        store.save_profile_row(row)
    for row in data.get("candidates") or []:
        store.save_candidate_rows([row])
    posts = data.get("posts") or []
    if posts:
        store.save_post_rows(posts)


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
        logger.exception("collect_01 sink 异常: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
