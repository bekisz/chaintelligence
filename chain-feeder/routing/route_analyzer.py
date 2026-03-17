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
    
    def __init__(self, verbose: bool = False, prices: Optional[Dict[str, float]] = None):
        self.verbose = verbose
        self.prices = prices or {}
        self.reset()

    def reset(self):
        """Reset internal accumulator state"""
        self.stats = {}
        self.total_tx_count = 0
        self.total_volume = 0.0

    def _get_log_index(self, swap_id: str) -> int:
        """Extract log index from swap ID (format: tx_hash#log_index)"""
        try:
            return int(swap_id.split('#')[1])
        except (IndexError, ValueError):
            return 0

    def process_batch(self, swaps: List[Dict], start_tokens: List[str], end_tokens: List[str]):
        """
        Process a batch of swaps and update internal stats.
        """
        # Normalize inputs
        if isinstance(start_tokens, str): start_tokens = [start_tokens]
        if isinstance(end_tokens, str): end_tokens = [end_tokens]
        
        start_tokens = [t.upper() for t in start_tokens]
        end_tokens = [t.upper() for t in end_tokens]
        
        start_is_wildcard = '*' in start_tokens
        end_is_wildcard = '*' in end_tokens
        
        # 1. Group swaps by transaction hash
        tx_swaps = defaultdict(list)
        for swap in swaps:
            tx_swaps[swap['tx_hash']].append(swap)
            
        # 2. Reconstruct path for each transaction
        
        for tx_hash, tx_events in tx_swaps.items():
            # Sort by log index to get correct order of swaps
            tx_events.sort(key=lambda x: self._get_log_index(x['id']))
            
            if not tx_events:
                continue
                
            path = []
            first_swap = tx_events[0]
            current_token = None
            tx_start_token = None
            
            # Determine effective start token for this transaction
            if start_is_wildcard:
                # Infer start from first swap (standard wildcard logic)
                if first_swap['amount0'] > 0:
                    tx_start_token = first_swap['token0_symbol'].upper()
                elif first_swap['amount1'] > 0:
                    tx_start_token = first_swap['token1_symbol'].upper()
                else:
                    continue
                    
                # Setup path based on inferred start
                if first_swap['token0_symbol'].upper() == tx_start_token:
                    current_token = first_swap['token1_symbol'].upper()
                    path = [tx_start_token, f"{first_swap['fee_tier']}|{first_swap.get('protocol', 'v3')}", current_token]
                else:
                    current_token = first_swap['token0_symbol'].upper()
                    path = [tx_start_token, f"{first_swap['fee_tier']}|{first_swap.get('protocol', 'v3')}", current_token]
            else:
                # Check membership
                t0_up = first_swap['token0_symbol'].upper()
                t1_up = first_swap['token1_symbol'].upper()
                if t0_up in start_tokens and first_swap['amount0'] > 0:
                    tx_start_token = t0_up
                    current_token = t1_up
                    path = [tx_start_token, f"{first_swap['fee_tier']}|{first_swap.get('protocol', 'v3')}", current_token]
                elif t1_up in start_tokens and first_swap['amount1'] > 0:
                    tx_start_token = t1_up
                    current_token = t0_up
                    path = [tx_start_token, f"{first_swap['fee_tier']}|{first_swap.get('protocol', 'v3')}", current_token]
                else:
                    continue
            
            # Process subsequent swaps
            for i in range(1, len(tx_events)):
                next_swap = tx_events[i]
                t0_next = next_swap['token0_symbol'].upper()
                t1_next = next_swap['token1_symbol'].upper()
                if t0_next == current_token and next_swap['amount0'] > 0:
                    current_token = t1_next
                    path.append(f"{next_swap['fee_tier']}|{next_swap.get('protocol', 'v3')}")
                    path.append(current_token)
                elif t1_next == current_token and next_swap['amount1'] > 0:
                    current_token = t0_next
                    path.append(f"{next_swap['fee_tier']}|{next_swap.get('protocol', 'v3')}")
                    path.append(current_token)
            
            # Check if route ended at desired token(s)
            if end_is_wildcard or path[-1] in end_tokens:
                # Calculate total volume (using first hop as proxy)
                try:
                    route_vol_usd = float(tx_events[0].get('amountUSD', tx_events[0].get('amount_usd', 0.0)) or 0.0)
                except (KeyError, TypeError):
                     # Fallback if key missing or None
                     route_vol_usd = 0.0

                # Volume Fallback for low-liquidity pairs or missing price data
                if route_vol_usd < 0.01:
                    t0_sym = first_swap['token0_symbol'].upper()
                    t1_sym = first_swap['token1_symbol'].upper()
                    
                    p0 = self.prices.get(t0_sym)
                    p1 = self.prices.get(t1_sym)
                    
                    # Stablecoin heuristics if price missing
                    if p0 is None and any(x in t0_sym for x in ['USD', 'EUR']): p0 = 1.0
                    if p1 is None and any(x in t1_sym for x in ['USD', 'EUR']): p1 = 1.0
                    
                    if p0 is not None:
                        route_vol_usd = abs(first_swap['amount0']) * p0
                    elif p1 is not None:
                        route_vol_usd = abs(first_swap['amount1']) * p1
                
                # Aggregate immediately
                # Format path: TokenA -- fee%|protocol --> TokenB -- fee%|protocol --> TokenC
                path_parts = []
                for i in range(len(path)):
                    if i % 2 == 0:
                        path_parts.append(path[i])
                    else:
                        # Remove protocol suffix for pretty string formatting if needed, but we keep it
                        # to aggregate v3 and v4 separately
                        path_parts.append(f"-- {path[i]} -->")
                
                path_str = ' '.join(path_parts)
                
                if path_str not in self.stats:
                    self.stats[path_str] = {
                        'tx_count': 0,
                        'volume_usd': 0.0,
                        'path': path
                    }
                
                self.stats[path_str]['tx_count'] += 1
                self.stats[path_str]['volume_usd'] += route_vol_usd
                self.total_tx_count += 1
                self.total_volume += route_vol_usd

    def get_results(self) -> Dict:
        """
        Finalize and return the analysis results.
        """
        # Convert to list for sorting
        results = []
        
        total_vol = self.total_volume # Use aggregated total
        if total_vol == 0:
             # Calculate from stats just in case
             total_vol = sum(r['volume_usd'] for r in self.stats.values())

        for path_str, data in self.stats.items():
            # Calculate cumulative fee for the route
            cumulative_fee = 0.0
            path_list = data['path']
            # Path format: [Token, Fee, Token, Fee, Token]
            # Fees are at odd indices: 1, 3, 5...
            for i in range(1, len(path_list), 2):
                fee_val = path_list[i]
                if isinstance(fee_val, str) and fee_val.endswith('%'):
                    try:
                        # '0.05%' -> 0.0005
                        cumulative_fee += float(fee_val.strip('%')) / 100.0
                    except ValueError:
                        pass
                elif isinstance(fee_val, (int, float)):
                    # Assume raw partial (e.g. 500 for 0.05%) -> 500/10000/100 ? 
                    # Or just 0.0005?
                    # Based on previous debug, DB has strings. fallback to 0 if unknown.
                    pass
            
            market_size = data['volume_usd'] * cumulative_fee

            results.append({
                'path': path_str,
                'path_tokens': data['path'],
                'count': data['tx_count'],
                'volume': data['volume_usd'],
                'market_size': market_size,
                'avg_volume': data['volume_usd'] / data['tx_count'] if data['tx_count'] > 0 else 0,
                'pct_volume': (data['volume_usd'] / total_vol * 100) if total_vol > 0 else 0,
                'hops': len(data['path']) // 2 
            })
            
        # Sort by volume descending and limit
        results.sort(key=lambda x: x['volume'], reverse=True)
        results = results[:200]  # Limit to top 200 routes
        
        return {
            'routes': results,
            'total_tx': self.total_tx_count,
            'total_volume': total_vol
        }

    def analyze_routes(self, swaps: List[Dict], start_tokens: List[str], end_tokens: List[str]) -> Dict:
        """
        Legacy wrapper for one-shot analysis
        """
        self.reset()
        self.process_batch(swaps, start_tokens, end_tokens)
        return self.get_results()
