# -*- coding: utf-8 -*-
from collect_01 import db
from pathlib import Path
import json

tid = "b022e968-573b-47d7-adeb-9b89aa06e14c"
out = []

def p(*a):
    out.append(" ".join(str(x) for x in a))

t = db.fetch_one(
    "SELECT status,session_id,error_message,updated_at,created_at FROM hermes_tasks WHERE task_id=%s",
    (tid,),
)
p("TASK", t)

rows = db.fetch_all(
    "SELECT step_key,status,LEFT(COALESCE(message,''),160) msg,updated_at FROM collect_phase_steps WHERE task_id=%s ORDER BY step_key",
    (tid,),
)
for r in rows:
    p("%-40s %-10s %s | %s" % (r["step_key"], r["status"], r["msg"], r["updated_at"]))

p("profiles", db.fetch_one("SELECT COUNT(*) c FROM collect_profiles WHERE task_id=%s", (tid,)))
p("posts", db.fetch_one("SELECT COUNT(*) c FROM collect_posts WHERE task_id=%s", (tid,)))
p("streams", db.fetch_all(
    "SELECT stream_type,validation_status,COUNT(*) c FROM collect_identity_streams WHERE task_id=%s GROUP BY stream_type,validation_status",
    (tid,),
))
p("validated", db.fetch_all(
    "SELECT platform,account_handle,verdict FROM collect_validated_accounts WHERE task_id=%s",
    (tid,),
))

tools = db.fetch_all(
    "SELECT id,tool_name,status,executed_at,LEFT(COALESCE(tool_output,''),180) o FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id",
    (tid,),
)
p("tools", len(tools))
for x in tools:
    p(x["id"], x["tool_name"], x["status"], x["executed_at"], x["o"])

ds = db.fetch_all(
    "SELECT id,role,LENGTH(content) n,LEFT(content,400) c,created_at FROM hermes_user_dialogues WHERE task_id=%s ORDER BY id",
    (tid,),
)
for d in ds:
    p("DLG", d["id"], d["role"], d["n"], d["created_at"], d["c"])

from report_04.engine import format_system_progress_board, build_agent_context
from report_04.orchestrator import infer_gate_step
from report_04.gates import can_run_step7_collect, can_advance_to_step5, can_advance_to_osint, posts_substantively_ready, step4_profiles_terminal

p("gate", infer_gate_step(tid))
p("step4_terminal", step4_profiles_terminal(tid))
p("can_step5", can_advance_to_step5(tid))
p("can_osint", can_advance_to_osint(tid))
p("can_step7", can_run_step7_collect(tid))
p("posts_ready", posts_substantively_ready(tid))
p("BOARD\n", format_system_progress_board(tid))
p("CTX\n", (build_agent_context(tid) or "")[:1500])

Path("_tmp_b022.txt").write_text("\n".join(out), encoding="utf-8")
print("ok", len(out))
