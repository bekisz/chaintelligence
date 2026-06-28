import psycopg2
import os
import argparse
from datetime import datetime, timedelta

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

# Default effective fee rate for dynamic-fee pools (Uniswap V4 hooks).
# Typical range for major pairs: 0.015%–0.025%.  We use 0.02% (2 bps).
DEFAULT_DYNAMIC_FEE_RATE = 0.0002

def calculate_apr(pool_id: int, days: int = 30, dynamic_fee_estimate: float = DEFAULT_DYNAMIC_FEE_RATE) -> None:
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()

    # 1️⃣ Fetch pool metadata (symbols, fee tier, protocol)
    cur.execute(
        """
        SELECT coin0_symbol, coin1_symbol, fee_tier, protocol
        FROM liquidity_pool
        WHERE id = %s
        """,
        (pool_id,)
    )
    meta = cur.fetchone()
    if not meta:
        print(f"Pool {pool_id} not found in liquidity_pool table.")
        cur.close()
        conn.close()
        return
    coin0_sym, coin1_sym, fee_tier_db, protocol = meta

    # Detect dynamic-fee pools
    is_dynamic = (fee_tier_db is None
                  or (isinstance(fee_tier_db, str) and fee_tier_db.strip().lower() == "dynamic"))

    # Normalize fee tier to a decimal fraction for static pools
    fee_rate = 0.0
    if is_dynamic:
        fee_rate = dynamic_fee_estimate
    elif fee_tier_db:
        cleaned = fee_tier_db.replace('%', '').replace('|v3', '').replace('|v4', '')
        try:
            val = float(cleaned)
            if val > 5:
                # Stored as basis-points (e.g. 500 → 0.05%)
                fee_rate = val / 1_000_000.0
            else:
                # Stored as percentage (e.g. 0.05 → 0.05%)
                fee_rate = val / 100.0
        except ValueError:
            fee_rate = 0.0

    start_date = datetime.now() - timedelta(days=days)
    end_date = datetime.now()

    # 2️⃣ Aggregate historic volume and TVL for the pool
    cur.execute(
        """
        SELECT SUM(h.volume_usd), AVG(h.tvl_usd)
        FROM liquidity_pool_history h
        WHERE h.pool_id = %s
          AND h.date >= %s::date AND h.date <= %s::date
        """,
        (pool_id, start_date, end_date)
    )
    row = cur.fetchone()
    total_vol = float(row[0]) if row and row[0] else 0
    avg_tvl = float(row[1]) if row and row[1] else 0

    # 3️⃣ Fallback to swap data when historic volume is missing
    if total_vol == 0 and avg_tvl > 0:
        swap_query = """
            SELECT token0_symbol, token1_symbol,
                   SUM(amount_usd), SUM(ABS(amount0)), SUM(ABS(amount1))
            FROM (
                SELECT amount_usd, amount0, amount1, timestamp,
                       token0_symbol, token1_symbol, fee_tier, 'Uniswap V3' as protocol
                FROM uniswap_v3_swaps
                UNION ALL
                SELECT amount_usd, amount0, amount1, timestamp,
                       token0_symbol, token1_symbol, fee_tier, 'Uniswap V4' as protocol
                FROM uniswap_v4_swaps
            ) as all_swaps
            WHERE timestamp >= %s AND timestamp <= %s
              AND protocol = %s
              AND (
                    (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s) OR
                    (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s)
                  )
              AND fee_tier = %s
            GROUP BY token0_symbol, token1_symbol
        """
        cur.execute(
            swap_query,
            (
                start_date,
                end_date,
                protocol,
                coin0_sym.upper(),
                coin1_sym.upper(),
                coin1_sym.upper(),
                coin0_sym.upper(),
                fee_tier_db if fee_tier_db else 'Dynamic',
            ),
        )
        fallback_vol = 0.0
        for sw in cur.fetchall():
            usd_sum = float(sw[2]) if sw[2] else 0
            amt0 = float(sw[3]) if sw[3] else 0
            amt1 = float(sw[4]) if sw[4] else 0
            if usd_sum > 0:
                fallback_vol += usd_sum
            else:
                p0 = 1.0 if any(c in sw[0].upper() for c in ["USD", "EUR"]) else 0
                p1 = 1.0 if any(c in sw[1].upper() for c in ["USD", "EUR"]) else 0
                fallback_vol += (amt0 * p0 + amt1 * p1) / 2.0
        total_vol = fallback_vol

    # 4️⃣ Compute APR (fees earned = volume × fee_rate)
    if avg_tvl > 0:
        fees_earned = total_vol * fee_rate
        days_count = (end_date - start_date).days or 1
        apr = (fees_earned / avg_tvl) * (365.0 / days_count)

        fee_label = f"Dynamic (~{fee_rate*100:.3f}%)" if is_dynamic else fee_tier_db
        print(f"Pool {pool_id} ({coin0_sym}/{coin1_sym}) – Fee tier {fee_label} – Protocol {protocol}")
        print(f"  Volume (USD): {total_vol:,.2f}")
        print(f"  Avg TVL (USD): {avg_tvl:,.2f}")
        print(f"  Fee rate: {fee_rate*100:.4f}%")
        print(f"  Fees earned (USD): {fees_earned:,.2f}")
        print(f"  APR: {apr:.4%} ({apr*100:.2f}%) over {days_count} days")
    else:
        print("No TVL data – cannot compute APR.")

    cur.close()
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate APR for a dynamic liquidity pool")
    parser.add_argument("pool_id", type=int, help="Liquidity pool ID from the DB")
    parser.add_argument("--days", type=int, default=30, help="Number of days to look back (default 30)")
    parser.add_argument("--dynamic-fee-estimate", type=float, default=DEFAULT_DYNAMIC_FEE_RATE,
                        help=f"Effective fee rate for dynamic-fee pools (default {DEFAULT_DYNAMIC_FEE_RATE})")
    args = parser.parse_args()
    calculate_apr(args.pool_id, args.days, args.dynamic_fee_estimate)
