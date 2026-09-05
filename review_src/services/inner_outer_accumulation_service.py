from __future__ import annotations

from statistics import mean
from typing import Any

from core.utils import parse_num
from repository.daily_inner_outer_volume_repository import (
    latest_daily_inner_outer_volume,
    normalize_stock_code,
    recent_daily_inner_outer_volume,
)


VALID_SIGNAL_STATUSES = {
    "unavailable",
    "normal",
    "conflict_watch",
    "possible_accumulation_weak",
    "possible_accumulation_medium",
    "possible_accumulation_strong",
    "rejected_distribution_risk",
}
VALID_SIGNAL_STRENGTHS = {"strong", "medium", "weak", "none"}


def _num(row: dict[str, Any], key: str) -> float | None:
    return parse_num(row.get(key))


def _none_if_invalid(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        return None
    return float(value)


def _ratio_from_volumes(inner: float | None, outer: float | None, total: float | None) -> tuple[float | None, float | None]:
    den = total if total and total > 0 else ((inner or 0) + (outer or 0))
    if not den:
        return None, None
    return round(float(inner or 0) / float(den), 4), round(float(outer or 0) / float(den), 4)


def _rsi_declining_three_days(rows_desc: list[dict[str, Any]]) -> bool | None:
    values: list[float] = []
    for row in rows_desc[:3]:
        value = _none_if_invalid(parse_num(row.get("rsi14")))
        if value is None:
            return None
        values.append(value)
    if len(values) < 3:
        return None
    latest, previous, oldest = values[0], values[1], values[2]
    return latest < previous < oldest


def unavailable_inner_outer_signal(reason: str = "no_authorized_inner_outer_source") -> dict[str, Any]:
    return {
        "available": False,
        "signal_status": "unavailable",
        "signal_strength": "none",
        "reasons": [],
        "risk_notes": [reason],
        "data_quality": "unavailable",
        "source": "not_configured",
        "updated_at": None,
        "trade_date": None,
        "observation_text": "內外盤資料不足，暫不判斷承接或吸籌訊號。",
        "source_status": "unavailable",
    }


def evaluate_inner_outer_accumulation(
    row: dict[str, Any] | None,
    *,
    recent_rows_desc: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not row:
        return unavailable_inner_outer_signal()
    recent_rows_desc = recent_rows_desc or []
    inner = _num(row, "inner_volume")
    outer = _num(row, "outer_volume")
    total = _num(row, "total_volume")
    total_check = _num(row, "total_volume_check")
    close = _num(row, "close_price")
    prev_close = _num(row, "prev_close_price")
    price_change_pct = _num(row, "price_change_pct")
    turnover_rate = _num(row, "turnover_rate")
    volume_ma20 = _num(row, "volume_ma20")
    volume_ma5 = _num(row, "volume_ma5")
    rsi14 = _num(row, "rsi14")
    chip = _num(row, "chip_concentration")
    chip_change = _num(row, "chip_concentration_change")
    inner_ratio, outer_ratio = _ratio_from_volumes(inner, outer, total)
    reasons: list[str] = []
    risk_notes: list[str] = []
    quality = str(row.get("data_quality") or "ok")
    source = str(row.get("source") or "unknown")

    if inner is None or outer is None or total is None or inner <= 0 or outer <= 0 or total <= 0:
        return {
            **unavailable_inner_outer_signal("missing_inner_outer_volume"),
            "trade_date": row.get("trade_date"),
            "data_quality": "unavailable",
            "source": source,
            "updated_at": row.get("updated_at"),
        }
    if total_check and abs((inner + outer) - total_check) / total_check > 0.03:
        quality = "partial"
        risk_notes.append("inner_outer_total_mismatch_gt_3pct")
    if volume_ma20 is None:
        quality = "insufficient_history"
        risk_notes.append("volume_ma20_unavailable")
    if rsi14 is None:
        risk_notes.append("rsi14_unavailable")
    if chip is None:
        risk_notes.append("chip_concentration_unavailable")

    rsi_declining = _rsi_declining_three_days(recent_rows_desc)
    if rsi_declining is None:
        risk_notes.append("rsi14_three_day_history_insufficient")

    rejected = False
    if price_change_pct is not None and price_change_pct > 5:
        rejected = True
        risk_notes.append("price_change_gt_5pct")
    if turnover_rate is not None and turnover_rate > 15:
        rejected = True
        risk_notes.append("turnover_rate_gt_15pct")
    if rsi14 is not None and rsi14 < 25:
        rejected = True
        risk_notes.append("rsi14_below_25")
    if rsi_declining is True:
        rejected = True
        risk_notes.append("rsi14_declining_three_days")
    if chip_change is not None and chip_change < 0:
        rejected = True
        risk_notes.append("chip_concentration_declining")
    if rejected:
        return {
            "available": True,
            "signal_status": "rejected_distribution_risk",
            "signal_strength": "none",
            "reasons": reasons,
            "risk_notes": risk_notes,
            "data_quality": quality,
            "source": source,
            "updated_at": row.get("updated_at"),
            "trade_date": row.get("trade_date"),
            "inner_ratio": inner_ratio,
            "outer_ratio": outer_ratio,
            "observation_text": "內外盤條件互相衝突，暫不視為承接訊號。",
        }

    candidate = (
        inner > outer
        and inner_ratio is not None
        and inner_ratio >= 0.55
        and close is not None
        and prev_close is not None
        and close >= prev_close
        and price_change_pct is not None
        and 0 <= price_change_pct <= 3
        and rsi14 is not None
        and 30 <= rsi14 <= 45
        and rsi_declining is False
    )
    if not candidate:
        status = "conflict_watch" if inner > outer and inner_ratio and inner_ratio >= 0.55 else "normal"
        return {
            "available": True,
            "signal_status": status,
            "signal_strength": "none",
            "reasons": reasons,
            "risk_notes": risk_notes,
            "data_quality": quality,
            "source": source,
            "updated_at": row.get("updated_at"),
            "trade_date": row.get("trade_date"),
            "inner_ratio": inner_ratio,
            "outer_ratio": outer_ratio,
            "observation_text": "內外盤暫無明確承接訊號。",
        }

    reasons.append("inner_volume_above_outer_volume")
    reasons.append("inner_ratio_at_least_55pct")
    reasons.append("price_stable_with_mid_low_rsi")
    strength = "weak"
    status = "possible_accumulation_weak"
    if chip is not None and volume_ma20 is not None:
        if total >= volume_ma20 * 1.2:
            strength = "strong"
            status = "possible_accumulation_strong"
        elif total >= volume_ma20 * 0.8:
            strength = "medium"
            status = "possible_accumulation_medium"
    elif chip is None:
        risk_notes.append("chip_concentration_missing_caps_strength_to_weak")
    if volume_ma20 is not None and total < volume_ma20 * 0.5:
        status = "conflict_watch"
        strength = "none"
        risk_notes.append("volume_below_half_ma20")

    text_map = {
        "possible_accumulation_strong": "量價結構補充：內盤占比偏高、股價仍守穩，且量能與籌碼條件同步，屬於較明確的承接觀察訊號。",
        "possible_accumulation_medium": "量價結構補充：內盤量大於外盤量且股價未明顯轉弱，初步顯示下方承接力道。",
        "possible_accumulation_weak": "量價結構補充：內盤占比偏高但量能或籌碼確認度不足，僅作輔助觀察。",
        "conflict_watch": "內外盤觀察：目前內外盤與股價表現不一致，暫列矛盾訊號。",
    }
    return {
        "available": True,
        "signal_status": status,
        "signal_strength": strength,
        "reasons": reasons,
        "risk_notes": risk_notes,
        "data_quality": quality,
        "source": source,
        "updated_at": row.get("updated_at"),
        "trade_date": row.get("trade_date"),
        "inner_ratio": inner_ratio,
        "outer_ratio": outer_ratio,
        "inner_volume": inner,
        "outer_volume": outer,
        "total_volume": total,
        "volume_ma5": volume_ma5,
        "volume_ma20": volume_ma20,
        "rsi14": rsi14,
        "chip_concentration": chip,
        "chip_concentration_change": chip_change,
        "observation_text": text_map.get(status, "內外盤暫無明確承接訊號。"),
    }


def build_inner_outer_accumulation_payload(stock_code: str) -> dict[str, Any]:
    code = normalize_stock_code(stock_code)
    if not code:
        return unavailable_inner_outer_signal("invalid_stock_code")
    row = latest_daily_inner_outer_volume(code)
    if not row:
        return unavailable_inner_outer_signal("no_authorized_inner_outer_source")
    recent = recent_daily_inner_outer_volume(code, limit=20)
    return evaluate_inner_outer_accumulation(row, recent_rows_desc=recent)


def refresh_daily_inner_outer_volume_for_codes(
    codes: list[str],
    *,
    source: str = "none",
    dry_run: bool = True,
) -> dict[str, Any]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in codes:
        code = normalize_stock_code(raw)
        if code and code not in seen:
            seen.add(code)
            normalized.append(code)
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "writes_db": False,
        "source": source,
        "source_status": "unavailable",
        "message": "No authorized daily inner/outer volume source is configured; no DB rows were written.",
        "total": len(normalized),
        "write_count": 0,
        "unavailable_count": len(normalized),
        "signal_status": "unavailable",
        "signal_strength": "none",
        "codes": normalized,
    }


def average_total_volume(rows: list[dict[str, Any]]) -> float | None:
    nums = [parse_num(row.get("total_volume")) for row in rows]
    vals = [float(x) for x in nums if x is not None]
    return mean(vals) if vals else None
