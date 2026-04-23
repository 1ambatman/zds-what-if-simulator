# ZDS What If Simulator — Quick Start

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

## Setup (one time)

1. Rename `.env.example` to `.env`
2. Open `.env` in any text editor and fill in:
   - `DATABRICKS_HOST` — your workspace URL (e.g. `https://adb-xxxx.azuredatabricks.net`)
   - `DATABRICKS_WAREHOUSE_ID` — SQL warehouse ID (Compute → SQL Warehouses → copy from URL)
   - `DATABRICKS_TOKEN` — personal access token (Settings → Developer → Access Tokens → Generate)
   - `MLFLOW_RUN_ID` — the MLflow run ID of the model you want to load

## Run

- **Mac:** double-click `launch.command`
- **Windows:** double-click `launch.bat`

The app opens automatically in your browser at **http://localhost:8765**.

## Stop

Run `docker compose down` in this folder, or quit Docker Desktop.
