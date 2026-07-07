"""Reverse-engineer PancakeSwap V4 poolId with confirmed inputs:
  currency0=ETH(0x2170...), currency1=USDT(0x55d3...), fee=670, hooks=0
  target poolId = 0x1e2faf20e424bda35e366d2bcdb01fd13f791b2e1e19d148e29573891bcebfb8
"""
from eth_hash.auto import keccak

ETH = '0x2170ed0880ac9a755fd29b2688956bd959f933f8'
USDT = '0x55d398326f99059ff775485246999027b3197955'
TARGET = '0x1e2faf20e424bda35e366d2bcdb01fd13f791b2e1e19d148e29573891bcebfb8'
FEE = 670

def w_addr(a):
    return bytes.fromhex(a[2:].rjust(40, '0')).rjust(32, b'\x00')
def w_u(v):
    return int(v).to_bytes(32, 'big', signed=False)
def w_s(v):
    return int(v).to_bytes(32, 'big', signed=True)

# sorted: ETH(0x2170) < USDT(0x55d3) -> currency0=ETH
c0, c1 = ETH.lower(), USDT.lower()
a, b = sorted([c0, c1])  # ETH, USDT

# Try abi.encode (padded) with signed and unsigned tickSpacing, wide range
for ts in range(0, 3000):
    for wts, label in [(w_s(ts), 'signed'), (w_u(ts), 'unsigned')]:
        for wf, flabel in [(w_u(FEE), 'u'), (w_s(FEE), 's')]:
            enc = w_addr(a) + w_addr(b) + wf + wts + w_addr('0x0000000000000000000000000000000000000000')
            if '0x' + keccak(enc).hex() == TARGET:
                print(f"MATCH abi.encode: tickSpacing={ts} ({label}) fee-enc={flabel}")
                import sys; sys.exit(0)

# Try abi.encodePacked: address(20) + address(20) + uint24(3) + int24(3) + address(20)
def p_addr(a):
    return bytes.fromhex(a[2:].rjust(40, '0'))
def p_u24(v):
    return int(v).to_bytes(3, 'big', signed=False)
def p_s24(v):
    return int(v).to_bytes(3, 'big', signed=True)

for ts in range(0, 3000):
    for wts, label in [(p_s24(ts), 's24'), (p_u24(ts), 'u24')]:
        enc = p_addr(a) + p_addr(b) + p_u24(FEE) + wts + p_addr('0x0000000000000000000000000000000000000000')
        if '0x' + keccak(enc).hex() == TARGET:
            print(f"MATCH encodePacked: tickSpacing={ts} ({label})")
            import sys; sys.exit(0)

print("NO MATCH")
