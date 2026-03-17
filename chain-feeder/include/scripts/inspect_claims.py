
import psycopg2
import os

# DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@localhost:5433/chaintelligence")
# Hardcoding for local execution since environment variable might be different or missing in shell
DB_CONN = "postgres://airflow:airflow@localhost:5433/chaintelligence"

def check_claims():
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        
        # Check total number of snapshots with non-zero claimed amounts
        cur.execute("""
            SELECT COUNT(*) 
            FROM liquidity_pool_position_snapshot 
            WHERE coin0_claimed_amount > 0 OR coin1_claimed_amount > 0;
        """)
        count_nonzero = cur.fetchone()[0]
        print(f"Snapshots with non-zero claimed amounts: {count_nonzero}")
        
        # Check distinct positions with claims
        cur.execute("""
            SELECT COUNT(DISTINCT position_id) 
            FROM liquidity_pool_position_snapshot 
            WHERE coin0_claimed_amount > 0 OR coin1_claimed_amount > 0;
        """)
        count_positions = cur.fetchone()[0]
        print(f"Distinct positions with claims: {count_positions}")
        
        # Look for increases in claimed amounts which would indicate events
        # This is strictly not perfect as it depends on snapshot frequency vs event frequency
        # But it gives an idea.
        # Actually, simpler: Let's lists some examples.
        
        # Get all positions with potential claims
        cur.execute("""
            SELECT DISTINCT position_id 
            FROM liquidity_pool_position_snapshot 
            WHERE coin0_claimed_amount > 0 OR coin1_claimed_amount > 0;
        """)
        position_ids = [r[0] for r in cur.fetchall()]
        
        total_events = 0
        
        for pid in position_ids:
            cur.execute("""
                SELECT timestamp, coin0_claimed_amount, coin1_claimed_amount
                FROM liquidity_pool_position_snapshot
                WHERE position_id = %s
                ORDER BY timestamp ASC;
            """, (pid,))
            rows = cur.fetchall()
            
            last_c0 = 0
            last_c1 = 0
            pos_events = 0
            
            for r in rows:
                c0 = float(r[1] or 0)
                c1 = float(r[2] or 0)
                
                # We strictly look for increases. 
                # If value drops to 0, it's likely a snapshot that initialized to 0 and wasn't backfilled yet.
                # If value drops but not to 0, it's weird, but we ignore it for "event" counting.
                # We count an event if value INCREASES.
                
                # Check for increase from non-zero previous value
                # OR increase from zero if current is > 0
                
                diff0 = c0 - last_c0
                diff1 = c1 - last_c1
                
                if (c0 > 0 or c1 > 0):
                    # If this is the first non-zero value, it's an event (or the start of history)
                    # But wait, history starts at 0.
                    if last_c0 == 0 and last_c1 == 0:
                        pos_events += 1
                        # print(f"Pos {pid}: Initial claim event at {r[0]} ({c0}, {c1})")
                    elif diff0 > 0 or diff1 > 0:
                        # Increase from previous non-zero
                        pos_events += 1
                        # print(f"Pos {pid}: Additional claim event at {r[0]} ({diff0}, {diff1})")
                
                # Update last known NON-ZERO value? 
                # No, if it resets to 0, we should reset last_c0/c1 to 0 so next increase counts?
                # Yes, if it resets to 0, the next non-zero is a re-discovery of the claim or a new claim?
                # If backfill runs again, it updates the 0s.
                # But we are looking at specific point in time analysis.
                
                last_c0 = c0
                last_c1 = c1
                
            print(f"Position {pid}: {pos_events} claim events detected.")
            total_events += pos_events
            
        print(f"\nTotal claim events detected across all pools: {total_events}")
        
        # Get pool info for Position 1
        cur.execute("""
            SELECT p.pool_name, pos.wallet_address
            FROM liquidity_pool_position pos
            JOIN liquidity_pool p ON pos.pool_id = p.id
            WHERE pos.id = 1
        """)
        pool_info = cur.fetchone()
        if pool_info:
             print(f"\nDetails for Position 1: Pool {pool_info[0]}, Wallet {pool_info[1]}")
             
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_claims()
