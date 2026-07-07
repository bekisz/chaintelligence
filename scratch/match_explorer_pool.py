"""Verify: match our PancakeSwap V4 pool to the explorer API's 32-byte poolId.

Our subgraph ETH/USDT 0.05% pool (fee_tier='500' -> 0.05%).
Explorer fee encoding: feeTier/6700 = percent (335 -> 0.05%).
Match by token pair + fee percentage, fetch 32-byte id, verify the URL.
"""
import requests, json

EXPLORER = "https://explorer.pancakeswap.com/api/cached/pools/infinity/{net}/list/top?token={addr}"
ETH = '0x2170ed0880ac9a755fd29b2688956bd959f933f8'
USDT = '0x55d398326f99059ff775485246999027b3197955'

# Our pool: ETH/USDT, fee_tier '500' (0.05%)
our_fee_tier = '500'
our_pct = int(our_fee_tier) / 10000.0  # 0.05

r = requests.get(EXPLORER.format(net='bsc', addr=ETH), timeout=30)
pools = r.json()
print(f"explorer returned {len(pools)} ETH pools")

# Find ETH/USDT pool with fee matching 0.05%
matches = []
for p in pools:
    t0 = p['token0']['id'].lower(); t1 = p['token1']['id'].lower()
    if {t0, t1} == {ETH, USDT}:
        exp_pct = p['feeTier'] / 6700.0
        if abs(exp_pct - our_pct) < 0.001:
            matches.append(p)
            print(f"  MATCH: id={p['id']} feeTier={p['feeTier']} ({exp_pct}%) tvl={p.get('tvlUSD')}")

if matches:
    pid = matches[0]['id']
    print(f"\n32-byte poolId for our ETH/USDT 0.05%: {pid}")
    print(f"URL: https://pancakeswap.finance/liquidity/pool/bsc/{pid}")
else:
    print("NO MATCH — fee encoding assumption wrong, or pool not in top list")
