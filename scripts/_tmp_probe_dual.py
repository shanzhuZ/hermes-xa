# -*- coding: utf-8 -*-
from collect_01 import db
from collect_01.config import load_env
import json

load_env()

ids = [
    "fad3aaea-7415-403c-825a-747bfb3aad34",
    "9a06a155-9dc2-43de-93c8-ffe52cfdce0b",
]

for tid in ids:
    print("=" * 72)
    task = db.fetch_one("SELECT * FROM hermes_tasks WHERE task_id=%s", (tid,))
    if not task:
        print("MISSING", tid)
        continue
    keys = [
        "task_id", "session_id", "task_type", "status", "current_phase",
        "cross_platform", "created_at", "started_at", "finished_at", "error_message",
    ]
    print({k: task.get(k) for k in keys})
    seed = task.get("seed_json")
    print("SEED", seed if isinstance(seed, str) else json.dumps(seed, ensure_ascii=False)[:800] if seed else None)

    dial = db.fetch_all(
        """
        SELECT id, role, msg_type, LEFT(content, 200) c, LEFT(CAST(created_at AS CHAR),19) t
        FROM hermes_user_dialogues WHERE task_id=%s ORDER BY id
        """,
        (tid,),
    )
    print("DIALOGUES")
    for d in dial:
        print(d)

    steps = db.fetch_all(
        """
        SELECT step_key, status, LEFT(IFNULL(message,''), 80) msg
        FROM collect_phase_steps WHERE task_id=%s ORDER BY step_order, step_key
        """,
        (tid,),
    )
    print("STEPS")
    for s in steps:
        print(s)

    tools = db.fetch_all(
        """
        SELECT id, tool_name, status, phase, LEFT(CAST(executed_at AS CHAR),19) t,
               LEFT(CAST(tool_output AS CHAR), 160) outp
        FROM hermes_tool_outputs WHERE task_id=%s ORDER BY id
        """,
        (tid,),
    )
    print("TOOLS", len(tools))
    for t in tools:
        print({k: t[k] for k in ("id", "tool_name", "status", "phase", "t")})
        if t.get("status") == "error" or "twitter" in str(t.get("tool_name") or "").lower():
            print("  OUT:", t.get("outp"))

# same session relation
print("=" * 72)
print("SESSION RELATION")
for tid in ids:
    t = db.fetch_one("SELECT session_id, created_at FROM hermes_tasks WHERE task_id=%s", (tid,))
    if not t:
        continue
    sib = db.fetch_all(
        """
        SELECT task_id, task_type, status, LEFT(CAST(created_at AS CHAR),19) t
        FROM hermes_tasks WHERE session_id=%s ORDER BY created_at
        """,
        (t["session_id"],),
    )
    print("session", t["session_id"], "tasks:")
    for s in sib:
        print(" ", s)
