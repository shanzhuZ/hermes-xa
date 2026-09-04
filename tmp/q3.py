import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
TASK='832e960e-49a6-4155-b25b-a76954d8cdaf'

print('##### VALIDATED #####')
cur.execute("SELECT platform, account_handle, confidence, verdict, is_seed FROM collect_validated_accounts WHERE task_id=%s",(TASK,))
for r in cur.fetchall(): print(r)

print('##### PROFILES #####')
cur.execute("SELECT platform, account_handle, display_name, follower_count, following_count, content_count, verified FROM collect_profiles WHERE task_id=%s",(TASK,))
for r in cur.fetchall(): print(r)

print('##### POSTS #####')
cur.execute("SELECT platform, account_id, content_id, title, LEFT(REPLACE(content_text,'\n',' '),300), published_at, view_count, like_count FROM collect_posts WHERE task_id=%s ORDER BY platform, published_at DESC",(TASK,))
rows=cur.fetchall()
print("total posts:", len(rows))
for r in rows[:80]: print(r)
conn.close()
