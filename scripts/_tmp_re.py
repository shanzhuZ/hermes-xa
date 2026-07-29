# -*- coding: utf-8 -*-
import json
from collect_01 import db
from pathlib import Path

tid = "b022e968-573b-47d7-adeb-9b89aa06e14c"
out = []
def p(*a): out.append(" ".join(str(x) for x in a))

# task + dialogues
p("TASK", db.fetch_one("SELECT status,session_id,updated_at,created_at,started_at,finished_at FROM hermes_tasks WHERE task_id=%s",(tid,)))
for d in db.fetch_all("SELECT id,role,LENGTH(content) n,content,created_at FROM hermes_user_dialogues WHERE task_id=%s ORDER BY id",(tid,)):
    p("DLG", d["id"], d["role"], d["n"], d["created_at"])
    p("DLG_BODY", d["content"][:800] if d["content"] else "")

# tools with timing
for t in db.fetch_all("SELECT id,tool_name,status,executed_at FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id",(tid,)):
    p("TOOL", t)

# step11 payload history can't get - current
r=db.fetch_one("SELECT status,message,payload_json,updated_at FROM collect_phase_steps WHERE task_id=%s AND step_key='step11_report'",(tid,))
p("step11", r["status"], r["message"], r["updated_at"])
p("payload", r["payload_json"])

# key steps timing
for sk in ["step4_profiles","step4_profile_youtube","step5_streams","step6_validated","step6_osint_es","step7_posts","step7_post_twitter","step7_post_youtube"]:
    row=db.fetch_one("SELECT status,message,updated_at FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",(tid,sk))
    p("STEP", sk, row)

# hermes messages from state.db if possible
task=db.fetch_one("SELECT session_id FROM hermes_tasks WHERE task_id=%s",(tid,))
sid=task["session_id"]
p("session", sid)

Path("_tmp_reanalyze.txt").write_text("\n".join(out), encoding="utf-8")
print("ok")
