"""Unit tests for Databricks MLflow artifact path mapping (no network)."""

from __future__ import annotations

from what_if_app.databricks_io import workspace_internal_path_for_databricks_artifact_uri


def test_workspace_path_logged_model() -> None:
    uri = (
        "dbfs:/databricks/mlflow-tracking/213756dd85174d71a05d72a8d6477b9b/"
        "logged_models/m-3006c62dcb804eeda407eb036749237b/artifacts"
    )
    ws = workspace_internal_path_for_databricks_artifact_uri(uri)
    assert ws == (
        "/WorkspaceInternal/Mlflow/Artifacts/213756dd85174d71a05d72a8d6477b9b/"
        "LoggedModels/m-3006c62dcb804eeda407eb036749237b/artifacts"
    )


def test_workspace_path_run_artifact() -> None:
    uri = (
        "dbfs:/databricks/mlflow-tracking/213756dd85174d71a05d72a8d6477b9b/"
        "9d740e9e5f544d9490100cef238bf074/artifacts/plots"
    )
    ws = workspace_internal_path_for_databricks_artifact_uri(uri)
    assert ws == (
        "/WorkspaceInternal/Mlflow/Artifacts/213756dd85174d71a05d72a8d6477b9b/"
        "Runs/9d740e9e5f544d9490100cef238bf074/artifacts/plots"
    )
