import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
TASK='832e960e-49a6-4155-b25b-a76954d8cdaf'

print('##### AVATAR/COVER #####')
cur.execute("SELECT source_type, platform, LEFT(IFNULL(vision_text,''),600), origin_url FROM collect_images WHERE task_id=%s AND source_type IN ('profile_avatar','profile_cover')",(TASK,))
for r in cur.fetchall():
    print("TYPE:", r[0], "| PLAT:", r[1])
    print("  VIS:", r[2])
    print("  URL:", r[3])
    print('  ---')

print('##### MORE POST MEDIA (avatar-like, short vis) #####')
cur.execute("SELECT post_id, LEFT(IFNULL(vision_text,''),350) FROM collect_images WHERE task_id=%s AND source_type='post_media' AND post_id NOT IN ('2092821126552703122','2092839147446952209','2092833121209376986','2092843966236696912','2092844402226106852','2092818476515856786') ORDER BY post_id DESC LIMIT 14",(TASK,))
for r in cur.fetchall():
    print("POST:", r[0])
    print("  VIS:", r[1])
    print('  ---')

print('##### TW POSTS REST #####')
cur.execute("SELECT LEFT(REPLACE(content_text,'\n',' '),220), published_at FROM collect_posts WHERE task_id=%s AND platform='twitter' ORDER BY published_at DESC LIMIT 98 OFFSET 40",(TASK,))
for r in cur.fetchall(): print(r)
conn.close()
