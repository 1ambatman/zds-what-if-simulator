@echo off
setlocal enabledelayedexpansion
title ZDS What If Simulator

echo.
echo ╔══════════════════════════════════════╗
echo ║      ZDS What If Simulator           ║
echo ╚══════════════════════════════════════╝
echo.

cd /d "%~dp0"
set DATABRICKS_HOST=https://adb-3834014070274745.5.azuredatabricks.net

:: ── 1. Install Databricks CLI if missing ──────────────────────────────────────
databricks --version >nul 2>&1
if errorlevel 1 (
    echo Installing Databricks CLI...
    powershell -Command "& { $url='https://raw.githubusercontent.com/databricks/setup-cli/main/install.ps1'; Invoke-Expression (Invoke-WebRequest -Uri $url -UseBasicParsing).Content }"
    if errorlevel 1 (
        echo [ERROR] Could not install Databricks CLI automatically.
        echo   Please install it manually from: https://docs.databricks.com/en/dev-tools/cli/install.html
        pause
        exit /b 1
    )
    echo [OK] Databricks CLI installed
)
echo [OK] Databricks CLI ready

:: ── 2. Log in if not authenticated ────────────────────────────────────────────
databricks auth token --host "%DATABRICKS_HOST%" >nul 2>&1
if errorlevel 1 (
    echo Logging in to Databricks ^(browser will open^)...
    databricks auth login --host "%DATABRICKS_HOST%"
)
echo [OK] Databricks auth OK

:: ── 3. Create .env if missing ─────────────────────────────────────────────────
if not exist ".env" (
    echo Creating .env with defaults...
    (
        echo DATABRICKS_HOST=https://adb-3834014070274745.5.azuredatabricks.net
        echo DATABRICKS_WAREHOUSE_ID=22f5ad0176ccc8df
        echo DATABRICKS_OAUTH_ONLY=true
        echo MLFLOW_TRACKING_URI=databricks
        echo PREDICTIONS_TABLE=mle.batch_model_inference.predictions
        echo MLFLOW_RUN_ID=9d740e9e5f544d9490100cef238bf074
    ) > .env
    echo [OK] .env created
)

:: ── 4. Mount .databrickscfg into the container ────────────────────────────────
(
    echo services:
    echo   app:
    echo     volumes:
    echo       - %USERPROFILE%\.databrickscfg:/root/.databrickscfg:ro
) > docker-compose.override.yml

:: ── 5. Check Docker ───────────────────────────────────────────────────────────
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Desktop is not installed.
    echo.
    echo   Download it from: https://www.docker.com/products/docker-desktop/
    start https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)
echo [OK] Docker found

:: ── 6. Start Docker daemon if not running ─────────────────────────────────────
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker Desktop is not running. Starting it...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Waiting for Docker to start ^(this may take ~30 seconds^)...
    for /L %%i in (1,1,30) do (
        timeout /t 2 /nobreak >nul
        docker info >nul 2>&1 && goto :docker_ready
    )
    echo [ERROR] Docker did not start in time. Please open Docker Desktop manually and try again.
    pause
    exit /b 1
)
:docker_ready
echo [OK] Docker is running

:: ── 7. Pull latest image ──────────────────────────────────────────────────────
echo Pulling latest image ^(first run may take a few minutes^)...
docker compose pull

:: ── 8. Start container ────────────────────────────────────────────────────────
echo Starting app...
docker compose up -d

:: ── 9. Wait for health check ──────────────────────────────────────────────────
echo Waiting for app to be ready...
for /L %%i in (1,1,30) do (
    timeout /t 2 /nobreak >nul
    curl -sf http://localhost:8765/api/health >nul 2>&1 && goto :app_ready
)
echo [ERROR] App did not become ready in time.
echo Check logs with:  docker compose logs
pause
exit /b 1

:app_ready
echo [OK] App is ready!
start http://localhost:8765

echo.
echo   Open:  http://localhost:8765
echo   Stop:  docker compose down   ^(or quit Docker Desktop^)
echo.
echo Showing live logs — close this window to detach ^(app keeps running^):
echo ──────────────────────────────────────────────────────────────────
docker compose logs -f
