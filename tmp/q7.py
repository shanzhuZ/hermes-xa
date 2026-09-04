import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
TASK='832e960e-49a6-4155-b25b-a76954d8cdaf'

print("### DATE RANGE ###")
cur.execute("SELECT DATE(published_at) d, COUNT(*) FROM collect_posts WHERE task_id=%s AND platform='twitter' GROUP BY d ORDER BY d",(TASK,))
for r in cur.fetchall(): print(r)

print("### REMAINING TW (offset 40) ###")
cur.execute("SELECT content_id, LEFT(REPLACE(content_text,'\n',' '),250), published_at FROM collect_posts WHERE task_id=%s AND platform='twitter' ORDER BY published_at DESC LIMIT 40, 60",(TASK,))
for r in cur.fetchall(): print(r)
conn.close()
