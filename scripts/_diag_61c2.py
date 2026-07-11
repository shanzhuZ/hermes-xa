#!/usr/bin/env python3
import sys
sys.path.insert(0, "d:/hermes-xa/scripts")
from collect_01 import db

tid = "61c29dc9-525d-4d7c-8626-a96008c20801"
print("=== task ===")
print(db.fetch_one("SELECT task_id, task_type, status, created_at FROM hermes_tasks WHERE task_id=%s", (tid,)))

print("\n=== steps ===")
for r in db.fetch_all(
    "SELECT step_key, status, message, parent_step_key FROM collect_phase_steps WHERE task_id=%s ORDER BY step_order",
    (tid,),
):
    p = r.get("parent_step_key") or ""
    print(("  " if p else "") + f"{r['step_key']:32} {r['status']:10} {(r.get('message') or '')[:70]}")

print("\n=== tools ===")
for t in db.fetch_all(
    "SELECT id, executed_at, tool_name, phase, status FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id",
    (tid,),
):
    print(t["id"], str(t["executed_at"])[11:19], t["tool_name"][:40], "phase=", t.get("phase"), t["status"])

print("\n=== candidates ===")
for r in db.fetch_all(
    "SELECT platform, account_handle, match_strategy FROM cross_platform_candidates WHERE task_id=%s",
    (tid,),
):
    print(r)
