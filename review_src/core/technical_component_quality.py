from __future__ import annotations

from typing import Any


def assess_technical_component_quality(
    *,
    input_row_count: int,
    warmup_rows: int,
    value_available: bool,
    latest_source_official: bool,
    recent_coverage: dict[str, Any] | None,
    full_ensemble_component: bool,
    full_ensemble_minimum_rows: int,
) -> dict[str, Any]:
    """Central V3 quality gate for persisted technical components."""

    if input_row_count < warmup_rows:
        status = "insufficient_history"
        reason = f"requires {warmup_rows} rows; found {input_row_count}"
    elif not value_available:
        status = "unavailable"
        reason = "formula result is null after complete warm-up"
    elif not latest_source_official:
        status = "fallback_source"
        reason = "latest OHLCV row is not an official TWSE/TPEx source"
    elif recent_coverage is not None and not recent_coverage.get("ready"):
        status = "stale_or_gapped"
        reason = (
            "recent official trading-date coverage failed: "
            f"{recent_coverage.get('reason') or 'unknown'}"
        )
    elif full_ensemble_component and input_row_count < full_ensemble_minimum_rows:
        status = "insufficient_history"
        reason = (
            f"full ensemble requires {full_ensemble_minimum_rows} rows; "
            f"found {input_row_count}"
        )
    else:
        status = "ok"
        reason = "calculated from ascending validated OHLCV under frozen V1 formula"
    return {
        "status": status,
        "decision_ready": status == "ok",
        "availability_reason": None if status == "ok" else status,
        "quality_reason": reason,
    }
