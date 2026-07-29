# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "scripts")
from collect_01 import db

tid = "03477065-5b3f-48b6-8a05-9c1e961c7767"
print(db.fetch_one(
    "SELECT status,current_phase,error_message,updated_at FROM hermes_tasks WHERE task_id=%s",
    (tid,),
))
print("--- tools ---")
for r in db.fetch_all(
    "SELECT tool_name,status,phase,LEFT(CAST(executed_at AS CHAR),19) t "
    "FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id DESC LIMIT 12",
    (tid,),
):
    print(r)
print("--- steps ---")
for r in db.fetch_all(
    "SELECT step_key,status,LEFT(COALESCE(message,''),80) m "
    "FROM collect_phase_steps WHERE task_id=%s AND status<>'pending' ORDER BY step_order",
    (tid,),
):
    print(r)
