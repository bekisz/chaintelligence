
import psycopg2
import os

DB_CONN = "postgres://airflow:airflow@localhost/chaintelligence"

def check_events():
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT position_id, event_type, COUNT(*) 
            FROM liquidity_pool_position_event
            GROUP BY position_id, event_type
            ORDER BY position_id, event_type
        """)
        rows = cur.fetchall()
        print("\nEvents Summary:")
        for r in rows:
            print(f"Pos {r[0]} | {r[1]}: {r[2]} events")
            
        cur.execute("""
            SELECT position_id, tx_hash, event_type, amount0, amount1, timestamp
            FROM liquidity_pool_position_event
            WHERE tx_hash = '0xd4bb10295efa8e76cb6d95a4da5e894dae2fcd529edd203ad9c962d9f206a37b'
        """)
        rows = cur.fetchall()
        if rows:
            print("\nFound missing TX events:")
            for r in rows:
                print(r)
        else:
            print("\nMissing TX NOT found yet.")
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_events()
