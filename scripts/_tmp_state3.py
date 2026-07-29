import sqlite3
conn=sqlite3.connect(r"D:\hermes-xa\state.db")
sid="api_1785292752_b5c3f674"
# full user continue message and later assistants
for rid in [15116,15117,15118,15122]:
    r=conn.execute("SELECT role,content,finish_reason,timestamp FROM messages WHERE id=?",(rid,)).fetchone()
    print("ID", rid, "role", r[0], "fr", r[2], "ts", r[3])
    print(r[1][:1200] if r[1] else "")
    print("====")
# timestamps around end of main vs continue
rows=conn.execute("SELECT id,role,length(content),finish_reason,timestamp FROM messages WHERE session_id=? AND id>=15114 ORDER BY id",(sid,)).fetchall()
for r in rows:
    print(r)
conn.close()
