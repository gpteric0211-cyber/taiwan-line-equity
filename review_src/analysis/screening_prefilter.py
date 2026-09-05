from __future__ import annotations

import math
from typing import Any


SCREENING_PREFILTER_VERSION = "screening-prefilter-v1"


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def screening_prefilter_score(row: dict[str, Any], strategy: str) -> float:
    """Rank candidates without creating or overriding the referee verdict.

    The formulas are moved unchanged from ``bot_market_data_service`` so the
    production screen and the research backtest share one implementation.
    """

    close = finite_number(row.get("close")) or 0.0
    ma20 = finite_number(row.get("ma20")) or close
    ma60 = finite_number(row.get("ma60")) or ma20
    rsi14 = finite_number(row.get("rsi14")) or 50.0
    previous_rsi14 = finite_number(row.get("previous_rsi14"))
    oscillator = finite_number(row.get("macd_osc")) or 0.0
    prior_oscillator = finite_number(row.get("previous_macd_osc"))
    volume = finite_number(row.get("volume")) or 0.0
    volume_ma20 = finite_number(row.get("volume_ma20")) or volume or 1.0
    turnover = max(finite_number(row.get("turnover_value")) or 0.0, 1.0)
    trend_pct = ((close - ma20) / ma20 * 100) if ma20 > 0 else 0.0
    ma_alignment_pct = ((ma20 - ma60) / ma60 * 100) if ma60 > 0 else 0.0
    momentum_change = oscillator - prior_oscillator if prior_oscillator is not None else 0.0
    rsi_change = rsi14 - previous_rsi14 if previous_rsi14 is not None else 0.0
    volume_ratio = volume / volume_ma20 if volume_ma20 > 0 else 0.0
    liquidity = math.log10(turnover)
    support_distance = abs(close - ma20) / close * 100 if close > 0 else 100.0

    if strategy == "bottom":
        return (
            liquidity * 3
            - support_distance * 6
            - abs(rsi14 - 38) * 1.5
            + max(min(rsi_change, 5), -5) * 3
            + momentum_change * 0.8
        )
    if strategy == "support":
        return liquidity * 3 - support_distance * 7 - abs(rsi14 - 42) + momentum_change * 0.2
    if strategy == "momentum":
        return (
            liquidity * 3
            + trend_pct * 2
            + ma_alignment_pct * 1.5
            + min(max(rsi14 - 50, -20), 20)
            + momentum_change * 0.25
            + min(volume_ratio, 3) * 3
        )
    if strategy == "conservative":
        return liquidity * 4 - support_distance * 5 - abs(rsi14 - 50) * 1.5 + ma_alignment_pct
    return (
        liquidity * 3
        - abs(rsi14 - 52)
        - support_distance * 2
        + ma_alignment_pct
        + momentum_change * 0.2
    )
