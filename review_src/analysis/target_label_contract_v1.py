from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from decimal import Decimal
from typing import Any, Mapping


TARGET_LABEL_CONTRACT_VERSION = "TargetLabelContractV1"
OFFICIAL_ADJUSTMENT_BASIS = "official_adjusted"
TARGET_CLASSES = {
    "next_open_gap": ("up", "flat", "down"),
    "continuation_reversal": ("continuation", "neutral", "reversal"),
    "next_close_direction": ("up", "flat", "down"),
}
HIGH_CONFIDENCE_THRESHOLD = 0.70
LARGE_MOVE_THRESHOLD = 0.03
MATERIAL_DIRECTION_MISS_THRESHOLD = 0.20


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _valid_date(value: Any) -> str | None:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def target_sample_id(
    *,
    stock_code: str,
    prediction_trade_date: str,
    analysis_cutoff: str,
    regime: str,
) -> str:
    code = str(stock_code or "").strip()
    prediction_date = _valid_date(prediction_trade_date)
    cutoff = str(analysis_cutoff or "").strip()
    normalized_regime = str(regime or "").strip()
    if not re.fullmatch(r"\d{4}", code):
        raise ValueError("stock_code must be four digits")
    if prediction_date is None:
        raise ValueError("prediction_trade_date must be an ISO date")
    if not cutoff:
        raise ValueError("analysis_cutoff is required")
    if normalized_regime not in {"normal", "material_event"}:
        raise ValueError("regime is invalid")
    identity = {
        "contract_version": TARGET_LABEL_CONTRACT_VERSION,
        "stock_code": code,
        "prediction_trade_date": prediction_date,
        "analysis_cutoff": cutoff,
        "regime": normalized_regime,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"target-v1-{digest}"


def derive_target_labels(
    *,
    t_close: Any,
    next_open: Any,
    next_close: Any,
    adjustment_basis: str,
    t_quality_status: str = "ok",
    next_quality_status: str = "ok",
    suspended: bool = False,
    no_trade: bool = False,
) -> dict[str, Any]:
    """Derive the three additive labels from official adjusted T/T+1 prices."""

    close_t = _finite_positive(t_close)
    open_next = _finite_positive(next_open)
    close_next = _finite_positive(next_close)
    reasons: list[str] = []
    if str(adjustment_basis or "") != OFFICIAL_ADJUSTMENT_BASIS:
        reasons.append("adjustment_basis_is_not_official_adjusted")
    if str(t_quality_status or "") != "ok" or str(next_quality_status or "") != "ok":
        reasons.append("official_price_quality_failed")
    if suspended:
        reasons.append("next_session_suspended")
    if no_trade:
        reasons.append("next_session_has_no_trade")
    if close_t is None or open_next is None or close_next is None:
        reasons.append("required_price_is_unavailable")
    if reasons:
        unavailable = {
            key: {
                "label": None,
                "eligible": False,
                "quality_status": "unavailable",
                "availability_reason": ",".join(dict.fromkeys(reasons)),
            }
            for key in TARGET_CLASSES
        }
        return {
            "contract_version": TARGET_LABEL_CONTRACT_VERSION,
            "quality_status": "unavailable",
            "reasons": list(dict.fromkeys(reasons)),
            "targets": unavailable,
        }

    assert close_t is not None and open_next is not None and close_next is not None
    close_t_decimal = Decimal(str(close_t))
    open_next_decimal = Decimal(str(open_next))
    close_next_decimal = Decimal(str(close_next))
    open_gap_decimal = open_next_decimal / close_t_decimal - Decimal("1")
    open_to_close_decimal = close_next_decimal / open_next_decimal - Decimal("1")
    close_change_decimal = close_next_decimal / close_t_decimal - Decimal("1")
    open_gap = float(open_gap_decimal)
    open_to_close = float(open_to_close_decimal)
    close_change = float(close_change_decimal)

    open_label = "up" if open_gap_decimal >= Decimal("0.01") else "down" if open_gap_decimal <= Decimal("-0.01") else "flat"
    close_label = "up" if close_change_decimal >= Decimal("0.01") else "down" if close_change_decimal <= Decimal("-0.01") else "flat"

    continuation_label: str | None = None
    continuation_reason: str | None = None
    if open_gap_decimal >= Decimal("0.01"):
        continuation_label = (
            "continuation"
            if open_to_close_decimal >= Decimal("0.005")
            else "reversal"
            if open_to_close_decimal <= Decimal("-0.005")
            else "neutral"
        )
    elif open_gap_decimal <= Decimal("-0.01"):
        continuation_label = (
            "continuation"
            if open_to_close_decimal <= Decimal("-0.005")
            else "reversal"
            if open_to_close_decimal >= Decimal("0.005")
            else "neutral"
        )
    else:
        continuation_reason = "absolute_open_gap_below_one_percent"

    targets = {
        "next_open_gap": {
            "label": open_label,
            "eligible": True,
            "quality_status": "ok",
            "availability_reason": None,
            "realized_change": open_gap,
            "large_move": abs(open_gap_decimal) >= Decimal(str(LARGE_MOVE_THRESHOLD)),
        },
        "continuation_reversal": {
            "label": continuation_label,
            "eligible": continuation_label is not None,
            "quality_status": "ok" if continuation_label is not None else "unavailable",
            "availability_reason": continuation_reason,
            "realized_change": open_to_close if continuation_label is not None else None,
            "large_move": False,
        },
        "next_close_direction": {
            "label": close_label,
            "eligible": True,
            "quality_status": "ok",
            "availability_reason": None,
            "realized_change": close_change,
            "large_move": abs(close_change_decimal) >= Decimal(str(LARGE_MOVE_THRESHOLD)),
        },
    }
    return {
        "contract_version": TARGET_LABEL_CONTRACT_VERSION,
        "quality_status": "ok",
        "reasons": [],
        "prices": {
            "t_close": close_t,
            "next_open": open_next,
            "next_close": close_next,
            "adjustment_basis": OFFICIAL_ADJUSTMENT_BASIS,
        },
        "changes": {
            "next_open_gap": open_gap,
            "open_to_close": open_to_close,
            "next_close_direction": close_change,
        },
        "targets": targets,
    }


def build_outcome_rows(
    *,
    sample_id: str,
    stock_code: str,
    prediction_trade_date: str,
    outcome_trade_date: str | None,
    outcome_revision: str,
    available_at: str,
    recorded_at: str,
    labels: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project a derived label result into the Stage 1 outcome repository contract."""

    targets = labels.get("targets") if isinstance(labels.get("targets"), Mapping) else {}
    prices = labels.get("prices") if isinstance(labels.get("prices"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    for target_key in TARGET_CLASSES:
        target = targets.get(target_key) if isinstance(targets.get(target_key), Mapping) else {}
        rows.append(
            {
                "sample_id": str(sample_id),
                "target_key": target_key,
                "outcome_revision": str(outcome_revision),
                "stock_code": str(stock_code),
                "prediction_trade_date": str(prediction_trade_date),
                "outcome_trade_date": str(outcome_trade_date or "") or None,
                "label": target.get("label"),
                "t_close": prices.get("t_close"),
                "next_open": prices.get("next_open"),
                "next_close": prices.get("next_close"),
                "adjustment_basis": str(prices.get("adjustment_basis") or OFFICIAL_ADJUSTMENT_BASIS),
                "quality_status": str(target.get("quality_status") or "unavailable"),
                "availability_reason": target.get("availability_reason"),
                "available_at": str(available_at),
                "recorded_at": str(recorded_at),
            }
        )
    return rows
