"""Test computing the PancakeSwap V4 32-byte poolId and verifying the URL."""
import os, sys
sys.path.insert(0, 'chain-feeder/dags'); sys.path.insert(0, 'chain-feeder/routing'); sys.path.insert(0, 'chain-feeder/include')
os.environ.setdefault('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=postgres port=5432')
from common.utils.uniswap_utils import UniswapV4Fetcher
from eth_hash.auto import keccak

# Uniswap V4 standard fee -> tickSpacing (PancakeSwap V4 is a fork; verify).
TICKSPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}

def word_address(addr):  # address -> 32-byte left-padded
    return bytes.fromhex(addr[2:].rjust(40, '0')).rjust(32, b'\x00')

def word_int(v):  # int -> 32-byte big-endian (works for uint24/int24)
    return int(v).to_bytes(32, 'big', signed=False)

def compute_poolid(c0, c1, fee, tickspacing, hooks='0x0000000000000000000000000000000000000000'):
    a, b = sorted([c0.lower(), c1.lower()])
    enc = word_address(a) + word_address(b) + word_int(fee) + word_int(tickspacing) + word_address(hooks)
    return '0x' + keccak(enc).hex()

f = UniswapV4Fetcher(network='BNB', protocol='PancakeSwap V4')
# Get the ETH/USDT 0.3% pool's token addresses
q = '{ pools(first: 10, where: { token0_in: ["0x2170ed0880ac9a755fd29b2688956bd959f933f8","0x55d398326f99059ff775485246999027b3197955"], token1_in: ["0x2170ed0880ac9a755fd29b2688956bd959f933f8","0x55d398326f99059ff775485246999027b3197955"] }) { id feeTier token0 { id symbol } token1 { id symbol } } }'
pools = f._execute_query(q)['data']['pools']
print('subgraph pools:')
for p in pools:
    t0, t1, fee = p['token0']['id'], p['token1']['id'], int(p['feeTier'])
    ts = TICKSPACING.get(fee)
    pid = compute_poolid(t0, t1, fee, ts) if ts else None
    print(f"  subgraph_id={p['id']} {p['token0']['symbol']}/{p['token1']['symbol']} fee={fee} tickSpacing={ts}")
    if pid:
        print(f"    computed 32-byte poolId: {pid}")
