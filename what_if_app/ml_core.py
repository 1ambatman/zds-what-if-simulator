"""LightGBM + SHAP scoring, tiering, preset scenarios, and cascade rules."""

from __future__ import annotations

import json
import logging
import shutil
from collections import deque
from typing import Any

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
import shap

from what_if_app.config import settings

_booster: lgb.Booster | None = None
_explainer: shap.TreeExplainer | None = None
V1_FEATURES: list[str] = []

_logger = logging.getLogger(__name__)


def discover_mlflow_model_subpath(run_id: str) -> str | None:
    """
    Find the run-relative directory that contains MLmodel.

    MlflowClient.list_artifacts(run_id) uses the raw backing store and omits MLflow 3 "logged
    model" layouts. We use RunsArtifactRepository via runs:/<run_id> (same as download) and BFS.
    Logged models often live under path "model" but may not appear in a root listing, so we seed
    the queue with "model".
    """
    from mlflow.tracking.artifact_utils import get_artifact_repository

    tracking = mlflow.get_tracking_uri()
    registry = mlflow.get_registry_uri()
    repo = get_artifact_repository(
        artifact_uri=f"runs:/{run_id}",
        tracking_uri=tracking,
        registry_uri=registry,
    )
    parents: list[str] = []
    seen: set[str | None] = set()
    q: deque[str | None] = deque([None, "model"])
    while q:
        prefix = q.popleft()
        if prefix in seen:
            continue
        seen.add(prefix)
        try:
            arts = repo.list_artifacts(prefix)
        except Exception:
            continue
        for fi in arts:
            p = fi.path.replace("\\", "/")
            if fi.is_dir:
                if p not in seen:
                    q.append(p)
            elif p.split("/")[-1] == "MLmodel":
                parents.append(p.rsplit("/", 1)[0])

    if not parents:
        return None
    parents.sort(key=lambda s: (len(s.split("/")), len(s)))
    return parents[0]


def _runs_model_uri(run_id: str, subpath: str) -> str:
    sub = subpath.strip().strip("/")
    return f"runs:/{run_id}/{sub}" if sub else f"runs:/{run_id}"


def _resolve_mlflow_tracking_and_registry_uris() -> tuple[str, str]:
    """
    PAT mode: use MLFLOW_TRACKING_URI / MLFLOW_REGISTRY_URI from settings (default databricks / databricks-uc).

    OAuth / databricks-cli (no DATABRICKS_TOKEN): use databricks://<profile> and databricks-uc://<profile>
    so MLflow authenticates via the Databricks SDK (same as AI dev kit), not raw tokens.
    """
    tracking = (settings.mlflow_tracking_uri or "databricks").strip()
    registry = (settings.mlflow_registry_uri or "databricks-uc").strip()
    if (settings.local_model_path or "").strip():
        return tracking, registry
    if settings.uses_databricks_pat():
        return tracking, registry
    from mlflow.utils.uri import construct_db_uc_uri_from_profile

    profile = (settings.databricks_config_profile or "DEFAULT").strip()
    uc = construct_db_uc_uri_from_profile(profile)
    return f"databricks://{profile}", uc if uc else "databricks-uc"


def load_model_from_mlflow() -> tuple[lgb.Booster, shap.TreeExplainer]:
    t_uri, r_uri = _resolve_mlflow_tracking_and_registry_uris()
    mlflow.set_tracking_uri(t_uri)
    mlflow.set_registry_uri(r_uri)
    mlflow.lightgbm.autolog(disable=True)
    if settings.local_model_path:
        booster = mlflow.lightgbm.load_model(settings.local_model_path)
    else:
        uri = (settings.mlflow_model_uri or "").strip()
        rid = settings.mlflow_run_id.strip()
        sub: str | None = None
        if not uri:
            raw = (settings.mlflow_model_artifact_path or "auto").strip().strip("/")
            if raw.lower() == "auto":
                sub = discover_mlflow_model_subpath(rid)
                if not sub:
                    raise mlflow.exceptions.MlflowException(
                        f"No MLmodel file found under run {rid!r} via list_artifacts. "
                        "Set MLFLOW_MODEL_ARTIFACT_PATH to the folder path shown in the MLflow UI, "
                        "or MLFLOW_MODEL_URI / LOCAL_MODEL_PATH."
                    )
            else:
                sub = raw
            uri = _runs_model_uri(rid, sub)

        # Avoid MLflow's fallback to Azure blob presigned URLs (often SSLEOF on VPN/proxy): fetch
        # the run artifact tree via Databricks Files API (same host as the workspace) first.
        if not (settings.mlflow_model_uri or "").strip() and sub:
            try:
                from what_if_app.databricks_io import download_run_artifact_dir_via_workspace_files

                tmp = download_run_artifact_dir_via_workspace_files(rid, sub, t_uri)
                if tmp is not None:
                    try:
                        booster = mlflow.lightgbm.load_model(tmp)
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                    explainer = shap.TreeExplainer(booster)
                    return booster, explainer
            except Exception as e:
                _logger.warning(
                    "Workspace Files API download failed (%s); falling back to MLflow URI download.",
                    e,
                )

        try:
            booster = mlflow.lightgbm.load_model(uri)
        except mlflow.exceptions.MlflowException as e:
            raise mlflow.exceptions.MlflowException(
                f"{e.message} | Tried URI {uri!r}. Set MLFLOW_MODEL_ARTIFACT_PATH to the artifact "
                "folder containing MLmodel (or use auto), MLFLOW_MODEL_URI for registry, or "
                "LOCAL_MODEL_PATH if downloads fail (TLS/network)."
            ) from e
    explainer = shap.TreeExplainer(booster)
    return booster, explainer


def init_runtime(booster: lgb.Booster, explainer: shap.TreeExplainer) -> None:
    global _booster, _explainer, V1_FEATURES
    _booster = booster
    _explainer = explainer
    V1_FEATURES = list(booster.feature_name())


def is_ready() -> bool:
    return _booster is not None and _explainer is not None


def get_booster() -> lgb.Booster:
    if _booster is None:
        raise RuntimeError("Model not loaded.")
    return _booster


def get_explainer() -> shap.TreeExplainer:
    if _explainer is None:
        raise RuntimeError("Explainer not loaded.")
    return _explainer


TIER_BOUNDARIES = [
    (1, 0.0000, 0.0393),
    (2, 0.0393, 0.0597),
    (3, 0.0597, 0.0787),
    (4, 0.0787, 0.1016),
    (5, 0.1016, 0.1398),
    (6, 0.1398, 0.2129),
    (7, 0.2129, 0.3426),
    (8, 0.3426, 0.5897),
    (9, 0.5897, 0.7855),
    (10, 0.7855, 1.0000),
]

TIER_LABELS = {
    "Good": (0.0000, 0.0597),
    "Okay": (0.0597, 0.5897),
    "Risky": (0.5897, 1.0000),
}


def build_feature_groups(features: list[str]) -> dict[str, list[str]]:
    return {
        "Delinquency": [f for f in features if f.startswith("delinquent_")],
        "Payment Timing": [
            f
            for f in features
            if any(f.startswith(p) for p in ["paid_amt_", "paid_cnt_", "ontime_amt_", "early_amt_", "late_amt_", "late_cnt_"])
        ],
        "Retry Charges": [f for f in features if f.startswith("retry_charge_")],
        "Charge Outcomes": [
            f for f in features if any(f.startswith(p) for p in ["charge_approved_", "charge_declined_"])
        ],
        "Purchase Requests": [f for f in features if f.startswith("pr_")],
        "Orders / Transactions": [
            f for f in features if any(f.startswith(p) for p in ["transact_", "gt4_installments_"])
        ],
        "Card Management": [
            f for f in features if any(f.startswith(p) for p in ["card_", "payment_card_"])
        ],
        "Balance / Credit": [
            f for f in features if any(f.startswith(p) for p in ["credit_utilization_", "outstanding_amt_", "due_amt_"])
        ],
        "Scheduled / Manual": [
            f for f in features if any(f.startswith(p) for p in ["scheduled_charge_", "manual_charge_"])
        ],
        "Tenure": [f for f in features if f.startswith("days_since_")],
    }


def score_to_tier_num(score: float) -> int:
    for tier_num, lo, hi in TIER_BOUNDARIES:
        if lo <= score <= hi:
            return tier_num
    return 10


def score_to_label(score: float) -> str:
    for label, (lo, hi) in TIER_LABELS.items():
        if lo <= score <= hi:
            return label
    return "Risky"


def profile_row_to_df(row: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame([{f: float(row.get(f, 0.0)) for f in V1_FEATURES}])


def score_profile(profile_df: pd.DataFrame) -> tuple[float, np.ndarray, float]:
    booster = get_booster()
    explainer = get_explainer()
    score = float(booster.predict(profile_df[V1_FEATURES])[0])
    sv = explainer.shap_values(profile_df[V1_FEATURES])
    if isinstance(sv, list):
        sv = sv[1]
    base_value = explainer.expected_value
    if isinstance(base_value, (list, np.ndarray)):
        base_value = base_value[1]
    return score, np.asarray(sv[0]).flatten(), float(base_value)


def shap_waterfall_rows(shap_vals: np.ndarray, base_value: float, profile_df: pd.DataFrame, n: int = 20) -> list[dict[str, Any]]:
    feats = V1_FEATURES
    vals = profile_df[feats].values.flatten()
    order = np.argsort(-np.abs(shap_vals))[:n]
    rows = []
    for i in order:
        rows.append(
            {
                "feature": feats[i],
                "shap": float(shap_vals[i]),
                "value": float(vals[i]),
            }
        )
    return rows


def tier_migration_text(score_before: float, score_after: float) -> str:
    t1, l1 = score_to_tier_num(score_before), score_to_label(score_before)
    t2, l2 = score_to_tier_num(score_after), score_to_label(score_after)
    diff = score_after - score_before
    arrow = "\u2191" if diff > 0 else "\u2193"
    return (
        f"Tier {t1} ({l1}) \u2192 Tier {t2} ({l2})  |  "
        f"Score: {score_before:.4f} {arrow} {score_after:.4f} "
        f"({'+' if diff > 0 else ''}{diff:.4f})"
    )


def feature_delta_table(
    profile_before: pd.DataFrame,
    profile_after: pd.DataFrame,
    shap_before: np.ndarray,
    shap_after: np.ndarray,
    features: list[str],
    n: int = 20,
) -> list[dict[str, Any]]:
    df = pd.DataFrame(
        {
            "feature": features,
            "original_value": profile_before[features].values.flatten(),
            "modified_value": profile_after[features].values.flatten(),
            "value_change": (profile_after[features].values - profile_before[features].values).flatten(),
            "shap_original": shap_before,
            "shap_modified": shap_after,
            "shap_delta": shap_after - shap_before,
        }
    )
    df["abs_shap_delta"] = df["shap_delta"].abs()
    df = df.sort_values("abs_shap_delta", ascending=False).head(n).reset_index(drop=True)
    out = df[
        ["feature", "original_value", "modified_value", "value_change", "shap_delta"]
    ].copy()
    return json.loads(out.to_json(orient="records"))


# ---------------------------------------------------------------------------
# Cascade rules
# ---------------------------------------------------------------------------

def compute_cascade(
    changed_feature: str,
    old_value: float,
    new_value: float,
    profile_df: pd.DataFrame,
    avg_installment: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Given one feature that changed, return cascaded updates for related features.

    Each entry: {"feature": str, "new_value": float, "reason": str}
    Only features already in V1_FEATURES and different from changed_feature are returned.
    """
    delta = new_value - old_value
    if abs(delta) < 1e-9:
        return []

    results: dict[str, dict[str, Any]] = {}

    def cur(f: str) -> float:
        if f in profile_df.columns:
            return float(profile_df[f].iloc[0])
        return 0.0

    def upd(f: str, nv: float, reason: str) -> None:
        if f in V1_FEATURES and f != changed_feature:
            results[f] = {"feature": f, "new_value": round(float(nv), 6), "reason": reason}

    # --- Delinquent count changed ---
    if changed_feature.startswith("delinquent_cnt"):
        count_delta = delta
        for f in V1_FEATURES:
            if f == changed_feature:
                continue
            if "delinquent_amt" in f and ("sum" in f or "max" in f):
                upd(f, max(0.0, cur(f) + count_delta * avg_installment),
                    f"Delinquent amounts grow by avg installment (${avg_installment:.0f}) per new missed payment")
            elif "delinquent_amt_avg" in f:
                upd(f, max(0.0, cur(f) + count_delta * avg_installment * 0.3),
                    "Average delinquent amount also rises with count")
            elif f.startswith("charge_approved_pct_"):
                upd(f, max(0.0, min(1.0, cur(f) - 0.05 * count_delta)),
                    "Delinquencies reduce charge approval rate (~5% per new missed payment)")
            elif "insufficient_funds_pct" in f:
                upd(f, max(0.0, min(1.0, cur(f) + 0.04 * count_delta)),
                    "Delinquencies correlate with insufficient funds events (~4% per event)")
            elif f.startswith("retry_charge_pct_"):
                upd(f, max(0.0, min(1.0, cur(f) + 0.03 * count_delta)),
                    "More missed payments lead to more payment retries (~3% per event)")
            elif f == "credit_utilization_lag1d":
                upd(f, max(0.0, min(1.0, cur(f) + 0.05 * count_delta)),
                    "Missed payments increase outstanding balance and credit utilization")

    # --- Charge approval rate changed ---
    elif changed_feature.startswith("charge_approved_pct_"):
        for f in V1_FEATURES:
            if f == changed_feature:
                continue
            if "insufficient_funds_pct" in f:
                upd(f, max(0.0, min(1.0, cur(f) - delta * 0.6)),
                    "Higher approval rate means fewer insufficient-fund declines")
            elif "charge_declined_insufficient_funds_pct" in f:
                upd(f, max(0.0, min(1.0, cur(f) - delta * 0.5)),
                    "Higher approval rate reduces the declined charge rate")
            elif f.startswith("retry_charge_pct_"):
                upd(f, max(0.0, min(1.0, cur(f) - delta * 0.4)),
                    "Higher approval rate reduces the need for payment retries")

    # --- Retry charge rate changed ---
    elif changed_feature.startswith("retry_charge_pct_"):
        for f in V1_FEATURES:
            if f == changed_feature:
                continue
            if f.startswith("charge_approved_pct_"):
                upd(f, max(0.0, min(1.0, cur(f) - delta)),
                    "More retried charges = fewer first-attempt approvals")
            elif f.startswith("retry_charge_amt_") and ("sum" in f or "max" in f):
                ratio = (new_value / old_value) if old_value > 1e-9 else 1.5
                upd(f, max(0.0, cur(f) * min(ratio, 3.0)),
                    "Retry amount scales proportionally with retry rate")

    # --- On-time payment sum changed ---
    elif changed_feature.startswith("ontime_amt_sum_"):
        ratio = (new_value / old_value) if old_value > 1e-9 else 1.0 + delta / max(avg_installment * 12, 1)
        ratio = max(0.1, min(ratio, 5.0))
        for f in V1_FEATURES:
            if f == changed_feature:
                continue
            if f.startswith("paid_amt_sum"):
                upd(f, max(0.0, cur(f) * min(ratio, 2.0)),
                    "On-time payments are included in total paid amounts")
            elif f.startswith("late_amt_sum"):
                upd(f, max(0.0, cur(f) * max(0.3, 2.0 - ratio)),
                    "More on-time payments means fewer late payments")
            elif f.startswith("charge_approved_pct_"):
                upd(f, min(1.0, cur(f) + (ratio - 1.0) * 0.08),
                    "Consistent on-time payments improve charge approval rate")

    # --- Credit utilization changed ---
    elif changed_feature == "credit_utilization_lag1d":
        for f in V1_FEATURES:
            if f == changed_feature:
                continue
            if f == "outstanding_amt_lag1d" and old_value > 1e-9:
                upd(f, max(0.0, cur(f) * (new_value / old_value)),
                    "Credit utilization directly reflects outstanding balance")
            elif "pr_approved_declined_amt_ratio" in f:
                upd(f, max(0.0, cur(f) * max(0.1, 1.5 - new_value)),
                    "Higher utilization leaves less credit room for new purchases")

    # --- Outstanding amount changed ---
    elif changed_feature == "outstanding_amt_lag1d" and old_value > 1e-9:
        for f in V1_FEATURES:
            if f == changed_feature:
                continue
            if f == "credit_utilization_lag1d":
                upd(f, min(1.0, cur(f) * min(new_value / old_value, 2.0)),
                    "Outstanding balance drives credit utilization")

    return list(results.values())


# ---------------------------------------------------------------------------
# Preset scenarios
# ---------------------------------------------------------------------------

def scenario_single_delinquency(profile: pd.DataFrame, amount: float = 200.0) -> pd.DataFrame:
    p = profile.copy()
    for f in [f for f in V1_FEATURES if f.startswith("delinquent_cnt")]:
        p[f] = p[f] + 1
    for f in [f for f in V1_FEATURES if "delinquent_amt" in f and ("sum" in f or "max" in f)]:
        p[f] = p[f] + amount
    for f in [f for f in V1_FEATURES if "delinquent_amt_avg" in f]:
        p[f] = p[f] + amount * 0.5
    for f in [f for f in V1_FEATURES if "insufficient_funds_pct" in f]:
        p[f] = np.minimum(p[f] + 0.15, 1.0)
    for f in [f for f in V1_FEATURES if "insufficient_funds_amt" in f]:
        p[f] = p[f] + amount
    return p


def scenario_miss_one_installment(profile: pd.DataFrame, order_amount: float = 120.0, num_installments: int = 4) -> pd.DataFrame:
    """Customer misses a single installment payment."""
    installment = order_amount / num_installments
    return scenario_single_delinquency(profile, amount=installment)


def scenario_miss_entire_order(profile: pd.DataFrame, order_amount: float = 120.0) -> pd.DataFrame:
    """Customer misses all installments — the full order goes delinquent."""
    return scenario_single_delinquency(profile, amount=order_amount)


def scenario_cure_delinquencies(profile: pd.DataFrame) -> pd.DataFrame:
    p = profile.copy()
    for f in [f for f in V1_FEATURES if f.startswith("delinquent_")]:
        p[f] = 0.0
    for f in [f for f in V1_FEATURES if f.startswith("ontime_amt_")]:
        p[f] = p[f] * 1.5
    for f in [f for f in V1_FEATURES if f.startswith("paid_amt_sum")]:
        p[f] = p[f] * 1.3
    return p


def scenario_missed_retries(profile: pd.DataFrame, retry_pct_increase: float = 0.3) -> pd.DataFrame:
    p = profile.copy()
    for f in [f for f in V1_FEATURES if f.startswith("retry_charge_pct_")]:
        p[f] = np.minimum(p[f] + retry_pct_increase, 1.0)
    for f in [f for f in V1_FEATURES if f.startswith("charge_approved_pct_")]:
        p[f] = np.maximum(p[f] - retry_pct_increase, 0.0)
    for f in [f for f in V1_FEATURES if f.startswith("retry_charge_amt_")]:
        p[f] = p[f] * 1.5
    return p


def scenario_consistent_ontime(profile: pd.DataFrame, months: int = 3) -> pd.DataFrame:
    p = profile.copy()
    multiplier = 1.0 + 0.15 * months
    for f in [f for f in V1_FEATURES if f.startswith("ontime_amt_")]:
        p[f] = p[f] * multiplier
    for f in [f for f in V1_FEATURES if f.startswith(("paid_amt_sum", "paid_amt_max", "paid_cnt_"))]:
        p[f] = p[f] * multiplier
    for f in [f for f in V1_FEATURES if f.startswith("early_amt_")]:
        p[f] = p[f] * (1.0 + 0.1 * months)
    for f in [f for f in V1_FEATURES if f.startswith(("late_amt_", "late_cnt_"))]:
        p[f] = p[f] * max(0.3, 1.0 - 0.2 * months)
    return p


def scenario_new_card(profile: pd.DataFrame) -> pd.DataFrame:
    p = profile.copy()
    for f in [f for f in V1_FEATURES if f.startswith("card_added_cnt_")]:
        p[f] = p[f] + 1
    for f in [f for f in V1_FEATURES if f.startswith("payment_card_first_use_cnt_")]:
        p[f] = p[f] + 1
    for f in [f for f in V1_FEATURES if f.startswith("payment_card_cnt_")]:
        p[f] = p[f] + 1
    if "card_active_cnt_lag1d" in V1_FEATURES:
        p["card_active_cnt_lag1d"] = p["card_active_cnt_lag1d"] + 1
    return p


def scenario_place_new_order(profile: pd.DataFrame, order_amount: float = 120.0, num_installments: int = 4) -> pd.DataFrame:
    """Customer places a new BNPL order — adds to outstanding balance and credit utilization."""
    p = profile.copy()
    installment = order_amount / num_installments
    for f in [f for f in V1_FEATURES if f.startswith("pr_cnt_")]:
        p[f] = p[f] + 1
    for f in [f for f in V1_FEATURES if "pr_approved_amt_sum" in f]:
        p[f] = p[f] + order_amount
    for f in [f for f in V1_FEATURES if "transact_order_amt_sum" in f]:
        p[f] = p[f] + order_amount
    for f in [f for f in V1_FEATURES if "transact_checkout_order_cnt" in f]:
        p[f] = p[f] + 1
    if "outstanding_amt_lag1d" in V1_FEATURES:
        p["outstanding_amt_lag1d"] = p["outstanding_amt_lag1d"] + order_amount
    if "credit_utilization_lag1d" in V1_FEATURES:
        p["credit_utilization_lag1d"] = np.minimum(p["credit_utilization_lag1d"] + 0.12, 1.0)
    for f in [f for f in V1_FEATURES if f.startswith("due_amt_")]:
        p[f] = p[f] + installment
    for f in [f for f in V1_FEATURES if "transact_order_amt_max" in f]:
        p[f] = np.maximum(p[f], order_amount)
    return p


def scenario_payment_extension(profile: pd.DataFrame, order_amount: float = 120.0, num_installments: int = 4) -> pd.DataFrame:
    """Customer requests a payment extension — defers one installment."""
    p = profile.copy()
    for f in [f for f in V1_FEATURES if "transact_extension_request_cnt" in f]:
        p[f] = p[f] + 1
    installment = order_amount / num_installments
    for f in [f for f in V1_FEATURES if f.startswith("late_amt_") and "sum" in f]:
        p[f] = p[f] + installment * 0.5
    return p


def scenario_card_declined_insufficient_funds(profile: pd.DataFrame, order_amount: float = 120.0, num_declines: int = 3) -> pd.DataFrame:
    """Customer's card is declined N times due to insufficient funds."""
    p = profile.copy()
    for f in [f for f in V1_FEATURES if "insufficient_funds_pct" in f]:
        p[f] = np.minimum(p[f] + 0.12 * num_declines, 1.0)
    for f in [f for f in V1_FEATURES if f.startswith("charge_approved_pct_")]:
        p[f] = np.maximum(p[f] - 0.08 * num_declines, 0.0)
    for f in [f for f in V1_FEATURES if "insufficient_funds_amt" in f and "sum" in f]:
        p[f] = p[f] + order_amount * num_declines
    for f in [f for f in V1_FEATURES if f.startswith("retry_charge_pct_")]:
        p[f] = np.minimum(p[f] + 0.10 * num_declines, 1.0)
    return p


def scenario_credit_utilization_maxed(profile: pd.DataFrame) -> pd.DataFrame:
    """Customer's credit limit is nearly exhausted — constrains new purchases."""
    p = profile.copy()
    if "credit_utilization_lag1d" in V1_FEATURES:
        p["credit_utilization_lag1d"] = 1.0
    if "outstanding_amt_lag1d" in V1_FEATURES:
        p["outstanding_amt_lag1d"] = p["outstanding_amt_lag1d"] * 2.5
    for f in [f for f in V1_FEATURES if "pr_approved_declined_amt_ratio" in f]:
        p[f] = p[f] * 0.3
    for f in [f for f in V1_FEATURES if f.startswith("pr_declined_cnt_")]:
        p[f] = p[f] + 2
    return p


def scenario_no_orders_60_days(profile: pd.DataFrame) -> pd.DataFrame:
    """Customer has placed no new orders in 60 days — early churn signal."""
    p = profile.copy()
    if "days_since_last_order_lag1d" in V1_FEATURES:
        p["days_since_last_order_lag1d"] = 60.0
    for f in [f for f in V1_FEATURES if f.startswith("pr_cnt_") and any(w in f for w in ["7d", "14d", "30d"])]:
        p[f] = 0.0
    return p


def scenario_pay_one_installment(profile: pd.DataFrame, order_amount: float = 120.0, num_installments: int = 4) -> pd.DataFrame:
    """Customer pays back one delinquent installment."""
    installment = order_amount / num_installments
    p = profile.copy()
    for f in [f for f in V1_FEATURES if f.startswith("delinquent_cnt")]:
        p[f] = np.maximum(p[f] - 1, 0.0)
    for f in [f for f in V1_FEATURES if "delinquent_amt" in f and "sum" in f]:
        p[f] = np.maximum(p[f] - installment, 0.0)
    for f in [f for f in V1_FEATURES if f.startswith("paid_amt_sum")]:
        p[f] = p[f] + installment
    for f in [f for f in V1_FEATURES if f.startswith("charge_approved_pct_")]:
        p[f] = np.minimum(p[f] + 0.04, 1.0)
    if "outstanding_amt_lag1d" in V1_FEATURES:
        p["outstanding_amt_lag1d"] = np.maximum(p["outstanding_amt_lag1d"] - installment, 0.0)
    return p


def scenario_pay_all_delinquent(profile: pd.DataFrame) -> pd.DataFrame:
    """Customer settles entire delinquent balance — full cure."""
    p = profile.copy()
    for f in [f for f in V1_FEATURES if f.startswith("delinquent_")]:
        p[f] = 0.0
    if "outstanding_amt_lag1d" in V1_FEATURES:
        outstanding = float(p["outstanding_amt_lag1d"].iloc[0])
        for f in [f for f in V1_FEATURES if f.startswith("paid_amt_sum")]:
            p[f] = p[f] + outstanding
        p["outstanding_amt_lag1d"] = 0.0
    if "credit_utilization_lag1d" in V1_FEATURES:
        p["credit_utilization_lag1d"] = np.maximum(p["credit_utilization_lag1d"] * 0.4, 0.0)
    for f in [f for f in V1_FEATURES if f.startswith("charge_approved_pct_")]:
        p[f] = np.minimum(p[f] + 0.15, 1.0)
    for f in [f for f in V1_FEATURES if "insufficient_funds_pct" in f]:
        p[f] = np.maximum(p[f] * 0.3, 0.0)
    for f in [f for f in V1_FEATURES if f.startswith("retry_charge_pct_")]:
        p[f] = np.maximum(p[f] * 0.4, 0.0)
    return p


def scenario_partial_payback(profile: pd.DataFrame, order_amount: float = 120.0, num_installments: int = 4) -> pd.DataFrame:
    """Customer pays back half their delinquent balance."""
    half = order_amount / 2
    p = profile.copy()
    for f in [f for f in V1_FEATURES if f.startswith("delinquent_cnt")]:
        p[f] = np.maximum(p[f] * 0.5, 0.0)
    for f in [f for f in V1_FEATURES if "delinquent_amt" in f and "sum" in f]:
        p[f] = np.maximum(p[f] * 0.5, 0.0)
    for f in [f for f in V1_FEATURES if f.startswith("paid_amt_sum")]:
        p[f] = p[f] + half
    for f in [f for f in V1_FEATURES if f.startswith("charge_approved_pct_")]:
        p[f] = np.minimum(p[f] + 0.08, 1.0)
    if "outstanding_amt_lag1d" in V1_FEATURES:
        p["outstanding_amt_lag1d"] = np.maximum(p["outstanding_amt_lag1d"] - half, 0.0)
    return p


PRESET_SCENARIOS: dict[str, dict[str, Any]] = {
    # Risk increasing
    "Miss one installment": {
        "fn": scenario_miss_one_installment,
        "params": {"order_amount": 120.0, "num_installments": 4},
        "param_defs": [
            {"name": "order_amount", "label": "Order amount ($)", "type": "number", "default": 120.0, "min": 1},
            {"name": "num_installments", "label": "No. of installments", "type": "integer", "default": 4, "min": 1},
        ],
        "description": "Customer misses one installment ($30 on a $120/4-installment order)",
        "category": "Risk ↑",
    },
    "Miss entire order": {
        "fn": scenario_miss_entire_order,
        "params": {"order_amount": 120.0},
        "param_defs": [
            {"name": "order_amount", "label": "Order amount ($)", "type": "number", "default": 120.0, "min": 1},
        ],
        "description": "All installments of one order go delinquent (full order missed)",
        "category": "Risk ↑",
    },
    "Missed retry charges": {
        "fn": scenario_missed_retries,
        "params": {"retry_pct_increase": 0.3},
        "param_defs": [
            {"name": "retry_pct_increase", "label": "Retry rate increase (0–1)", "type": "number", "default": 0.3, "min": 0, "max": 1},
        ],
        "description": "Payment retries start failing — bank account under stress",
        "category": "Risk ↑",
    },
    "Card declined 3× (insufficient funds)": {
        "fn": scenario_card_declined_insufficient_funds,
        "params": {"order_amount": 120.0, "num_declines": 3},
        "param_defs": [
            {"name": "order_amount", "label": "Order amount ($)", "type": "number", "default": 120.0, "min": 1},
            {"name": "num_declines", "label": "Number of declines", "type": "integer", "default": 3, "min": 1},
        ],
        "description": "Card is declined multiple times — signals insufficient funds",
        "category": "Risk ↑",
    },
    "Payment extension request": {
        "fn": scenario_payment_extension,
        "params": {"order_amount": 120.0, "num_installments": 4},
        "param_defs": [
            {"name": "order_amount", "label": "Order amount ($)", "type": "number", "default": 120.0, "min": 1},
            {"name": "num_installments", "label": "No. of installments", "type": "integer", "default": 4, "min": 1},
        ],
        "description": "Customer requests to defer one installment — cash flow stress signal",
        "category": "Risk ↑",
    },
    "Credit utilization maxed out": {
        "fn": scenario_credit_utilization_maxed,
        "params": {},
        "param_defs": [],
        "description": "Credit limit nearly exhausted — constrains new purchase approvals",
        "category": "Risk ↑",
    },
    "No orders for 60 days": {
        "fn": scenario_no_orders_60_days,
        "params": {},
        "param_defs": [],
        "description": "Customer hasn't placed a new order in 60 days — early churn signal",
        "category": "Activity",
    },
    "Place a new order": {
        "fn": scenario_place_new_order,
        "params": {"order_amount": 120.0, "num_installments": 4},
        "param_defs": [
            {"name": "order_amount", "label": "Order amount ($)", "type": "number", "default": 120.0, "min": 1},
            {"name": "num_installments", "label": "No. of installments", "type": "integer", "default": 4, "min": 1},
        ],
        "description": "Customer places a new BNPL order — adds to outstanding balance",
        "category": "Activity",
    },
    # Risk decreasing / recovery
    "Pay back one installment": {
        "fn": scenario_pay_one_installment,
        "params": {"order_amount": 120.0, "num_installments": 4},
        "param_defs": [
            {"name": "order_amount", "label": "Order amount ($)", "type": "number", "default": 120.0, "min": 1},
            {"name": "num_installments", "label": "No. of installments", "type": "integer", "default": 4, "min": 1},
        ],
        "description": "Customer pays one missed installment — partial recovery signal",
        "category": "Risk ↓",
    },
    "Pay back half the delinquent balance": {
        "fn": scenario_partial_payback,
        "params": {"order_amount": 120.0, "num_installments": 4},
        "param_defs": [
            {"name": "order_amount", "label": "Order amount ($)", "type": "number", "default": 120.0, "min": 1},
            {"name": "num_installments", "label": "No. of installments", "type": "integer", "default": 4, "min": 1},
        ],
        "description": "Customer settles half of their delinquent balance",
        "category": "Risk ↓",
    },
    "Settle all delinquent balance": {
        "fn": scenario_pay_all_delinquent,
        "params": {},
        "param_defs": [],
        "description": "Customer clears entire delinquent balance — full cure scenario",
        "category": "Risk ↓",
    },
    "Cure all delinquencies": {
        "fn": scenario_cure_delinquencies,
        "params": {},
        "param_defs": [],
        "description": "All delinquencies resolved; on-time and paid amounts improve",
        "category": "Risk ↓",
    },
    "Consistent on-time payments (3 months)": {
        "fn": scenario_consistent_ontime,
        "params": {"months": 3},
        "param_defs": [
            {"name": "months", "label": "Months of on-time payments", "type": "integer", "default": 3, "min": 1},
        ],
        "description": "Customer pays every installment on time for N months",
        "category": "Risk ↓",
    },
    "New card added": {
        "fn": scenario_new_card,
        "params": {},
        "param_defs": [],
        "description": "Customer adds a fresh payment card — signals access to new credit",
        "category": "Risk ↓",
    },
}
