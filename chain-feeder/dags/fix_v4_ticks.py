import sys
import os
import psycopg2
import traceback
sys.path.append(os.path.abspath('/opt/airflow'))
sys.path.append(os.path.abspath('/opt/airflow/include'))

from graph_discovery_client import verify_v4_position_rpc
from uniswap_v3_range_fetcher import tick_to_price
from dotenv import load_dotenv

def fix_v4_ticks_in_db():
    os.environ['RPC_URL_ETHEREUM'] = 'https://rpc.ankr.com/eth/2087a416f7a49024a0de38a87ae2c088cf7aaa743e57d7c9c8c9573aed7829de,https://eth.llamarpc.com'
    conn = psycopg2.connect("postgresql://airflow:airflow@postgres:5432/chaintelligence")
    with conn.cursor() as cur:
        cur.execute("SELECT position_key FROM liquidity_pool_position WHERE position_key LIKE 'uniswapv4-%'")
        rows = cur.fetchall()
        
        updated = 0
        for row in rows:
            pkey = row[0]
            parts = pkey.split('-')
            if len(parts) >= 3:
                network = parts[1].capitalize()
                if network == "Ethereum": network = "Ethereum"
                tid = int(parts[2])
                
                try:
                    liq, owner, pkey_dict, trange = verify_v4_position_rpc(tid, network=network)
                    if not trange or 'tick_lower' not in trange:
                        continue
                        
                    t0_addr = pkey_dict['token0']
                    t1_addr = pkey_dict['token1']
                    
                    cur.execute("SELECT c.decimals FROM coin_contract cc JOIN coin c ON c.coin_id = cc.coin_id WHERE cc.contract_address = %s", (t0_addr,))
                    res = cur.fetchone()
                    d0 = res[0] if res else 18
                    
                    cur.execute("SELECT c.decimals FROM coin_contract cc JOIN coin c ON c.coin_id = cc.coin_id WHERE cc.contract_address = %s", (t1_addr,))
                    res = cur.fetchone()
                    d1 = res[0] if res else 18
                    
                    p_l = tick_to_price(trange['tick_lower'], d0, d1)
                    p_u = tick_to_price(trange['tick_upper'], d0, d1)
                    
                    cur.execute("""
                        UPDATE liquidity_pool_position 
                        SET tick_lower = %s, tick_upper = %s, price_lower = %s, price_upper = %s
                        WHERE position_key = %s
                    """, (trange['tick_lower'], trange['tick_upper'], p_l, p_u, pkey))
                    updated += 1
                    print(f"Updated {pkey} -> tick [{trange['tick_lower']}, {trange['tick_upper']}], price [{p_l}, {p_u}]")
                except Exception as e:
                    print(f"Error updating {pkey}:")
                    traceback.print_exc()
        
        conn.commit()
        print(f"Fixed {updated} V4 positions in the database.")

if __name__ == "__main__":
    fix_v4_ticks_in_db()
