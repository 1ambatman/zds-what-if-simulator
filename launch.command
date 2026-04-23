#!/usr/bin/env bash
# =============================================================================
# ZDS What If Simulator — Mac launcher
# Double-click this file in Finder to start the app.
# =============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BOLD=$'\033[1m'; GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'
APP_URL="http://localhost:8765"

step() { echo "${BOLD}▶ $1${NC}"; }
ok()   { echo "${GREEN}✓ $1${NC}"; }
warn() { echo "${YELLOW}⚠ $1${NC}"; }
err()  { echo "${RED}✗ $1${NC}"; }

echo ""
echo "${BOLD}╔══════════════════════════════════════╗${NC}"
echo "${BOLD}║      ZDS What If Simulator           ║${NC}"
echo "${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""

# ── 1. Check .env ─────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    err ".env not found."
    echo ""
    echo "  1. Rename  .env.example  →  .env"
    echo "  2. Open .env in a text editor and fill in your Databricks details"
    echo "  3. Run this launcher again"
    echo ""
    read -r -p "Press Enter to exit..."
    exit 1
fi
ok ".env found"

# ── 2. Check Docker ───────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    err "Docker Desktop is not installed."
    echo ""
    echo "  Download it from: https://www.docker.com/products/docker-desktop/"
    echo ""
    open "https://www.docker.com/products/docker-desktop/" 2>/dev/null || true
    read -r -p "Press Enter to exit..."
    exit 1
fi
ok "Docker found"

# ── 3. Start Docker daemon if not running ─────────────────────────────────────
if ! docker info &>/dev/null 2>&1; then
    warn "Docker Desktop is not running — starting it now..."
    open -a "Docker" 2>/dev/null || true
    for i in $(seq 1 30); do
        sleep 2
        docker info &>/dev/null 2>&1 && break
        if [ "$i" -eq 30 ]; then
            err "Docker did not start in time."
            echo "  Please open Docker Desktop manually and try again."
            read -r -p "Press Enter to exit..."
            exit 1
        fi
    done
fi
ok "Docker is running"

# ── 4. Pull latest image ──────────────────────────────────────────────────────
step "Pulling latest image (first run may take a few minutes)..."
docker compose pull

# ── 5. Start container ────────────────────────────────────────────────────────
step "Starting app..."
docker compose up -d

# ── 6. Wait for health check ──────────────────────────────────────────────────
step "Waiting for app to be ready..."
for i in $(seq 1 30); do
    sleep 2
    curl -sf "${APP_URL}/api/health" &>/dev/null && break
    if [ "$i" -eq 30 ]; then
        err "App did not become ready in time."
        echo "  Check logs with:  docker compose logs"
        read -r -p "Press Enter to exit..."
        exit 1
    fi
done

# ── 7. Open browser ───────────────────────────────────────────────────────────
ok "App is ready!"
open "$APP_URL"

echo ""
echo "  ${BOLD}Open:${NC}  $APP_URL"
echo "  ${BOLD}Stop:${NC}  docker compose down   (or quit Docker Desktop)"
echo ""
echo "Showing live logs — close this window to detach (app keeps running):"
echo "──────────────────────────────────────────────────────────────────"
docker compose logs -f
