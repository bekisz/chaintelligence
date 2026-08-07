"""Explore real swap sizes in the warehouse to find their probability distribution.

Usage: cd api/routing && python ../scratch/swap_size_explore.py
"""
import os
import sys
from datetime import datetime, timedelta

import psycopg2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'api', 'routing'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder', 'include'))
from config import DATA_WAREHOUSE_DB

def main():
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()

    cur.execute("""SELECT COUNT(*), MIN(ts), MAX(ts),
                          COUNT(amount_usd), MIN(amount_usd), MAX(amount_usd),
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY amount_usd),
                          percentile_cont(0.9) WITHIN GROUP (ORDER BY amount_usd)
                   FROM swaps WHERE amount_usd > 0""")
    row = cur.fetchone()
    print(f"count={row[0]:,}  range={row[1]}..{row[2]}")
    print(f"amount_usd>0: n={row[3]:,} min={row[4]:,.0f} max={row[5]:,.0f} med={row[6]:,.0f} p90={row[7]:,.0f}")

    cur.execute("""SELECT ch.name, COUNT(*)
                   FROM swaps s LEFT JOIN chain ch ON ch.id = s.pool_id
                   WHERE s.amount_usd > 0 GROUP BY ch.name ORDER BY 2 DESC""")
    for net, n in cur.fetchall():
        print(f"  {net}: {n:,}")

    conn.close()

if __name__ == '__main__':
    main()
