from __future__ import annotations

import math
from contextlib import closing
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from core.data_quality import assess_daily_ohlcv
from core.db import db
from core.market_analytics_schema import (
    TECHNICAL_FORMULA_VERSION,
    TECHNICAL_LOOKBACK_ROWS,
    ensure_market_analytics_schema,
)
from core.market_foundation_schema import source_rank
from repository.market_analytics_repository import (
    history_rows_for_technicals,
    prune_daily_technical_snapshots,
    upsert_daily_technical_snapshots,
)
from repository.history_repository import history_date_coverage, recent_market_reference_dates
from repository.rsi_adjustment_repository import (
    apply_rsi_split_adjustments,
    load_rsi_split_adjustments,
)
from scoring import calculate_indicators, wilder_rsi_value


TPE = ZoneInfo("Asia/Taipei")
MIN_DECISION_HISTORY_ROWS = 120
DAILY_TECHNICAL_WRITE_COMMIT_INTERVAL_CODES = 50

INDICATOR_COLUMN_MAP = {
    "ma5": "ma5",
    "ma10": "ma10",
    "ma20": "ma20",
    "ma60": "ma60",
    "ema12": "ema12",
    "ema26": "ema26",
    "rsi5": "rsi5",
    "rsi10": "rsi10",
    "rsi14": "rsi14",
    "macd_dif": "dif",
    "macd_signal": "macd_signal",
    "macd_osc": "osc",
    "kd_k": "k",
    "kd_d": "d",
    "atr14": "atr14",
    "boll_mid": "boll_mid",
    "boll_upper": "boll_upper",
    "boll_lower": "boll_lower",
    "boll_width": "boll_width",
    "obv": "obv",
    "volume_ma5": "vol_ma5",
    "volume_ma20": "vol_ma20",
    "previous_10d_low": "previous_10d_low",
    "previous_20d_low": "previous_20d_low",
    "previous_20d_high": "previous_20d_high",
    "previous_60d_high": "previous_60d_high",
}


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _default_codes(conn) -> list[str]:
    ensure_market_analytics_schema(conn)
    active = [
        str(row[0])
        for row in conn.execute(
            "SELECT code FROM stock_master WHERE is_active=1 AND security_type='stock' ORDER BY market,code"
        ).fetchall()
    ]
    if active:
        return active
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT code FROM stock_industry_profile ORDER BY code"
        ).fetchall()
    ]


def _large_gap_count(rows: list[dict[str, Any]]) -> int:
    if len(rows) < 2:
        return 0
    dates = pd.to_datetime([row.get("date") for row in rows], errors="coerce")
    if dates.isna().any():
        return 1
    return int((pd.Series(dates).diff().dt.days.dropna() > 7).sum())


def _snapshot_row(
    code: str,
    window: list[dict[str, Any]],
    *,
    adjustment_event_count: int,
    computed_at: str,
    indicator_row: pd.Series | None = None,
    recent_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest = window[-1]
    last = indicator_row
    if last is None:
        frame = pd.DataFrame(window)
        last = calculate_indicators(frame).iloc[-1]
    input_count = len(window)
    gap_count = _large_gap_count(window)
    official_latest = source_rank(latest.get("source")) >= 100
    official_input_count = sum(source_rank(row.get("source")) >= 100 for row in window)
    required_values_available = all(
        _finite_number(last.get(column)) is not None
        for column in ("rsi5", "rsi10", "rsi14", "dif", "macd_signal", "osc")
    )
    if input_count < MIN_DECISION_HISTORY_ROWS:
        data_quality = "insufficient_history"
        reason = f"requires {MIN_DECISION_HISTORY_ROWS} valid rows; found {input_count}"
    elif not official_latest:
        data_quality = "fallback_source"
        reason = "latest OHLCV row is not an official TWSE/TPEx source"
    elif recent_coverage is not None and not recent_coverage.get("ready"):
        data_quality = "stale_or_gapped"
        reason = (
            "recent official trading-date coverage failed: "
            f"{recent_coverage.get('reason') or 'unknown'}"
        )
    elif recent_coverage is None and gap_count > 3:
        data_quality = "stale_or_gapped"
        reason = f"history contains {gap_count} gaps longer than seven calendar days"
    elif not required_values_available:
        data_quality = "unavailable"
        reason = "required RSI/MACD values could not be calculated"
    else:
        data_quality = "ok"
        reason = (
            "calculated from date-sorted validated OHLCV with official no-trade dates verified"
            if recent_coverage and recent_coverage.get("verified_no_trade_dates")
            else "calculated from date-sorted validated OHLCV with the shared scoring formula"
        )
    decision_ready = data_quality == "ok"
    source_quality = (
        "official"
        if official_input_count == input_count
        else "mixed"
        if official_input_count
        else "fallback"
    )
    values = {
        target: _finite_number(last.get(source))
        for target, source in INDICATOR_COLUMN_MAP.items()
    }
    return {
        "trade_date": str(latest["date"]),
        "code": code,
        "formula_version": TECHNICAL_FORMULA_VERSION,
        "input_row_count": input_count,
        "input_start_date": str(window[0]["date"]),
        "input_end_date": str(latest["date"]),
        "adjustment_event_count": adjustment_event_count,
        **values,
        "history_source": str(latest.get("source") or ""),
        "source_quality": source_quality,
        "data_quality": data_quality,
        "decision_ready": 1 if decision_ready else 0,
        "quality_reason": reason,
        "computed_at": computed_at,
    }


def _rsi_as_of(window: list[dict[str, Any]], period: int) -> float | None:
    source = [
        row.get("technical_close")
        if row.get("technical_close") is not None
        else row.get("rsi_close")
        if row.get("rsi_close") is not None
        else row.get("close")
        for row in window[-120:]
    ]
    return _finite_number(wilder_rsi_value(source, period))


def rebuild_daily_technical_snapshots(
    *,
    codes: list[str] | None = None,
    trade_date: str | None = None,
    backfill: bool = False,
    retain_trading_days: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Materialize shared-formula technicals for official full-market history.

    ``backfill=False`` computes one latest/exact-date row per stock.  Backfill
    computes every locally available date using the same maximum 260-row input
    window used by the Dashboard, which prevents a second RSI/MACD formula.
    """

    normalized_codes = sorted(
        {
            str(code or "").strip().zfill(4)
            for code in (codes or [])
            if str(code or "").strip().isdigit()
        }
    )
    computed_at = datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S")
    rows_ready = 0
    rows_partial = 0
    rows_written = 0
    codes_without_history: list[str] = []
    code_results: list[dict[str, Any]] = []
    write_batch_count = 0
    pending_write_codes = 0

    with closing(db()) as conn:
        ensure_market_analytics_schema(conn)
        selected_codes = normalized_codes or _default_codes(conn)
        for code in selected_codes:
            history = history_rows_for_technicals(
                conn,
                code,
                end_date=trade_date,
                limit=max(int(retain_trading_days), TECHNICAL_LOOKBACK_ROWS),
            )
            history = [row for row in history if assess_daily_ohlcv(row).get("ready")]
            if trade_date:
                history = [row for row in history if str(row.get("date")) <= trade_date]
            if not history or (trade_date and str(history[-1].get("date")) != trade_date):
                codes_without_history.append(code)
                continue
            events = load_rsi_split_adjustments(conn, code)
            adjusted = apply_rsi_split_adjustments(history, events)
            latest_coverage = None
            if not backfill:
                latest_date = str(adjusted[-1].get("date") or "")
                reference_dates = recent_market_reference_dates(
                    conn,
                    MIN_DECISION_HISTORY_ROWS,
                    latest_completed_date=latest_date,
                )
                latest_coverage = history_date_coverage(
                    conn,
                    code,
                    required_days=MIN_DECISION_HISTORY_ROWS,
                    reference_dates=reference_dates,
                )
            indexes = range(len(adjusted)) if backfill else [len(adjusted) - 1]
            vectorized = calculate_indicators(pd.DataFrame(adjusted)) if backfill else None
            snapshots: list[dict[str, Any]] = []
            for index in indexes:
                start = max(0, index + 1 - TECHNICAL_LOOKBACK_ROWS)
                window = adjusted[start : index + 1]
                indicator_row = None
                if vectorized is not None and index < TECHNICAL_LOOKBACK_ROWS:
                    indicator_row = vectorized.iloc[index].copy()
                    for period in (5, 10, 14):
                        if index + 1 >= period + 1:
                            indicator_row[f"rsi{period}"] = _rsi_as_of(window, period)
                effective_events = sum(
                    1
                    for event in events
                    if str(event.get("event_date") or "") > str(window[0].get("date") or "")
                )
                snapshots.append(
                    _snapshot_row(
                        code,
                        window,
                        adjustment_event_count=effective_events,
                        computed_at=computed_at,
                        indicator_row=indicator_row,
                        recent_coverage=latest_coverage,
                    )
                )
            ready = sum(int(row["decision_ready"]) for row in snapshots)
            rows_ready += ready
            rows_partial += len(snapshots) - ready
            if not dry_run:
                rows_written += upsert_daily_technical_snapshots(conn, snapshots)
                pending_write_codes += 1
                if pending_write_codes >= DAILY_TECHNICAL_WRITE_COMMIT_INTERVAL_CODES:
                    conn.commit()
                    write_batch_count += 1
                    pending_write_codes = 0
            code_results.append(
                {
                    "code": code,
                    "history_rows": len(history),
                    "snapshot_rows": len(snapshots),
                    "decision_ready_rows": ready,
                    "latest_date": snapshots[-1]["trade_date"],
                    "latest_quality": snapshots[-1]["data_quality"],
                }
            )
        if not dry_run and pending_write_codes:
            conn.commit()
            write_batch_count += 1
            pending_write_codes = 0
        prune = (
            {"skipped": True, "reason": "dry_run"}
            if dry_run
            else prune_daily_technical_snapshots(
                conn,
                retain_trading_days=retain_trading_days,
            )
        )
        if not dry_run:
            conn.commit()

    return {
        "ok": bool(code_results),
        "status": "ok" if code_results and not codes_without_history else "partial",
        "dry_run": dry_run,
        "backfill": backfill,
        "trade_date": trade_date,
        "formula_version": TECHNICAL_FORMULA_VERSION,
        "lookback_rows": TECHNICAL_LOOKBACK_ROWS,
        "selected_code_count": len(selected_codes),
        "processed_code_count": len(code_results),
        "missing_history_code_count": len(codes_without_history),
        "missing_history_codes": codes_without_history[:100],
        "snapshot_rows_ready": rows_ready,
        "snapshot_rows_not_ready": rows_partial,
        "rows_written": rows_written,
        "write_commit_interval_codes": DAILY_TECHNICAL_WRITE_COMMIT_INTERVAL_CODES,
        "write_batch_count": write_batch_count,
        "prune": prune,
        "code_result_sample": code_results[:50],
        "code_results_truncated": len(code_results) > 50,
    }
