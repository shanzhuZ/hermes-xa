"""04 写报编排引擎 v2：确定性推进步骤，减轻 Agent 空转与 Hook 超时。

原则：
- 步骤终态仅由 Hook/task_store 写入；Java 粗同步对 account_report 不写库。
- post_tool 热路径只跑轻量收口，全量 reconcile 仅 session_end + 环境变量。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from collect_01 import db
from report_04.gates import (
    can_advance_to_step5,
    can_advance_to_step7,
    get_step_status,
    is_stream_compare_ready,
    step4_profiles_terminal,
)
from report_04.orchestrator import infer_gate_step, block_tool_reason
from report_04.phases import (
    ANALYSIS_STEP_KEYS,
    PHASE_DONE,
    POST_PARENT_STEP_KEY,
    PROFILE_PARENT_STEP_KEY,
    STEP5_STREAM_TOOLS,
)

logger = logging.getLogger(__name__)

ENGINE_VERSION = "v2"

# 步骤四子节点长时间无 step4 工具成功时，允许 peer-skip（秒）
STEP4_QUIET_SKIP_SECONDS = 45.0


def snapshot(task_id: str) -> Dict[str, Any]:
    """给 pre_llm / 日志用的当前编排快照。"""
    gate = infer_gate_step(task_id)
    return {
        "engineVersion": ENGINE_VERSION,
        "gateStep": gate,
        "step4Terminal": step4_profiles_terminal(task_id),
        "step5": get_step_status(task_id, "step5_streams"),
        "step6": get_step_status(task_id, "step6_validated"),
        "step7": get_step_status(task_id, "step7_posts"),
        "step11": get_step_status(task_id, "step11_report"),
    }


def build_agent_context(task_id: str) -> Optional[str]:
    """注入 Agent：当前 gate + 应做之事（减少只读 error 空转）。"""
    gate = infer_gate_step(task_id)
    lines: List[str] = [f"【04引擎 {ENGINE_VERSION}】当前编排步骤：{gate}"]

    if gate == "step5_streams":
        from report_04.sink import _pending_image_stream_lines  # noqa: PLC0415 — 复用既有 URL 列表

        pending = _pending_image_stream_lines(task_id)
        s5 = get_step_status(task_id, "step5_streams")
        if s5 in {"completed", "skipped"}:
            lines.append("步骤5已系统收口，禁止再 vision/OCR；请等待或触发步骤6收敛（系统会自动跑）。")
        elif not step4_profiles_terminal(task_id):
            pend = None
            try:
                from report_04.gates import _step4_profile_children_pending

                pend = _step4_profile_children_pending(task_id)
            except Exception:
                pend = "step4_profile_*"
            lines.append(
                f"步骤4未全终态（仍有 {pend or 'pending/running'}），请先完成/跳过该平台主页，再并行 vision。"
            )
        elif pending:
            lines.append(
                f"步骤5：请本回合并行 {len(pending)} 次 vision_analyze（一次齐发），image_url 必须用："
            )
            lines.extend(pending[:8])
        else:
            lines.append("步骤5：图片流已齐，系统将自动 completed 并进入步骤6。")

    elif gate == "step6_validated":
        lines.append("步骤6：系统正在/即将收敛可信账号，禁止发文工具。")

    elif gate == "step7_posts":
        lines.append("步骤7：仅允许各平台发文 MCP/Apify；子步完成后系统自动关父节点。")
        lines.append(
            "步骤7全部发文子步终态后必须先跑图片资产管线，再进步骤8："
            "python -m image_pipeline.run --task-id <taskId> --force-analyze"
        )

    elif gate in ANALYSIS_STEP_KEYS or gate == "step11_report":
        lines.append(
            f"当前 task_id={task_id}。图片资产由系统 Hook 兜底；"
            "禁止在终稿前缀/正文写「跳过步骤7.5 / 管线未找到 / 即席执行」等元叙述。"
        )
        try:
            from report_04.image_assets import has_stored_images

            if not has_stored_images(task_id):
                lines.append(
                    "若需补跑图片入库（勿写入报告正文）："
                    f"python -m image_pipeline.run --task-id {task_id} --force-analyze ；"
                    "失败只记日志，勿判失败，继续步骤8/9/10。"
                )
            else:
                lines.append(
                    "图片资产已入库，步骤8可优先结合 collect_images / "
                    f"GET /api/tasks/{task_id}/images 写分析，勿重复全量空跑 vision。"
                )
        except Exception:
            lines.append(
                "步骤8前可确认图片管线："
                f"python -m image_pipeline.run --task-id {task_id} --force-analyze"
            )
        if gate == "step11_report":
            lines.append(
                "步骤11：终稿必须以「一、账号基本信息」开头，勿在第一节前写进度/管线句。"
            )
        elif gate in ANALYSIS_STEP_KEYS:
            lines.append("步骤8/9/10：同一次响应内并行输出三步分析正文。")

    return "\n".join(lines)


def enrich_block_reason(task_id: str, reason: str) -> str:
    ctx = build_agent_context(task_id)
    if not ctx:
        return reason
    return reason + "\n\n" + ctx


def run_post_tool_light(store: Any, task_id: str) -> None:
    """post_tool 末尾：毫秒～百毫秒级，禁止 reconcile_stuck_pipeline。"""
    from report_04.step_reconcile import (
        close_collect_parent_if_ready,
        ensure_step4_parent_not_premature,
        ensure_step7_parent_not_premature,
        maybe_close_abandoned_step4,
    )

    try:
        ensure_step4_parent_not_premature(store, task_id)
        ensure_step7_parent_not_premature(store, task_id)
    except Exception as exc:
        logger.warning("engine ensure parent 失败 task=%s: %s", task_id, exc)

    s5 = get_step_status(task_id, "step5_streams") or "pending"
    try:
        if s5 in {"running", "completed"}:
            maybe_close_abandoned_step4(
                store,
                task_id,
                min_quiet_seconds=STEP4_QUIET_SKIP_SECONDS,
                force=(s5 == "completed"),
            )
        elif not step4_profiles_terminal(task_id):
            maybe_close_abandoned_step4(
                store, task_id, min_quiet_seconds=90.0, force=False
            )
    except Exception as exc:
        logger.warning("engine step4 收口失败 task=%s: %s", task_id, exc)

    try:
        close_collect_parent_if_ready(
            store, task_id, PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"
        )
        close_collect_parent_if_ready(
            store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕"
        )
    except Exception as exc:
        logger.warning("engine close parent 失败 task=%s: %s", task_id, exc)

    _auto_step5_step6(store, task_id)

    try:
        from report_04.orchestrator import on_post_tool_step7_close_parent

        on_post_tool_step7_close_parent(store, task_id)
    except Exception as exc:
        logger.warning("engine step7 close 失败 task=%s: %s", task_id, exc)


def run_pre_llm_auto(store: Any, task_id: str) -> None:
    """每轮 LLM 前：推进可自动完成的步骤。"""
    run_post_tool_light(store, task_id)
    _auto_step5_step6(store, task_id)
    _try_finalize_report(store, task_id, light_only=True)


def run_session_finalize_light(store: Any, task_id: str) -> None:
    """session_end / finalize 默认路径（无 FULL_RECONCILE）。"""
    from report_04.task_store import _reconcile_report_post_child_steps
    from report_04.step_reconcile import (
        close_collect_parent_if_ready,
        ensure_step7_parent_active,
        reconcile_step4_and_step7_children,
        reconcile_step7_from_post_tools,
    )
    from report_04.orchestrator import advance_to_analysis_phase

    _reconcile_report_post_child_steps(store, task_id)
    try:
        reconcile_step7_from_post_tools(store, task_id)
        reconcile_step4_and_step7_children(store, task_id)
    except Exception as exc:
        logger.warning("engine session reconcile children 失败 task=%s: %s", task_id, exc)

    store.reconcile_collect_child_steps(task_id)
    ensure_step7_parent_active(store, task_id)
    close_collect_parent_if_ready(store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")

    if get_step_status(task_id, "step11_report") == "completed":
        advance_to_analysis_phase(store, task_id, "会话结束收口")
    elif get_step_status(task_id, "step7_posts") in {"completed", "skipped"}:
        try:
            from report_04.image_assets import run_image_pipeline_for_report

            run_image_pipeline_for_report(task_id, force_analyze=True, skip_if_stored=True)
        except Exception as exc:
            logger.warning("session_finalize 图片管线兜底失败 task=%s: %s", task_id, exc)

    _auto_step5_step6(store, task_id)


def _auto_step5_step6(store: Any, task_id: str) -> None:
    """步骤5 completed 后自动跑步骤6，不等 Agent。"""
    if get_step_status(task_id, "step6_validated") == "completed":
        return
    if not can_advance_to_step5(task_id).get("ok"):
        return

    s5 = get_step_status(task_id, "step5_streams")
    if s5 not in {"completed", "skipped"}:
        if step4_profiles_terminal(task_id):
            store.kickoff_step5_if_ready(task_id)
        if s5 == "running" and is_stream_compare_ready(task_id):
            try:
                from report_04.sink import _try_complete_step5_if_settled

                _try_complete_step5_if_settled(store, task_id)
            except Exception as exc:
                logger.warning("engine step5 settle 失败 task=%s: %s", task_id, exc)
        return

    if get_step_status(task_id, "step6_validated") != "completed":
        try:
            store.run_validated_accounts(task_id)
            logger.info("engine 自动步骤6 task=%s", task_id)
        except Exception as exc:
            logger.warning("engine run_validated 失败 task=%s: %s", task_id, exc)


def _try_finalize_report(store: Any, task_id: str, *, light_only: bool) -> None:
    """终稿已 completed 时把任务标 completed（并可选轻量 finalize）。"""
    if get_step_status(task_id, "step11_report") != "completed":
        return
    task = store.get_task(task_id) or {}
    if str(task.get("status") or "") in {"completed", "failed"}:
        return
    has_summary = db.fetch_one(
        "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
        (task_id,),
    )
    if not has_summary:
        return
    if light_only:
        db.execute(
            """
            UPDATE hermes_tasks
            SET status='completed', current_phase=%s,
                finished_at=COALESCE(finished_at, NOW(3)), updated_at=NOW(3)
            WHERE task_id=%s AND status NOT IN ('failed')
            """,
            (PHASE_DONE, task_id),
        )
        logger.info("engine 终稿已出，任务标 completed task=%s", task_id)
        return
    store.finalize_task(task_id)


def pre_tool_allowed(task_id: str, tool_name: str, *, phase: Optional[str] = None) -> Optional[str]:
    """统一 pre_tool：返回 block reason（含引擎指引）。"""
    if not tool_name:
        return None
    # sink 里还有更细的门禁，此处只做编排白名单
    reason = block_tool_reason(task_id, tool_name, phase=phase)
    if reason:
        return enrich_block_reason(task_id, reason)
    if tool_name in STEP5_STREAM_TOOLS and get_step_status(task_id, "step5_streams") in {
        "completed",
        "skipped",
    }:
        return enrich_block_reason(
            task_id,
            "步骤5图片流已收口，禁止再调用 vision/OCR。系统已或即将执行步骤6。",
        )
    return None


def full_reconcile_enabled() -> bool:
    return os.environ.get("HERMES_REPORT_FULL_RECONCILE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
