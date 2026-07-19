"""Shared Uniswap V4 (and PancakeSwap V4) poolId derivation.

V4 pools live behind a singleton PoolManager, so there is no per-pool
contract address. Each pool is identified by a bytes32 poolId:

    poolId = keccak256(abi.encode(currency0, currency1, fee, tickSpacing, hooks))

with currency0 < currency1 (numerically sorted, exactly as the V4 core
stores them). This is the value the Uniswap/PancakeSwap explorers expect
in `app.uniswap.org/explore/pools/<network>/<poolId>` style URLs.

Importable by both the Airflow DAGs (`from include.v4_pool import ...`) and
the FastAPI layer (`chain-feeder` is on sys.path, `include` is a package).
"""
from eth_hash.auto import keccak

_NATIVE_ZERO = '0x' + '0' * 40


def _addr_bytes(hex_addr):
    """Parse a 0x-prefixed 40-hex address into 20 bytes (native ETH = 0x0...0)."""
    return bytes.fromhex((hex_addr or _NATIVE_ZERO).lower().removeprefix('0x').rjust(40, '0'))


def derive_v4_pool_id(c0_hex, c1_hex, fee, tick_spacing, hooks_hex=None):
    """Return the 0x-prefixed bytes32 V4 poolId.

    c0_hex/c1_hex: 0x-prefixed 40-hex contract addresses (native ETH = 0x0...0).
    fee, tick_spacing: ints as returned by the V4 PoolManager getPoolKey
                       (fee in Uniswap units, e.g. 3000 = 0.30%).
    hooks_hex: 0x-prefixed 20-byte hook address (default address(0)).
    """
    a = _addr_bytes(c0_hex)
    b = _addr_bytes(c1_hex)
    if b < a:
        a, b = b, a
    hooks = _addr_bytes(hooks_hex).rjust(32, b'\x00')
    enc = (a.rjust(32, b'\x00') + b.rjust(32, b'\x00') +
           int(fee).to_bytes(32, 'big') +
           int(tick_spacing).to_bytes(32, 'big', signed=True) + hooks)
    return '0x' + keccak(enc).hex()
