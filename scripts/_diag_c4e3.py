#!/usr/bin/env python3
import sys
sys.path.insert(0, "d:/hermes-xa/scripts")
from collect_01 import db

tid = "c4e3c2c5-1694-4ea6-b97a-ad9effcc8534"
print("=== task ===")
print(db.fetch_one("SELECT task_id, task_type, status, created_at FROM hermes_tasks WHERE task_id=%s", (tid,)))

print("\n=== steps ===")
for r in db.fetch_all(
    "SELECT step_key, status, message, parent_step_key FROM collect_phase_steps WHERE task_id=%s ORDER BY step_order",
    (tid,),
):
    p = r.get("parent_step_key") or ""
    print(("  " if p else "") + f"{r['step_key']:32} {r['status']:10} {(r.get('message') or '')[:80]}")

print("\n=== tools ===")
for t in db.fetch_all(
    "SELECT id, executed_at, tool_name, phase, status FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id",
    (tid,),
):
    print(t["id"], str(t["executed_at"])[11:19], t["tool_name"][:50], "phase=", t.get("phase"), t["status"])

print("\n=== profiles by platform ===")
for r in db.fetch_all(
    "SELECT platform, account_handle, account_id FROM collect_profiles WHERE task_id=%s",
    (tid,),
):
    print(r)

print("\n=== facebook candidates ===")
for r in db.fetch_all(
    "SELECT platform, account_handle, match_strategy, profile_url FROM cross_platform_candidates WHERE task_id=%s AND platform='facebook'",
    (tid,),
):
    print(r)

print("\n=== facebook tools detail ===")
for t in db.fetch_all(
    "SELECT id, tool_name, phase, status, LEFT(tool_args,200) args FROM hermes_tool_outputs WHERE task_id=%s AND (tool_name LIKE '%%facebook%%' OR phase LIKE '%%facebook%%') ORDER BY id",
    (tid,),
):
    print(t)
