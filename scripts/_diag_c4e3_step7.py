#!/usr/bin/env python3
import sys
sys.path.insert(0, "d:/hermes-xa/scripts")
from collect_01 import db

tid = "c4e3c2c5-1694-4ea6-b97a-ad9effcc8534"
print("=== steps ===")
for r in db.fetch_all(
    "SELECT step_key, status, message, parent_step_key FROM collect_phase_steps WHERE task_id=%s ORDER BY step_order",
    (tid,),
):
    p = r.get("parent_step_key") or ""
    if p or not r["step_key"].startswith("step") or int(r["step_key"][4:5] or 0) >= 4:
        print(("  " if p else "") + f"{r['step_key']:32} {r['status']:10} {(r.get('message') or '')[:70]}")

print("\n=== posts in DB ===")
for r in db.fetch_all(
    "SELECT platform, COUNT(*) c FROM collect_posts WHERE task_id=%s GROUP BY platform",
    (tid,),
):
    print(r)

print("\n=== post tools ===")
for t in db.fetch_all(
    "SELECT id, tool_name, phase, status FROM hermes_tool_outputs WHERE task_id=%s AND tool_name IN ('mcp_twitter_get_user_tweets','mcp_youtube_analyze_channel_videos') ORDER BY id",
    (tid,),
):
    print(t)

print("\n=== validated ===")
for r in db.fetch_all("SELECT platform, account_handle, verdict FROM collect_validated_accounts WHERE task_id=%s", (tid,)):
    print(r)
