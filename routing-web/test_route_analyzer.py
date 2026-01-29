
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
                'amountUSD': 1000
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_B',
                'token1_symbol': 'TOKEN_C',
                'amount0': 50,   # Input to pool (User sold B)
                'amount1': -25,  # Output from pool (User bought C)
                'amountUSD': 1000
            }
        ]
        
        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_C')
        routes = result['routes']
        
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['path'], 'TOKEN_A -> TOKEN_B -> TOKEN_C')
        self.assertEqual(routes[0]['count'], 1)
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
                'amountUSD': 1000
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_B',
                'token1_symbol': 'TOKEN_C',
                'amount0': 50,   # Input B
                'amount1': -25,  # Output C
                'amountUSD': 1000
            }
        ]
        
        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_C')
        routes = result['routes']
        
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['path'], 'TOKEN_A -> TOKEN_B -> TOKEN_C')

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
                'amountUSD': 1000
            },
            {
                'id': 'tx1#2',
                'tx_hash': 'tx1',
                'token0_symbol': 'TOKEN_D',
                'token1_symbol': 'TOKEN_E',
                'amount0': 10,
                'amount1': -5,
                'amountUSD': 100
            }
        ]
        
        result = self.analyzer.analyze_routes(swaps, 'TOKEN_A', 'TOKEN_E')
        self.assertEqual(len(result['routes']), 0)

if __name__ == '__main__':
    unittest.main()
