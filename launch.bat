@echo off
setlocal enabledelayedexpansion
title ZDS What If Simulator

echo.
echo ╔══════════════════════════════════════╗
echo ║      ZDS What If Simulator           ║
echo ╚══════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ── 1. Check .env ─────────────────────────────────────────────────────────────
if not exist ".env" (
    echo [ERROR] .env not found.
    echo.
    echo   1. Rename .env.example to .env
    echo   2. Open .env in Notepad and fill in your Databricks details
    echo   3. Run this launcher again
    echo.
    pause
    exit /b 1
)
echo [OK] .env found

:: ── 2. Check Docker ───────────────────────────────────────────────────────────
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Desktop is not installed.
    echo.
    echo   Download it from: https://www.docker.com/products/docker-desktop/
    echo.
    start https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)
echo [OK] Docker found

:: ── 3. Start Docker daemon if not running ─────────────────────────────────────
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker Desktop is not running. Starting it...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Waiting for Docker to start ^(this may take ~30 seconds^)...
    for /L %%i in (1,1,30) do (
        timeout /t 2 /nobreak >nul
        docker info >nul 2>&1 && goto :docker_ready
    )
    echo [ERROR] Docker did not start in time.
    echo Please open Docker Desktop manually and try again.
    pause
    exit /b 1
)
:docker_ready
echo [OK] Docker is running

:: ── 4. Pull latest image ──────────────────────────────────────────────────────
echo Pulling latest image ^(first run may take a few minutes^)...
docker compose pull

:: ── 5. Start container ────────────────────────────────────────────────────────
echo Starting app...
docker compose up -d

:: ── 6. Wait for health check ──────────────────────────────────────────────────
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
