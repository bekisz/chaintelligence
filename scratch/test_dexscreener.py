import asyncio
import requests
from web3 import Web3

def _derive_address(t0_bytes: bytes, t1_bytes: bytes, fee_val: int, factory_hex: str, init_hash_hex: str, is_v2: bool = False) -> str:
    from eth_utils import keccak, to_checksum_address
    fee_bytes = fee_val.to_bytes(32, byteorder='big')
    salt = keccak(t0_bytes + t1_bytes + fee_bytes)
    factory_bytes = bytes.fromhex(factory_hex[2:])
    init_hash_bytes = bytes.fromhex(init_hash_hex[2:])
    derived = keccak(b'\xff' + factory_bytes + salt + init_hash_bytes)[12:]
    return to_checksum_address(derived)

addr0 = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48' # USDC
addr1 = '0xdac17f958d2ee523a2206206994597c13d831ec7' # USDT

tokens = sorted([addr0.lower(), addr1.lower()])
t0_bytes = bytes.fromhex(tokens[0][2:])
t1_bytes = bytes.fromhex(tokens[1][2:])

fee_val = int(0.0035 * 10000)
print('fee_val:', fee_val)

factory_hex = '0x1F98431c8aD98523631AE4a59f267346ea31F984'
init_hash_hex = '0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54'

pool_addr = _derive_address(t0_bytes, t1_bytes, fee_val, factory_hex, init_hash_hex)
print('pool_addr:', pool_addr)

url = f"https://api.dexscreener.com/latest/dex/pairs/ethereum/{pool_addr.lower()}"
resp = requests.get(url)
print('resp:', resp.json())
