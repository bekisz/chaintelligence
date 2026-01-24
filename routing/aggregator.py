"""
Swap Data Aggregator

This module aggregates swap data by token pairs and calculates
transaction volumes and counts.
"""

from typing import List, Dict, Tuple
from collections import defaultdict


class SwapAggregator:
    """Aggregates swap data by token pairs"""
    
    def __init__(self):
        self.pair_data = defaultdict(lambda: {
            'volume_usd': 0.0,
            'tx_count': 0,
            'transactions': []
        })
    
    @staticmethod
    def normalize_pair(token0: str, token1: str) -> Tuple[str, str]:
        """
        Normalize token pair to ensure consistent ordering
        (e.g., AAVE-LINK and LINK-AAVE both become AAVE-LINK)
        """
        return tuple(sorted([token0, token1]))
    
    def aggregate_swaps(self, swaps: List[Dict]) -> Dict[str, Dict]:
        """
        Aggregate swaps by token pairs
        
        Args:
            swaps: List of swap events
        
        Returns:
            Dictionary mapping pair names to aggregated statistics
        """
        self.pair_data.clear()
        
        for swap in swaps:
            # Normalize pair to avoid duplicates
            token0_sym = swap['token0_symbol']
            token1_sym = swap['token1_symbol']
            pair = self.normalize_pair(token0_sym, token1_sym)
            pair_name = f"{pair[0]}-{pair[1]}"
            
            # Aggregate data
            volume = abs(swap['amountUSD'])
            
            # Fallback for missing USD volume (common for new/low-liq pairs)
            # Check if volume is effectively zero (e.g. < 0.01 or exactly 0)
            if volume < 0.01:
                # Approximate prices
                PRICES = {
                    'USDC': 1.0,
                    'USDT': 1.0,
                    'DAI': 1.0,
                    'EURC': 1.05,  # Approx EUR/USD
                    'EURCV': 1.05, # Approx EUR/USD
                    'WETH': 2500.0 # Rough fallback
                }
                
                t0_sym = swap['token0_symbol']
                t1_sym = swap['token1_symbol']
                
                if t0_sym in PRICES:
                    volume = abs(swap['amount0']) * PRICES[t0_sym]
                elif t1_sym in PRICES:
                    volume = abs(swap['amount1']) * PRICES[t1_sym]
            
            self.pair_data[pair_name]['volume_usd'] += volume
            self.pair_data[pair_name]['tx_count'] += 1
            self.pair_data[pair_name]['transactions'].append({
                'timestamp': swap['timestamp'],
                'tx_hash': swap['tx_hash'],
                'amountUSD': volume
            })
        
        return dict(self.pair_data)
    
    def get_sorted_pairs(self, by: str = 'volume') -> List[Tuple[str, Dict]]:
        """
        Get pairs sorted by volume or transaction count
        
        Args:
            by: Sort key - 'volume' or 'tx_count'
        
        Returns:
            List of (pair_name, data) tuples sorted by specified metric
        """
        if by == 'volume':
            sort_key = lambda x: x[1]['volume_usd']
        elif by == 'tx_count':
            sort_key = lambda x: x[1]['tx_count']
        else:
            raise ValueError(f"Invalid sort key: {by}. Use 'volume' or 'tx_count'")
        
        return sorted(self.pair_data.items(), key=sort_key, reverse=True)
    
    def get_summary(self) -> Dict:
        """Get summary statistics across all pairs"""
        total_volume = sum(data['volume_usd'] for data in self.pair_data.values())
        total_txs = sum(data['tx_count'] for data in self.pair_data.values())
        
        return {
            'total_pairs': len(self.pair_data),
            'total_volume_usd': total_volume,
            'total_transactions': total_txs,
            'avg_volume_per_pair': total_volume / len(self.pair_data) if self.pair_data else 0,
            'avg_txs_per_pair': total_txs / len(self.pair_data) if self.pair_data else 0
        }
    
    def get_token_volumes(self) -> Dict[str, Dict]:
        """
        Get volume statistics per token (aggregated across all pairs)
        
        Returns:
            Dictionary mapping token symbols to their total volumes and transaction counts
        """
        token_stats = defaultdict(lambda: {
            'volume_usd': 0.0,
            'tx_count': 0,
            'pairs': []
        })
        
        for pair_name, data in self.pair_data.items():
            # Split pair into tokens
            tokens = pair_name.split('-')
            
            # Add volume to each token
            for token in tokens:
                token_stats[token]['volume_usd'] += data['volume_usd']
                token_stats[token]['tx_count'] += data['tx_count']
                token_stats[token]['pairs'].append(pair_name)
        
        return dict(token_stats)
    
    def get_sorted_tokens(self, by: str = 'volume') -> List[Tuple[str, Dict]]:
        """
        Get tokens sorted by volume or transaction count
        
        Args:
            by: Sort key - 'volume' or 'tx_count'
        
        Returns:
            List of (token_symbol, stats) tuples sorted by specified metric
        """
        token_volumes = self.get_token_volumes()
        
        if by == 'volume':
            sort_key = lambda x: x[1]['volume_usd']
        elif by == 'tx_count':
            sort_key = lambda x: x[1]['tx_count']
        else:
            raise ValueError(f"Invalid sort key: {by}. Use 'volume' or 'tx_count'")
        
        return sorted(token_volumes.items(), key=sort_key, reverse=True)
