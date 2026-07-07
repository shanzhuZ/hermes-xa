#!/usr/bin/env python3
"""重放任务已有 hermes_tool_outputs，补写 collect_* 与步骤状态。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from collect_01 import db
from collect_01.gates import get_step_status
from collect_01.normalizers.registry import dispatch
from collect_01.sink import (
    _STEP4_TOOLS,
    _enter_step4,
    _on_step4_tool_success,
    _persist_normalized,
    _update_steps_after_tool,
)
from collect_01.task_store import TaskStore


def replay(task_id: str) -> None:
    store = TaskStore()
    rows = db.fetch_all(
        """
        SELECT id, tool_name, tool_args, tool_output, status
        FROM hermes_tool_outputs
        WHERE task_id=%s AND status='success'
        ORDER BY id
        """,
        (task_id,),
    )
    for row in rows:
        tool_name = row["tool_name"]
        if tool_name in {"skill_view", "clarify", "todo", "terminal"}:
            continue
        try:
            tool_args = json.loads(row["tool_args"] or "{}")
        except json.JSONDecodeError:
            tool_args = {}
        ctx = {
            "task_id": task_id,
            "tool_output_id": row["id"],
            "tool_name": tool_name,
            "tool_args": tool_args,
            "platform_hint": "",
        }
        if tool_name in _STEP4_TOOLS:
            _enter_step4(store, task_id)
        result_data = dispatch(tool_name, row["tool_output"], ctx)
        _persist_normalized(store, task_id, result_data)
        _update_steps_after_tool(store, task_id, tool_name, result_data)
        if result_data.get("profiles"):
            for prof in result_data["profiles"]:
                store.build_streams_from_profile(task_id, prof)
        if tool_name == "mcp_maigret_collect_accounts":
            store.set_step_status(task_id, "step2_cross_platform", "completed", message="Maigret 跨平台扫描完成")
        if tool_name in _STEP4_TOOLS:
            _on_step4_tool_success(store, task_id)
    store.finalize_task(task_id)
    print(f"replay 完成 task_id={task_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python replay_collect_task.py <task_id>")
        sys.exit(1)
    replay(sys.argv[1])
