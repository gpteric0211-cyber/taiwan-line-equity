from __future__ import annotations

import logging
import math
import sqlite3
from contextlib import closing
from typing import Any

import pandas as pd

from core.data_quality import DataQualityStatus, assess_component_freshness
from core.date_utils import normalize_date
from core.db import db
from core.utils import parse_num

try:
    from scoring import _wilder_rsi as shared_wilder_rsi
except Exception:
    shared_wilder_rsi = None
try:
    from scoring import calculate_indicators
except Exception:
    calculate_indicators = None


_INDICATOR_BASE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
_INDICATOR_ADJUSTED_COLUMNS = [
    "rsi_close",
    "technical_open",
    "technical_high",
    "technical_low",
    "technical_close",
]


def _indicator_input_frame(rows_asc: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows_asc)
    columns = _INDICATOR_BASE_COLUMNS + [
        column for column in _INDICATOR_ADJUSTED_COLUMNS if column in frame.columns
    ]
    return frame[columns].copy()


def calc_rsi(closes: list[float], period: int) -> float | None:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    """Wilder RSI with SMA seed, using scoring.py as the single source of truth."""
    vals = [float(x) for x in reversed(closes) if x is not None][-120:]  # old -> new
    if len(vals) < period + 1:
        return None
    if shared_wilder_rsi is not None:
        try:
            rsi = shared_wilder_rsi(pd.Series(vals, dtype="float64"), period).dropna()
            return round(float(rsi.iloc[-1]), 2) if not rsi.empty else None
        except Exception:
            logging.exception("shared RSI calculation failed; falling back to local implementation")
    s = pd.Series(vals, dtype="float64")
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    if len(s) < period + 1:
        return None
    avg_gain = gain.iloc[1:period + 1].mean()
    avg_loss = loss.iloc[1:period + 1].mean()
    for i in range(period + 1, len(s)):
        avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
    if avg_loss == 0 and avg_gain > 0:
        return 100.0
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    rs = avg_gain / avg_loss if avg_loss else 0
    return round(float(100 - 100 / (1 + rs)), 2)


def calc_ma_from_rows(rows: list[dict[str, Any]], period: int) -> float | None:
    vals = [parse_num(r.get('close')) for r in rows[-period:]]
    vals = [v for v in vals if v is not None]
    if len(vals) < period:
        return None
    return sum(vals) / len(vals)


def calc_pivot_from_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    if len(rows) < 2:
        return {}
    prev = rows[-2]
    h, l, c = parse_num(prev.get('high')), parse_num(prev.get('low')), parse_num(prev.get('close'))
    if h is None or l is None or c is None:
        return {}
    pp = (h + l + c) / 3.0
    return {'PP': pp, 'R1': 2 * pp - l, 'R2': pp + (h - l), 'S1': 2 * pp - h, 'S2': pp - (h - l)}


def component_states(code: str, eod: sqlite3.Row | None, hist: list[sqlite3.Row]) -> dict[str, Any]:
    # Split component data states; do not treat one fresh table as all data fresh.
    hist_count = len([r for r in hist if r["close"] is not None])
    k_dates = [normalize_date(eod["date"])] if eod and eod["date"] else []
    k_dates.extend(normalize_date(row["date"]) for row in hist if row["date"])
    latest_k_date = max((value for value in k_dates if value), default=None)
    with closing(db()) as conn:
        inst_row = conn.execute(
            """
            SELECT COUNT(*) AS c, MAX(date) AS latest_date
            FROM institution_daily
            WHERE code=? AND date<=?
            """,
            (code, latest_k_date),
        ).fetchone()
        margin_row = conn.execute(
            """
            SELECT COUNT(*) AS c, MAX(date) AS latest_date
            FROM margin_daily
            WHERE code=? AND date<=?
            """,
            (code, latest_k_date),
        ).fetchone()
    inst_count = int(inst_row["c"] or 0)
    margin_count = int(margin_row["c"] or 0)
    institution_date = normalize_date(inst_row["latest_date"])
    margin_date = normalize_date(margin_row["latest_date"])
    institution_freshness = assess_component_freshness(institution_date, latest_k_date, max_lag_days=3)
    margin_freshness = assess_component_freshness(margin_date, latest_k_date, max_lag_days=3)
    price_state = "price_ok" if eod or hist_count else "price_missing"
    tech_state = "tech_ok" if hist_count >= 30 else ("tech_partial" if hist_count >= 15 else "tech_missing")

    def component_state(prefix: str, count: int, freshness: dict[str, Any]) -> str:
        if freshness["status"] == DataQualityStatus.SOURCE_DELAYED.value:
            return f"{prefix}_source_delayed"
        if freshness["status"] == DataQualityStatus.MISSING.value:
            return f"{prefix}_missing"
        return f"{prefix}_ok" if count >= 20 else (f"{prefix}_partial" if count >= 5 else f"{prefix}_missing")

    inst_state = component_state("inst", inst_count, institution_freshness)
    margin_state = component_state("margin", margin_count, margin_freshness)
    inst_text = f"{inst_state}({institution_date or 'missing'})"
    margin_text = f"{margin_state}({margin_date or 'missing'})"
    return {
        "price": price_state,
        "tech": tech_state,
        "institution": inst_state,
        "margin": margin_state,
        "latest_k_date": latest_k_date,
        "institution_date": institution_date,
        "margin_date": margin_date,
        "institution_freshness": institution_freshness,
        "margin_freshness": margin_freshness,
        "hist_count": hist_count,
        "inst_count": inst_count,
        "margin_count": margin_count,
        "text": f"{price_state} | {tech_state} | {inst_text} | {margin_text}",
    }


def historical_data_quality(rows_asc: list[dict[str, Any]]) -> dict[str, Any]:
    count = len([r for r in rows_asc if r.get('close') is not None])
    level = 'missing'
    if count >= 250:
        level = 'full'
    elif count >= 120:
        level = 'partial'
    elif count >= 30:
        level = 'basic'
    dates = pd.to_datetime([r.get('date') for r in rows_asc if r.get('date')], errors='coerce')
    dates = dates.dropna().sort_values()
    gap_count = 0
    if len(dates) >= 2:
        gaps = pd.Series(dates).diff().dt.days.dropna()
        gap_count = int((gaps > 7).sum())
        if gap_count > 3 and level == 'full':
            level = 'partial'
    return {'count': count, 'level': level, 'gap_count': gap_count}



def technical_context_from_rows(rows_asc: list[dict[str, Any]]) -> dict[str, Any]:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    if not rows_asc or calculate_indicators is None:
        return {}
    try:
        df = _indicator_input_frame(rows_asc)
        ind = calculate_indicators(df)
        def last(col, off=-1):
            try:
                v = ind[col].iloc[off]
                return float(v) if pd.notna(v) else None
            except Exception:
                return None
        return {
            'date': str(ind['date'].iloc[-1]), 'open': last('open'), 'high': last('high'), 'low': last('low'), 'close': last('close'),
            'volume': last('volume'), 'ma5': last('ma5'), 'ma10': last('ma10'), 'ma20': last('ma20'), 'ma60': last('ma60'), 'atr14': last('atr14'), 'rsi5': last('rsi5'), 'rsi10': last('rsi10'), 'rsi14': last('rsi14'),
            'dif': last('dif'), 'macd_signal': last('macd_signal'), 'osc': last('osc'), 'vol_ma20': last('vol_ma20'),
            'prev_10d_low': last('previous_10d_low'), 'prev_20d_low': last('previous_20d_low'),
            'prev_close': last('close', -2), 'k': last('k'), 'd': last('d'),
        }
    except Exception:
        return {}



def _indicator_value_at(rows_asc: list[dict[str, Any]], column: str, offset: int) -> float | None:
    if not rows_asc or calculate_indicators is None:
        return None
    try:
        df = _indicator_input_frame(rows_asc)
        ind = calculate_indicators(df)
        value = ind[column].iloc[offset]
        return float(value) if pd.notna(value) and math.isfinite(float(value)) else None
    except Exception:
        return None
