import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
TASK='832e960e-49a6-4155-b25b-a76954d8cdaf'
for t in ['collect_posts','collect_images','collect_profiles','collect_validated_accounts','collect_videos','collect_video_frames','collect_osint_hits','collect_display_records']:
    cur.execute(f"SHOW COLUMNS FROM {t}")
    cols=[c[0] for c in cur.fetchall()]
    print('===',t,'===')
    print(cols)
conn.close()
