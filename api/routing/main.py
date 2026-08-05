"""
Uniswap V3 Routing Aggregation CLI

Command-line tool to fetch and aggregate Uniswap V3 swap data
for specified token pairs over a given time period.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from tabulate import tabulate

from uniswap_fetcher import UniswapV3Fetcher
from postgres_fetcher import PostgresFetcher
from aggregator import SwapAggregator


def parse_date(date_str: str) -> datetime:
    """Parse date string in YYYY-MM-DD format"""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def format_usd(amount: float) -> str:
    """Format USD amount with commas and 2 decimal places"""
    return f"${amount:,.2f}"


def main():
    parser = argparse.ArgumentParser(
        description='Fetch and aggregate Uniswap V3 routing data for specified tokens',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get data for the last 7 days
  python main.py --days 7
  
  # Get data for a specific date range
  python main.py --start-date 2026-01-01 --end-date 2026-01-24
  
  # Filter to specific tokens only
  python main.py --days 7 --tokens EURC EURCV USDC
  
  # Output as JSON
  python main.py --days 7 --format json
  
  # Verbose mode for debugging
  python main.py --days 1 --verbose
        """
    )
    
    # Date range options
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument(
        '--start-date',
        type=parse_date,
        help='Start date (YYYY-MM-DD)'
    )
    date_group.add_argument(
        '--days',
        type=float,
        help='Number of days to look back from today'
    )
    
    parser.add_argument(
        '--end-date',
        type=parse_date,
        help='End date (YYYY-MM-DD), defaults to today'
    )
    
    # Output options
    parser.add_argument(
        '--format',
        choices=['table', 'json'],
        default='table',
        help='Output format (default: table)'
    )
    
    parser.add_argument(
        '--sort-by',
        choices=['volume', 'tx_count'],
        default='volume',
        help='Sort pairs by volume or transaction count (default: volume)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    parser.add_argument(
        '--tokens',
        nargs='+',
        help='Filter to specific tokens (e.g., --tokens EURC EURCV USDC). If not specified, uses all configured tokens.'
    )
    
    # Route analysis options
    parser.add_argument(
        '--route',
        help='Analyze routing paths between two tokens (e.g. "EURC-EURCV")'
    )
    
    parser.add_argument(
        '--source',
        choices=['db', 'graph'],
        default='db',
        help='Data source: "db" for Postgres, "graph" for The Graph (default: db)'
    )
    
    args = parser.parse_args()
    
    # Determine date range
    if args.days:
        end_date = args.end_date or datetime.now()
        start_date = end_date - timedelta(days=args.days)
    else:
        start_date = args.start_date
        end_date = args.end_date or datetime.now()
    
    # Validate date range
    if start_date >= end_date:
        print("Error: start date must be before end date", file=sys.stderr)
        sys.exit(1)
    
    # Validate and filter tokens if specified
    token_filter = None
    if args.tokens:
        from config import TOKENS
        # Validate that all specified tokens exist
        invalid_tokens = [t for t in args.tokens if t.upper() not in TOKENS]
        if invalid_tokens:
            print(f"Error: Invalid token(s): {', '.join(invalid_tokens)}", file=sys.stderr)
            print(f"Valid tokens: {', '.join(sorted(TOKENS.keys()))}", file=sys.stderr)
            sys.exit(1)
        token_filter = [t.upper() for t in args.tokens]
        if args.verbose:
            print(f"Filtering to tokens: {', '.join(token_filter)}")
            
    # Route analysis validation
    route_start = None
    route_end = None
    if args.route:
        try:
            parts = args.route.split('-')
            if len(parts) != 2:
                raise ValueError("Format must be TOKEN1-TOKEN2")
            route_start, route_end = parts[0].upper(), parts[1].upper()
            
            # Warn if tokens are not in config (fetcher might miss them)
            # But we don't block it, maybe user knows what they are doing if they added them to config
            from config import TOKENS
            if route_start not in TOKENS or route_end not in TOKENS:
                print(f"Warning: {route_start} or {route_end} not in configured tokens. Swaps might be missing.", file=sys.stderr)
                
        except Exception as e:
            print(f"Error parsing route: {e}", file=sys.stderr)
            sys.exit(1)
            
    try:
        # Fetch swap data
        if args.source == 'db':
            fetcher = PostgresFetcher(verbose=args.verbose)
        else:
            fetcher = UniswapV3Fetcher(verbose=args.verbose)
        
        # If doing route analysis, we should probably fetch ALL tokens to ensure intermediate hops are captured
        # Unless user explicitly restricted with --tokens
        # But for now let's respect --tokens if present, otherwise fetch all.
        
        swaps = fetcher.fetch_swaps(start_date, end_date, token_filter=token_filter)
        
        if not swaps:
            print("No swap data found for the specified time range and tokens")
            sys.exit(0)

        if args.route:
            from route_analyzer import RouteAnalyzer
            print(f"\n{'='*80}")
            print(f"Uniswap V3 Route Analysis: {route_start} -> {route_end}")
            print(f"Time Range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
            print(f"{'='*80}\n")
            
            analyzer = RouteAnalyzer(verbose=args.verbose)
            analysis = analyzer.analyze_routes(swaps, route_start, route_end)
            
            print(f"Found {analysis['total_tx']} valid routing transactions")
            print(f"Total Volume: {format_usd(analysis['total_volume'])}")
            print("-" * 80)
            
            if analysis['routes']:
                table_data = []
                for r in analysis['routes']:
                    table_data.append([
                        r['path'],
                        f"{r['count']}",
                        format_usd(r['volume']),
                        format_usd(r['avg_volume']),
                        f"{r['pct_volume']:.1f}%"
                    ])
                
                headers = ['Route Path', 'Tx Count', 'Volume', 'Avg Volume', '% Volume']
                print(tabulate(table_data, headers=headers, tablefmt='grid'))
                
                print(f"\nDetailed Route Breakdown:")
                print("-" * 80)
                for r in analysis['routes']:
                    print(f"Path: {r['path']}")
                    print(f"  • Transactions: {r['count']}")
                    print(f"  • Total Volume: {format_usd(r['volume'])}")
                    print(f"  • Avg Volume:   {format_usd(r['avg_volume'])}")
                    print("-" * 40)
                
            else:
                print(f"No routes found from {route_start} to {route_end}")
                
            print(f"\n{'='*80}\n")
            return

        # Aggregate by pairs
        aggregator = SwapAggregator()
        pair_data = aggregator.aggregate_swaps(swaps)
        
        # Get summary
        summary = aggregator.get_summary()
        
        # Output results
        if args.format == 'json':
            output = {
                'time_range': {
                    'start': start_date.isoformat(),
                    'end': end_date.isoformat()
                },
                'summary': summary,
                'pairs': pair_data,
                'token_volumes': aggregator.get_token_volumes()
            }
            print(json.dumps(output, indent=2))
        
        else:  # table format
            print(f"\n{'='*80}")
            print(f"Uniswap V3 Routing Analysis")
            print(f"Time Range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
            print(f"{'='*80}\n")
            
            # Summary table
            print("SUMMARY")
            print("-" * 80)
            summary_table = [
                ['Total Pairs', summary['total_pairs']],
                ['Total Volume (USD)', format_usd(summary['total_volume_usd'])],
                ['Total Transactions', f"{summary['total_transactions']:,}"],
                ['Avg Volume per Pair', format_usd(summary['avg_volume_per_pair'])],
                ['Avg Txs per Pair', f"{summary['avg_txs_per_pair']:.1f}"],
                ['Earliest Swap', datetime.fromtimestamp(summary['earliest_swap']).strftime('%Y-%m-%d %H:%M') if summary['earliest_swap'] else 'N/A'],
                ['Latest Swap', datetime.fromtimestamp(summary['latest_swap']).strftime('%Y-%m-%d %H:%M') if summary['latest_swap'] else 'N/A']
            ]
            print(tabulate(summary_table, tablefmt='simple'))
            
            # Pairs table
            print(f"\n\nTOKEN PAIRS (sorted by {args.sort_by})")
            print("-" * 80)
            
            sorted_pairs = aggregator.get_sorted_pairs(by=args.sort_by)
            
            # Filter output if specific tokens requested
            # Show pair if at least one of its tokens is in the filter list
            if token_filter:
                filtered_sorted_pairs = []
                for pair_name, data in sorted_pairs:
                    tokens = pair_name.split('-')
                    if any(t in token_filter for t in tokens):
                        filtered_sorted_pairs.append((pair_name, data))
                sorted_pairs = filtered_sorted_pairs
            
            if sorted_pairs:
                table_data = []
                for pair_name, data in sorted_pairs:
                    table_data.append([
                        pair_name,
                        format_usd(data['volume_usd']),
                        f"{data['tx_count']:,}",
                        format_usd(data['volume_usd'] / data['tx_count']) if data['tx_count'] > 0 else '$0.00'
                    ])
                
                headers = ['Pair', 'Total Volume', 'Tx Count', 'Avg per Tx']
                print(tabulate(table_data, headers=headers, tablefmt='grid'))
            else:
                print("No pairs found")
            
            # Token volume summary
            print(f"\n\nTOKEN VOLUME SUMMARY (sorted by {args.sort_by})")
            print("-" * 80)
            
            sorted_tokens = aggregator.get_sorted_tokens(by=args.sort_by)
            
            # Filter output if specific tokens requested
            if token_filter:
                sorted_tokens = [t for t in sorted_tokens if t[0] in token_filter]
            
            if sorted_tokens:
                token_table_data = []
                for token_symbol, stats in sorted_tokens:
                    token_table_data.append([
                        token_symbol,
                        format_usd(stats['volume_usd']),
                        f"{stats['tx_count']:,}",
                        f"{len(stats['pairs'])}",
                        ', '.join(stats['pairs'][:3]) + ('...' if len(stats['pairs']) > 3 else '')
                    ])
                
                token_headers = ['Token', 'Total Volume', 'Tx Count', 'Pairs', 'Top Pairs']
                print(tabulate(token_table_data, headers=token_headers, tablefmt='grid'))
            else:
                print("No tokens found")
            
    except KeyboardInterrupt:
        print("\n\nInterrupted by user", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
