data = "0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffffffce68ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffd1cb600000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000019526"
# Remove 0x
d = data[2:]
# Split into 32 bytes (64 chars)
chunks = [d[i:i+64] for i in range(0, len(d), 64)]

print(f"Chunks: {len(chunks)}")
for i, c in enumerate(chunks):
    val_int = int(c, 16)
    # Check signed
    if val_int > 2**255:
        val_int -= 2**256
    print(f"Slot {i}: {c} -> {val_int}")
