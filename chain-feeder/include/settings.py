"""Shared (layer-agnostic) configuration helpers.

Single source of truth for the data-warehouse connection-string derivation.
Both the API layer (`api/routing/config.py`) and the ETL layer
(`chain-feeder/dags/common/utils/config.py`) read `DATA_WAREHOUSE_DB` here so
the env-variable handling never drifts between layers.

This module lives under `chain-feeder/include/` because that namespace is
mounted and importable from BOTH the API server and Airflow, and the name
`include` does not collide with the `config` module name already taken by
`api/routing/config.py`.
"""

import os

DISTRIBUTION_CONFIG_DEFAULTS = {
    'bucket_count': 80,
    'min_amount_usd': 10.0,
    'max_amount_usd': 100000000.0,
}


def data_warehouse_dsn(default: str) -> str:
    """Return the data-warehouse connection string from env (authoritative).

    Reads ``DATA_WAREHOUSE_DB``; ``default`` is only a fallback so each layer
    can supply a context-appropriate value (localhost vs in-container host).
    """
    return os.getenv('DATA_WAREHOUSE_DB', default)


def load_distribution_config() -> dict:
    """Load the global swap-size distribution bucket settings.

    Reads ``config/swap-distribution.yaml`` from the repo either via the repo
    layout (local dev / API layer) or the Docker-mount layout (``/opt/airflow``
    for Airflow, ``/app`` for the API container). Falls back to defaults when
    the file is absent or malformed so every layer agrees on the same values.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, '..', '..', 'config', 'swap-distribution.yaml'),
        os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'),
                     'config', 'swap-distribution.yaml'),
        '/app/config/swap-distribution.yaml',
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            import yaml
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
            result = {
                'bucket_count': int(cfg.get('bucket_count', DISTRIBUTION_CONFIG_DEFAULTS['bucket_count'])),
                'min_amount_usd': float(cfg.get('min_amount_usd', DISTRIBUTION_CONFIG_DEFAULTS['min_amount_usd'])),
                'max_amount_usd': float(cfg.get('max_amount_usd', DISTRIBUTION_CONFIG_DEFAULTS['max_amount_usd'])),
            }
            if not (8 <= result['bucket_count'] <= 256):
                result['bucket_count'] = DISTRIBUTION_CONFIG_DEFAULTS['bucket_count']
            if not (result['min_amount_usd'] > 0 and result['max_amount_usd'] > result['min_amount_usd']):
                return dict(DISTRIBUTION_CONFIG_DEFAULTS)
            return result
        except Exception:
            continue
    return dict(DISTRIBUTION_CONFIG_DEFAULTS)