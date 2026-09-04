import pymysql, json
conn = pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='hermes-xa',charset='utf8mb4')
cur = conn.cursor()
cur.execute("SHOW TABLES")
tables = [t[0] for t in cur.fetchall()]
print(json.dumps(tables, ensure_ascii=False))
conn.close()
