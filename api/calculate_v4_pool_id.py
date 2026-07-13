import eth_abi
from eth_hash.auto import keccak

def get_v4_pool_id(c0, c1, fee, tick_spacing, hooks):
    # Ensure addresses are bytes
    c0_bytes = bytes.fromhex(c0[2:])
    c1_bytes = bytes.fromhex(c1[2:])
    hooks_bytes = bytes.fromhex(hooks[2:])
    
    # Sort currency0 and currency1 lexicographically
    if c0_bytes > c1_bytes:
        c0_bytes, c1_bytes = c1_bytes, c0_bytes
        
    # PoolKey: (currency0, currency1, fee, tickSpacing, hooks)
    # Types: (address, address, uint24, int24, address)
    encoded = eth_abi.encode(
        ['address', 'address', 'uint24', 'int24', 'address'],
        [c0_bytes, c1_bytes, fee, tick_spacing, hooks_bytes]
    )
    
    pool_id = keccak(encoded)
    return "0x" + pool_id.hex()

c0 = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1" # WETH
c1 = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831" # USDC
hooks = "0x0000000000000000000000000000000000000000"

print("--- 0.05% TIER (500 fee) ---")
for ts in [10, 60, 100, 200]:
    pid = get_v4_pool_id(c0, c1, 500, ts, hooks)
    print(f"tickSpacing={ts}: {pid}")

print("\n--- 0.3% TIER (3000 fee) ---")
for ts in [10, 60, 100, 200]:
    pid = get_v4_pool_id(c0, c1, 3000, ts, hooks)
    print(f"tickSpacing={ts}: {pid}")
