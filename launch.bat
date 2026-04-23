@echo off
setlocal enabledelayedexpansion
title ZDS What If Simulator

echo.
echo ╔══════════════════════════════════════╗
echo ║      ZDS What If Simulator           ║
echo ╚══════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ── 1. Create .env if missing ─────────────────────────────────────────────────
if not exist ".env" (
    echo Creating .env with defaults...
    (
        echo DATABRICKS_HOST=https://adb-3834014070274745.5.azuredatabricks.net
        echo DATABRICKS_WAREHOUSE_ID=22f5ad0176ccc8df
        echo DATABRICKS_TOKEN=
        echo MLFLOW_TRACKING_URI=databricks
        echo PREDICTIONS_TABLE=mle.batch_model_inference.predictions
        echo MLFLOW_RUN_ID=9d740e9e5f544d9490100cef238bf074
    ) > .env
    echo [OK] .env created
)

:: ── 2. Prompt for token if not set ────────────────────────────────────────────
set "TOKEN="
for /f "tokens=2 delims==" %%A in ('findstr /b "DATABRICKS_TOKEN=" .env 2^>nul') do set TOKEN=%%A
set TOKEN=!TOKEN: =!
if "!TOKEN!"=="" (
    echo.
    echo Your Databricks personal access token is needed ^(one-time setup^).
    echo.
    echo   Go to: Settings -^> Developer -^> Access tokens -^> Generate new token
    echo.
    set /p TOKEN="  Paste your token here and press Enter: "
    set TOKEN=!TOKEN: =!
    if "!TOKEN!"=="" (
        echo [ERROR] No token entered. Please run this launcher again.
        pause
        exit /b 1
    )
    powershell -Command "(Get-Content .env) -replace '^DATABRICKS_TOKEN=.*','DATABRICKS_TOKEN=!TOKEN!' | Set-Content .env"
    echo [OK] Token saved to .env
    echo.
)

:: ── 3. Check Docker ───────────────────────────────────────────────────────────
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

:: ── 4. Start Docker daemon if not running ─────────────────────────────────────
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

:: ── 5. Pull latest image ──────────────────────────────────────────────────────
echo Pulling latest image ^(first run may take a few minutes^)...
docker compose pull

:: ── 6. Start container ────────────────────────────────────────────────────────
echo Starting app...
docker compose up -d

:: ── 7. Wait for health check ──────────────────────────────────────────────────
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
