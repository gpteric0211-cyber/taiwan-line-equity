from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


SYSTEM_VERSION = "pv1.0.0"

VOLUME_CLUSTER_BASELINE = "median"
HIGH_VOLUME_MULTIPLIER = 1.5
MAX_CLUSTER_GAP_TICKS = 1
POC_TIE_BREAKER = "nearest_reference_price"


SOURCE_LEVELS = {
    "twse_official": 1,
    "finmind_tick": 2,
    "fugle_intraday_volumes": 3,
    "broker_authorized": 4,
    "ohlcv_estimated": 5,
    "ohlcv_reconstructed": 5,
}


def price_tick(price: float) -> float:
    """Taiwan stock regular-board tick size by price level."""
    p = float(price)
    if p < 10:
        return 0.01
    if p < 50:
        return 0.05
    if p < 100:
        return 0.1
    if p < 500:
        return 0.5
    if p < 1000:
        return 1.0
    return 5.0


def price_decimals(tick: float) -> int:
    s = f"{tick:.4f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def normalize_price(price: float) -> float:
    tick = price_tick(price)
    decimals = price_decimals(tick)
    return round(round(float(price) / tick) * tick, decimals)


def is_legal_price(price: float, tolerance: float = 1e-6) -> bool:
    p = float(price)
    return abs(normalize_price(p) - p) <= tolerance


def canonical_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_profile_rows(rows: list[dict[str, Any]], *, volume_unit: str = "shares") -> tuple[list[dict[str, float]], list[str]]:
    """Normalize source price-volume rows to legal Taiwan price bins and shares."""
    issues: list[str] = []
    factor = 1000.0 if str(volume_unit).lower() in {"lot", "lots", "張"} else 1.0
    bins: dict[float, float] = {}
    illegal = 0
    for row in rows:
        try:
            price = float(row.get("price"))
            volume = float(row.get("volume"))
        except Exception:
            issues.append("來源含無法解析的價格或成交量")
            continue
        if not math.isfinite(price) or not math.isfinite(volume) or price <= 0 or volume < 0:
            issues.append("來源含不合法價格或成交量")
            continue
        norm_price = normalize_price(price)
        if not is_legal_price(price):
            illegal += 1
        bins[norm_price] = bins.get(norm_price, 0.0) + volume * factor
    if illegal:
        issues.append(f"來源含 {illegal} 筆非標準 tick 價格，已轉入標準 price bin")
    out = [{"price": float(k), "volume": float(v)} for k, v in sorted(bins.items()) if v > 0]
    if not out:
        issues.append("標準化後無有效分價量資料")
    return out, issues


def merge_profiles(daily_profiles: list[list[dict[str, Any]]]) -> list[dict[str, float]]:
    bins: dict[float, float] = {}
    for profile in daily_profiles:
        for row in profile:
            price = normalize_price(float(row["price"]))
            volume = float(row["volume"])
            if volume > 0:
                bins[price] = bins.get(price, 0.0) + volume
    return [{"price": float(k), "volume": float(v)} for k, v in sorted(bins.items()) if v > 0]


def _median(values: list[float]) -> float | None:
    clean = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2.0


def _cluster_to_dict(cluster: list[dict[str, float]]) -> dict[str, Any] | None:
    if not cluster:
        return None
    prices = [float(r["price"]) for r in cluster]
    volumes = [float(r["volume"]) for r in cluster]
    total = sum(volumes)
    peak = max(cluster, key=lambda r: (float(r["volume"]), -abs(float(r["price"]) - (sum(prices) / len(prices)))))
    return {
        "low": min(prices),
        "high": max(prices),
        "price": float(peak["price"]),
        "volume": int(round(total)),
        "levels": len(cluster),
    }


def _split_adjacent_clusters(rows: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    clusters: list[list[dict[str, float]]] = []
    current: list[dict[str, float]] = []
    prev_price: float | None = None
    prev_tick: float | None = None
    for row in sorted(rows, key=lambda r: float(r["price"])):
        price = float(row["price"])
        tick = price_tick(price)
        if not current:
            current = [row]
        else:
            same_tick = abs(float(prev_tick or tick) - tick) <= 1e-9
            max_gap = tick * MAX_CLUSTER_GAP_TICKS
            adjacent = prev_price is not None and abs(price - prev_price) <= max_gap + 1e-9
            if same_tick and adjacent:
                current.append(row)
            else:
                clusters.append(current)
                current = [row]
        prev_price = price
        prev_tick = tick
    if current:
        clusters.append(current)
    return clusters


def analyze_volume_structure(profile: list[dict[str, Any]], reference_price: float | None) -> dict[str, Any]:
    """Analyze true price-volume rows without falling back to day high/low."""
    ref = float(reference_price) if reference_price is not None else None
    rows, issues = normalize_profile_rows(profile, volume_unit="lots")
    if not rows or ref is None or not math.isfinite(ref) or ref <= 0:
        return {
            "support_zone": None,
            "pressure_zone": None,
            "poc_price": None,
            "poc_volume": None,
            "source_status": "insufficient_volume_profile",
            "data_quality": "missing",
            "reason": "價量分布資料不足，未使用最高最低價假裝支撐賣壓",
            "issues": issues,
        }

    max_volume = max(float(r["volume"]) for r in rows)
    poc_candidates = [r for r in rows if abs(float(r["volume"]) - max_volume) <= 1e-9]
    if POC_TIE_BREAKER == "nearest_reference_price":
        poc = min(poc_candidates, key=lambda r: abs(float(r["price"]) - ref))
    else:
        poc = poc_candidates[0]

    median_volume = _median([float(r["volume"]) for r in rows])
    if not median_volume or median_volume <= 0:
        threshold = max_volume
        data_quality = "low"
    else:
        threshold = median_volume * HIGH_VOLUME_MULTIPLIER
        data_quality = "ok" if len(rows) >= 5 else "low"

    high_volume_rows = [r for r in rows if float(r["volume"]) >= threshold]
    clusters = _split_adjacent_clusters(high_volume_rows)
    support_clusters = [c for c in clusters if max(float(r["price"]) for r in c) < ref]
    pressure_clusters = [c for c in clusters if min(float(r["price"]) for r in c) > ref]

    support = None
    if support_clusters:
        support = min(support_clusters, key=lambda c: abs(ref - max(float(r["price"]) for r in c)))
    pressure = None
    if pressure_clusters:
        pressure = min(pressure_clusters, key=lambda c: abs(min(float(r["price"]) for r in c) - ref))

    reason = ""
    if not high_volume_rows:
        reason = "成交量未達高量群門檻，未形成明確支撐賣壓區"
        data_quality = "low"
    elif not support and not pressure:
        reason = "高量群集中於現價附近，未形成明確上下方區間"
    else:
        reason = "使用真實價量分布與成交量中位數門檻估算"

    return {
        "support_zone": _cluster_to_dict(support or []),
        "pressure_zone": _cluster_to_dict(pressure or []),
        "poc_price": float(poc["price"]),
        "poc_volume": int(round(float(poc["volume"]))),
        "source_status": "ok",
        "data_quality": data_quality,
        "reason": reason,
        "issues": issues,
        "threshold_volume": int(round(threshold)),
        "baseline": VOLUME_CLUSTER_BASELINE,
        "high_volume_multiplier": HIGH_VOLUME_MULTIPLIER,
    }


def score_position(distance_pct: float | None) -> int:
    if distance_pct is None:
        return 0
    if distance_pct >= 10:
        return 30
    if distance_pct >= 5:
        return 22
    if distance_pct >= -5:
        return 15
    if distance_pct >= -10:
        return 8
    return 0


def score_pressure(pressure_pct: float | None) -> int:
    if pressure_pct is None:
        return 0
    if pressure_pct < 15:
        return 25
    if pressure_pct < 25:
        return 20
    if pressure_pct < 40:
        return 12
    if pressure_pct < 55:
        return 5
    return 0


def score_concentration(concentration_pct: float | None) -> int:
    if concentration_pct is None:
        return 0
    if concentration_pct > 60:
        return 20
    if concentration_pct >= 45:
        return 15
    if concentration_pct >= 30:
        return 8
    return 3


def grade(total_score: int | float | None) -> str:
    s = float(total_score or 0)
    if s >= 80:
        return "A"
    if s >= 60:
        return "B"
    if s >= 40:
        return "C"
    if s >= 20:
        return "D"
    return "E"


def behavior_state(closes: list[float], peak_price: float) -> tuple[str, int]:
    if not closes or not peak_price:
        return "unknown", 0
    recent = [float(x) for x in closes[-5:] if x is not None and math.isfinite(float(x))]
    if len(recent) < 3:
        return "insufficient", 0
    above = [x > peak_price for x in recent]
    if len(recent) >= 5 and all(above[-3:]) and any(not x for x in above[:-3]):
        return "breakout_confirmed", 10
    if len(recent) >= 5 and all(above):
        return "stable_above_peak", 7
    if len(recent) >= 5 and all(not x for x in above[-3:]) and any(above[:-3]):
        return "breakdown_confirmed", -10
    if recent[-1] <= peak_price:
        return "below_or_consolidating", 0
    return "above_peak_unconfirmed", 0


def _nearest_peaks(profile: list[dict[str, float]], close: float, *, side: str, total_volume: float) -> list[dict[str, Any]]:
    if side == "support":
        rows = [r for r in profile if r["price"] < close]
    else:
        rows = [r for r in profile if r["price"] > close]
    rows.sort(key=lambda r: (-r["volume"], abs(r["price"] - close)))
    out = []
    for row in rows[:2]:
        pct = row["volume"] / total_volume * 100 if total_volume else 0.0
        distance = abs(row["price"] - close) / close * 100 if close else None
        if side == "support":
            if pct >= 15 and (distance or 999) <= 10:
                strength = "strong"
            elif pct >= 8 and (distance or 999) <= 20:
                strength = "medium"
            else:
                strength = "weak"
        else:
            if pct >= 15 and (distance or 999) <= 10:
                strength = "strong"
            elif pct >= 8 and (distance or 999) <= 20:
                strength = "medium"
            else:
                strength = "weak"
        out.append({
            "price": round(row["price"], 4),
            "volume_share_pct": round(pct, 2),
            "distance_pct": round(distance, 2) if distance is not None else None,
            "strength": strength,
        })
    return out


def evaluate_profile(
    *,
    profile: list[dict[str, Any]],
    close: float,
    closes: list[float],
    short_profile: list[dict[str, Any]] | None = None,
    long_profile: list[dict[str, Any]] | None = None,
    coverage_days: int = 0,
    required_days: int = 30,
    source_mix: dict[str, int] | None = None,
    quality_base: str = "high",
) -> dict[str, Any]:
    profile = [{"price": float(r["price"]), "volume": float(r["volume"])} for r in profile if float(r.get("volume") or 0) > 0]
    total_volume = sum(r["volume"] for r in profile)
    if not profile or total_volume <= 0:
        return {"available": False, "status": "source_parse_error", "quality": "invalid", "quality_reason": "無有效分價量資料"}
    if coverage_days < max(1, int(required_days * 0.8)):
        return {
            "available": False,
            "status": "accumulating",
            "quality": "accumulating",
            "quality_reason": f"有效分價資料僅 {coverage_days}/{required_days} 日，尚未啟用正式評分",
            "coverage_days": coverage_days,
            "required_days": required_days,
        }

    peak = max(profile, key=lambda r: r["volume"])
    main_peak_price = peak["price"]
    distance_pct = (close - main_peak_price) / main_peak_price * 100 if main_peak_price else None
    overhead_volume = sum(r["volume"] for r in profile if r["price"] > close)
    pressure_pct = overhead_volume / total_volume * 100
    top3 = sorted(profile, key=lambda r: r["volume"], reverse=True)[:3]
    concentration_pct = sum(r["volume"] for r in top3) / total_volume * 100
    weighted_cost = sum(r["price"] * r["volume"] for r in profile) / total_volume

    short_peak_pct = None
    long_peak_pct = None
    recent_strength_score = 3
    recent_strength_note = "近期量峰低於長期或資料不足"
    if short_profile:
        short_total = sum(float(r["volume"]) for r in short_profile)
        if short_total > 0:
            sp = max(short_profile, key=lambda r: float(r["volume"]))
            short_peak_pct = float(sp["volume"]) / short_total * 100
    if long_profile:
        long_total = sum(float(r["volume"]) for r in long_profile)
        if long_total > 0:
            lp = max(long_profile, key=lambda r: float(r["volume"]))
            long_peak_pct = float(lp["volume"]) / long_total * 100
    if short_peak_pct is not None and long_peak_pct is not None:
        if short_peak_pct > long_peak_pct * 1.05:
            recent_strength_score = 15
            recent_strength_note = "近期量峰比例高於長期主峰"
        elif short_peak_pct >= long_peak_pct * 0.8:
            recent_strength_score = 10
            recent_strength_note = "近期量峰接近長期水準"

    state, behavior_score = behavior_state(closes, main_peak_price)
    position_score = score_position(distance_pct)
    pressure_score = score_pressure(pressure_pct)
    concentration_score = score_concentration(concentration_pct)
    total_score = max(0, min(100, position_score + pressure_score + concentration_score + recent_strength_score + behavior_score))

    source_mix = source_mix or {}
    quality = quality_base
    if quality_base == "medium_ohlcv_reconstructed":
        quality_reason = f"OHLCV 重建分價資料 {coverage_days}/{required_days} 日；非逐筆真實分價量"
    else:
        quality_reason = f"有效分價資料 {coverage_days}/{required_days} 日"
    if source_mix:
        dominant = max(source_mix.values()) / sum(source_mix.values()) if sum(source_mix.values()) else 1
        if dominant < 0.7:
            quality = "low_mixed"
            quality_reason += "；來源混合比例過高"
        elif dominant < 0.9 and quality == "high":
            quality = "medium_mixed"
            quality_reason += "；來源混合"
        elif dominant < 1:
            quality = "high_mixed"
            quality_reason += "；少量來源混合"

    support = _nearest_peaks(profile, close, side="support", total_volume=total_volume)
    resistance = _nearest_peaks(profile, close, side="resistance", total_volume=total_volume)
    cost_state = "market_profit" if close > weighted_cost else "market_trapped"
    return {
        "available": True,
        "status": "ready",
        "quality": quality,
        "quality_reason": quality_reason,
        "coverage_days": coverage_days,
        "required_days": required_days,
        "main_peak_price": round(main_peak_price, 4),
        "main_peak_distance_pct": round(distance_pct, 2) if distance_pct is not None else None,
        "overhead_pressure_pct": round(pressure_pct, 2),
        "top3_concentration_pct": round(concentration_pct, 2),
        "recent_peak_strength_pct": round(short_peak_pct, 2) if short_peak_pct is not None else None,
        "long_peak_strength_pct": round(long_peak_pct, 2) if long_peak_pct is not None else None,
        "behavior_state": state,
        "weighted_cost": round(weighted_cost, 4),
        "cost_state": cost_state,
        "position_score": position_score,
        "pressure_score": pressure_score,
        "concentration_score": concentration_score,
        "recent_peak_score": recent_strength_score,
        "recent_peak_note": recent_strength_note,
        "behavior_score": behavior_score,
        "total_score": int(total_score),
        "grade": grade(total_score),
        "support_peaks": support,
        "resistance_peaks": resistance,
    }
