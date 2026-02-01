try:
    from eth_utils import keccak
    print("Found eth_utils.keccak")
except ImportError:
    print("No eth_utils")

try:
    from eth_hash.auto import keccak as eth_keccak
    print("Found eth_hash")
except ImportError:
    print("No eth_hash")

try:
    import sha3
    print("Found pysha3")
except ImportError:
    print("No pysha3")
