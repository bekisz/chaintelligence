import os
import requests
import json
import psycopg2
import re
from eth_hash.auto import keccak
from dotenv import load_dotenv

load_dotenv('.env.config')
load_dotenv('.env.secrets')

db_url = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=localhost port=5433')
db_url = re.sub(r'host=\S+', 'host=localhost', db_url)
db_url = re.sub(r'port=\S+', 'port=5433', db_url)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

# Check all V4 pools in DB with EURI and USDC
cur.execute("""
    SELECT lp.id, lp.pool_name, lp.pool_id, lp.fee_bps, lp.created_at,
           pr.name as protocol, ch.name as chain
    FROM liquidity_pool lp
    JOIN coin c0 ON lp.coin0_id = c0.coin_id
    JOIN coin c1 ON lp.coin1_id = c1.coin_id
    JOIN protocol pr ON lp.protocol_id = pr.id
    JOIN chain ch ON lp.chain_id = ch.id
    WHERE (c0.symbol = 'EURI' AND c1.symbol = 'USDC')
       OR (c0.symbol = 'USDC' AND c1.symbol = 'EURI');
""")
db_pools = cur.fetchall()
print("=== EURI-USDC Pools in DB ===")
for p in db_pools:
    print(p)

cur.close()
conn.close()

# Query RPC for both pool IDs on-chain
rpc_urls = ["https://ethereum-rpc.publicnode.com", "https://rpc.ankr.com/eth"]
pool_manager = "0x000000000004444c5dc75cb358380d2e3de08a90"

def rpc_post(payload):
    for u in rpc_urls:
        try:
            r = requests.post(u, json=payload, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if "result" in data:
                    return data["result"]
        except Exception:
            continue
    return None

def inspect_v4_pool_rpc(pool_id_hex, label):
    print(f"\n=== On-Chain RPC Inspection: {label} ({pool_id_hex}) ===")
    pool_id_bytes = bytes.fromhex(pool_id_hex.removeprefix("0x").lower())
    slot_bytes = (6).to_bytes(32, "big")
    base_slot = int.from_bytes(keccak(pool_id_bytes + slot_bytes), "big")

    slot0 = rpc_post({"jsonrpc":"2.0","method":"eth_getStorageAt","params":[pool_manager, hex(base_slot), "latest"],"id":1})
    slot3 = rpc_post({"jsonrpc":"2.0","method":"eth_getStorageAt","params":[pool_manager, hex(base_slot+3), "latest"],"id":1})

    if slot0 and slot0 != "0x":
        val = int(slot0, 16)
        sqrtPriceX96 = val & ((1 << 160) - 1)
        tick_raw = (val >> 160) & ((1 << 24) - 1)
        if tick_raw >= (1 << 23):
            tick = tick_raw - (1 << 24)
        else:
            tick = tick_raw
        lpFee = (val >> 208) & ((1 << 24) - 1)
        print(f"Slot0: sqrtPriceX96={sqrtPriceX96}, tick={tick}, lpFee={lpFee} ({lpFee/10000.0}%)")
    
    if slot3 and slot3 != "0x":
        liq = int(slot3, 16) & ((1 << 128) - 1)
        print(f"Liquidity (Slot3): {liq}")

pool_3000 = "0xd85173a34c0567501850854604460efff465b40f3121b9ad17b3bcc705e083f8"
pool_3499 = "0xec0a57d0ad701e7c54a084f4a69ab633955b6eec9dbef9a7092d78096ff1521b"

inspect_v4_pool_rpc(pool_3000, "Pool 0xd851... (0.3% static fee)")
inspect_v4_pool_rpc(pool_3499, "Pool 0xec0a... (0.3499% fee)")
