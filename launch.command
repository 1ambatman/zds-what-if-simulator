#!/usr/bin/env bash
# =============================================================================
# ZDS What If Simulator — Mac launcher
# Double-click this file in Finder to start the app.
# =============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BOLD=$'\033[1m'; GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[1;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
APP_URL="http://localhost:8765"
DATABRICKS_HOST="https://adb-3834014070274745.5.azuredatabricks.net"

step() { echo "${BOLD}▶ $1${NC}"; }
ok()   { echo "${GREEN}✓ $1${NC}"; }
warn() { echo "${YELLOW}⚠ $1${NC}"; }
err()  { echo "${RED}✗ $1${NC}"; }

echo ""
echo "${BOLD}╔══════════════════════════════════════╗${NC}"
echo "${BOLD}║      ZDS What If Simulator           ║${NC}"
echo "${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""

# ── 1. Install Databricks CLI if missing ──────────────────────────────────────
if ! command -v databricks &>/dev/null; then
    step "Installing Databricks CLI..."
    if command -v brew &>/dev/null; then
        brew install databricks/tap/databricks
    else
        curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
        # Reload PATH so the newly installed binary is found
        export PATH="$HOME/.databricks/bin:$PATH"
    fi
    ok "Databricks CLI installed"
fi
ok "Databricks CLI ready"

# ── 2. Log in if not authenticated ────────────────────────────────────────────
if ! databricks auth token --host "$DATABRICKS_HOST" &>/dev/null 2>&1; then
    step "Logging in to Databricks (browser will open)..."
    databricks auth login --host "$DATABRICKS_HOST"
fi
ok "Databricks auth OK"

# ── 3. Create .env if missing ─────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    step "Creating .env with defaults..."
    cat > .env << 'ENVEOF'
DATABRICKS_HOST=https://adb-3834014070274745.5.azuredatabricks.net
DATABRICKS_WAREHOUSE_ID=22f5ad0176ccc8df
DATABRICKS_OAUTH_ONLY=true
MLFLOW_TRACKING_URI=databricks
PREDICTIONS_TABLE=mle.batch_model_inference.predictions
MLFLOW_RUN_ID=9d740e9e5f544d9490100cef238bf074
ENVEOF
    ok ".env created"
fi

# ── 4. Mount ~/.databrickscfg into the container ──────────────────────────────
cat > docker-compose.override.yml << OVERRIDEOF
services:
  app:
    volumes:
      - ${HOME}/.databrickscfg:/root/.databrickscfg:ro
OVERRIDEOF

# ── 5. Check Docker ───────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    err "Docker Desktop is not installed."
    echo ""
    echo "  Download it from: https://www.docker.com/products/docker-desktop/"
    open "https://www.docker.com/products/docker-desktop/" 2>/dev/null || true
    read -r -p "  Install Docker Desktop, then run this launcher again. Press Enter to exit..."
    exit 1
fi
ok "Docker found"

# ── 6. Start Docker daemon if not running ─────────────────────────────────────
if ! docker info &>/dev/null 2>&1; then
    warn "Docker Desktop is not running — starting it now..."
    open -a "Docker" 2>/dev/null || true
    for i in $(seq 1 30); do
        sleep 2
        docker info &>/dev/null 2>&1 && break
        if [ "$i" -eq 30 ]; then
            err "Docker did not start in time. Please open Docker Desktop manually and try again."
            read -r -p "Press Enter to exit..."
            exit 1
        fi
    done
fi
ok "Docker is running"

# ── 7. Pull latest image ──────────────────────────────────────────────────────
step "Pulling latest image (first run may take a few minutes)..."
docker compose pull

# ── 8. Start container ────────────────────────────────────────────────────────
step "Starting app..."
docker compose up -d

# ── 9. Wait for health check ──────────────────────────────────────────────────
step "Waiting for app to be ready..."
for i in $(seq 1 30); do
    sleep 2
    curl -sf "${APP_URL}/api/health" &>/dev/null && break
    if [ "$i" -eq 30 ]; then
        err "App did not become ready in time. Check logs with: docker compose logs"
        read -r -p "Press Enter to exit..."
        exit 1
    fi
done

# ── 10. Open browser ──────────────────────────────────────────────────────────
ok "App is ready!"
open "$APP_URL"

echo ""
echo "  ${BOLD}Open:${NC}  $APP_URL"
echo "  ${BOLD}Stop:${NC}  docker compose down   (or quit Docker Desktop)"
echo ""
echo "Showing live logs — close this window to detach (app keeps running):"
echo "──────────────────────────────────────────────────────────────────"
docker compose logs -f
