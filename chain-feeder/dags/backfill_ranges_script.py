
import os
import psycopg2
import json
import logging
import sys
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment
load_dotenv()

# Import fetcher logic - it is in the same directory
from uniswap_v3_range_fetcher import fetch_position_range_data

DB_CONN = os.getenv('DATA_WAREHOUSE_DB', "dbname=chaintelligence user=airflow password=airflow host=postgres port=5432")
GRAPH_API_KEY = os.getenv('GRAPH_API_KEY')

def backfill_ranges():
    logger.info(f"Connecting to DB: {DB_CONN}")
    try:
        conn = psycopg2.connect(DB_CONN)
    except Exception as e:
        logger.error(f"Failed to connect to DB: {e}")
        return

    cur = conn.cursor()
    
    # Select rows that need backfill: Uniswap positions with Token ID but no range data
    # Checking for "Token ID:" or "#" in label
    # Select positions that need backfill: Uniswap positions missing Token ID or limits
    query = """
    SELECT pos.id, pool.pool_name, pool.network, pool.protocol, pos.position_key 
    FROM liquidity_pool_position pos
    JOIN liquidity_pool pool ON pos.pool_id = pool.id
    WHERE (pool.protocol ILIKE '%Uniswap%') 
      -- AND (pool.pool_name LIKE '%Token ID:%' OR pool.pool_name LIKE '%#%') -- Name is cleaned now!
      AND (pos.token_id IS NULL OR pos.tick_lower IS NULL)
      AND pool.network = 'Ethereum'
    ORDER BY pos.created_at DESC
    """
    
    cur.execute(query)
    rows = cur.fetchall()
    logger.info(f"Found {len(rows)} positions eligible for range data backfill.")
    
    updated_count = 0
    
    for row in rows:
        row_id, label, network, protocol, pos_key = row
        
        # We need to construct a label that the fetcher understands (might need Token ID if we stripped it?)
        # Fetcher expects "ETH / USDC (Token ID: ...)" or similar to extract ID.
        # But wait, our `pool_name` is CLEANED (no ID).
        # We need the ID to fetch!
        # If `token_id` is missing in DB, we can't construct the label with ID for the fetcher to parse!
        # Catch-22?
        # The fetcher extracts ID from label.
        # If we stored cleaned label in `liquidity_pool`, we lost the ID in that column.
        # BUT we have `position_key`. Zapper's position_key often contains the ID or address.
        # Or we can use the `lp_snapshots_legacy` to look up the original label?
        # NO, we want to move forward.
        
        # Actually, in `zapper_balance_loader_dag`, we call fetcher *before* cleaning.
        # Here we are post-migration.
        # If we migrated from legacy, we should have migrated the ID if it existed.
        # If it was missing in legacy, it's missing here.
        
        # If `position_key` has the format `...-0x...` maybe we can infer?
        # Usually Zapper `position_key` is opaque or long string.
        
        # Let's try to pass the `pool_name` combined with `position_key` if `position_key` looks like an ID?
        # Or checks `wallet_address`? The fetcher needs `Token ID`.
        
        # If the original label had "Token ID: ...", the migration script extracted it to `token_id`.
        # So `token_id` should NOT be NULL if it was present.
        # If `token_id` IS NULL, it means the label didn't have it.
        # So the Fetcher `extract_token_id` function would fail anyway.
        # So we can effectively skip these unless we improve the Fetcher to use `position_key`.
        
        # Let's log warning and skip for now, or assume label is enough if ID is somehow implicit? (Unlikely for V3).
        
        # Wait, if `token_id` is present but `tick_lower` is NULL?
        # Then we CAN fetch using `token_id`!
        # We can reconstruct a label "Label (Token ID: <id>)" to trick the fetcher.
        
        # Querying token_id from DB
        cur.execute("SELECT token_id FROM liquidity_pool_position WHERE id = %s", (row_id,))
        res = cur.fetchone()
        current_token_id = res[0] if res else None
        
        search_label = label
        if current_token_id:
             search_label = f"{label} (Token ID: {current_token_id})"
             
        logger.info(f"Processing ID {row_id}: {search_label} on {network}...")
        
        range_data = fetch_position_range_data(search_label, network, GRAPH_API_KEY)
        
        if range_data:
            # Update Position (Static)
            update_pos_query = """
            UPDATE liquidity_pool_position
            SET 
                token_id = COALESCE(token_id, %s),
                tick_lower = %s,
                tick_upper = %s,
                price_lower = %s,
                price_upper = %s
            WHERE id = %s
            """
            cur.execute(update_pos_query, (
                range_data.get("token_id"),
                range_data.get("tick_lower"),
                range_data.get("tick_upper"),
                range_data.get("price_lower"),
                range_data.get("price_upper"),
                row_id
            ))
            
            # Update Pool Fee (Static)
            if range_data.get("fee_tier"):
                cur.execute("UPDATE liquidity_pool SET fee_tier = %s WHERE id = (SELECT pool_id FROM liquidity_pool_position WHERE id = %s)", (range_data.get("fee_tier"), row_id))

            # Update Recent Snapshots (Dynamic)
            update_snap_query = """
            UPDATE liquidity_pool_position_snapshot
            SET 
                current_tick = %s,
                current_price = %s,
                in_range = %s
            WHERE position_id = %s AND timestamp > NOW() - INTERVAL '1 day'
            """
            cur.execute(update_snap_query, (
                range_data.get("current_tick"),
                range_data.get("current_price"),
                range_data.get("in_range"),
                row_id
            ))

            updated_count += 1
            logger.info(f"  -> Updated range data: {range_data['price_lower']:.2f} - {range_data['price_upper']:.2f}")
        else:
            logger.warning(f"  -> Failed to fetch range data.")
            # Clear invalid legacy data if we want?
            # Maybe redundant now.
            pass
            
    conn.commit()
    cur.close()
    conn.close()
    
    logger.info(f"Backfill complete. Updated {updated_count} positions.")

if __name__ == "__main__":
    backfill_ranges()
