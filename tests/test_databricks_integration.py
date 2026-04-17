"""Live integration tests: Databricks SQL warehouse + optional MLflow model load."""

from __future__ import annotations

import os

import numpy as np
import pytest

pytestmark = pytest.mark.integration


def test_resolve_warehouse_id_or_discover(databricks_env: None) -> None:
    from what_if_app.databricks_io import _resolve_warehouse_id

    wh = _resolve_warehouse_id()
    assert wh, "No SQL warehouse id — set DATABRICKS_WAREHOUSE_ID or fix DATABRICKS_HTTP_PATH"


def test_ping_databricks_sql(databricks_env: None) -> None:
    from what_if_app.databricks_io import ping_databricks_sql

    ping_databricks_sql()


def test_query_predictions_table(databricks_env: None) -> None:
    from what_if_app.config import settings
    from what_if_app.databricks_io import _execute_sql, _sanitize_table_name

    t = _sanitize_table_name(settings.predictions_table)
    df = _execute_sql(f"SELECT COUNT(*) AS row_count FROM {t}")
    assert len(df) == 1
    assert "row_count" in df.columns


@pytest.mark.slow
@pytest.mark.timeout(1800)
def test_load_mlflow_model(databricks_env: None) -> None:
    """
    End-to-end MLflow load (Databricks OAuth + Files API + LightGBM).

    Skips unless ``RUN_LIVE_DATABRICKS_MLFLOW=1`` — requires stable access to the workspace Files API
    (some networks break TLS to blob or throttle API downloads for 10+ minutes).
    """
    if os.environ.get("RUN_LIVE_DATABRICKS_MLFLOW", "").strip().lower() not in ("1", "true", "yes"):
        pytest.skip("Set RUN_LIVE_DATABRICKS_MLFLOW=1 to run live MLflow load (slow, needs reliable network).")

    from what_if_app.ml_core import load_model_from_mlflow

    booster, explainer = load_model_from_mlflow()
    nfeat = booster.num_feature()
    assert nfeat > 0
    x = np.zeros((1, nfeat))
    pred = booster.predict(x)
    assert len(pred) == 1
    assert explainer is not None
