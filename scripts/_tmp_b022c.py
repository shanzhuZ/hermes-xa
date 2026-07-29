# -*- coding: utf-8 -*-
from collect_01 import db
tid="b022e968-573b-47d7-adeb-9b89aa06e14c"
print(db.fetch_one("SELECT status,updated_at FROM hermes_tasks WHERE task_id=%s",(tid,)))
for r in db.fetch_all("SELECT step_key,status,LEFT(COALESCE(message,''),100) m,updated_at FROM collect_phase_steps WHERE task_id=%s AND (step_key LIKE 'step7%%' OR step_key LIKE 'step8%%' OR step_key LIKE 'step9%%' OR step_key LIKE 'step10%%' OR step_key LIKE 'step11%%' OR step_key LIKE 'phase_%%') ORDER BY step_key",(tid,)):
    print(r)
print("posts", db.fetch_one("SELECT platform,COUNT(*) c FROM collect_posts WHERE task_id=%s GROUP BY platform",(tid,)))
print("posts_all", db.fetch_all("SELECT platform,COUNT(*) c FROM collect_posts WHERE task_id=%s GROUP BY platform",(tid,)))
print("tools", db.fetch_all("SELECT id,tool_name,status,executed_at FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id",(tid,)))
print("dlg", db.fetch_all("SELECT id,role,LENGTH(content) n,LEFT(content,120) c,created_at FROM hermes_user_dialogues WHERE task_id=%s ORDER BY id",(tid,)))
