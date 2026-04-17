"""FastAPI app: what-if simulator API + static UI."""

from __future__ import annotations

import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from what_if_app import ml_core
from what_if_app.config import settings
from what_if_app.databricks_io import fetch_customer_pairs_from_input_table, fetch_profiles_from_predictions_table, parse_prediction_json
from what_if_app.ml_core import PRESET_SCENARIOS, get_booster, init_runtime, load_model_from_mlflow

STATIC_DIR = Path(__file__).resolve().parent / "static"

profiles_store: dict[str, pd.DataFrame] = {}
profile_meta: dict[str, dict[str, Any]] = {}
model_load_error: str | None = None
model_loading: bool = False
_load_generation: int = 0
_model_load_started_at: float | None = None


def _model_unavailable_detail() -> str:
    if model_loading:
        return "Model is still loading from MLflow (downloading artifacts). Retry in a few seconds."
    return model_load_error or "Model not loaded"


def _background_load_model(gen: int) -> None:
    global model_load_error, model_loading, _load_generation
    try:
        from what_if_app.databricks_io import apply_databricks_profile_to_environ

        apply_databricks_profile_to_environ()
        booster, explainer = load_model_from_mlflow()
        if gen != _load_generation:
            return
        init_runtime(booster, explainer)
        model_load_error = None
    except Exception as e:  # noqa: BLE001
        if gen != _load_generation:
            return
        model_load_error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        if gen == _load_generation:
            model_loading = False


def _watchdog_load_timeout(expected_gen: int) -> None:
    global model_load_error, model_loading, _load_generation
    time.sleep(settings.mlflow_load_timeout_seconds)
    if expected_gen != _load_generation or not model_loading:
        return
    model_load_error = (
        f"Timed out after {settings.mlflow_load_timeout_seconds}s waiting for MLflow artifacts. "
        "Downloads often fail or retry forever with SSLError (VPN, proxy, TLS inspection). "
        "Export the model folder from the MLflow UI (or use `mlflow artifacts download`) and set "
        "LOCAL_MODEL_PATH in .env, or try another network. Increase MLFLOW_LOAD_TIMEOUT_SECONDS if needed."
    )
    _load_generation += 1
    model_loading = False


def _profile_id(customer_id: str, ref: str) -> str:
    return f"{customer_id}__{ref}"


def _build_profiles_from_df(df: pd.DataFrame) -> None:
    profiles_store.clear()
    profile_meta.clear()
    if not ml_core.is_ready():
        raise RuntimeError("Model not loaded")
    for _, row in df.iterrows():
        cid = str(row["customer_id"]).strip()
        ref_full = str(row["reference_date"]).strip()
        ref = ref_full[:10]
        pid = _profile_id(cid, ref)
        raw = row.get("prediction_info_json")
        inp = parse_prediction_json(raw)
        data = {f: float(inp.get(f) or 0.0) for f in ml_core.V1_FEATURES}
        pdf = pd.DataFrame([data])
        profiles_store[pid] = pdf
        score = float(get_booster().predict(pdf[ml_core.V1_FEATURES])[0])
        tier = ml_core.score_to_tier_num(score)
        label = ml_core.score_to_label(score)
        ms = row.get("model_score")
        profile_meta[pid] = {
            "id": pid,
            "customer_id": cid,
            "reference_date": ref_full,
            "label": f"Tier {tier} ({label}) — {cid} · {ref_full}",
            "score": score,
            "model_score_table": float(ms) if ms is not None and not pd.isna(ms) else None,
            "tier": tier,
            "risk_label": label,
        }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model_load_error, model_loading, _load_generation, _model_load_started_at
    model_load_error = None
    model_loading = True
    _model_load_started_at = time.monotonic()
    _load_generation += 1
    gen = _load_generation
    threading.Thread(target=_background_load_model, args=(gen,), daemon=True, name="mlflow-model-load").start()
    threading.Thread(
        target=_watchdog_load_timeout, args=(gen,), daemon=True, name="mlflow-load-watchdog"
    ).start()
    yield


app = FastAPI(title="ZDS What If Simulator", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, Any]:
    elapsed = None
    stuck_hint = None
    if model_loading and _model_load_started_at is not None:
        elapsed = round(time.monotonic() - _model_load_started_at, 1)
        if elapsed > 90:
            stuck_hint = (
                "Still downloading — SSL errors in the terminal usually mean the blob download cannot complete. "
                "Set LOCAL_MODEL_PATH to an exported model folder, or wait for timeout."
            )
    return {
        "ok": model_load_error is None and ml_core.is_ready(),
        "model_loaded": ml_core.is_ready(),
        "model_loading": model_loading,
        "load_elapsed_sec": elapsed,
        "load_stuck_hint": stuck_hint,
        "error": model_load_error,
    }


@app.get("/api/databricks-health")
def databricks_health() -> dict[str, Any]:
    """Verify SQL warehouse credentials (profile or .env) with SELECT 1."""
    from what_if_app.databricks_io import ping_databricks_sql

    try:
        ping_databricks_sql()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": "SQL warehouse accepts queries"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    fg = ml_core.build_feature_groups(ml_core.V1_FEATURES) if ml_core.V1_FEATURES else {}
    scenarios = [
        {"name": k, "description": v["description"]}
        for k, v in PRESET_SCENARIOS.items()
    ]
    return {
        "predictions_table_default": settings.predictions_table,
        "mlflow_run_id_default": settings.mlflow_run_id,
        "feature_groups": {k: v for k, v in fg.items() if v},
        "scenarios": scenarios,
        "profile_count": len(profiles_store),
        "tier_boundaries": [[tn, lo, hi] for tn, lo, hi in ml_core.TIER_BOUNDARIES],
        "tier_labels": {k: [float(v[0]), float(v[1])] for k, v in ml_core.TIER_LABELS.items()},
    }


class LoadRequest(BaseModel):
    predictions_table: str | None = None
    mode: str = "inline"  # inline | input_table
    customer_ids: list[str] = Field(default_factory=list)
    reference_dates: list[str] = Field(default_factory=list)
    input_table: str | None = None


@app.post("/api/load")
def load_profiles(req: LoadRequest) -> dict[str, Any]:
    if not ml_core.is_ready():
        raise HTTPException(503, detail=_model_unavailable_detail())
    table = (req.predictions_table or settings.predictions_table).strip()
    if req.mode == "input_table":
        if not req.input_table:
            raise HTTPException(400, detail="input_table is required when mode=input_table")
        pairs = fetch_customer_pairs_from_input_table(req.input_table.strip())
    else:
        ids = req.customer_ids
        dates = req.reference_dates
        if not ids:
            raise HTTPException(400, detail="customer_ids required for inline mode")
        if len(dates) == 1:
            dlist = dates * len(ids)
        elif len(dates) == len(ids):
            dlist = dates
        else:
            raise HTTPException(
                400,
                detail=f"reference_dates must have 1 entry or {len(ids)} entries (got {len(dates)})",
            )
        pairs = list(zip(ids, dlist))
    try:
        raw = fetch_profiles_from_predictions_table(table, pairs)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, detail=str(e)) from e
    if raw.empty:
        return {"loaded": 0, "profiles": [], "warning": "No rows returned for the given pairs."}
    _build_profiles_from_df(raw)
    missing = []
    loaded_keys = {(str(r["customer_id"]), str(r["reference_date"])[:10]) for _, r in raw.iterrows()}
    for cid, dt in pairs:
        if (cid, dt[:10]) not in loaded_keys:
            missing.append(f"{cid} / {dt}")
    return {
        "loaded": len(profiles_store),
        "profiles": list(profile_meta.values()),
        "warnings": [f"Not found in predictions table: {m}" for m in missing],
    }


@app.get("/api/profiles")
def list_profiles() -> dict[str, Any]:
    return {"profiles": list(profile_meta.values())}


class WhatIfRequest(BaseModel):
    profile_id: str
    scenario: str = "(No scenario)"  # (No scenario) | Manual | preset name
    manual_features: dict[str, float] | None = None


@app.post("/api/what-if")
def what_if(req: WhatIfRequest) -> dict[str, Any]:
    if not ml_core.is_ready():
        raise HTTPException(503, detail=_model_unavailable_detail())
    pid = req.profile_id
    if pid not in profiles_store:
        raise HTTPException(404, detail="Unknown profile_id; run load first.")
    profile = profiles_store[pid].copy()
    scenario_name = req.scenario

    if scenario_name == "(No scenario)":
        score, sv, base = ml_core.score_profile(profile)
        return {
            "mode": "baseline",
            "profile_label": profile_meta.get(pid, {}).get("label", pid),
            "score": score,
            "tier": ml_core.score_to_tier_num(score),
            "risk_label": ml_core.score_to_label(score),
            "base_value": base,
            "waterfall": ml_core.shap_waterfall_rows(sv, base, profile, n=24),
        }

    if scenario_name == "Manual adjustment":
        mod = profile.copy()
        if req.manual_features:
            for k, v in req.manual_features.items():
                if k in mod.columns:
                    mod[k] = float(v)
    elif scenario_name in PRESET_SCENARIOS:
        spec = PRESET_SCENARIOS[scenario_name]
        mod = spec["fn"](profile, **spec["params"])
    else:
        raise HTTPException(400, detail=f"Unknown scenario: {scenario_name}")

    score_before, shap_before, base = ml_core.score_profile(profile)
    score_after, shap_after, _ = ml_core.score_profile(mod)
    delta = ml_core.feature_delta_table(profile, mod, shap_before, shap_after, ml_core.V1_FEATURES, n=20)

    return {
        "mode": "compare",
        "profile_label": profile_meta.get(pid, {}).get("label", pid),
        "scenario": scenario_name,
        "description": PRESET_SCENARIOS.get(scenario_name, {}).get("description", ""),
        "tier_migration": ml_core.tier_migration_text(score_before, score_after),
        "score_before": score_before,
        "score_after": score_after,
        "tier_before": ml_core.score_to_tier_num(score_before),
        "tier_after": ml_core.score_to_tier_num(score_after),
        "label_before": ml_core.score_to_label(score_before),
        "label_after": ml_core.score_to_label(score_after),
        "base_value": base,
        "waterfall_before": ml_core.shap_waterfall_rows(shap_before, base, profile, n=20),
        "waterfall_after": ml_core.shap_waterfall_rows(shap_after, base, mod, n=20),
        "delta_table": delta,
    }


@app.get("/api/profile-features/{profile_id}")
def profile_features(profile_id: str) -> dict[str, Any]:
    if not ml_core.is_ready():
        raise HTTPException(503, detail=_model_unavailable_detail())
    if profile_id not in profiles_store:
        raise HTTPException(404, detail="Unknown profile")
    pdf = profiles_store[profile_id]
    fg = ml_core.build_feature_groups(ml_core.V1_FEATURES)
    out: dict[str, Any] = {"groups": {}}
    for gname, feats in fg.items():
        if not feats:
            continue
        sliders = []
        for feat in feats:
            val = float(pdf[feat].iloc[0])
            ref_vals = [float(profiles_store[p][feat].iloc[0]) for p in profiles_store if feat in profiles_store[p].columns]
            max_val = max(abs(v) for v in ref_vals) * 3 if ref_vals else 10.0
            if max_val == 0:
                max_val = 10.0
            is_pct = "pct" in feat
            step = 0.01 if is_pct else max(0.01, max_val / 200)
            sliders.append(
                {
                    "name": feat,
                    "label": feat.replace("_lag1d", "").replace("_", " ")[:44],
                    "value": val,
                    "min": 0.0,
                    "max": 1.0 if is_pct else max_val,
                    "step": step,
                    "is_pct": is_pct,
                }
            )
        out["groups"][gname] = sliders
    return out


# Static single-page app
@app.get("/")
def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(404, detail="UI not built — missing static/index.html")
    return FileResponse(index_path)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
