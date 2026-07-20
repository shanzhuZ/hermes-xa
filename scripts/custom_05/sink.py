# -*- coding: utf-8 -*-
"""05 自定义任务 Hook：管任务 running/completed；步骤真相源为 flow_cli / Java Flow API。

终稿兜底：若模型未写流程图就直接作答，补 synthesize + finish step_plan + completed。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from collect_01 import db
from custom_05 import flow_store

logger = logging.getLogger(__name__)

_SKIP_TOOLS = frozenset({
    "skill_view", "clarify", "tool_search", "describe_tool", "todo",
})


def _extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    ex = payload.get("extra")
    return ex if isinstance(ex, dict) else {}


def _task_id(payload: Dict[str, Any]) -> Optional[str]:
    ex = _extra(payload)
    for key in ("gateway_session_key", "session_key"):
        raw = str(ex.get(key) or "").strip()
        if raw.startswith("task:"):
            return raw[5:].strip() or None
    tid = str(payload.get("task_id") or "").strip()
    return tid or None


def reconcile_on_final(task_id: str) -> None:
    """与 Java FlowStepService.reconcileOnFinalAnswer 对齐。"""
    tid = (task_id or "").strip()
    if not tid:
        return
    steps = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s",
        (tid,),
    )
    business = [s for s in steps if str(s.get("step_key") or "") != "step_plan"]
    if not business:
        flow_store.upsert_steps(
            tid,
            [{"stepKey": "synthesize", "title": "汇总结论", "stepOrder": 20, "stepNode": "2"}],
            mode="merge",
        )
        flow_store.finish_step(tid, "synthesize", "completed", "终稿已生成（自动收口）")
    plan = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key='step_plan'",
        (tid,),
    )
    plan_st = str((plan or {}).get("status") or "")
    if plan_st in {"pending", "running"}:
        flow_store.finish_step(tid, "step_plan", "completed", "计划已完成（终稿兜底）")
    leftovers = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s "
        "AND status IN ('pending','running')",
        (tid,),
    )
    for row in leftovers:
        key = str(row.get("step_key") or "")
        if key:
            flow_store.finish_step(tid, key, "completed", "终稿兜底收口")
    flow_store.mark_task_completed(tid)


def handle_event(payload: Dict[str, Any]) -> None:
    """最小 Hook：转 running；终稿时兜底收口步骤树。"""
    event = str(payload.get("event") or payload.get("type") or "").strip().lower()
    tid = _task_id(payload)
    if not tid:
        return

    row = db.fetch_one("SELECT task_type, status FROM hermes_tasks WHERE task_id=%s", (tid,))
    if not row or str(row.get("task_type") or "") != "account_custom":
        return

    try:
        flow_store.mark_task_running(tid)
    except Exception as exc:
        logger.warning("custom_05 mark running failed: %s", exc)

    tool_name = str(payload.get("tool_name") or "").strip()
    if tool_name and tool_name not in _SKIP_TOOLS and "tool" in event:
        return

    content = str(payload.get("content") or payload.get("message") or "")
    if event in {"assistant", "agent_end", "agent.end", "run.completed", "post_llm_call"} or (
        "assistant" in event and content
    ) or ("llm" in event and content):
        if _looks_like_final(content) or len(content.strip()) >= 80:
            try:
                reconcile_on_final(tid)
            except Exception as exc:
                logger.warning("custom_05 reconcile failed: %s", exc)


def _looks_like_final(content: str) -> bool:
    text = (content or "").strip()
    if len(text) < 40:
        return False
    if re.search(r"(?m)^#{1,3}\s+|一、|结论|总结|报告|\*\*", text):
        return True
    return len(text) >= 80
