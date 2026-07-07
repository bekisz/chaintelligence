from web3 import Web3
import eth_abi
FACTORY = Web3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")
WBTC = Web3.to_checksum_address("0x2260fac5e5542a773aa44fbcfedf7c193bc2c599")
CBBTC = Web3.to_checksum_address("0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf")
INIT_CODE_HASH = bytes.fromhex("e34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54")
fee = 100
token0, token1 = sorted([WBTC, CBBTC], key=lambda x: x.lower())
salt = Web3.keccak(eth_abi.encode(["address", "address", "uint24"], [token0, token1, fee]))
address = Web3.keccak(b'\xff' + bytes.fromhex(FACTORY[2:]) + salt + INIT_CODE_HASH)[-20:]
print("Derived:", Web3.to_checksum_address(address.hex()))

