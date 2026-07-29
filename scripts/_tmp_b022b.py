# -*- coding: utf-8 -*-
from collect_01 import db
from pathlib import Path
tid="b022e968-573b-47d7-adeb-9b89aa06e14c"
out=[]
def p(*a): out.append(" ".join(str(x) for x in a))

# continue related tables
for tbl in ["hermes_continue_jobs","hermes_session_continues","collect_continue_log","hermes_task_meta"]:
    try:
        cols=db.fetch_all(f"SHOW COLUMNS FROM {tbl}")
        p("TBL", tbl, [c["Field"] for c in cols][:12])
    except Exception as e:
        p("NO", tbl, type(e).__name__)

# payload step7 / step11
for sk in ["step7_posts","step7_post_youtube","step7_post_twitter","step11_report","step6_validated"]:
    r=db.fetch_one("SELECT status,message,LEFT(COALESCE(payload_json,''),400) p,updated_at FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",(tid,sk))
    p(sk, r)

# any meta/flags
try:
    rows=db.fetch_all("SELECT * FROM hermes_tasks WHERE task_id=%s",(tid,))
    p("task_keys", list(rows[0].keys()) if rows else None)
except Exception as e:
    p(e)

# search continue in any table with task_id and continue
tables=db.fetch_all("SHOW TABLES")
tname=list(tables[0].keys())[0]
for row in tables:
    name=row[tname]
    if "continue" in name.lower() or "inflight" in name.lower() or "session" in name.lower():
        p("CAND", name)

# tool times gap
tools=db.fetch_all("SELECT id,tool_name,status,executed_at FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id",(tid,))
for t in tools:
    p("T", t)

# youtube validated url
p("yt", db.fetch_all("SELECT * FROM collect_validated_accounts WHERE task_id=%s",(tid,)))
p("yt_prof", db.fetch_all("SELECT platform,account_id,account_handle,LEFT(COALESCE(profile_url,''),80) u FROM collect_profiles WHERE task_id=%s",(tid,)))

# unattempted
from report_04.step_reconcile import list_unattempted_post_platforms
p("unattempted", list_unattempted_post_platforms(tid))

# flow flags in payload
p11=db.fetch_one("SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key='step11_report'",(tid,))
p("p11", p11)

Path("_tmp_b022b.txt").write_text("\n".join(out), encoding="utf-8")
print("ok")
