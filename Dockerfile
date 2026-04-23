FROM python:3.11-slim

# LightGBM requires the OpenMP runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependency layer (cached unless pyproject.toml changes) ──────────────────
COPY pyproject.toml README.md ./
# Stub package so pip can resolve deps without the full source
RUN mkdir -p what_if_app && touch what_if_app/__init__.py
RUN pip install --no-cache-dir -e .

# ── Application source ───────────────────────────────────────────────────────
COPY what_if_app/ ./what_if_app/
COPY run.py ./

EXPOSE 8765

# APP_HOST must be 0.0.0.0 so the port is reachable from outside the container
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8765

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -sf http://localhost:8765/api/health || exit 1

CMD ["python", "-m", "uvicorn", "what_if_app.main:app", "--host", "0.0.0.0", "--port", "8765"]
