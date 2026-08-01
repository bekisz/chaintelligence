import sys
import os

sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('api'))

from api.main import build_pool_links

print("=== Testing build_pool_links ===")

# Test 1: Uniswap V3 on Ethereum
links_eth = build_pool_links("0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", None, "Uniswap V3", "Ethereum", "uuid-123")
print("Ethereum V3 Links:")
for k, v in links_eth.items():
    print(f"  {k}: {v}")

assert "uniswap" in links_eth
assert "dexscreener" in links_eth
assert "revert" in links_eth and "revert.finance" in links_eth["revert"]
assert "explorer" in links_eth and "etherscan.io" in links_eth["explorer"]
assert "geckoterminal" in links_eth and "/eth/pools/" in links_eth["geckoterminal"]
assert "dextools" not in links_eth
assert "defined" in links_eth and "/eth/" in links_eth["defined"]

# Test 2: Uniswap V3 on Arbitrum
links_arb = build_pool_links("0xC6962004f452bE9203591991D15f6b388e09E8D0", None, "Uniswap V3", "Arbitrum")
print("\nArbitrum V3 Links:")
for k, v in links_arb.items():
    print(f"  {k}: {v}")

assert "arbiscan.io" in links_arb["explorer"]
assert "/arbitrum/pools/" in links_arb["geckoterminal"]
assert "dextools" not in links_arb
assert "/arbitrum/" in links_arb["defined"]

# Test 3: PancakeSwap V3 on BNB
links_bnb = build_pool_links("0x3684234c7b60a3730e461f681531e21b794d2325", None, "PancakeSwap V3", "BNB")
print("\nBNB V3 Links:")
for k, v in links_bnb.items():
    print(f"  {k}: {v}")

assert "bscscan.com" in links_bnb["explorer"]
assert "/bsc/pools/" in links_bnb["geckoterminal"]

print("\nALL UNIT TESTS PASSED SUCCESSFULLY!")
