import os
import sys
import psycopg2
import logging
from datetime import datetime

# Add parent dir
sys.path.append(os.path.join(os.path.dirname(__file__), '../'))
from uniswap_v4_range_fetcher import fetch_v4_position_range_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=postgres port=5432")

def run():
    logger.info("Starting manual V4 range refresh...")
    
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()

        # 1. Fetch ALL relevant positions (V4, Arbitrum, Base) that use this fetch logic
        cur.execute("""
            SELECT p.id, p.token_id, pool.network, pool.pool_name, pool.protocol
            FROM liquidity_pool_position p
            JOIN liquidity_pool pool ON p.pool_id = pool.id
            WHERE pool.protocol = 'Uniswap V4' 
               OR pool.network IN ('Arbitrum', 'Base')
        """)
        rows = cur.fetchall()
        logger.info(f"Found {len(rows)} positions to refresh.")

        for row in rows:
            pos_id, token_id, network, pool_name, protocol = row
            
            # Construct label (heuristic if token_id present)
            label = f"{pool_name} (Token ID: {token_id})"
            
            logger.info(f"Fetching range for Pos ID {pos_id} ({label})...")
            
            # Fetch Data
            data = fetch_v4_position_range_data(label, network)
            
            if data:
                logger.info(f" -> New Data: Tick={data['current_tick']}, Price={data['current_price']}")
                
                # 1. Update Position Metadata
                cur.execute("""
                    UPDATE liquidity_pool_position
                    SET tick_lower = %s, tick_upper = %s, 
                        price_lower = %s, price_upper = %s,
                        current_tick = %s, current_price = %s,
                        fee_tier = %s
                    WHERE id = %s
                """, (
                    data['tick_lower'], data['tick_upper'], 
                    data['price_lower'], data['price_upper'], 
                    data['current_tick'], data['current_price'],
                    data.get('fee_tier'),
                    pos_id
                ))
                
                # 2. Update Latest Snapshot (for immediate view refresh)
                cur.execute("""
                    SELECT id FROM liquidity_pool_position_snapshot 
                    WHERE position_id = %s 
                    ORDER BY timestamp DESC LIMIT 1
                """, (pos_id,))
                res = cur.fetchone()
                
                if res:
                    snap_id = res[0]
                    in_range = False
                    if data['tick_lower'] is not None and data['tick_upper'] is not None and data['current_tick'] is not None:
                         in_range = (data['tick_lower'] <= data['current_tick'] <= data['tick_upper'])
                    
                    logger.info(f" -> Updating snapshot {snap_id} (In Range: {in_range})...")
                    cur.execute("""
                        UPDATE liquidity_pool_position_snapshot
                        SET current_tick = %s, current_price = %s, in_range = %s
                        WHERE id = %s
                    """, (data['current_tick'], data['current_price'], in_range, snap_id))
                else:
                    logger.warning(f" -> No snapshot found for Pos {pos_id}.")

                conn.commit()
            else:
                logger.error(f" -> Failed to fetch data for Pos {pos_id}.")
            
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    run()
