from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.market_session import recent_market_date_for_eod
from core.utils import parse_num

def _clamp_num(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _prob_label(probability: float | None) -> str:
    if probability is None:
        return "資料不足"
    if probability >= 60:
        return "偏多"
    if probability <= 40:
        return "偏空"
    return "中性"


def _confidence_value(confidence: str | None) -> int:
    text = str(confidence or "").lower()
    if text in {"high", "高", "中高"} or "high" in text or "高" in text:
        return 3
    if text in {"medium", "中"} or "medium" in text or "中" in text:
        return 2
    if text in {"low", "低"} or "low" in text or "低" in text:
        return 1
    return 0


def _source_age_decay_from_date(date_text: Any) -> tuple[float, str, int | None]:
    date_str = str(date_text or "").strip()[:10]
    if not date_str:
        return 0.0, "missing", None
    try:
        source_date = date.fromisoformat(date_str)
        target_date = date.fromisoformat(recent_market_date_for_eod())
    except Exception:
        return 0.5, "unknown_date", None
    age_days = max(0, (target_date - source_date).days)
    if age_days <= 1:
        return 1.0, "fresh", age_days
    if age_days == 2:
        return 0.5, "stale_24h", age_days
    if age_days <= 4:
        return 0.2, "stale_48h", age_days
    return 0.0, "expired", age_days


def _source_age_decay_from_quote_time(us_forecast: dict[str, Any]) -> tuple[float, str, float | None]:
    times = []
    for item in us_forecast.get("top_contributors") or []:
        # top_contributors intentionally omits quote metadata; fall back to date
        # when the caller does not provide quote timestamps.
        pass
    quote_dates = []
    for item in us_forecast.get("_source_quotes") or []:
        ts = parse_num(item.get("regular_market_time"))
        if ts:
            times.append(float(ts))
        if item.get("date"):
            quote_dates.append(item.get("date"))
    if times:
        age_hours = max(0.0, (datetime.now(ZoneInfo("UTC")).timestamp() - max(times)) / 3600.0)
        if age_hours <= 24:
            return 1.0, "fresh", round(age_hours, 1)
        if age_hours <= 48:
            return 0.5, "stale_24h", round(age_hours, 1)
        if age_hours <= 96:
            return 0.2, "stale_48h", round(age_hours, 1)
        return 0.0, "expired", round(age_hours, 1)
    if quote_dates:
        return _source_age_decay_from_date(max(str(x)[:10] for x in quote_dates))
    return 0.5, "unknown_date", None


def _factor_payload(
    name: str,
    score: float,
    available: bool,
    confidence: str,
    source: str,
    date_value: Any,
    reason: str,
    *,
    decay: float | None = None,
    freshness: str | None = None,
    raw_score: float | None = None,
    score_basis: str = "rule_score",
) -> dict[str, Any]:
    if decay is None or freshness is None:
        decay, freshness, _ = _source_age_decay_from_date(date_value)
    adjusted = float(score) * float(decay if available else 0.0)
    return {
        "name": name,
        "available": bool(available),
        "score": round(adjusted, 2),
        "raw_score": round(float(raw_score if raw_score is not None else score), 2),
        "decay": round(float(decay), 2),
        "freshness": freshness,
        "confidence": confidence,
        "source": source,
        "date": date_value,
        "reason": reason,
        "score_basis": score_basis,
    }


def factor_is_decision_usable(factor: dict[str, Any]) -> bool:
    """Return whether a factor may enter a directional score.

    ``available`` alone is insufficient: expired inputs have zero decay and a
    non-finite/missing score must never be converted into a neutral value.
    """

    score = parse_num(factor.get("score"))
    decay = parse_num(factor.get("decay"))
    return bool(
        factor.get("available")
        and score is not None
        and math.isfinite(float(score))
        and decay is not None
        and float(decay) > 0
    )


def weighted_available_score(
    factors: dict[str, dict[str, Any]],
    base_weights: dict[str, float],
    *,
    minimum_coverage_ratio: float,
    minimum_factor_count: int = 1,
    required_groups: tuple[tuple[str, ...], ...] = (),
) -> dict[str, Any]:
    """Compose only usable factors and expose the missing-data gate.

    Remaining weights are normalized only after minimum original-weight
    coverage and required factor groups pass. This prevents one surviving
    input from being silently amplified into a complete outlook.
    """

    positive_weights = {
        key: max(float(weight), 0.0)
        for key, weight in base_weights.items()
        if float(weight) > 0
    }
    declared_weight = sum(positive_weights.values())
    usable_keys = [
        key
        for key in positive_weights
        if factor_is_decision_usable(factors.get(key) or {})
    ]
    available_weight = sum(positive_weights[key] for key in usable_keys)
    coverage_ratio = available_weight / declared_weight if declared_weight else 0.0
    missing_factors = [key for key in positive_weights if key not in usable_keys]
    missing_required_groups = [
        list(group)
        for group in required_groups
        if not any(key in usable_keys for key in group)
    ]
    ready = bool(
        declared_weight > 0
        and coverage_ratio >= float(minimum_coverage_ratio)
        and len(usable_keys) >= max(int(minimum_factor_count), 1)
        and not missing_required_groups
    )
    effective_weights = (
        {key: positive_weights[key] / available_weight for key in usable_keys}
        if ready and available_weight > 0
        else {}
    )
    score = (
        sum(float(factors[key]["score"]) * effective_weights[key] for key in usable_keys)
        if ready
        else None
    )
    if ready:
        reason = "minimum factor coverage passed"
    elif missing_required_groups:
        reason = "required factor group unavailable"
    elif len(usable_keys) < max(int(minimum_factor_count), 1):
        reason = "insufficient available factor count"
    else:
        reason = "insufficient original-weight coverage"
    return {
        "available": ready,
        "score": round(float(score), 4) if score is not None else None,
        "coverage_ratio": round(float(coverage_ratio), 4),
        "minimum_coverage_ratio": round(float(minimum_coverage_ratio), 4),
        "available_factor_count": len(usable_keys),
        "minimum_factor_count": max(int(minimum_factor_count), 1),
        "usable_factors": usable_keys,
        "missing_factors": missing_factors,
        "missing_required_groups": missing_required_groups,
        "effective_weights": {
            key: round(value, 6) for key, value in effective_weights.items()
        },
        "reason": reason,
    }


def us_sentiment_factor(us_forecast: dict[str, Any], us_assets: list[dict[str, Any]]) -> dict[str, Any]:
    if not us_forecast.get("available"):
        return _factor_payload(
            "美股關聯情緒",
            0.0,
            False,
            "低",
            "Yahoo Finance chart",
            None,
            us_forecast.get("label") or "美股關聯報價不足",
            decay=0.0,
            freshness="missing",
        )
    source_quotes = [(x.get("quote") or {}) for x in us_assets or [] if (x.get("quote") or {}).get("ok")]
    us_forecast["_source_quotes"] = source_quotes
    decay, freshness, age = _source_age_decay_from_quote_time(us_forecast)
    sentiment_score = parse_num(us_forecast.get("sentiment_score"))
    if sentiment_score is None:
        return _factor_payload(
            "美股關聯情緒",
            0.0,
            False,
            "低",
            "Yahoo Finance chart / yfinance",
            max([str(q.get("date") or "")[:10] for q in source_quotes] or [None]),
            "海外報價存在，但尚無可用的定性情緒分數；未校準機率不以 50 補值",
            decay=0.0,
            freshness="missing_score",
            score_basis="unavailable",
        )
    raw_score = _clamp_num(sentiment_score, -35.0, 35.0)
    reason = f"加權美股 {us_forecast.get('weighted_change_pct')}%，正向權重 {us_forecast.get('positive_weight_ratio')}%"
    if freshness != "fresh":
        reason += f"；行情時效 {freshness}"
    return _factor_payload(
        "美股關聯情緒",
        raw_score,
        True,
        us_forecast.get("confidence") or "中",
        "Yahoo Finance chart / yfinance",
        max([str(q.get("date") or "")[:10] for q in source_quotes] or [None]),
        reason,
        decay=decay,
        freshness=freshness,
        raw_score=raw_score,
        score_basis="qualitative_sentiment_score",
    )


def futures_night_factor(futures_night: dict[str, Any]) -> dict[str, Any]:
    if not futures_night.get("available"):
        return _factor_payload(
            "台灣期貨夜盤",
            0.0,
            False,
            "低",
            futures_night.get("source") or "TAIFEX OpenAPI DailyMarketReportFut",
            futures_night.get("date"),
            futures_night.get("quality_reason") or futures_night.get("label") or "沒有可用官方盤後期貨資料",
            decay=0.0,
            freshness="missing",
        )
    raw_change = parse_num(futures_night.get("weighted_change_pct")) or 0.0
    confidence_mult = {"high": 1.0, "medium": 0.85, "low": 0.6}.get(str(futures_night.get("confidence") or "").lower(), 0.75)
    raw_score = _clamp_num(raw_change * 13.0 * confidence_mult, -40.0, 40.0)
    decay, freshness, _ = _source_age_decay_from_date(futures_night.get("date"))
    reason = f"官方期交所盤後加權 {raw_change:+.2f}%，納入 {futures_night.get('used_count', 0)} 個商品"
    return _factor_payload(
        "台灣期貨夜盤",
        raw_score,
        True,
        futures_night.get("confidence") or "中",
        futures_night.get("source") or "TAIFEX OpenAPI DailyMarketReportFut",
        futures_night.get("date"),
        reason,
        decay=decay,
        freshness=freshness,
        raw_score=raw_score,
    )

