# -*- coding: utf-8 -*-
"""05 自定义任务 — 动态流程图直写 MySQL（与 Java FlowStepService 语义对齐）。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from collect_01 import db

_STEP_KEY_OK = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,62}$")
_FINISH_OK = frozenset({"completed", "failed", "skipped"})
_STATUS_OK = frozenset({"pending", "running", "completed", "failed", "skipped"})


def _require_task(task_id: str) -> str:
    tid = (task_id or "").strip()
    if not tid:
        raise ValueError("taskId_required")
    row = db.fetch_one("SELECT task_id FROM hermes_tasks WHERE task_id=%s", (tid,))
    if not row:
        raise ValueError("task_not_found")
    return tid


def _require_step_key(step_key: str) -> str:
    key = (step_key or "").strip()
    if not _STEP_KEY_OK.match(key):
        raise ValueError(f"invalid_stepKey:{key}")
    return key


def upsert_steps(
    task_id: str,
    steps: List[Dict[str, Any]],
    *,
    mode: str = "merge",
) -> Dict[str, Any]:
    """mode=merge 追加/更新；replace 删除未出现在列表中的步骤后再 upsert。"""
    tid = _require_task(task_id)
    m = (mode or "merge").strip().lower()
    if m not in {"merge", "replace"}:
        raise ValueError("invalid_mode")
    if not steps:
        raise ValueError("steps_required")

    keys: List[str] = []
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        step_key = str(raw.get("stepKey") or raw.get("step_key") or "").strip()
        step_key = _require_step_key(step_key)
        title = str(raw.get("title") or "").strip()
        if not title:
            raise ValueError(f"title_required:{step_key}")
        title = title[:256]
        parent = str(raw.get("parentStepKey") or raw.get("parent_step_key") or "").strip() or None
        try:
            step_order = int(raw.get("stepOrder") if raw.get("stepOrder") is not None else raw.get("step_order", (i + 1) * 10))
        except (TypeError, ValueError):
            step_order = (i + 1) * 10
        step_node = str(raw.get("stepNode") or raw.get("step_node") or str(i + 1)).strip() or str(i + 1)

        db.execute(
            """
            INSERT INTO collect_phase_steps
              (task_id, step_key, parent_step_key, step_order, step_node, title, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            ON DUPLICATE KEY UPDATE
              parent_step_key=VALUES(parent_step_key),
              step_order=VALUES(step_order),
              step_node=VALUES(step_node),
              title=VALUES(title),
              updated_at=NOW(3)
            """,
            (tid, step_key, parent, step_order, step_node, title),
        )
        status = str(raw.get("status") or "").strip().lower()
        if status:
            message = raw.get("message")
            msg = None if message is None else str(message)
            set_step_status(tid, step_key, status, message=msg)
        keys.append(step_key)

    if not keys:
        raise ValueError("steps_required")

    if m == "replace":
        placeholders = ",".join(["%s"] * len(keys))
        db.execute(
            f"DELETE FROM collect_phase_steps WHERE task_id=%s AND step_key NOT IN ({placeholders})",
            tuple([tid] + keys),
        )

    return {"ok": True, "taskId": tid, "mode": m, "upserted": len(keys), "stepKeys": keys}


def begin_step(task_id: str, step_key: str, message: Optional[str] = None) -> Dict[str, Any]:
    tid = _require_task(task_id)
    key = _require_step_key(step_key)
    cur = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (tid, key),
    )
    if not cur:
        raise ValueError("step_not_found")
    msg = (message or "执行中").strip()[:2000]
    set_step_status(tid, key, "running", message=msg)
    return {"ok": True, "taskId": tid, "stepKey": key, "status": "running", "message": msg}


def finish_step(
    task_id: str,
    step_key: str,
    status: str = "completed",
    message: Optional[str] = None,
) -> Dict[str, Any]:
    tid = _require_task(task_id)
    key = _require_step_key(step_key)
    cur = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (tid, key),
    )
    if not cur:
        raise ValueError("step_not_found")
    st = (status or "").strip().lower()
    if st not in _FINISH_OK:
        raise ValueError("invalid_finish_status")
    msg = (message or st).strip()[:2000]
    set_step_status(tid, key, st, message=msg)
    return {"ok": True, "taskId": tid, "stepKey": key, "status": st, "message": msg}


def set_step_status(
    task_id: str,
    step_key: str,
    status: str,
    *,
    message: Optional[str] = None,
) -> None:
    st = (status or "").strip().lower()
    if st not in _STATUS_OK:
        raise ValueError(f"invalid_status:{st}")
    fields = ["status=%s", "updated_at=NOW(3)"]
    params: List[Any] = [st]
    if st == "running":
        fields.append("started_at=COALESCE(started_at, NOW(3))")
        fields.append("finished_at=NULL")
    elif st == "pending":
        fields.append("finished_at=NULL")
    elif st in _FINISH_OK:
        fields.append("finished_at=COALESCE(finished_at, NOW(3))")
    if message is not None:
        fields.append("message=%s")
        params.append(str(message)[:2000])
    params.extend([task_id, step_key])
    db.execute(
        f"UPDATE collect_phase_steps SET {', '.join(fields)} WHERE task_id=%s AND step_key=%s",
        tuple(params),
    )


def mark_task_running(task_id: str) -> None:
    db.execute(
        """
        UPDATE hermes_tasks
        SET status=CASE WHEN status='pending' THEN 'running' ELSE status END,
            started_at=COALESCE(started_at, NOW(3)),
            updated_at=NOW(3)
        WHERE task_id=%s AND status IN ('pending', 'running')
        """,
        (task_id,),
    )


def mark_task_completed(task_id: str, phase: str = "done") -> None:
    db.execute(
        """
        UPDATE hermes_tasks
        SET status='completed', current_phase=%s,
            finished_at=COALESCE(finished_at, NOW(3)), updated_at=NOW(3)
        WHERE task_id=%s AND status NOT IN ('failed', 'cancelled')
        """,
        (phase, task_id),
    )


def is_custom_intent(message: str) -> bool:
    msg = message or ""
    return bool(re.search(r"account-intelligence-custom|自定义", msg, re.I))
