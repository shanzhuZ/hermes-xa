import sqlite3
sid="api_1785292752_b5c3f674"
conn=sqlite3.connect(r"D:\hermes-xa\state.db")
cols=conn.execute("PRAGMA table_info(messages)").fetchall()
print("cols", cols)
rows=conn.execute("SELECT id,role,substr(content,1,200),length(content) FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
print("n", len(rows))
for r in rows:
    print("---", r[0], r[1], r[3])
    print(r[2])
conn.close()
