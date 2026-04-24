# ZDS What If Simulator — Quick Start

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

That's it — everything else (Databricks CLI, authentication) is handled automatically on first launch.

## Run

**Mac:**
1. Right-click `launch.command` → click **Open** → click **Open** again *(first run only — macOS security prompt)*
2. If this is your first time, a browser window will open to log in to Databricks — sign in and close it
3. The app opens automatically

**Windows:** double-click `launch.bat` and follow any prompts.

The app is at **http://localhost:8765**.

## Stop

Run `docker compose down` in this folder, or quit Docker Desktop.

## Change the model

Enter a different MLflow Run ID in the **Model** panel and click **↺ Load model**.
