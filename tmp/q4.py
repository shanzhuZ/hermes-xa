import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
TASK='832e960e-49a6-4155-b25b-a76954d8cdaf'

print('##### YT POSTS #####')
cur.execute("SELECT content_id, title, LEFT(REPLACE(content_text,'\n',' '),400), published_at, view_count, like_count, comment_count FROM collect_posts WHERE task_id=%s AND platform='youtube' ORDER BY published_at DESC LIMIT 20",(TASK,))
for r in cur.fetchall(): print(r)

print('##### IMAGES (analyzed) #####')
cur.execute("SELECT source_type, platform, LEFT(IFNULL(ocr_text,''),120), LEFT(IFNULL(vision_text,''),400), analyze_status FROM collect_images WHERE task_id=%s AND analyze_status='analyzed' LIMIT 25",(TASK,))
rows=cur.fetchall()
print("analyzed imgs:", len(rows))
for r in rows: print(r)

print('##### VIDEOS #####')
cur.execute("SELECT video_id, platform, LEFT(IFNULL(video_analysis_text,''),2000), analyze_status, frame_analyzed_cnt FROM collect_videos WHERE task_id=%s",(TASK,))
for r in cur.fetchall():
    print("VID:", r[0], r[1], "status=",r[3], "frames=",r[4])
    print(r[2])
    print('---')
conn.close()
