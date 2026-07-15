# -*- coding: utf-8 -*-
from collect_01 import db
from collect_01.config import load_env
load_env()
tid = "9a06a155-9dc2-43de-93c8-ffe52cfdce0b"
# seed history / how platform set
print("task seed", db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (tid,)))
print("profiles", db.fetch_all(
    "SELECT platform, account_handle, display_name, collect_status FROM collect_profiles WHERE task_id=%s ORDER BY id",
    (tid,),
))
# dialogue payload
print("payload", db.fetch_one(
    "SELECT payload_json, content FROM hermes_user_dialogues WHERE task_id=%s AND role='user' LIMIT 1",
    (tid,),
))
# all tasks around that minute
print("nearby", db.fetch_all(
    """
    SELECT task_id, task_type, status, session_id, LEFT(CAST(created_at AS CHAR),19) t
    FROM hermes_tasks
    WHERE created_at BETWEEN '2026-07-15 22:26:00' AND '2026-07-15 22:30:00'
    ORDER BY created_at
    """
))
