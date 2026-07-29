# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path
sid="api_1785292752_b5c3f674"
# find state.db
cands=[
 Path.home()/".hermes"/"state.db",
 Path(r"D:\hermes-xa\hermes-agent")/"state.db",
 Path(r"D:\hermes-xa")/"state.db",
]
for p in Path(r"D:\hermes-xa").rglob("state.db"):
    cands.append(p)
    if len(cands)>20: break
print("cands", cands[:15])
for dbp in cands:
    if not dbp.is_file():
        continue
    print("try", dbp)
    conn=sqlite3.connect(str(dbp))
    try:
        rows=conn.execute("SELECT id,role,substr(content,1,120),length(content),created_at FROM messages WHERE session_id=? ORDER BY id",(sid,)).fetchall()
        print("n", len(rows))
        for r in rows[-30:]:
            print(r)
    except Exception as e:
        print("err", e)
    finally:
        conn.close()
