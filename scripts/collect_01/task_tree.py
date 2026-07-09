"""将采集任务聚合为密塔式简易进度树 JSON（供 Postman / 前端轮询）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from collect_01 import db
from collect_01.phases import is_step_key, tool_step_key
from collect_01.task_store import TaskStore

_SKIP_TOOLS = frozenset({"skill_view", "clarify", "tool_search", "describe_tool", "todo", "terminal"})

# 前端展示用状态标签（简易密塔风格）
_STATUS_LABEL = {
    "completed": "结论明确",
    "running": "进行中",
    "failed": "信息缺失",
    "skipped": "已跳过",
    "pending": "待执行",
}


def _tool_step_key(tool_name: str, phase: Optional[str] = None) -> str:
    if is_step_key(phase):
        return str(phase)
    return tool_step_key(tool_name)


def _status_label(status: str) -> str:
    return _STATUS_LABEL.get(status or "pending", status or "pending")


def build_task_tree(task_id: str) -> Dict[str, Any]:
    store = TaskStore()
    task = store.get_task(task_id)
    if not task:
        return {"error": "task_not_found", "taskId": task_id}

    user_row = db.fetch_one(
        """
        SELECT content FROM hermes_user_dialogues
        WHERE task_id=%s AND msg_type='user_input'
        ORDER BY id ASC LIMIT 1
        """,
        (task_id,),
    )
    summary_row = db.fetch_one(
        """
        SELECT content, created_at FROM hermes_user_dialogues
        WHERE task_id=%s AND msg_type='summary'
        ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    )
    steps = db.fetch_all(
        """
        SELECT step_key, parent_step_key, step_order, step_node, title,
               status, message, progress_pct, started_at, finished_at
        FROM collect_phase_steps
        WHERE task_id=%s
        ORDER BY step_order ASC, step_key ASC
        """,
        (task_id,),
    )
    tools = db.fetch_all(
        """
        SELECT id, tool_name, status, duration_ms, executed_at, phase
        FROM hermes_tool_outputs
        WHERE task_id=%s
        ORDER BY id ASC
        """,
        (task_id,),
    )

    tool_buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in tools:
        name = str(row.get("tool_name") or "")
        if name in _SKIP_TOOLS:
            continue
        step_key = _tool_step_key(name, row.get("phase"))
        tool_buckets.setdefault(step_key, []).append(
            {
                "id": f"tool_{row['id']}",
                "type": "tool",
                "title": name,
                "status": row.get("status") or "success",
                "statusLabel": _status_label("completed" if row.get("status") == "success" else "failed"),
                "durationMs": row.get("duration_ms"),
                "executedAt": str(row.get("executed_at") or ""),
                "detailRef": f"/api/tasks/{task_id}/nodes/tool_{row['id']}",
            }
        )

    nodes: List[Dict[str, Any]] = []
    for step in steps:
        key = step["step_key"]
        st = str(step.get("status") or "pending")
        nodes.append(
            {
                "id": key,
                "type": "step",
                "stepKey": key,
                "stepNode": step.get("step_node"),
                "parentStepKey": step.get("parent_step_key"),
                "title": step.get("title"),
                "status": st,
                "statusLabel": _status_label(st),
                "message": step.get("message"),
                "progressPct": step.get("progress_pct"),
                "startedAt": str(step.get("started_at") or ""),
                "finishedAt": str(step.get("finished_at") or ""),
                "detailRef": f"/api/tasks/{task_id}/nodes/{key}",
                "children": tool_buckets.get(key, []),
            }
        )

    root_query = (user_row or {}).get("content") or ""
    summary = None
    if summary_row:
        content = str(summary_row.get("content") or "")
        summary = {
            "id": "summary",
            "type": "summary",
            "title": "采集报告",
            "status": "completed",
            "statusLabel": _STATUS_LABEL["completed"],
            "preview": content[:300] + ("…" if len(content) > 300 else ""),
            "detailRef": f"/api/tasks/{task_id}/nodes/summary",
        }

    return {
        "taskId": task_id,
        "sessionId": task.get("session_id"),
        "status": task.get("status"),
        "currentPhase": task.get("current_phase"),
        "crossPlatform": int(task.get("cross_platform") or 0),
        "root": {
            "id": "user_query",
            "type": "query",
            "title": root_query[:200] or "用户提问",
            "status": "completed",
            "statusLabel": _STATUS_LABEL["completed"],
            "detailRef": f"/api/tasks/{task_id}/nodes/user_query",
        },
        "nodes": nodes,
        "summary": summary,
    }


def get_node_detail(task_id: str, node_id: str) -> Dict[str, Any]:
    if node_id == "user_query":
        row = db.fetch_one(
            """
            SELECT content, created_at FROM hermes_user_dialogues
            WHERE task_id=%s AND msg_type='user_input'
            ORDER BY id ASC LIMIT 1
            """,
            (task_id,),
        )
        return {"nodeId": node_id, "type": "query", "content": (row or {}).get("content"), "createdAt": str((row or {}).get("created_at") or "")}

    if node_id == "summary":
        row = db.fetch_one(
            """
            SELECT content, created_at FROM hermes_user_dialogues
            WHERE task_id=%s AND msg_type='summary'
            ORDER BY id DESC LIMIT 1
            """,
            (task_id,),
        )
        return {"nodeId": node_id, "type": "summary", "content": (row or {}).get("content"), "createdAt": str((row or {}).get("created_at") or "")}

    if node_id.startswith("tool_"):
        tool_pk = node_id[5:]
        row = db.fetch_one(
            """
            SELECT id, tool_name, tool_args, tool_output, status, duration_ms, executed_at
            FROM hermes_tool_outputs
            WHERE task_id=%s AND id=%s
            """,
            (task_id, tool_pk),
        )
        if not row:
            return {"error": "node_not_found", "nodeId": node_id}
        args_raw = row.get("tool_args")
        out_raw = row.get("tool_output")
        try:
            args_json = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args_json = args_raw
        try:
            out_json = json.loads(out_raw) if isinstance(out_raw, str) else out_raw
        except json.JSONDecodeError:
            out_json = out_raw
        return {
            "nodeId": node_id,
            "type": "tool",
            "toolName": row.get("tool_name"),
            "status": row.get("status"),
            "durationMs": row.get("duration_ms"),
            "executedAt": str(row.get("executed_at") or ""),
            "toolArgs": args_json,
            "toolOutput": out_json,
        }

    row = db.fetch_one(
        """
        SELECT step_key, title, status, message, payload_json, started_at, finished_at
        FROM collect_phase_steps
        WHERE task_id=%s AND step_key=%s
        """,
        (task_id, node_id),
    )
    if not row:
        return {"error": "node_not_found", "nodeId": node_id}
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass
    return {
        "nodeId": node_id,
        "type": "step",
        "stepKey": row.get("step_key"),
        "title": row.get("title"),
        "status": row.get("status"),
        "statusLabel": _status_label(str(row.get("status") or "")),
        "message": row.get("message"),
        "payload": payload,
        "startedAt": str(row.get("started_at") or ""),
        "finishedAt": str(row.get("finished_at") or ""),
    }
