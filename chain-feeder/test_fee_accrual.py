import psycopg2
import json
from datetime import datetime

DB_CONN = "dbname=chaintelligence user=airflow password=airflow host=localhost port=5432"

def calculate_fee_accrual():
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        
        # Get unique positions
        cur.execute("SELECT DISTINCT protocol, network, position_label FROM lp_snapshots")
        positions = cur.fetchall()
        
        print(f"{'Position':<40} | {'Current Rewards ($)':<20} | {'Accrued (24h/Last)':<20}")
        print("-" * 85)
        
        for protocol, network, label in positions:
            # Get last two snapshots for this position
            cur.execute("""
                SELECT timestamp, unclaimed, balance_usd
                FROM lp_snapshots
                WHERE protocol = %s AND network = %s AND position_label = %s
                ORDER BY timestamp DESC
                LIMIT 2
            """, (protocol, network, label))
            
            snapshots = cur.fetchall()
            if len(snapshots) < 2:
                continue
                
            latest = snapshots[0]
            previous = snapshots[1]
            
            latest_unclaimed = latest[1] if latest[1] else []
            previous_unclaimed = previous[1] if previous[1] else []
            
            # Simple sum of USD value of unclaimed rewards
            def sum_usd(uncl):
                return sum(float(u.get('balanceUSD', 0)) for u in uncl)
            
            latest_usd = sum_usd(latest_unclaimed)
            previous_usd = sum_usd(previous_unclaimed)
            
            accrued = latest_usd - previous_usd
            # If accrued is negative, it might mean rewards were claimed.
            # In that case, we can't easily tell the accrual without claim events.
            # But let's just report the delta for now.
            
            pos_display = f"{protocol} - {label}"
            print(f"{pos_display[:40]:<40} | {latest_usd:<20.4f} | {accrued:<20.4f}")
            
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    calculate_fee_accrual()
