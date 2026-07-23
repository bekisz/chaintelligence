from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
import logging
import os

@task
def fetch_missing_ranges():
    """Fetches range data for positions missing ranges OR missing current state."""
    from include.uniswap_v4_range_fetcher import fetch_v4_position_range_data
    from include.uniswap_v4_graph_fetcher import fetch_v4_position_range_data_from_graph
    from include.uniswap_v3_range_fetcher import fetch_position_range_data
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    # Select positions needing update, including protocol
    cur.execute("""
        SELECT p.id, p.token_id, ch.name AS network,
               c0.symbol || '/' || c1.symbol AS pool_name,
               p.wallet_address, pr.name AS protocol
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        JOIN chain ch ON pool.chain_id = ch.id
        JOIN protocol pr ON pool.protocol_id = pr.id
        JOIN coin c0 ON pool.coin0_id = c0.coin_id
        JOIN coin c1 ON pool.coin1_id = c1.coin_id
        WHERE (p.tick_lower IS NULL OR p.current_tick IS NULL)
          AND p.token_id IS NOT NULL 
          AND pr.name ILIKE '%Uniswap%'
    """)
    rows = cur.fetchall()
    logging.info(f"Found {len(rows)} positions needing range/state backfill.")
    
    api_key = os.environ.get("GRAPH_API_KEY")
    
    updated = 0
    for row in rows:
        pos_id, token_id, network, pool_name, wallet, protocol = row
        label_for_fetcher = f"{pool_name} (Token ID: {token_id})"
        
        data = None
        if protocol == 'Uniswap V4':
            # Use Graph-based fetcher for Arbitrum and Base, RPC for Ethereum
            if network in ["Arbitrum", "Base"]:
                data = fetch_v4_position_range_data_from_graph(label_for_fetcher, network, graph_api_key=api_key)
            else:
                data = fetch_v4_position_range_data(label_for_fetcher, network, graph_api_key=api_key)
        else:
            data = fetch_position_range_data(label_for_fetcher, network, graph_api_key=api_key)

        if data:
            try:
                # Update Position Ranges, Current State, AND Fee Tier
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
                
                updated += 1
                conn.commit()
            except Exception as e:
                conn.rollback()
                logging.error(f"Error updating ranges for {pos_id}: {e}")
        else:
             logging.warning(f"Failed to fetch range for {token_id} on {network} ({protocol})")

    cur.close()
    conn.close()
    logging.info(f"Backfilled ranges for {updated} positions.")
