import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
TASK='832e960e-49a6-4155-b25b-a76954d8cdaf'
cur.execute("SELECT COUNT(*), SUM(like_count), MAX(like_count), MIN(published_at), MAX(published_at) FROM collect_posts WHERE task_id=%s AND platform='twitter'",(TASK,))
r=cur.fetchone()
print("tw posts:", r[0], "sum_likes:", r[1], "max:", r[2], "earliest:", r[3], "latest:", r[4])
cur.execute("SELECT COUNT(*), SUM(view_count), SUM(like_count) FROM collect_posts WHERE task_id=%s AND platform='youtube'",(TASK,))
r2=cur.fetchone()
print("yt posts:", r2[0], "sum_views:", r2[1], "sum_likes:", r2[2])
conn.close()
