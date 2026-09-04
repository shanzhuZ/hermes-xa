import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
TASK='832e960e-49a6-4155-b25b-a76954d8cdaf'

print('##### IMAGES ALL #####')
cur.execute("SELECT source_type, platform, analyze_status, COUNT(*) FROM collect_images WHERE task_id=%s GROUP BY source_type, platform, analyze_status",(TASK,))
for r in cur.fetchall(): print(r)

cur.execute("SELECT source_type, platform, post_id, LEFT(IFNULL(ocr_text,''),150), LEFT(IFNULL(vision_text,''),500) FROM collect_images WHERE task_id=%s AND (analyze_status='analyzed' OR vision_text IS NOT NULL) ORDER BY source_type LIMIT 30",(TASK,))
rows=cur.fetchall()
print("with vision:", len(rows))
for r in rows:
    print("TYPE:", r[0], "| PLAT:", r[1], "| POST:", str(r[2])[:20])
    print("  OCR:", r[3])
    print("  VIS:", r[4])
    print('  ---')

print('##### DISPLAY RECORDS #####')
cur.execute("SELECT step_key, data_type, platform, record_title, LEFT(display_fields,200) FROM collect_display_records WHERE task_id=%s ORDER BY id LIMIT 40",(TASK,))
for r in cur.fetchall(): print(r)
conn.close()
