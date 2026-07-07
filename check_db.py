import psycopg2
conn = psycopg2.connect("dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")
cur = conn.cursor()
cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'uniswap_v3_swaps';")
for row in cur.fetchall():
    print(row)
