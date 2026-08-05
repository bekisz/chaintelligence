
import unittest
from route_analyzer import RouteAnalyzer

class TestRouteAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = RouteAnalyzer()

    def test_simple_route(self):
        # A -> B -> C
        swaps = [
            {
                'id': 'tx1#1',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_A',
                'token1_symbol': 'TOKEN_B',
                'amount0': 100,  # Input to pool (User sold A)
                'amount1': -50,  # Output from pool (User bought B)
                'amountUSD': 1000,
                'fee_tier': '0.05%',
                'protocol': 'Uniswap V3',
                'network': 'Ethereum',
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_B',
                'token1_symbol': 'TOKEN_C',
                'amount0': 50,   # Input to pool (User sold B)
                'amount1': -25,  # Output from pool (User bought C)
                'amountUSD': 1000,
                'fee_tier': '0.05%',
                'protocol': 'Uniswap V3',
                'network': 'Ethereum',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_C')
        routes = result['routes']

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['path'],
                         'TOKEN_A -- 0.05%|Uniswap V3|Ethereum --> TOKEN_B -- 0.05%|Uniswap V3|Ethereum --> TOKEN_C')
        self.assertEqual(routes[0]['count'], 1)
        self.assertEqual(routes[0]['swaps'], 2)  # 2-hop route = 2 swap events for 1 tx
        self.assertEqual(routes[0]['volume'], 1000)

    def test_reverse_pair_order(self):
        # A -> B (Pool is B-A) -> C
        swaps = [
            {
                'id': 'tx1#1',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_B',
                'token1_symbol': 'TOKEN_A',
                'amount0': -50,  # Output B (User bought B)
                'amount1': 100,  # Input A (User sold A)
                'amountUSD': 1000,
                'fee_tier': '0.05%',
                'protocol': 'Uniswap V3',
                'network': 'Ethereum',
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_B',
                'token1_symbol': 'TOKEN_C',
                'amount0': 50,   # Input B
                'amount1': -25,  # Output C
                'amountUSD': 1000,
                'fee_tier': '0.05%',
                'protocol': 'Uniswap V3',
                'network': 'Ethereum',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_C')
        routes = result['routes']

        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['path'],
                         'TOKEN_A -- 0.05%|Uniswap V3|Ethereum --> TOKEN_B -- 0.05%|Uniswap V3|Ethereum --> TOKEN_C')

    def test_aerodrome_base_route(self):
        # USDC -> AERO via an Aerodrome (Base) pool — verifies the route analyzer
        # reconstructs paths for protocol='Aerodrome' on network='Base' exactly as
        # it does for Uniswap V3 on Ethereum (protocol/network are labels, not keys).
        swaps = [
            {
                'id': 'txaero#1',
                'tx_hash': 'txaero',
                'token0_symbol': 'USDC',
                'token1_symbol': 'AERO',
                'amount0': 1000,   # Input USDC
                'amount1': -500,   # Output AERO
                'amountUSD': 1000,
                'fee_tier': '0.3%',
                'protocol': 'Aerodrome',
                'network': 'Base',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'USDC', 'AERO')
        routes = result['routes']

        self.assertEqual(len(routes), 1)
        self.assertEqual(
            routes[0]['path'],
            'USDC -- 0.3%|Aerodrome|Base --> AERO'
        )
        self.assertEqual(routes[0]['count'], 1)
        self.assertEqual(routes[0]['swaps'], 1)  # 1-hop route = 1 swap event
        self.assertEqual(routes[0]['volume'], 1000)

    def test_broken_chain(self):
        # A -> B ... break ... D -> E
        swaps = [
            {
                'id': 'tx1#1',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_A',
                'token1_symbol': 'TOKEN_B',
                'amount0': 100,
                'amount1': -50,
                'amountUSD': 1000,
                'fee_tier': '0.05%',
                'protocol': 'Uniswap V3',
                'network': 'Ethereum',
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_D',
                'token1_symbol': 'TOKEN_E',
                'amount0': 10,
                'amount1': -5,
                'amountUSD': 100,
                'fee_tier': '0.3%',
                'protocol': 'Uniswap V3',
                'network': 'Ethereum',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_E')
        self.assertEqual(len(result['routes']), 0)

    def test_router_split_full_volume(self):
        # ONE tx, two A -> B log entries (a router split across two pools):
        # counts as 1 tx, 2 swap log entries, and FULL volume (both legs), so
        # the top table total matches the per-pool backtest total.
        swaps = [
            {
                'id': 'tx1#1', 'tx_hash': 'tx1', 'log_index': 1,
                'token0_symbol': 'TOKEN_A', 'token1_symbol': 'TOKEN_B',
                'amount0': 100, 'amount1': -420, 'amountUSD': 420,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V3', 'network': 'Ethereum',
            },
            {
                'id': 'tx1#2', 'tx_hash': 'tx1', 'log_index': 2,
                'token0_symbol': 'TOKEN_A', 'token1_symbol': 'TOKEN_B',
                'amount0': 200, 'amount1': -840, 'amountUSD': 840,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V4', 'network': 'Ethereum',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_B')
        routes = result['routes']
        self.assertEqual(result['total_tx'], 1)
        self.assertEqual(result['total_volume'], 1260.0)  # both legs counted
        self.assertEqual(routes[0]['count'], 1)
        self.assertEqual(routes[0]['swaps'], 2)           # 2 log entries

    def test_multi_hop_volume_counted_once(self):
        # A -> B -> C multi-hop: the same value flows through both hops, so the
        # swap volume is counted once (only the first hop consumes A).
        swaps = [
            {
                'id': 'tx1#1', 'tx_hash': 'tx1', 'log_index': 1,
                'token0_symbol': 'TOKEN_A', 'token1_symbol': 'TOKEN_B',
                'amount0': 100, 'amount1': -50, 'amountUSD': 1000,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V3', 'network': 'Ethereum',
            },
            {
                'id': 'tx1#2', 'tx_hash': 'tx1', 'log_index': 2,
                'token0_symbol': 'TOKEN_B', 'token1_symbol': 'TOKEN_C',
                'amount0': 50, 'amount1': -1000, 'amountUSD': 1000,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V3', 'network': 'Ethereum',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_C')
        self.assertEqual(result['total_tx'], 1)
        self.assertEqual(result['total_volume'], 1000.0)  # counted once, not 2000

    def test_round_trip_direct_leg_counts(self):
        # A -> B then B -> A in one tx (arb round-trip): it has a direct A->B
        # leg, so it counts as 1 swap with only the A-consuming leg's volume.
        swaps = [
            {
                'id': 'tx1#1', 'tx_hash': 'tx1', 'log_index': 1,
                'token0_symbol': 'TOKEN_A', 'token1_symbol': 'TOKEN_B',
                'amount0': 100, 'amount1': -420, 'amountUSD': 420,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V3', 'network': 'Ethereum',
            },
            {
                'id': 'tx1#2', 'tx_hash': 'tx1', 'log_index': 2,
                'token0_symbol': 'TOKEN_A', 'token1_symbol': 'TOKEN_B',
                'amount0': -420, 'amount1': 100, 'amountUSD': 420,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V3', 'network': 'Ethereum',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_B')
        self.assertEqual(result['total_tx'], 1)
        self.assertEqual(result['total_volume'], 420.0)   # only the A->B leg

    def test_reverse_first_leg_excluded(self):
        # First log entry is B -> A (the tx initiates by spending B), then a
        # small A -> B unwind. Not an A->B swap -> excluded entirely.
        swaps = [
            {
                'id': 'tx1#1', 'tx_hash': 'tx1', 'log_index': 1,
                'token0_symbol': 'TOKEN_A', 'token1_symbol': 'TOKEN_B',
                'amount0': -485, 'amount1': 2033, 'amountUSD': 2033,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V3', 'network': 'Ethereum',
            },
            {
                'id': 'tx1#2', 'tx_hash': 'tx1', 'log_index': 2,
                'token0_symbol': 'TOKEN_A', 'token1_symbol': 'TOKEN_B',
                'amount0': 28, 'amount1': -121, 'amountUSD': 121,
                'fee_tier': '0.3%', 'protocol': 'Uniswap V3', 'network': 'Ethereum',
            }
        ]

        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_B')
        self.assertEqual(result['total_tx'], 0)
        self.assertEqual(len(result['routes']), 0)

if __name__ == '__main__':
    unittest.main()
