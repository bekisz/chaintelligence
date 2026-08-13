"""Enable compact swap-size buckets for selected route IDs.

Examples:
  python configure_route_distribution.py --route-id 123 --route-id 456
  python configure_route_distribution.py --route-id 123 --bucket-count 64
  python configure_route_distribution.py --route-id 123 --disable
"""

import argparse
import os
import sys

import psycopg2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--route-id', action='append', type=int, required=True)
    parser.add_argument('--bucket-count', type=int, default=80)
    parser.add_argument('--min-usd', type=float, default=10.0)
    parser.add_argument('--max-usd', type=float, default=100_000_000.0)
    parser.add_argument('--disable', action='store_true')
    args = parser.parse_args()

    if not 8 <= args.bucket_count <= 256:
        parser.error('--bucket-count must be between 8 and 256')
    if args.min_usd <= 0 or args.max_usd <= args.min_usd:
        parser.error('require 0 < --min-usd < --max-usd')

    dsn = os.environ.get('DATA_WAREHOUSE_DB')
    if not dsn:
        feeder_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        sys.path.insert(0, os.path.join(feeder_root, 'dags'))
        sys.path.insert(0, feeder_root)
        from common.utils.config import DATA_WAREHOUSE_DB
        dsn = DATA_WAREHOUSE_DB

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for route_id in sorted(set(args.route_id)):
            cur.execute(
                """
                INSERT INTO route_distribution_config
                    (route_id, enabled, bucket_count, min_amount_usd, max_amount_usd, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (route_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    bucket_count = EXCLUDED.bucket_count,
                    min_amount_usd = EXCLUDED.min_amount_usd,
                    max_amount_usd = EXCLUDED.max_amount_usd,
                    updated_at = NOW()
                """,
                (route_id, not args.disable, args.bucket_count, args.min_usd, args.max_usd),
            )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
