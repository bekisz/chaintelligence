import eth_abi

# Fetch the exact log data from RPC receipt
import requests
rpc_urls = ["https://ethereum-rpc.publicnode.com", "https://rpc.ankr.com/eth"]
tx_hash = "0xf769484c9413c2f89fe2cf4eb9302e5e4e6de9b3d33f6509ac2b554efaab1fc2"

for u in rpc_urls:
    try:
        r = requests.post(u, json={"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[tx_hash],"id":1}, timeout=5)
        receipt = r.json()["result"]
        break
    except:
        pass

log15 = None
for log in receipt["logs"]:
    if log["topics"][0].lower() == "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f":
        log15 = log
        break

data_hex = log15["data"]
print("Log Data Hex:", data_hex)

data_bytes = bytes.fromhex(data_hex.removeprefix("0x"))
decoded = eth_abi.abi.decode(["int256", "int256", "uint256", "uint256", "int256", "uint256"], data_bytes)

amount0, amount1, sqrt_price_x96, liquidity, tick, fee = decoded

print("\n=== DECODED UNISWAP V4 SWAP EVENT ===")
print("PoolId (Topic 1):", log15["topics"][1])
print("amount0 (EURI):", amount0 / 10**18)
print("amount1 (USDC):", amount1 / 10**6)
print("sqrtPriceX96:", sqrt_price_x96)
print("liquidity:", liquidity)
print("tick:", tick)
print("FEE (pips):", fee)
print(f"FEE in bps: {fee / 100.0} bps")
print(f"FEE in %: {fee / 10000.0}%")
print(f"FEE in % (exact): {fee / 10000.0}% (or {fee/10000:.4f}%)")
