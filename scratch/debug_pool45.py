import psycopg2, os
from datetime import datetime, timedelta

conn = psycopg2.connect(os.environ["DATA_WAREHOUSE_DB"])
cur = conn.cursor()

# Pool 45 details
cur.execute("""
    SELECT lp.id, lp.pool_name, lp.fee_bps, lp.pool_address, lp.protocol_id,
           p.name as protocol, ch.name as chain,
           lp.coin0_id, lp.coin1_id,
           c0.symbol as coin0, c1.symbol as coin1
    FROM liquidity_pool lp
    JOIN protocol p ON lp.protocol_id = p.id
    JOIN chain ch ON lp.chain_id = ch.id
    JOIN coin c0 ON c0.coin_id = lp.coin0_id
    JOIN coin c1 ON c1.coin_id = lp.coin1_id
    WHERE lp.id = 45
""")
r = cur.fetchone()
print(f"Pool {r[0]}: {r[1]}")
print(f"  Protocol: {r[5]}/{r[6]}")
print(f"  fee_bps: {r[2]} (={r[2]/100:.2f}% fee)")
print(f"  pool_address: {r[3]}")
print(f"  coins: {r[8]} - {r[9]}")

# Swap stats
cur.execute("SELECT COUNT(*), COALESCE(SUM(amount_usd), 0), MIN(ts), MAX(ts) FROM swaps WHERE pool_id = 45")
s = cur.fetchone()
print(f"\nSwaps: {s[0]} rows, ${s[1]:.2f} total volume")
print(f"  Range: {s[2]} to {s[3]}")

# Total fees = volume * fee_bps / 10000
fee_bps = r[2]
total_vol = s[1]
total_fees = total_vol * fee_bps / 10000
print(f"  Estimated fees: ${total_fees:.2f} (volume * {fee_bps}bps / 10000)")

# Latest history
cur.execute("SELECT date, volume_usd, tx_count FROM liquidity_pool_history WHERE pool_id = 45 AND volume_usd > 0 ORDER BY date DESC LIMIT 10")
print("\nRecent volume days:")
for h in cur.fetchall():
    print(f"  {h[0]}: vol=${h[1]:.2f} txs={h[2]}")

# Check if pool_address is correct (V3 Ethereum)
from eth_hash.auto import keccak
import yaml
WRAPPED_MAP = {
    ("ethereum", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"): "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
}
dex_config = yaml.safe_load(open("/app/config/dex-config.yaml", "r"))
cfg = dex_config["uniswap_v3"]["ethereum"]

cur.execute("""
    SELECT c0c.contract_address, c1c.contract_address
    FROM liquidity_pool lp
    JOIN coin_contract c0c ON c0c.coin_id = lp.coin0_id AND c0c.chain_id = lp.chain_id
    JOIN coin_contract c1c ON c1c.coin_id = lp.coin1_id AND c1c.chain_id = lp.chain_id
    WHERE lp.id = 45
""")
r2 = cur.fetchone()
t0, t1 = r2[0].lower(), r2[1].lower()
if ("ethereum", t0) in WRAPPED_MAP: t0 = WRAPPED_MAP[("ethereum", t0)]
if ("ethereum", t1) in WRAPPED_MAP: t1 = WRAPPED_MAP[("ethereum", t1)]
t0_b = bytes.fromhex(t0.removeprefix("0x"))
t1_b = bytes.fromhex(t1.removeprefix("0x"))
if t1_b < t0_b: t0_b, t1_b = t1_b, t0_b
fee_contract = int(round(float(fee_bps) * 100))
salt = keccak(b"\x00" * 12 + t0_b + b"\x00" * 12 + t1_b + fee_contract.to_bytes(32, "big"))
f_b = bytes.fromhex(cfg["factory"].removeprefix("0x"))
ih_b = bytes.fromhex(cfg["init_hash"].removeprefix("0x"))
correct_addr = "0x" + keccak(b"\xff" + f_b + salt + ih_b)[12:].hex()

print(f"\nAddress check:")
print(f"  Stored:  {r[3]}")
print(f"  Correct: {correct_addr}")
print(f"  Match: {correct_addr.lower() == r[3].lower() if r[3] else False}")

# Check for duplicate pools with same pair+fee
cur.execute("""
    SELECT lp.id, lp.pool_name, lp.fee_bps, lp.pool_address
    FROM liquidity_pool lp
    WHERE lp.coin0_id = (SELECT coin0_id FROM liquidity_pool WHERE id = 45)
      AND lp.coin1_id = (SELECT coin1_id FROM liquidity_pool WHERE id = 45)
      AND lp.protocol_id = (SELECT protocol_id FROM liquidity_pool WHERE id = 45)
      AND lp.chain_id = (SELECT chain_id FROM liquidity_pool WHERE id = 45)
      AND lp.id != 45
      AND lp.fee_bps IS NOT NULL
    ORDER BY lp.fee_bps
""")
print("\nSame-pair pools on same protocol/chain:")
for same in cur.fetchall():
    print(f"  id={same[0]} {same[1]} fee={same[2]}bp")

cur.close()
conn.close()
