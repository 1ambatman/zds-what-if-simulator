# ZDS What If Simulator — Quick Start

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- Databricks CLI set up — if you use the AI Dev Kit you already have this. Otherwise run:
  ```
  databricks auth login
  ```

## Run

**Mac:**
1. Right-click `launch.command` → click **Open** → click **Open** again (first run only — macOS security prompt)
2. The app opens automatically in your browser

**Windows:** double-click `launch.bat`

The app is at **http://localhost:8765**.

## Stop

Run `docker compose down` in this folder, or quit Docker Desktop.

## Change the model

Enter a different MLflow Run ID in the **Model** panel and click **↺ Load model**.
