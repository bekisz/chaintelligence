"""O&D goal-state self-healing backfill DAG.

Trigger manually (``airflow dags trigger ods_goal_state_backfill``) or let the
schedule run it to converge the warehouse toward the goal state declared in
`config/ods-goal-state.yaml`. It keeps working until every requirement is met:

Each run:
  1. Evaluates coverage and lists what is still missing (``gaps``).
  2. Recomputes ``route_daily_stats`` / ``route_daily_stats_bucket`` from the
     swaps already present (cheap, no network call).
  3. If gaps remain, triggers the per-chain swap ETL DAGs (``graph_*_swaps``)
     with a ``backfill_days`` conf for the networks that have raw-``swaps``
     gaps, plus the rollup DAGs (``route_daily_stats_rollup``,
     ``global_liquidity_pool_daily_stats_rollup``).

The swap ETL DAGs ingest asynchronously, so the DAG is scheduled (default every
30 min) to re-check and keep converging. Once every requirement is met,
``compute_backfill_plan`` returns no triggers and the run becomes a cheap
recompute+report no-op — it never re-queries The Graph for data already present.

Only networks with an actual raw-``swaps`` gap are asked to re-fetch. Param
``backfill_days_cap`` caps how far back each network backfills per trigger.
"""
import logging
from datetime import timedelta

from airflow import DAG
from airflow.sdk import task, Param
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
import pendulum
import psycopg2

from common.utils.config import DATA_WAREHOUSE_DB
from include.od_retention import (
    load_goal_state,
    run_checks,
    backfill_missing_daily_stats,
)
from include.od_catalog import compile_catalog
from include.reconcile import load_coverage_state, plan_requirement

DEFAULT_CAP_DAYS = 90
DEFAULT_SCHEDULE = '*/30 * * * *'  # keep converging until requirements are met

# (network display name, conf key used by the target DAG, dag_id)
SWAP_DAGS = [
    ('Ethereum', 'Ethereum', 'graph_ethereum_uniswap_v2_swaps'),
    ('Ethereum', 'Ethereum', 'graph_ethereum_uniswap_v3_swaps'),
    ('Ethereum', 'Ethereum', 'graph_ethereum_uniswap_v4_swaps'),
    ('Arbitrum', 'Arbitrum', 'graph_arbitrum_uniswap_v3_swaps'),
    ('Arbitrum', 'Arbitrum', 'graph_arbitrum_uniswap_v4_swaps'),
    ('Base', 'Base', 'graph_base_uniswap_v3_swaps'),
    ('Base', 'Base', 'graph_base_uniswap_v4_swaps'),
    ('Base', 'Base_Aerodrome', 'graph_base_aerodrome_v3_swaps'),
    ('BNB', 'BNB', 'graph_bnb_uniswap_v3_swaps'),
    ('BNB', 'BNB', 'graph_bnb_uniswap_v4_swaps'),
    ('BNB', 'BNB_PancakeSwap_V3', 'graph_bnb_pancakeswap_v3_swaps'),
    ('BNB', 'BNB_PancakeSwap_V4', 'graph_bnb_pancakeswap_v4_swaps'),
]

ROLLUP_DAGS = ['route_daily_stats_rollup', 'global_liquidity_pool_daily_stats_rollup']

CHAIN_TO_NETWORK = {'ethereum': 'Ethereum', 'arbitrum': 'Arbitrum',
                    'base': 'Base', 'bnb': 'BNB'}


def connect():
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    conn.autocommit = False
    return conn


def _networks_for_chain(chain) -> list:
    """Resolve a gap's ``chain`` (``*`` or a list) to network display names."""
    if chain == '*':
        return list(CHAIN_TO_NETWORK.values())
    out = []
    for c in chain:
        net = CHAIN_TO_NETWORK.get(str(c).lower())
        if net:
            out.append(net)
    return out


@task
def compute_backfill_plan(**context):
    """Build the reconciliation plan and translate it into ETL dispatches.

    Uses the control-plane coverage ledger (od_catalog + reconcile) rather than
    the legacy gap-scan: raw-present-but-unclassified is CLASSIFY (never FETCH),
    so a classification lag can no longer blind-trigger Graph re-ingestion.
    """
    params = context.get('params', {})
    cap = int(params.get('backfill_days_cap', DEFAULT_CAP_DAYS))

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()

    conn = connect()
    try:
        state = load_coverage_state(conn)
        cat = compile_catalog()
        plans = []
        for cs in cat['sets']:
            plans.extend(plan_requirement(cs, state, today))
    finally:
        conn.close()

    # Dispatch plan actions to the ETL DAGs.
    #   FETCH       -> per-network swap ingestion DAGs (only where raw is missing)
    #   CLASSIFY    -> (handled by the always-on classifier queue; surfaced below)
    #   MATERIALIZE -> rollup DAGs
    fetch_nets = {}
    for r in plans:
        if r['action'] == 'FETCH':
            for net in _networks_for_chain(r['chain']):
                day = datetime.strptime(r['day'], '%Y-%m-%d').date()
                if net not in fetch_nets or day < fetch_nets[net]:
                    fetch_nets[net] = day

    specs = []
    if fetch_nets:
        for net, conf_key, dag_id in SWAP_DAGS:
            if net in fetch_nets:
                days = min((today - fetch_nets[net]).days + 1, cap)
                specs.append({'trigger_dag_id': dag_id,
                              'conf': {'backfill_days': {conf_key: days}}})
    need_materialize = any(r['action'] == 'MATERIALIZE' for r in plans)
    need_classify = any(r['action'] == 'CLASSIFY' for r in plans)
    if need_materialize:
        for dag_id in ROLLUP_DAGS:
            specs.append({'trigger_dag_id': dag_id, 'conf': {}})

    from reconcile import summarize_rows
    logging.info("control-plane plan: %s", summarize_rows(plans))
    logging.info("dispatch: %d ETL triggers, classify=%s materialize=%s",
                 len(specs), need_classify, need_materialize)
    for s in specs:
        logging.info("  -> %s conf=%s", s['trigger_dag_id'], s['conf'])

    context['task_instance'].xcom_push(key='plans', value=plans)
    return specs


@task
def recompute_and_report(**context):
    """Recompute route daily stats/buckets from present swaps and report coverage."""
    goal = load_goal_state()
    conn = connect()
    try:
        report = run_checks(conn, goal)
        n = backfill_missing_daily_stats(conn, report)
    finally:
        conn.close()
    not_ok = [r for r in report if r['status'] != 'ok']
    logging.info("recomputed %d missing route daily-stats days", n)
    logging.info("FINAL coverage: %d/%d checks not ok", len(not_ok), len(report))
    return {'not_ok': len(not_ok), 'checks': len(report)}


with DAG(
    'ods_goal_state_backfill',
    max_active_runs=1,
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': True,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    },
    description='Converge the warehouse toward the O&D goal state (self-healing ETL backfill)',
    schedule=DEFAULT_SCHEDULE,
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['ods', 'goal-state', 'backfill'],
    params={
        'backfill_days_cap': Param(
            default=DEFAULT_CAP_DAYS, type='integer',
            description='Max number of days each network is asked to backfill per trigger.'),
    },
) as dag:

    plan = compute_backfill_plan()

    trigger_etl = TriggerDagRunOperator.partial(
        task_id='trigger_etl',
        wait_for_completion=False,
        reset_dag_run=True,
        trigger_rule='all_done',
    ).expand_kwargs(plan)

    recompute = recompute_and_report()

    plan >> trigger_etl
    plan >> recompute
