#!/usr/bin/env python3
from collect_01 import db

tid = "1b433d57-aa7e-4350-9221-e3b4f4da5bae"

print("=== TASK ===")
print(db.fetch_one("SELECT task_id, status, current_phase, started_at FROM hermes_tasks WHERE task_id=%s", (tid,)))

print("\n=== ROOT STEPS ===")
for r in db.fetch_all(
    "SELECT step_key, step_node, status, message, started_at, finished_at "
    "FROM collect_phase_steps WHERE task_id=%s AND parent_step_key IS NULL ORDER BY step_order",
    (tid,),
):
    print(r)

print("\n=== STEP4 CHILDREN ===")
for r in db.fetch_all(
    "SELECT step_key, step_node, status, message, started_at, finished_at "
    "FROM collect_phase_steps WHERE task_id=%s AND parent_step_key='step4_profiles' ORDER BY step_order, step_key",
    (tid,),
):
    print(r)

print("\n=== STEP7 CHILDREN ===")
for r in db.fetch_all(
    "SELECT step_key, step_node, status, message "
    "FROM collect_phase_steps WHERE task_id=%s AND parent_step_key='step7_posts' ORDER BY step_order",
    (tid,),
):
    print(r)

print("\n=== ALL TOOLS ===")
for r in db.fetch_all(
    "SELECT id, tool_name, phase, status, executed_at FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id",
    (tid,),
):
    print(r)

print("\n=== PROFILES by platform ===")
for r in db.fetch_all(
    "SELECT platform, COUNT(*) c FROM collect_profiles WHERE task_id=%s GROUP BY platform",
    (tid,),
):
    print(r)

print("\n=== POSTS by platform ===")
for r in db.fetch_all(
    "SELECT platform, COUNT(*) c FROM collect_posts WHERE task_id=%s GROUP BY platform",
    (tid,),
):
    print(r)

print("\n=== CANDIDATES ===")
print(db.fetch_one("SELECT COUNT(*) c FROM cross_platform_candidates WHERE task_id=%s", (tid,)))
for r in db.fetch_all(
    "SELECT platform, account_handle, match_strategy FROM cross_platform_candidates WHERE task_id=%s",
    (tid,),
):
    print(r)

print("\n=== VALIDATED ===")
for r in db.fetch_all(
    "SELECT platform, account_id, verdict, is_seed FROM collect_validated_accounts WHERE task_id=%s",
    (tid,),
):
    print(r)

print("\n=== STREAMS (image) ===")
for r in db.fetch_all(
    "SELECT stream_id, source_platform, validation_status FROM collect_identity_streams "
    "WHERE task_id=%s AND stream_type='image' LIMIT 20",
    (tid,),
):
    print(r)

print("\n=== VISION TOOLS ===")
for r in db.fetch_all(
    "SELECT id, tool_name, phase, status, executed_at FROM hermes_tool_outputs "
    "WHERE task_id=%s AND tool_name IN ('vision_analyze','mcp_vision_analyze','mcp_ocr_perform_ocr') ORDER BY id",
    (tid,),
):
    print(r)
