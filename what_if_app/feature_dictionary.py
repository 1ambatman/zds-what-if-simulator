"""Feature descriptions — from bundled CSV or a configured Delta table."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

_DATA_CSV = Path(__file__).resolve().parent / "data" / "unified_rcm_v1_features.csv"
_logger = logging.getLogger(__name__)

_cache: dict[str, str] | None = None


def get_feature_descriptions() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    _cache = _load_descriptions()
    return _cache


def reload_descriptions() -> None:
    global _cache
    _cache = None


def _load_descriptions() -> dict[str, str]:
    from what_if_app.config import settings

    table = (settings.feature_dictionary_table or "").strip()
    if table:
        try:
            from what_if_app.databricks_io import fetch_feature_dictionary_from_table

            result = fetch_feature_dictionary_from_table(table)
            if result:
                _logger.info("Loaded %d feature descriptions from Delta table %s", len(result), table)
                return result
        except Exception as e:
            _logger.warning(
                "Could not load feature dictionary from %s: %s — falling back to CSV.", table, e
            )
    return _load_from_csv()


def _load_from_csv() -> dict[str, str]:
    if not _DATA_CSV.is_file():
        return {}
    out: dict[str, str] = {}
    with _DATA_CSV.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            feat = (row.get("Feature") or "").strip()
            desc = (row.get("Feature Description") or "").strip()
            if feat:
                out[feat] = desc
    return out


def description_for_feature(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    m = get_feature_descriptions()
    if raw in m:
        return (m[raw] or "").strip()
    low = raw.lower()
    for k, v in m.items():
        if k.lower() == low:
            return (v or "").strip()
    return ""
