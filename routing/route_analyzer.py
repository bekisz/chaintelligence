"""
Route Analyzer Module

This module analyzes the routing paths for Uniswap V3 swaps.
It reconstructs multi-hop trades by grouping swap events by transaction hash
and ordering them by their log index.
"""

from typing import List, Dict, Tuple, Optional
from collections import defaultdict

class RouteAnalyzer:
    """Analyzes swap routes between tokens"""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def _get_log_index(self, swap_id: str) -> int:
        """Extract log index from swap ID (format: tx_hash#log_index)"""
        try:
            return int(swap_id.split('#')[1])
        except (IndexError, ValueError):
            return 0

    def analyze_routes(self, swaps: List[Dict], start_token: str, end_token: str) -> Dict:
        """
        Analyze routing paths between start_token and end_token.
        
        Args:
            swaps: List of normalized swap dictionaries
            start_token: Symbol of the starting token (e.g. 'EURC')
            end_token: Symbol of the ending token (e.g. 'EURCV')
            
        Returns:
            Dictionary containing route statistics
        """
        # 1. Group swaps by transaction hash
        tx_swaps = defaultdict(list)
        for swap in swaps:
            tx_swaps[swap['tx_hash']].append(swap)
            
        valid_routes = []
        start_token = start_token.upper()
        end_token = end_token.upper()
        
        # 2. Reconstruct path for each transaction
        for tx_hash, tx_events in tx_swaps.items():
            # Sort by log index to get correct order of swaps
            tx_events.sort(key=lambda x: self._get_log_index(x['id']))
            
            # Trace the token path
            # A transaction might be complex, but for a simple multi-hop swap:
            # Swap 1: Token A -> Token B
            # Swap 2: Token B -> Token C
            
            if not tx_events:
                continue
                
            # Build the path
            # We look at the first swap's input and last swap's output
            # But we need to strictly follow flow.
            # In a single pool swap, amount0 and amount1 have opposite signs.
            # Negative = Input (sold), Positive = Output (bought)
            
            # However, our normalized data just gives us token0/token1 and amount0/amount1.
            # We need to determine flow for each hop.
            
            path = []
            
            # Identify initial input
            first_swap = tx_events[0]
            
            # Try to determine flow of first swap
            # If amount0 is negative, token0 was the input (sold)
            # If amount1 is negative, token1 was the input (sold)
            
            current_token = None
            
            # Heuristic for first token:
            # We are looking for routes STARTING with start_token
            # In Uniswap V3, positive amount means user SOLD (Pool RECEIVED)
            # Negative amount means user BOUGHT (Pool SENT)
            # So start_token should have POSITIVE amount.
            
            if first_swap['token0_symbol'] == start_token and first_swap['amount0'] > 0:
                current_token = first_swap['token1_symbol'] # Output of first swap
                path = [start_token, current_token]
            elif first_swap['token1_symbol'] == start_token and first_swap['amount1'] > 0:
                current_token = first_swap['token0_symbol'] # Output of first swap
                path = [start_token, current_token]
            else:
                # This transaction doesn't start with our target token swap
                continue
            
            # Process subsequent swaps
            valid_path = True
            for i in range(1, len(tx_events)):
                next_swap = tx_events[i]
                
                # Check if this swap connects to our current token
                # The input of this swap should be our current_token (which was output of prev swap)
                # So current_token should be the one with POSITIVE amount in this swap
                
                if next_swap['token0_symbol'] == current_token and next_swap['amount0'] > 0:
                    current_token = next_swap['token1_symbol']
                    path.append(current_token)
                elif next_swap['token1_symbol'] == current_token and next_swap['amount1'] > 0:
                    current_token = next_swap['token0_symbol']
                    path.append(current_token)
                else:
                    # Broken chain or complex multi-branch tx
                    # For simple routing, we expect linear chain.
                    pass
            
            # Check if route ended at desired token
            if path[-1] == end_token:
                # Calculate total volume for the *input* of the trade (the start token side)
                # Volume is usually tracked in USD, we can just sum the USD volume of all segments?
                # No, volume of the trade is the input amount * price. 
                # Aggregating volume of all hops would double count.
                # Let's take the max USD volume of any hop as the trade volume? 
                # Or average? Or just the first hop?
                # Best proxy is probably the first hop's USD value.
                
                route_vol_usd = tx_events[0]['amountUSD']
                
                # Volume Fallback for low-liquidity pairs (e.g., EURC-EURCV)
                if route_vol_usd < 0.01:
                    # Approximate prices for fallback
                    PRICES = {
                        'USDC': 1.0,
                        'USDT': 1.0,
                        'DAI': 1.0,
                        'EURC': 1.05,
                        'EURCV': 1.05,
                        'WETH': 2500.0,
                        'WBTC': 95000.0
                    }
                    
                    first_swap = tx_events[0]
                    t0_sym = first_swap['token0_symbol']
                    t1_sym = first_swap['token1_symbol']
                    
                    if t0_sym in PRICES:
                        route_vol_usd = abs(first_swap['amount0']) * PRICES[t0_sym]
                    elif t1_sym in PRICES:
                        route_vol_usd = abs(first_swap['amount1']) * PRICES[t1_sym]
                
                
                valid_routes.append({
                    'tx_hash': tx_hash,
                    'path': path,
                    'volume_usd': route_vol_usd,
                    'swaps_count': len(tx_events)
                })

        # 3. Aggregate stats by unique path
        stats = {}
        
        for route in valid_routes:
            path_str = ' -> '.join(route['path'])
            
            if path_str not in stats:
                stats[path_str] = {
                    'tx_count': 0,
                    'volume_usd': 0.0,
                    'path': route['path']
                }
            
            stats[path_str]['tx_count'] += 1
            stats[path_str]['volume_usd'] += route['volume_usd']
            
        # Convert to list for sorting
        results = []
        total_vol = sum(r['volume_usd'] for r in stats.values())
        
        for path_str, data in stats.items():
            results.append({
                'path': path_str,
                'count': data['tx_count'],
                'volume': data['volume_usd'],
                'avg_volume': data['volume_usd'] / data['tx_count'] if data['tx_count'] > 0 else 0,
                'pct_volume': (data['volume_usd'] / total_vol * 100) if total_vol > 0 else 0,
                'hops': len(data['path']) - 1 # Hops = number of arrows
            })
            
        # Sort by volume descending
        results.sort(key=lambda x: x['volume'], reverse=True)
        
        return {
            'routes': results,
            'total_tx': len(valid_routes),
            'total_volume': total_vol
        }
