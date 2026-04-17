# What-If Score Simulator

Local web app that reproduces the **what_if_simulator** notebook: load customer feature rows from a Databricks Unity Catalog predictions table, score with a **LightGBM** model from **MLflow**, and explore **SHAP**-driven what-if scenarios.

## Prerequisites

- Python 3.10+
- Network access to your Databricks workspace (SQL warehouse + MLflow)
- Either **`databricks auth login`** (OAuth) or a **personal access token** with permission to use the SQL warehouse and read the MLflow run artifact

## Setup

```bash
cd what-if-simulator
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env
```

If you see `No module named '_cffi_backend'` when the app loads the model, reinstall native deps:

```bash
pip install --force-reinstall 'cffi>=1.16.0' cryptography
```

Edit `.env`:

- **DATABRICKS_HOST** — workspace URL, e.g. `https://adb-xxxx.azuredatabricks.net`
- **DATABRICKS_HTTP_PATH** — SQL warehouse HTTP path, e.g. `/sql/1.0/warehouses/xxxxxxxx` (or set **DATABRICKS_WAREHOUSE_ID** instead)
- **DATABRICKS_TOKEN** — personal access token (omit if you use **`databricks auth login`** / OAuth; then SQL goes through the Databricks SDK and you must set **DATABRICKS_WAREHOUSE_ID** or **DATABRICKS_HTTP_PATH** with a real warehouse id). If you still have an **old PAT** in `.env` but want OAuth, set **DATABRICKS_OAUTH_ONLY=true** or delete the token line — otherwise MLflow can return **403 Invalid access token**
- **MLFLOW_TRACKING_URI** / **registry** — with a PAT, defaults `databricks` / `databricks-uc` are fine. **Without** a PAT (`databricks auth login` only), the app sets `databricks://…` and `databricks-uc://…` with your config profile (default `DEFAULT`) so MLflow uses the same SDK/CLI auth as the AI dev kit
- **PREDICTIONS_TABLE** / **MLFLOW_RUN_ID** — defaults match the notebook; override as needed. **MLFLOW_MODEL_ARTIFACT_PATH** defaults to **`auto`** (finds the folder that contains `MLmodel`). Override with an explicit path if needed, or use **MLFLOW_MODEL_URI** (`models:/catalog.schema.name/1`) / **LOCAL_MODEL_PATH**
- **LOCAL_MODEL_PATH** (optional) — path to an exported MLflow LightGBM model directory to skip loading from a run

## Run

```bash
python run.py
```

The app starts at `http://127.0.0.1:8765` and opens your default browser. The HTTP server comes up immediately; the LightGBM model loads in the background from MLflow (the UI shows **Loading model…** until that finishes). If artifact downloads fail repeatedly (e.g. TLS/proxy), set **LOCAL_MODEL_PATH** to a folder you exported from MLflow.

To confirm the app can reach your SQL warehouse (after configuring `.env` or a profile), open `http://127.0.0.1:8765/api/databricks-health` — you should see `{"ok": true, ...}`.

Alternatively:

```bash
what-if-simulator
```

## Usage

1. **Load profiles** — Enter the predictions table name, then either paste **customer IDs** and **reference dates** (inline), or point at an **input table** with `customer_id` and `reference_date`.
2. Choose a **profile** and a **scenario** (or **Manual adjustment** to tweak features with sliders).
3. **Run what-if** to see tier migration, score comparison, SHAP drivers, and top feature deltas.

## Data expectations

- Predictions table must include `customer_id`, `prediction_timestamp` (or castable to date), `model_score`, and `prediction_info` (VARIANT/JSON with an `input` object keyed by model feature names), consistent with the original notebook.

If `TO_JSON(prediction_info)` fails in your warehouse, adjust the SQL in `what_if_app/databricks_io.py` (e.g. cast rules for your table).

## License

Use and modify per your organization’s policies. This repository is generated as a standalone tool and is not coupled to the parent monorepo.
