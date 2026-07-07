from Crypto.Hash import keccak
addr0 = "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f"
addr1 = "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"
tokens = sorted([addr0, addr1])
t0_bytes = bytes.fromhex(tokens[0][2:])
t1_bytes = bytes.fromhex(tokens[1][2:])
fee_val = 10000

factory_hex = '0x1F98431c8aD985736e4f3a7465352E461f092301'
init_hash_hex = '0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54'
payload = (
    b'\x00'*12 + t0_bytes +
    b'\x00'*12 + t1_bytes +
    b'\x00'*29 + fee_val.to_bytes(3, 'big')
)
salt = keccak.new(digest_bits=256, data=payload).digest()
factory = bytes.fromhex(factory_hex[2:])
init_hash = bytes.fromhex(init_hash_hex[2:])
create2_input = b'\xff' + factory + salt + init_hash
pool_addr = '0x' + keccak.new(digest_bits=256, data=create2_input).digest()[-20:].hex()
print("Computed:", pool_addr)
print("Expected:", "0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997".lower())
