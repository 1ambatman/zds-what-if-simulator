"""Shared fixtures for integration tests (Databricks AI dev kit / CLI auth)."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def databricks_env() -> None:
    """Match app startup: profile + host in os.environ for MLflow and SDK."""
    from what_if_app.databricks_io import apply_databricks_profile_to_environ

    apply_databricks_profile_to_environ()
