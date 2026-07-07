from Crypto.Hash import keccak
sig = keccak.new(digest_bits=256, data=b"getPool(address,address,uint24)").digest()[:4].hex()
print("sig:", sig)
