import requests
import json
from eth_hash.auto import keccak

rpc_urls = [
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.ankr.com/eth",
    "https://eth.llamarpc.com"
]

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

pool_manager = "0x000000000004444c5dc75cb358380d2e3de08a90"
pool_id_hex = "0xd85173a34c0567501850854604460efff465b40f3121b9ad17b3bcc705e083f8"

euri_address = "0x9d1A7A3191102e9F900Faa10540837ba84dCBAE7"
usdc_address = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# balanceOf selector: 0x70a08231
def get_balance(token_address, owner):
    owner_padded = owner.lower().removeprefix("0x").zfill(64)
    data = "0x70a08231" + owner_padded
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": token_address, "data": data}, "latest"],
        "id": 1
    }
    res = rpc_post(payload)
    return int(res, 16) if res else 0

# Get decimals
def get_decimals(token_address):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": token_address, "data": "0x313ce567"}, "latest"],
        "id": 1
    }
    res = rpc_post(payload)
    return int(res, 16) if res else 18

euri_bal_pm = get_balance(euri_address, pool_manager)
usdc_bal_pm = get_balance(usdc_address, pool_manager)

euri_dec = get_decimals(euri_address)
usdc_dec = get_decimals(usdc_address)

print("=== PoolManager Total ERC20 Balances ===")
print(f"EURI (dec {euri_dec}): {euri_bal_pm / 10**euri_dec}")
print(f"USDC (dec {usdc_dec}): {usdc_bal_pm / 10**usdc_dec}")

# 2. Check storage slot of pool 0xd851...
pool_id_bytes = bytes.fromhex(pool_id_hex.removeprefix("0x").lower())
slot_bytes = (6).to_bytes(32, "big")
base_slot = int.from_bytes(keccak(pool_id_bytes + slot_bytes), "big")

def get_storage_at(slot):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getStorageAt",
        "params": [pool_manager, hex(slot), "latest"],
        "id": 1
    }
    return rpc_post(payload)

slot0 = get_storage_at(base_slot)
slot1 = get_storage_at(base_slot + 1)
slot2 = get_storage_at(base_slot + 2)
slot3 = get_storage_at(base_slot + 3)

print("\n=== Storage Slots for Pool 0xd851... ===")
print("Slot 0 (slot0):", slot0)
print("Slot 1:", slot1)
print("Slot 2:", slot2)
print("Slot 3 (liquidity):", slot3)

sqrtPriceX96 = 0
if slot0 and slot0 != "0x":
    val = int(slot0, 16)
    sqrtPriceX96 = val & ((1 << 160) - 1)
    tick_raw = (val >> 160) & ((1 << 24) - 1)
    if tick_raw >= (1 << 23):
        tick = tick_raw - (1 << 24)
    else:
        tick = tick_raw
    protocolFee = (val >> 184) & ((1 << 24) - 1)
    lpFee = (val >> 208) & ((1 << 24) - 1)
    print(f"Decoded Slot0: sqrtPriceX96={sqrtPriceX96}, tick={tick}, lpFee={lpFee}")

if slot3 and slot3 != "0x":
    liq = int(slot3, 16) & ((1 << 128) - 1)
    print(f"Decoded Liquidity (Slot3): {liq}")
    if sqrtPriceX96 and liq:
        r0_raw = liq * (1 << 96) // sqrtPriceX96
        r1_raw = liq * sqrtPriceX96 // (1 << 96)
        print(f"Active in-range Reserve0 (EURI): {r0_raw / 10**euri_dec}")
        print(f"Active in-range Reserve1 (USDC): {r1_raw / 10**usdc_dec}")

