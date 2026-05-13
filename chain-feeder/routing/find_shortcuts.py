#!/usr/bin/env python3
"""
Stable-Pair Shortcut Finder CLI

Find lucrative opportunities for direct stable-pair pools that can undercut
multi-hop routes through volatile intermediaries.

Usage:
    python find_shortcuts.py --days 30
    python find_shortcuts.py --days 30 --families USD EUR
    python find_shortcuts.py --days 30 --cross-family
    python find_shortcuts.py --days 30 --format json
    python find_shortcuts.py --days 30 --tvl-targets 100000 500000 1000000
    python find_shortcuts.py --days 30 --min-volume 5000
"""

import argparse
import json
import sys
from datetime import datetime, timedelta

from shortcut_finder import ShortcutFinder, DEFAULT_TVL_TARGETS, DEFAULT_MIN_VOLUME


def parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def main():
    parser = argparse.ArgumentParser(
        description='Find stable-pair shortcut opportunities on Uniswap',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan last 30 days for all shortcut opportunities
  python find_shortcuts.py --days 30

  # Focus on specific families
  python find_shortcuts.py --days 30 --families USD EUR ETH

  # Include cross-family analysis (e.g. USD×EUR for USDC/EURC shortcuts)
  python find_shortcuts.py --days 30 --cross-family

  # Custom TVL targets for revenue projections
  python find_shortcuts.py --days 30 --tvl-targets 100000 500000 1000000

  # Lower minimum volume threshold to catch smaller opportunities
  python find_shortcuts.py --days 30 --min-volume 1000

  # JSON output for programmatic use
  python find_shortcuts.py --days 30 --format json

  # Custom date range
  python find_shortcuts.py --start-date 2026-01-01 --end-date 2026-03-01
        """
    )

    # Date range
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument('--days', type=float, help='Lookback period in days')
    date_group.add_argument('--start-date', type=parse_date, help='Start date (YYYY-MM-DD)')

    parser.add_argument('--end-date', type=parse_date, help='End date (YYYY-MM-DD, defaults to now)')

    # Family configuration
    parser.add_argument(
        '--families', nargs='+',
        help='Specific families to analyze (e.g., USD EUR ETH). Default: all correlated families.'
    )
    parser.add_argument(
        '--cross-family', action='store_true',
        help='Also analyze cross-family pairs (e.g., USDC/EURC from USD×EUR)'
    )

    # Thresholds
    parser.add_argument(
        '--min-volume', type=float, default=DEFAULT_MIN_VOLUME,
        help=f'Minimum divertable volume (USD) to include an opportunity (default: ${DEFAULT_MIN_VOLUME:,.0f})'
    )
    parser.add_argument(
        '--tvl-targets', nargs='+', type=float, default=DEFAULT_TVL_TARGETS,
        help='TVL targets for APR projections (default: 100000 500000 1000000)'
    )

    # Output
    parser.add_argument(
        '--format', choices=['table', 'json'], default='table',
        help='Output format (default: table)'
    )
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    # Determine date range
    if args.days:
        end_date = args.end_date or datetime.now()
        start_date = end_date - timedelta(days=args.days)
    else:
        start_date = args.start_date
        end_date = args.end_date or datetime.now()

    if start_date >= end_date:
        print("Error: start date must be before end date", file=sys.stderr)
        sys.exit(1)

    period_days = (end_date - start_date).total_seconds() / 86400

    # Build finder
    finder = ShortcutFinder(
        families=args.families,
        cross_family=args.cross_family,
        min_volume=args.min_volume,
        tvl_targets=args.tvl_targets,
        verbose=args.verbose,
    )

    # Run analysis
    opportunities = finder.find(start_date, end_date)

    # Output
    if args.format == 'json':
        result = {
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat(),
                'days': period_days,
            },
            'config': {
                'families': args.families,
                'cross_family': args.cross_family,
                'min_volume': args.min_volume,
                'tvl_targets': args.tvl_targets,
            },
            'opportunities': finder.to_json(opportunities, period_days),
        }
        print(json.dumps(result, indent=2))
    else:
        report = finder.format_results(opportunities, period_days)
        print(report)

    if not opportunities:
        sys.exit(0)

    # Summary
    if args.format == 'table':
        total_divertable = sum(o.total_divertable_volume for o in opportunities)
        total_txns = sum(o.total_divertable_txns for o in opportunities)
        print(f"\n{'=' * 90}")
        print(f"Summary: {len(opportunities)} opportunities, ${total_divertable:,.0f} total divertable volume, {total_txns} transactions")
        print(f"{'=' * 90}")


if __name__ == '__main__':
    main()
