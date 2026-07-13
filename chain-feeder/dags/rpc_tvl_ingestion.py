from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
from datetime import timedelta, datetime, timezone
import logging
import requests

from include.rpc_discovery_engine import RpcClient, CONTRACTS

logger = logging.getLogger(__name__)

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

# balanceOf(address) signature
SIG_BALANCE_OF = "0x70a08231"

def sync_tvl_from_rpc():
    logger.info("Starting RPC TVL Sync...")
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    # 1. Fetch token mapping: chain -> { token_id : { 'address': addr, 'decimals': d, 'price': p } }
    cur.execute("""
        SELECT c.coin_id, cc.chain, cc.contract_address, cc.decimals, c.price 
        FROM coin_contract cc
        JOIN coin c ON cc.coin_id = c.coin_id
        WHERE c.price IS NOT NULL
    """)
    token_map = {}
    for row in cur.fetchall():
        coin_id, chain, address, decimals, price = row
        if not address: continue
        chain = chain.capitalize()
        if chain not in token_map: token_map[chain] = {}
        token_map[chain][coin_id] = {
            'address': address.lower(),
            'decimals': decimals if decimals is not None else 18,
            'price': float(price)
        }
        
    # 2. Fetch active pools
    cur.execute("""
        SELECT id, network, COALESCE(pool_address, pool_id) as addr, coin0_id, coin1_id
        FROM liquidity_pool
        WHERE COALESCE(pool_address, pool_id) IS NOT NULL 
          AND length(COALESCE(pool_address, pool_id)) = 42
    """)
    pools = cur.fetchall()
    
    # Group pools by network
    pools_by_network = {}
    for p in pools:
        net = p[1]
        if net not in pools_by_network: pools_by_network[net] = []
        pools_by_network[net].append(p)
        
    today = datetime.now(timezone.utc).date()
    updated_pools = 0
        
    for network, net_pools in pools_by_network.items():
        if network not in CONTRACTS:
            logger.warning(f"Skipping network {network}: no RPC config")
            continue
            
        rpc_client = RpcClient(network, CONTRACTS[network])
        net_tokens = token_map.get(network, {})
        
        # Build multicall batches
        batch_size = 50 # 50 pools per batch = 100 calls
        
        for i in range(0, len(net_pools), batch_size):
            batch_pools = net_pools[i:i+batch_size]
            calls = []
            pool_ctx = []
            
            for p in batch_pools:
                pool_id, _, pool_addr, c0_id, c1_id = p
                t0 = net_tokens.get(c0_id)
                t1 = net_tokens.get(c1_id)
                
                if not t0 or not t1:
                    continue # missing token details or price
                    
                if not pool_addr.startswith('0x') or len(pool_addr) != 42:
                    continue # Skip V4 or malformed pool addresses
                
                target0 = t0['address']
                target1 = t1['address']
                
                calldata = SIG_BALANCE_OF + format(int(pool_addr, 16), '064x')
                
                calls.append({"target": target0, "callData": calldata})
                calls.append({"target": target1, "callData": calldata})
                
                pool_ctx.append({
                    'pool_id': pool_id,
                    't0_price': t0['price'], 't0_dec': t0['decimals'],
                    't1_price': t1['price'], 't1_dec': t1['decimals']
                })
                
            if not calls: continue
            
            batch_payload = []
            for idx, call in enumerate(calls):
                batch_payload.append({
                    "jsonrpc": "2.0", "method": "eth_call",
                    "params": [{"to": call["target"], "data": call["callData"]}, "latest"],
                    "id": idx
                })
                
            results = []
            try:
                rpc_endpoint = rpc_client.rpc_urls[0] if rpc_client.rpc_urls else None
                if not rpc_endpoint: continue
                
                resp = requests.post(rpc_endpoint, json=batch_payload, timeout=30)
                resp.raise_for_status()
                for r in resp.json():
                    results.append(r.get("result", "0x"))
            except Exception as e:
                logger.error(f"Multicall failed on {network}: {e}")
                continue
                
            # Process results
            for idx, ctx in enumerate(pool_ctx):
                if idx*2 + 1 >= len(results): break
                res0 = results[idx*2]
                res1 = results[idx*2 + 1]
                
                if not res0 or res0 == '0x' or not res1 or res1 == '0x':
                    continue
                    
                bal0 = int(res0, 16)
                bal1 = int(res1, 16)
                
                usd0 = (bal0 / (10**ctx['t0_dec'])) * ctx['t0_price']
                usd1 = (bal1 / (10**ctx['t1_dec'])) * ctx['t1_price']
                
                total_tvl = usd0 + usd1
                
                cur.execute("""
                    INSERT INTO liquidity_pool_history (pool_id, date, tvl_usd)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (pool_id, date) DO UPDATE 
                    SET tvl_usd = EXCLUDED.tvl_usd;
                """, (ctx['pool_id'], today, total_tvl))
                
                
                updated_pools += 1
                
            conn.commit()

    cur.close()
    conn.close()
    logger.info(f"Updated TVL for {updated_pools} pools across networks.")


with DAG(
    'rpc_tvl_ingestion',
    default_args=default_args,
    description='Fetches current TVL from RPC nodes',
    schedule=timedelta(hours=1),
    start_date=pendulum.today('UTC').add(days=-1),
    catchup=False,
    max_active_runs=1,
    tags=['tvl', 'rpc']
) as dag:
    
    sync_task = PythonOperator(
        task_id='sync_tvl_from_rpc',
        python_callable=sync_tvl_from_rpc,
    )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sync_tvl_from_rpc()
