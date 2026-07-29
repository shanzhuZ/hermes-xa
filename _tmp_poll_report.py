# -*- coding: utf-8 -*-
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "scripts")
from collect_01 import db

tid = "03477065-5b3f-48b6-8a05-9c1e961c7767"
base = "http://127.0.0.1:4377"

def get(path):
    req = urllib.request.Request(base + path)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

for i in range(24):
    t = db.fetch_one(
        "SELECT status,current_phase,LEFT(COALESCE(error_message,''),200) err,updated_at FROM hermes_tasks WHERE task_id=%s",
        (tid,),
    )
    steps = db.fetch_all(
        """
        SELECT step_key,status FROM collect_phase_steps
        WHERE task_id=%s AND status IN ('running','completed','failed','skipped')
        ORDER BY step_order
        """,
        (tid,),
    )
    tools = db.fetch_all(
        """
        SELECT tool_name,status,phase FROM hermes_tool_outputs
        WHERE task_id=%s ORDER BY id DESC LIMIT 5
        """,
        (tid,),
    )
    print(f"\n=== poll {i} t={time.strftime('%H:%M:%S')} ===")
    print("task:", t)
    print("active/done steps:", [(s["step_key"], s["status"]) for s in steps[-12:]])
    print("last tools:", tools)
    st = (t or {}).get("status")
    if st in {"completed", "failed", "cancelled"}:
        break
    # also peek tree
    try:
        tree = get(f"/api/tasks/{tid}/tree")
        print("tree status/phase:", tree.get("status"), tree.get("currentPhase") or tree.get("currentStage"))
    except Exception as e:
        print("tree err", e)
    time.sleep(15)

# final summary
print("\n=== FINAL ===")
print(db.fetch_one("SELECT status,current_phase,error_message FROM hermes_tasks WHERE task_id=%s", (tid,)))
for s in db.fetch_all(
    "SELECT step_key,status,LEFT(COALESCE(message,''),80) m FROM collect_phase_steps WHERE task_id=%s ORDER BY step_order",
    (tid,),
):
    if s["status"] != "pending":
        print(s)
