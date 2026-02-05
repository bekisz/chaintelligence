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
        
        # Validation: Allow at most one wildcard
        if start_token == '*' and end_token == '*':
            return {
                'routes': [],
                'total_tx': 0,
                'total_volume': 0
            }
        
        # 2. Reconstruct path for each transaction
        stats = {}
        total_tx_count = 0
        
        for tx_hash, tx_events in tx_swaps.items():
            # Sort by log index to get correct order of swaps
            tx_events.sort(key=lambda x: self._get_log_index(x['id']))
            
            if not tx_events:
                continue
                
            path = []
            first_swap = tx_events[0]
            current_token = None
            
            # Determine effective start token for this transaction
            tx_start_token = start_token
            if start_token == '*':
                # Infer start from first swap
                if first_swap['amount0'] > 0:
                    tx_start_token = first_swap['token0_symbol']
                elif first_swap['amount1'] > 0:
                    tx_start_token = first_swap['token1_symbol']
                else:
                    continue

            if first_swap['token0_symbol'] == tx_start_token and first_swap['amount0'] > 0:
                current_token = first_swap['token1_symbol']
                path = [tx_start_token, first_swap['fee_tier'], current_token]
            elif first_swap['token1_symbol'] == tx_start_token and first_swap['amount1'] > 0:
                current_token = first_swap['token0_symbol']
                path = [tx_start_token, first_swap['fee_tier'], current_token]
            else:
                continue
            
            # Process subsequent swaps
            for i in range(1, len(tx_events)):
                next_swap = tx_events[i]
                if next_swap['token0_symbol'] == current_token and next_swap['amount0'] > 0:
                    current_token = next_swap['token1_symbol']
                    path.append(next_swap['fee_tier'])
                    path.append(current_token)
                elif next_swap['token1_symbol'] == current_token and next_swap['amount1'] > 0:
                    current_token = next_swap['token0_symbol']
                    path.append(next_swap['fee_tier'])
                    path.append(current_token)
            
            # Check if route ended at desired token
            if end_token == '*' or path[-1] == end_token:
                # Calculate total volume (using first hop as proxy)
                route_vol_usd = tx_events[0]['amountUSD']
                
                # Volume Fallback for low-liquidity pairs
                if route_vol_usd < 0.01:
                    PRICES = {
                        'USDC': 1.0, 'USDT': 1.0, 'DAI': 1.0,
                        'EURC': 1.05, 'EURCV': 1.05,
                        'WETH': 2500.0, 'WBTC': 95000.0, 'GNO': 300.0,
                    }
                    t0_sym = first_swap['token0_symbol']
                    t1_sym = first_swap['token1_symbol']
                    if t0_sym in PRICES:
                        route_vol_usd = abs(first_swap['amount0']) * PRICES[t0_sym]
                    elif t1_sym in PRICES:
                        route_vol_usd = abs(first_swap['amount1']) * PRICES[t1_sym]
                
                # Aggregate immediately
                # Format path: TokenA -- fee% --> TokenB -- fee% --> TokenC
                path_parts = []
                for i in range(len(path)):
                    if i % 2 == 0:
                        path_parts.append(path[i])
                    else:
                        path_parts.append(f"-- {path[i]} -->")
                
                path_str = ' '.join(path_parts)
                
                if path_str not in stats:
                    stats[path_str] = {
                        'tx_count': 0,
                        'volume_usd': 0.0,
                        'path': path
                    }
                
                stats[path_str]['tx_count'] += 1
                stats[path_str]['volume_usd'] += route_vol_usd
                total_tx_count += 1

        # Convert to list for sorting
        results = []
        total_vol = sum(r['volume_usd'] for r in stats.values())
        
        for path_str, data in stats.items():
            results.append({
                'path': path_str,
                'path_tokens': data['path'],
                'count': data['tx_count'],
                'volume': data['volume_usd'],
                'avg_volume': data['volume_usd'] / data['tx_count'] if data['tx_count'] > 0 else 0,
                'pct_volume': (data['volume_usd'] / total_vol * 100) if total_vol > 0 else 0,
                'hops': len(data['path']) // 2 
            })
            
        # Sort by volume descending and limit
        results.sort(key=lambda x: x['volume'], reverse=True)
        results = results[:200]  # Limit to top 200 routes
        
        return {
            'routes': results,
            'total_tx': total_tx_count,
            'total_volume': total_vol
        }
