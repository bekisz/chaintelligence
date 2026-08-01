import requests
import json
from eth_hash.auto import keccak

# Token addresses (EVM sorted)
token0 = "0x9d1A7A3191102e9F900Faa10540837ba84dCBAE7"
token1 = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# Target pool ID in our DB
target_pool_id = "0xd85173a34c0567501850854604460efff465b40f3121b9ad17b3bcc705e083f8"

def compute_v4_pool_id(t0, t1, fee, tick_spacing, hooks):
    t0_b = bytes.fromhex(t0.lower().removeprefix("0x")).zfill(32)
    t1_b = bytes.fromhex(t1.lower().removeprefix("0x")).zfill(32)
    fee_b = fee.to_bytes(32, "big")
    # tick_spacing is int24 signed. In abi.encode, int24 is sign-extended to 32 bytes (int256)
    if tick_spacing < 0:
        ts_b = ( (1 << 256) + tick_spacing ).to_bytes(32, "big")
    else:
        ts_b = tick_spacing.to_bytes(32, "big")
    hooks_b = bytes.fromhex(hooks.lower().removeprefix("0x")).zfill(32)
    
    encoded = t0_b + t1_b + fee_b + ts_b + hooks_b
    return "0x" + keccak(encoded).hex()

print("=== Testing PoolKey parameters against target pool_id ===")
# Try static fees: 3000, 500, 100, 10000, 3499, etc.
# Try tick_spacings: 60, 10, 200, 1, 100
# Try hooks: 0x0000000000000000000000000000000000000000
found = False
for fee in [3000, 3499, 500, 100, 10000, 0x800000]: # 0x800000 = DYNAMIC_FEE_FLAG in V4!
    for ts in [60, 10, 200, 1, 100, 30, 120]:
        for hooks in ["0x0000000000000000000000000000000000000000"]:
            pid = compute_v4_pool_id(token0, token1, fee, ts, hooks)
            if pid.lower() == target_pool_id.lower():
                print(f"MATCH FOUND!")
                print(f"  fee: {fee} (0.3% if 3000)")
                print(f"  tickSpacing: {ts}")
                print(f"  hooks: {hooks}")
                found = True
                break

if not found:
    print("No match found with zero hooks. Checking other hook addresses or dynamic fees...")

# Check DYNAMIC_FEE_FLAG (0x800000 = 8388608) with hooks
print("\nChecking dynamic fee flag or other fees with various tick spacings:")
for fee in [8388608, 3499, 3000]:
    for ts in [1, 10, 30, 60, 100, 200]:
        pid = compute_v4_pool_id(token0, token1, fee, ts, "0x0000000000000000000000000000000000000000")
        print(f"fee={fee}, ts={ts} -> pool_id={pid[:18]}...")
