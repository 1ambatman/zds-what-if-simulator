# ZDS What If Simulator — Quick Start

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

## Run

**Mac:**
1. Right-click `launch.command` → click **Open** → click **Open** again (first run only — macOS security prompt)
2. When asked, paste your Databricks personal access token
   - Get one at: **Settings → Developer → Access tokens → Generate new token**

**Windows:** double-click `launch.bat`, then paste your token when asked.

The app opens automatically in your browser at **http://localhost:8765**.

## Stop

Run `docker compose down` in this folder, or quit Docker Desktop.

## Change the model

Enter a different MLflow Run ID in the **Model** panel and click **↺ Load model**.
