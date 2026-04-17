"""Load customer rows from Databricks SQL (Unity Catalog tables)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd
from databricks import sql
from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from databricks.sdk.service.sql import StatementResponse, StatementState

from what_if_app.config import settings

_workspace_client: WorkspaceClient | None = None

_logger = logging.getLogger(__name__)

# Align with mlflow.store.artifact.databricks_*_artifact_repo Workspace Files paths.
_DBX_LOGGED_MODEL_URI = re.compile(
    r"databricks/mlflow-tracking/(?P<experiment_id>[^/]+)/logged_models/(?P<model_id>[^/]+)"
    r"(?P<relative_path>/.*)?$"
)
_DBX_RUN_ARTIFACT_URI = re.compile(
    r"databricks/mlflow-tracking/(?P<experiment_id>[^/]+)/(?P<run_id>(?!tr-|logged_models)[^/]+)"
    r"(?P<relative_path>/.*)?$"
)


def merged_workspace_host_token() -> tuple[str, str]:
    """Host and token from .env, with optional ~/.databrickscfg profile fill-in (AI dev kit / CLI)."""
    host = (settings.databricks_host or "").strip()
    token = (settings.databricks_token or "").strip() if settings.uses_databricks_pat() else ""
    profile = (settings.databricks_config_profile or "").strip()
    if profile and (not host or not token):
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient(profile=profile)
        cfg = w.config
        if not host:
            h = (cfg.host or "").strip()
            if h and not h.startswith("http"):
                h = f"https://{h}"
            host = h
        if not token:
            token = (cfg.token or "").strip()
    return host, token


def apply_databricks_profile_to_environ() -> None:
    """Expose host/token/profile to MLflow and other tools that read DATABRICKS_* from the process environment."""
    import os

    if not settings.uses_databricks_pat():
        # Stale PAT in .env or shell breaks SDK OAuth; MLflow reads os.environ first.
        os.environ.pop("DATABRICKS_TOKEN", None)

    host, token = merged_workspace_host_token()
    prof = (settings.databricks_config_profile or "").strip()
    if host and not os.environ.get("DATABRICKS_HOST"):
        os.environ["DATABRICKS_HOST"] = host
    if settings.uses_databricks_pat() and token and not os.environ.get("DATABRICKS_TOKEN"):
        os.environ["DATABRICKS_TOKEN"] = token
    # OAuth / CLI: MLflow + SDK default to DEFAULT profile; pin it when unset.
    if not token:
        p = prof or "DEFAULT"
        if not os.environ.get("DATABRICKS_CONFIG_PROFILE"):
            os.environ["DATABRICKS_CONFIG_PROFILE"] = p
    elif prof and not os.environ.get("DATABRICKS_CONFIG_PROFILE"):
        os.environ["DATABRICKS_CONFIG_PROFILE"] = prof


def resolve_databricks_sql_config() -> tuple[str, str, str]:
    """
    Return (host_url, http_path, access_token) for databricks-sql-connector (PAT path only).

    Uses DATABRICKS_HOST / DATABRICKS_HTTP_PATH / DATABRICKS_TOKEN from the environment when set,
    and fills gaps from DATABRICKS_CONFIG_PROFILE via the Databricks SDK (CLI config file), matching
    the AI dev kit / Cursor MCP Databricks server setup.
    """
    host, token = merged_workspace_host_token()
    http_path = (settings.databricks_http_path or "").strip()
    wh_id = (settings.databricks_warehouse_id or "").strip()

    if wh_id and not http_path:
        http_path = f"/sql/1.0/warehouses/{wh_id}"

    if not host or not http_path or not token:
        raise RuntimeError(
            "Databricks SQL (token mode) is not fully configured. Set DATABRICKS_HOST, "
            "DATABRICKS_HTTP_PATH (or DATABRICKS_WAREHOUSE_ID), and DATABRICKS_TOKEN — or use "
            "OAuth/CLI only (omit DATABRICKS_TOKEN) and set DATABRICKS_WAREHOUSE_ID or HTTP path "
            "(see .env.example)."
        )
    return host, http_path, token


def _looks_like_databricks_warehouse_id(w: str) -> bool:
    """Reject placeholders like ``xxxxxxxx`` in sample .env paths."""
    s = (w or "").strip()
    if len(s) < 16:
        return False
    if not re.fullmatch(r"[0-9a-f]+", s, re.I):
        return False
    if len(set(s.lower())) <= 2 and "x" in s.lower():
        return False
    return True


def _discover_running_warehouse_id() -> str:
    """Pick a RUNNING SQL warehouse via SDK (AI dev kit / ``databricks auth login``)."""
    try:
        wc = _get_workspace_client()
        candidates: list[tuple[str, str]] = []
        for wh in wc.warehouses.list():
            st = getattr(wh, "state", None)
            label = getattr(st, "name", None) or (str(st) if st is not None else "")
            if "RUNNING" not in label.upper():
                continue
            wid = str(wh.id or "").strip()
            if _looks_like_databricks_warehouse_id(wid):
                name = (getattr(wh, "name", None) or "").strip()
                candidates.append((name, wid))
        if not candidates:
            return ""
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
    except Exception:
        _logger.debug("Could not auto-discover SQL warehouse", exc_info=True)
        return ""


def _resolve_warehouse_id() -> str:
    """Warehouse id for SQL Statement Execution API (OAuth / AI dev kit path)."""
    wid = (settings.databricks_warehouse_id or "").strip()
    if _looks_like_databricks_warehouse_id(wid):
        return wid
    hp = (settings.databricks_http_path or "").strip()
    m = re.search(r"/sql/1\.0/warehouses/([a-fA-F0-9]+)", hp)
    parsed = m.group(1) if m else ""
    if _looks_like_databricks_warehouse_id(parsed):
        return parsed
    discovered = _discover_running_warehouse_id()
    if discovered:
        _logger.info("Using auto-discovered SQL warehouse %s (set DATABRICKS_WAREHOUSE_ID to pin).", discovered)
    return discovered


def _get_workspace_client() -> WorkspaceClient:
    global _workspace_client
    if _workspace_client is None:
        import os

        prof = (settings.databricks_config_profile or "").strip() or (
            os.environ.get("DATABRICKS_CONFIG_PROFILE") or ""
        ).strip()
        # Default SDK caps Files API at 3 retries (instance attrs must be set after Config()).
        cfg = Config(profile=prof) if prof else Config()
        # The experimental client tries CSP presigned URLs (Azure blob) first; those often fail with
        # SSLEOF on VPN/proxy and exhaust retries before falling back. Legacy download uses workspace
        # HTTPS only (/api/2.0/fs/files), same host as the control plane — see FilesExt.download in SDK.
        cfg.disable_experimental_files_api_client = True
        cfg.files_ext_parallel_download_max_retries = 15
        cfg.experimental_files_ext_cloud_api_max_retries = 15
        cfg.retry_timeout_seconds = 600
        _workspace_client = WorkspaceClient(config=cfg)
    return _workspace_client


def workspace_internal_path_for_databricks_artifact_uri(artifact_uri: str) -> str | None:
    """
    Map dbfs:/databricks/mlflow-tracking/... to /WorkspaceInternal/Mlflow/Artifacts/... for Files API.

    MLflow 3 logged models use ``.../logged_models/<id>/artifacts`` (Workspace ``LoggedModels``).
    Run-relative paths like ``Runs/<id>/artifacts/model`` often do not exist on disk when the
    model is only stored under LoggedModels — prefer :func:`logged_model_artifact_uri_for_run`.
    """
    m = _DBX_LOGGED_MODEL_URI.search(artifact_uri)
    if m:
        eid = m.group("experiment_id")
        mid = m.group("model_id")
        rel = m.group("relative_path") or ""
        return f"/WorkspaceInternal/Mlflow/Artifacts/{eid}/LoggedModels/{mid}{rel}"
    m = _DBX_RUN_ARTIFACT_URI.search(artifact_uri)
    if not m:
        return None
    eid = m.group("experiment_id")
    rid = m.group("run_id")
    rel = m.group("relative_path") or ""
    return f"/WorkspaceInternal/Mlflow/Artifacts/{eid}/Runs/{rid}{rel}"


def logged_model_artifact_uri_for_run(run_id: str) -> str | None:
    """
    Return the dbfs artifact_location for an MLflow 3 *logged model* tied to this run, if any.

    When present, this is where ``MLmodel`` / ``model.lgb`` actually live for workspace download.
    """
    from mlflow import MlflowClient

    c = MlflowClient()
    run = c.get_run(run_id)
    eid = run.info.experiment_id
    for lm in c.search_logged_models(experiment_ids=[eid]):
        if getattr(lm, "source_run_id", None) == run_id:
            loc = getattr(lm, "artifact_location", None)
            if isinstance(loc, str) and loc.strip():
                return loc.strip()
    return None


def _download_workspace_file(wc: WorkspaceClient, remote_file_path: str, local_file: Path) -> None:
    """Download one file with retries (large ``model.lgb`` can exceed SDK default retry budget)."""
    local_file.parent.mkdir(parents=True, exist_ok=True)
    max_attempts = 6
    last_err: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            dr = wc.files.download(remote_file_path)
            with open(local_file, "wb") as out:
                while True:
                    chunk = dr.contents.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            return
        except Exception as e:
            last_err = e
            wait = min(2.0**attempt, 45.0)
            _logger.warning(
                "Files API download attempt %s/%s failed for %s: %s (retry in %.1fs)",
                attempt + 1,
                max_attempts,
                remote_file_path,
                e,
                wait,
            )
            time.sleep(wait)
    assert last_err is not None
    raise last_err


def download_logged_model_lightgbm_minimal_via_workspace(
    wc: WorkspaceClient, workspace_artifacts_dir: str, dest: Path
) -> None:
    """
    For MLflow 3 logged models, only ``MLmodel`` + ``model.lgb`` are required for LightGBM inference.

    Skips ``metadata/``, conda files, etc., which are unnecessary for ``mlflow.lightgbm.load_model``
    and can trigger flaky SDK retries on small files.
    """
    dest.mkdir(parents=True, exist_ok=True)
    base = workspace_artifacts_dir.rstrip("/")
    for fname in ("MLmodel", "model.lgb"):
        _download_workspace_file(wc, f"{base}/{fname}", dest / fname)


def download_workspace_internal_tree(wc: WorkspaceClient, remote_dir: str, dest: Path) -> None:
    """Recursively download a directory via Databricks Files API (no direct blob access)."""
    dest.mkdir(parents=True, exist_ok=True)
    entries = [e for e in wc.files.list_directory_contents(remote_dir) if e.path]
    dirs = [e for e in entries if e.is_directory]
    files = [e for e in entries if not e.is_directory]
    files.sort(key=lambda e: int(e.file_size or 0))
    for entry in dirs:
        name = entry.name or entry.path.rstrip("/").rsplit("/", 1)[-1]
        download_workspace_internal_tree(wc, entry.path, dest / name)
    for entry in files:
        name = entry.name or entry.path.rstrip("/").rsplit("/", 1)[-1]
        _download_workspace_file(wc, entry.path, dest / name)


def download_run_artifact_dir_via_workspace_files(
    run_id: str,
    artifact_subpath: str,
    tracking_uri: str,
) -> str | None:
    """
    Download the run artifact subtree (e.g. ``model/``) to a new temp directory using only the
    workspace Files API. Returns the temp directory path, or None if this run is not stored under
    the Databricks mlflow-tracking layout.

    Tries **logged model** artifact URIs first (MLflow 3), then ``get_artifact_uri`` for the run
    subpath. Caller must ``shutil.rmtree`` the returned path when done.
    """
    from mlflow.tracking.artifact_utils import get_artifact_uri

    sub = (artifact_subpath or "").strip().strip("/")
    candidates: list[str] = []
    lm_uri = logged_model_artifact_uri_for_run(run_id)
    if lm_uri:
        candidates.append(lm_uri)
    try:
        primary = get_artifact_uri(run_id, sub or None, tracking_uri=tracking_uri)
        if primary not in candidates:
            candidates.append(primary)
    except Exception as e:
        _logger.debug("get_artifact_uri failed: %s", e)

    if not candidates:
        return None

    wc = _get_workspace_client()
    last_err: BaseException | None = None
    for full_uri in candidates:
        ws_path = workspace_internal_path_for_databricks_artifact_uri(full_uri)
        if not ws_path:
            continue
        root = Path(tempfile.mkdtemp(prefix="mlflow-ws-artifacts-"))
        try:
            if "/logged_models/" in full_uri.replace("\\", "/"):
                download_logged_model_lightgbm_minimal_via_workspace(wc, ws_path, root)
            else:
                download_workspace_internal_tree(wc, ws_path, root)
            _logger.info(
                "Downloaded MLflow run artifacts via workspace Files API (path %s -> local %s)",
                ws_path,
                root,
            )
            return str(root)
        except Exception as e:
            last_err = e
            shutil.rmtree(root, ignore_errors=True)
            _logger.warning("Workspace Files API download failed for %s: %s", full_uri, e)

    if last_err:
        _logger.warning("All workspace download candidates failed; last error: %s", last_err)
    return None


def _statement_response_to_dataframe(resp: StatementResponse) -> pd.DataFrame:
    if resp.status is None or resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error if resp.status else None
        msg = getattr(err, "message", None) or str(err or "statement failed")
        raise RuntimeError(msg)
    manifest = resp.manifest
    result = resp.result
    if manifest is None or not manifest.schema or not manifest.schema.columns:
        return pd.DataFrame()
    cols = [c.name for c in manifest.schema.columns if c.name]
    rows = result.data_array if result and result.data_array else []
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def _execute_sql_via_sdk(statement: str, wait_timeout: str = "50s") -> pd.DataFrame:
    wh = _resolve_warehouse_id()
    if not wh:
        raise RuntimeError(
            "OAuth / CLI auth needs a SQL warehouse id. Set DATABRICKS_WAREHOUSE_ID or "
            "DATABRICKS_HTTP_PATH like /sql/1.0/warehouses/<warehouse_id>."
        )
    w = _get_workspace_client()
    resp = w.statement_execution.execute_statement(
        warehouse_id=wh,
        statement=statement,
        wait_timeout=wait_timeout,
    )
    return _statement_response_to_dataframe(resp)


def _connection():
    host, http_path, token = resolve_databricks_sql_config()
    return sql.connect(
        server_hostname=host.replace("https://", "").rstrip("/"),
        http_path=http_path,
        access_token=token,
    )


def _execute_sql(statement: str) -> pd.DataFrame:
    """Run SQL: PAT + Thrift if DATABRICKS_TOKEN is set; else SDK Statement Execution (OAuth / databricks-cli)."""
    _, token = merged_workspace_host_token()
    if token:
        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(statement)
                rows = cur.fetchall()
                cols = [c[0] for c in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=cols) if cols else pd.DataFrame()
    return _execute_sql_via_sdk(statement)


def ping_databricks_sql() -> None:
    """Run a trivial query; raises if connection or credentials fail."""
    _, token = merged_workspace_host_token()
    if token:
        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return
    df = _execute_sql_via_sdk("SELECT 1 AS _x", wait_timeout="30s")
    if df.empty:
        raise RuntimeError("Databricks SQL ping returned no rows.")


def _sanitize_table_name(table: str) -> str:
    """Allow only safe Unity Catalog three-part names."""
    t = table.strip()
    if not re.fullmatch(r"[\w.-]+\.[\w.-]+\.[\w.-]+", t):
        raise ValueError("Table must look like catalog.schema.table (letters, numbers, underscores, dots).")
    return t


def _pairs_union_sql(pairs: list[tuple[str, str]]) -> str:
    parts = []
    for cid, d in pairs:
        esc = cid.replace("'", "''")
        parts.append(f"SELECT '{esc}' AS customer_id, CAST('{d}' AS DATE) AS reference_date")
    return " UNION ALL ".join(parts)


def fetch_profiles_from_predictions_table(
    table: str,
    pairs: list[tuple[str, str]],
    prediction_info_column: str = "prediction_info",
) -> pd.DataFrame:
    """
    Fetch model_score and prediction_info for each (customer_id, reference_date) pair.
    Parses JSON from prediction_info to avoid hundreds of try_variant_get columns in SQL.
    """
    if not pairs:
        return pd.DataFrame()
    t = _sanitize_table_name(table)
    union_sql = _pairs_union_sql(pairs)
    query = f"""
    WITH pairs AS (
      {union_sql}
    ),
    joined AS (
      SELECT
        p.customer_id,
        CAST(p.prediction_timestamp AS DATE) AS reference_date,
        p.model_score,
        TO_JSON(p.{prediction_info_column}) AS prediction_info_json,
        ROW_NUMBER() OVER (
          PARTITION BY p.customer_id, CAST(p.prediction_timestamp AS DATE)
          ORDER BY p.prediction_timestamp DESC
        ) AS rn
      FROM {t} AS p
      INNER JOIN pairs AS q
        ON p.customer_id = q.customer_id
        AND CAST(p.prediction_timestamp AS DATE) = q.reference_date
    )
    SELECT customer_id, reference_date, model_score, prediction_info_json
    FROM joined
    WHERE rn = 1
    """
    return _execute_sql(query)


def fetch_customer_pairs_from_input_table(input_table: str) -> list[tuple[str, str]]:
    t = _sanitize_table_name(input_table)
    query = f"""
    SELECT DISTINCT
      CAST(customer_id AS STRING) AS customer_id,
      CAST(reference_date AS STRING) AS reference_date
    FROM {t}
    """
    df = _execute_sql(query)
    if df.empty or len(df.columns) < 2:
        return []
    return [(str(df.iloc[i, 0]), str(df.iloc[i, 1])[:10]) for i in range(len(df))]


def parse_prediction_json(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        obj = raw
    else:
        try:
            obj = json.loads(raw)
            if isinstance(obj, str):
                obj = json.loads(obj)
        except (TypeError, json.JSONDecodeError):
            return {}
    if "input" in obj and isinstance(obj["input"], dict):
        return obj["input"]
    return obj if isinstance(obj, dict) else {}
