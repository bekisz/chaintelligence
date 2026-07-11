from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Asset
import pendulum
from datetime import timedelta
import logging
import json
import re
import os
import math

# Import discovery client
from include.graph_discovery_client import fetch_graph_positions
# Reuse existing helpers if possible, or define them here for independence
from include.uniswap_v3_range_fetcher import fetch_position_range_data

# Assets
asset_coins = Asset("postgres://postgres:5432/chaintelligence/public/coin")
asset_pools = Asset("postgres://postgres:5432/chaintelligence/public/liquidity_pool")
asset_positions = Asset("postgres://postgres:5432/chaintelligence/public/liquidity_pool_position")
asset_snapshots = Asset("postgres://postgres:5432/chaintelligence/public/liquidity_pool_position_snapshot")

# Configuration
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Add normalization and hardness logic (same as Zapper for consistency)
def normalize_symbol(sym):
    if not sym: return "UNKNOWN"
    s = sym.upper().strip()
    # Basic mapping
    mapping = {'WRAPPED ETHER': 'WETH', 'WRAPPED BITCOIN': 'WBTC'}
    s = mapping.get(s, s)
    return s[:8]

def get_standard_pool_info(label, assets, hardness_map):
    c0, c1 = None, None
    pool_name = None
    reverted = False
    
    if assets and len(assets) >= 2:
        a0 = assets[0]
        a1 = assets[1]
        sym0 = normalize_symbol(a0.get('symbol'))
        sym1 = normalize_symbol(a1.get('symbol'))
        adr0 = a0.get('address', '').lower()
        adr1 = a1.get('address', '').lower()
        
        h0 = hardness_map.get(sym0, 0)
        h1 = hardness_map.get(sym1, 0)
        
        is_swapped = False
        if h0 > h1: is_swapped = True
        elif h0 == h1 and sym0 > sym1: is_swapped = True
        
        if is_swapped:
            c0, c1 = sym1, sym0
            addr_c0, addr_c1 = adr1, adr0
        else:
            c0, c1 = sym0, sym1
            addr_c0, addr_c1 = adr0, adr1
            
        pool_name = f"{c0} - {c1}"
        if addr_c0 and addr_c1 and addr_c0 > addr_c1:
            reverted = True
    return pool_name, c0, c1, reverted

@task
def discover_graph_positions():
    """Finds positions across all subgraphs."""
    wallets = os.getenv("TARGET_ADDRESS", "")
    if not wallets:
        logging.warning("TARGET_ADDRESS environment variable is not set. Discovery will return no results.")
        return []
    logging.info(f"Starting Graph discovery for addresses: {wallets}")
    positions = fetch_graph_positions(wallets)
    logging.info(f"Discovered {len(positions)} total positions via The Graph.")
    return positions

from include.graph_ingestion_helpers import (
    ingest_coins_data, ingest_pools_data, ingest_positions_data, ingest_snapshots_data
)

@task(outlets=[asset_coins])
def ingest_coins(positions: list):
    """Ingests coins discovered from The Graph."""
    if not positions: return
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    with pg_hook.get_conn() as conn:
        ingest_coins_data(conn, positions)
    logging.info(f"Ensured coins exist in database.")

@task(outlets=[asset_pools])
def ingest_pools(positions: list):
    """Ingests pools discovered from The Graph."""
    if not positions: return
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    with pg_hook.get_conn() as conn:
        ingest_pools_data(conn, positions)
    logging.info(f"Ensured pools exist in database.")

@task(outlets=[asset_positions])
def ingest_positions(positions: list):
    """Ingests specific position instances."""
    if not positions: return
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    with pg_hook.get_conn() as conn:
        ingest_positions_data(conn, positions)
    logging.info(f"Ensured positions exist in database.")

@task(outlets=[asset_snapshots])
def ingest_snapshots(positions: list):
    """Ingests time-series snapshots."""
    if not positions: return
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    with pg_hook.get_conn() as conn:
        ingest_snapshots_data(conn, positions)
    logging.info(f"Ensured snapshots exist in database.")

@task
def update_virtual_lp_family():
    """Populates the virtual 'current-lp-tokens' family with all tokens currently in LP pools."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    with pg_hook.get_conn() as conn:
        cur = conn.cursor()
        
        # Insert or update coins from liquidity pools into coin_family "current-lp-tokens"
        cur.execute("""
            INSERT INTO coin_family (name, coin_id)
            SELECT DISTINCT 'current-lp-tokens', c.coin_id
            FROM coin c
            JOIN liquidity_pool lp ON (lp.coin0_id = c.coin_id OR lp.coin1_id = c.coin_id)
            ON CONFLICT (name, coin_id) DO NOTHING;
        """)
        
        # Remove coins from "current-lp-tokens" that are NO LONGER in any LP pool
        cur.execute("""
            DELETE FROM coin_family
            WHERE name = 'current-lp-tokens'
            AND coin_id NOT IN (
                SELECT coin0_id FROM liquidity_pool
                UNION
                SELECT coin1_id FROM liquidity_pool
            );
        """)
        conn.commit()
        cur.close()
    logging.info("Virtual 'current-lp-tokens' family updated successfully.")

from dags.common.tasks import fetch_missing_ranges

with DAG(
    'graph_lp_ingestion',
    default_args=default_args,
    description='Native LP position discovery and ingestion via The Graph',
    schedule='*/15 * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    max_active_runs=1,
    tags=['defi', 'graph', 'lp'],
) as dag:
    
    raw_positions = discover_graph_positions()
    
    t_coins = ingest_coins(raw_positions)
    t_pools = ingest_pools(raw_positions)
    t_virtual_family = update_virtual_lp_family()
    t_positions = ingest_positions(raw_positions)
    t_snap = ingest_snapshots(raw_positions)
    t_ranges = fetch_missing_ranges()
    
    raw_positions >> t_coins >> t_pools >> t_virtual_family >> t_positions >> [t_snap, t_ranges]
