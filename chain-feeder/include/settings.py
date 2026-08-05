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


def data_warehouse_dsn(default: str) -> str:
    """Return the data-warehouse connection string from env (authoritative).

    Reads ``DATA_WAREHOUSE_DB``; ``default`` is only a fallback so each layer
    can supply a context-appropriate value (localhost vs in-container host).
    """
    return os.getenv('DATA_WAREHOUSE_DB', default)