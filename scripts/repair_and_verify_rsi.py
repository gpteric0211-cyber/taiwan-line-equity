from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.twse import fetch_twse_stock_month_rows  # noqa: E402
from adapter.tpex_history import fetch_tpex_stock_month_rows  # noqa: E402
from adapter.yahoo_history import fetch_yahoo_chart_tw_history_rows  # noqa: E402
from core.components import read_components  # noqa: E402
from core.db import db  # noqa: E402
from core.config import TWSE_STOCK_DAY_BY_CODE  # noqa: E402
from core.data_quality import (  # noqa: E402
    assess_daily_ohlcv,
    derive_official_monthly_no_bar_evidence,
)
from core.market_foundation_schema import upsert_daily_ohlcv_rows  # noqa: E402
from core.utils import now_tpe, parse_num  # noqa: E402
from repository.history_repository import (  # noqa: E402
    history_date_coverage,
    recent_market_reference_dates,
)
from repository.market_profile_repository import resolve_market_profile  # noqa: E402
from repository.stock_no_trade_repository import upsert_verified_no_trade_dates  # noqa: E402
from repository.rsi_adjustment_repository import (  # noqa: E402
    apply_rsi_split_adjustments,
    replace_yahoo_rsi_split_adjustments,
    select_yahoo_applied_split_events,
)
from scoring import calculate_indicators  # noqa: E402
RSI_PERIODS = (5, 10, 14)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _month_targets(months: int) -> list[str]:
    current = _month_start(now_tpe().date())
    return [_shift_month(current, -offset).isoformat() for offset in reversed(range(max(int(months), 1)))]


def _parse_codes(raw: str | None) -> list[str]:
    if not raw:
        return [str(item["code"]).zfill(4) for item in read_components()]
    out: list[str] = []
    for value in raw.replace(";", ",").split(","):
        code = value.strip().zfill(4)
        if len(code) == 4 and code.isdigit() and code not in out:
            out.append(code)
    return out


def _fetch_official(
    codes: list[str],
    months: list[str],
    workers: int,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    profiles = {code: resolve_market_profile(code) for code in codes}
    tasks = [
        (
            code,
            month,
            fetch_tpex_stock_month_rows
            if str((profiles.get(code) or {}).get("market_type") or "") == "otc"
            else fetch_twse_stock_month_rows,
        )
        for code in codes
        for month in months
    ]
    rows: list[dict[str, Any]] = []
    no_trade_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    successful_reports: list[dict[str, Any]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(fetcher, code, month): (code, month)
            for code, month, fetcher in tasks
        }
        for future in as_completed(futures):
            code, month = futures[future]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:
                result = {"ok": False, "error": str(exc), "rows": []}
            if result.get("ok"):
                fetched_rows = list(result.get("rows") or [])
                fetched_no_trade = list(result.get("verified_no_trade_dates") or [])
                rows.extend(fetched_rows)
                no_trade_rows.extend(fetched_no_trade)
                market = (
                    "otc"
                    if str((profiles.get(code) or {}).get("market_type") or "") == "otc"
                    else "listed"
                )
                successful_reports.append({
                    "code": code,
                    "month": month[:7],
                    "market": market,
                    "rows": fetched_rows,
                    "explicit_no_trade_rows": fetched_no_trade,
                    "source": "TPEX TRADING_STOCK" if market == "otc" else "TWSE STOCK_DAY",
                    "source_url": (
                        str(result.get("source_url") or "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock")
                        if market == "otc"
                        else TWSE_STOCK_DAY_BY_CODE
                    ),
                })
            else:
                failures.append({"code": code, "month": month[:7], "error": result.get("error") or "official_fetch_failed"})
            if completed % 25 == 0 or completed == len(tasks):
                print(f"official progress {completed}/{len(tasks)} rows={len(rows)} failures={len(failures)}", flush=True)
    return rows, failures, no_trade_rows, successful_reports


def _local_rows(code: str, limit: int = 260) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT date,open,high,low,close,volume,source
            FROM history_price
            WHERE code=?
            ORDER BY date DESC
            LIMIT ?
            """,
            (code, limit),
        ).fetchall()
    normalized = [dict(row) for row in reversed(rows)]
    for row in normalized:
        row["code"] = code
    return [row for row in normalized if assess_daily_ohlcv(row)["ready"]]


def _reference_rsi(closes: list[float], period: int) -> float | None:
    """Independent Wilder RSI loop; intentionally does not import production code."""

    values = [float(value) for value in closes[-120:]]
    if len(values) < period + 1:
        return None
    changes = [values[idx] - values[idx - 1] for idx in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for idx in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _reference_ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for idx in range(period, len(values)):
        current = current + alpha * (values[idx] - current)
        out[idx] = current
    return out


def _reference_macd(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    ema12 = _reference_ema(closes, 12)
    ema26 = _reference_ema(closes, 26)
    dif: list[float | None] = [
        (left - right) if left is not None and right is not None else None
        for left, right in zip(ema12, ema26)
    ]
    valid_positions = [idx for idx, value in enumerate(dif) if value is not None]
    signal: list[float | None] = [None] * len(closes)
    if len(valid_positions) >= 9:
        seed_positions = valid_positions[:9]
        current = sum(float(dif[idx]) for idx in seed_positions) / 9.0
        signal[seed_positions[-1]] = current
        alpha = 2.0 / 10.0
        for idx in valid_positions[9:]:
            current = current + alpha * (float(dif[idx]) - current)
            signal[idx] = current
    if not valid_positions:
        return None, None, None
    last = valid_positions[-1]
    dif_last = dif[last]
    signal_last = signal[last]
    osc = float(dif_last) - float(signal_last) if dif_last is not None and signal_last is not None else None
    return dif_last, signal_last, osc


def _reference_kd(highs: list[float], lows: list[float], closes: list[float]) -> tuple[float | None, float | None]:
    k = 50.0
    d = 50.0
    seen = False
    for idx in range(8, len(closes)):
        low9 = min(lows[idx - 8 : idx + 1])
        high9 = max(highs[idx - 8 : idx + 1])
        if high9 == low9:
            continue
        rsv = (closes[idx] - low9) / (high9 - low9) * 100.0
        k = k * (2.0 / 3.0) + rsv / 3.0
        d = d * (2.0 / 3.0) + k / 3.0
        seen = True
    return (k, d) if seen else (None, None)


def _reference_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period:
        return None
    true_ranges: list[float] = []
    for idx, (high, low) in enumerate(zip(highs, lows)):
        if idx == 0:
            true_ranges.append(high - low)
        else:
            true_ranges.append(max(high - low, abs(high - closes[idx - 1]), abs(low - closes[idx - 1])))
    atr = sum(true_ranges[:period]) / period
    for value in true_ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def _reference_technicals(rows: list[dict[str, Any]], *, adjusted: bool) -> dict[str, float | None]:
    def value(row: dict[str, Any], field: str) -> float:
        candidate = row.get(f"technical_{field}") if adjusted else row.get(field)
        parsed = parse_num(candidate if candidate is not None else row.get(field))
        if parsed is None:
            raise ValueError(f"missing {field}")
        return float(parsed)

    closes = [value(row, "close") for row in rows]
    highs = [value(row, "high") for row in rows]
    lows = [value(row, "low") for row in rows]
    dif, signal, osc = _reference_macd(closes)
    k, d = _reference_kd(highs, lows, closes)
    return {
        **{f"rsi{period}": _reference_rsi(closes, period) for period in RSI_PERIODS},
        "ma5": sum(closes[-5:]) / 5 if len(closes) >= 5 else None,
        "ma10": sum(closes[-10:]) / 10 if len(closes) >= 10 else None,
        "ma20": sum(closes[-20:]) / 20 if len(closes) >= 20 else None,
        "ma60": sum(closes[-60:]) / 60 if len(closes) >= 60 else None,
        "dif": dif,
        "macd_signal": signal,
        "osc": osc,
        "k": k,
        "d": d,
        "atr14": _reference_atr(highs, lows, closes),
    }


def _compare_one(
    code: str,
    yahoo_days: int,
    rsi_tolerance: float,
    close_tolerance: float,
    technical_tolerance: float,
) -> dict[str, Any]:
    local = _local_rows(code)
    market_profile = resolve_market_profile(code)
    market_type = str(market_profile.get("market_type") or "unknown")
    yahoo = fetch_yahoo_chart_tw_history_rows(
        code,
        days=yahoo_days,
        market_type=market_type,
    )
    result: dict[str, Any] = {
        "code": code,
        "market_type": market_type,
        "ok": False,
        "local_rows": len(local),
        "yahoo_symbol": yahoo.get("symbol"),
        "yahoo_rows": yahoo.get("row_count") or 0,
        "missing_yahoo_dates": [],
        "missing_recent_yahoo_dates": [],
        "yahoo_extra_dates_ignored": [],
        "ohlc_mismatches": [],
        "recent_ohlc_mismatches": [],
        "close_mismatches": [],
        "local_rsi": {},
        "yahoo_rsi": {},
        "rsi_differences": {},
        "local_technicals": {},
        "yahoo_technicals": {},
        "technical_differences": {},
        "technical_tolerances": {},
        "production_technicals": {},
        "production_reference_technicals": {},
        "production_reference_differences": {},
        "production_formula_ok": False,
        "error": yahoo.get("error"),
        "split_events_observed": yahoo.get("split_events") or [],
        "split_events_applied": [],
        "latest_close": {},
        "latest_ohlcv": {},
    }
    if not local or not yahoo.get("ok"):
        return result
    yahoo_rows = [
        row for row in (yahoo.get("rows") or [])
        if local and str(row.get("date") or "") <= str(local[-1]["date"])
    ]
    yahoo_row_by_date = {str(row["date"]): dict(row) for row in yahoo_rows}
    yahoo_by_date = {
        trade_date: parse_num(row.get("close"))
        for trade_date, row in yahoo_row_by_date.items()
    }
    latest_local = local[-1]
    latest_date = str(latest_local.get("date") or "")
    latest_official_close = parse_num(latest_local.get("close"))
    latest_yahoo_close = yahoo_by_date.get(latest_date)
    latest_difference = (
        abs(float(latest_official_close) - float(latest_yahoo_close))
        if latest_official_close is not None and latest_yahoo_close is not None
        else None
    )
    result["latest_close"] = {
        "date": latest_date,
        "official_close": latest_official_close,
        "yahoo_close": latest_yahoo_close,
        "difference": round(latest_difference, 6) if latest_difference is not None else None,
        "match": bool(latest_difference is not None and latest_difference <= close_tolerance),
    }
    latest_yahoo_row = yahoo_row_by_date.get(latest_date) or {}
    latest_ohlcv: dict[str, Any] = {"date": latest_date}
    latest_ohlcv_ok = True
    for field in ("open", "high", "low", "close"):
        official_value = parse_num(latest_local.get(field))
        yahoo_value = parse_num(latest_yahoo_row.get(field))
        difference = (
            abs(float(official_value) - float(yahoo_value))
            if official_value is not None and yahoo_value is not None
            else None
        )
        matches = bool(difference is not None and difference <= close_tolerance)
        latest_ohlcv[field] = {
            "official": official_value,
            "yahoo": yahoo_value,
            "difference": round(difference, 6) if difference is not None else None,
            "match": matches,
        }
        latest_ohlcv_ok = latest_ohlcv_ok and matches
    official_volume = parse_num(latest_local.get("volume"))
    yahoo_volume = parse_num(latest_yahoo_row.get("volume"))
    volume_difference = (
        abs(float(official_volume) - float(yahoo_volume))
        if official_volume is not None and yahoo_volume is not None
        else None
    )
    latest_ohlcv["volume"] = {
        "official": official_volume,
        "yahoo": yahoo_volume,
        "difference": round(volume_difference, 3) if volume_difference is not None else None,
        # Volume is reported for traceability but is not used to prove price-based indicators.
        "informational_only": True,
    }
    latest_ohlcv["price_fields_match"] = latest_ohlcv_ok
    result["latest_ohlcv"] = latest_ohlcv
    applied_events, split_decisions = select_yahoo_applied_split_events(
        local,
        yahoo_by_date,
        yahoo.get("split_events") or [],
    )
    result["split_events_applied"] = applied_events
    result["split_event_decisions"] = split_decisions
    adjusted_local = apply_rsi_split_adjustments(local, applied_events)
    local_dates = {str(row["date"]) for row in adjusted_local}
    result["yahoo_extra_dates_ignored"] = sorted(
        trade_date
        for trade_date in yahoo_row_by_date
        if adjusted_local
        and str(adjusted_local[0]["date"]) <= trade_date <= str(adjusted_local[-1]["date"])
        and trade_date not in local_dates
    )
    missing_dates = [
        str(row["date"])
        for row in adjusted_local
        if str(row["date"]) not in yahoo_row_by_date
        or any(parse_num(yahoo_row_by_date[str(row["date"])].get(field)) is None for field in ("open", "high", "low", "close"))
    ]
    result["missing_yahoo_dates"] = missing_dates
    recent_dates = {str(row["date"]) for row in adjusted_local[-120:]}
    missing_recent_dates = [trade_date for trade_date in missing_dates if trade_date in recent_dates]
    result["missing_recent_yahoo_dates"] = missing_recent_dates
    last_missing_index = max(
        (
            idx
            for idx, row in enumerate(adjusted_local)
            if str(row["date"]) in set(missing_dates)
        ),
        default=-1,
    )
    # Use the longest contiguous official-date suffix available from both sources.
    # This prevents a remote vendor's old isolated gap from changing EMA/KD/ATR
    # seeding, while still requiring every one of the latest 120 official rows.
    aligned_local = adjusted_local[last_missing_index + 1 :]
    for row in aligned_local:
        trade_date = str(row["date"])
        yahoo_row = yahoo_row_by_date.get(trade_date)
        if not yahoo_row:
            continue
        for field in ("open", "high", "low", "close"):
            local_value = parse_num(row.get(f"technical_{field}"))
            yahoo_value = parse_num(yahoo_row.get(field))
            if local_value is None or yahoo_value is None:
                continue
            difference = abs(float(local_value) - float(yahoo_value))
            if difference > close_tolerance:
                mismatch = {
                    "date": trade_date,
                    "field": field,
                    "official_adjusted": local_value,
                    "official_raw": parse_num(row.get(field)),
                    "yahoo": yahoo_value,
                    "difference": round(difference, 6),
                }
                result["ohlc_mismatches"].append(mismatch)
                if trade_date in recent_dates:
                    result["recent_ohlc_mismatches"].append(mismatch)
                if field == "close":
                    result["close_mismatches"].append(mismatch)
    result["aligned_history_rows"] = len(aligned_local)
    result["aligned_recent_rows"] = min(120, result["aligned_history_rows"])
    if missing_recent_dates or len(aligned_local) < 120:
        result["error"] = "Yahoo/local official-date alignment incomplete or fewer than 120 valid rows"
        return result
    aligned_yahoo = [yahoo_row_by_date[str(row["date"])] for row in aligned_local]
    local_technicals = _reference_technicals(aligned_local, adjusted=True)
    yahoo_technicals = _reference_technicals(aligned_yahoo, adjusted=False)
    differences: dict[str, float | None] = {}
    tolerances: dict[str, float] = {}
    technicals_ok = True
    for key in local_technicals:
        left, right = local_technicals.get(key), yahoo_technicals.get(key)
        diff = abs(float(left) - float(right)) if left is not None and right is not None else None
        tolerance = rsi_tolerance if key.startswith("rsi") else technical_tolerance
        differences[key] = round(diff, 6) if diff is not None else None
        tolerances[key] = tolerance
        if diff is None or diff > tolerance:
            technicals_ok = False
    local_rsi = {key: local_technicals.get(key) for key in ("rsi5", "rsi10", "rsi14")}
    yahoo_rsi = {key: yahoo_technicals.get(key) for key in ("rsi5", "rsi10", "rsi14")}
    rsi_differences = {key: differences.get(key) for key in local_rsi}
    # Older Yahoo rows may be vendor-adjusted for events that are not part of
    # the current 120-row technical input.  They remain in the audit trail, but
    # current output passes only when the latest 120 official-date OHLC inputs
    # and the independently recomputed indicators agree.
    prices_ok = latest_ohlcv_ok and not result["recent_ohlc_mismatches"]

    production_frame = calculate_indicators(pd.DataFrame(adjusted_local))
    production_latest = production_frame.iloc[-1]
    production_technicals: dict[str, float | None] = {}
    for key in local_technicals:
        try:
            value = float(production_latest.get(key))
        except (TypeError, ValueError):
            value = float("nan")
        production_technicals[key] = value if math.isfinite(value) else None
    production_reference = _reference_technicals(adjusted_local, adjusted=True)
    production_differences: dict[str, float | None] = {}
    production_ok = True
    for key, reference_value in production_reference.items():
        production_value = production_technicals.get(key)
        difference = (
            abs(float(production_value) - float(reference_value))
            if production_value is not None and reference_value is not None
            else None
        )
        production_differences[key] = round(difference, 12) if difference is not None else None
        if difference is None or difference > 1e-8:
            production_ok = False

    passed = technicals_ok and prices_ok and production_ok
    result.update({
        "local_rsi": local_rsi,
        "yahoo_rsi": yahoo_rsi,
        "local_rsi_input_rows": min(120, len(aligned_local)),
        "yahoo_rsi_input_rows": min(120, len(aligned_yahoo)),
        "rsi_differences": rsi_differences,
        "local_technicals": local_technicals,
        "yahoo_technicals": yahoo_technicals,
        "technical_differences": differences,
        "technical_tolerances": tolerances,
        "production_technicals": production_technicals,
        "production_reference_technicals": production_reference,
        "production_reference_differences": production_differences,
        "production_reference_tolerance": 1e-8,
        "production_formula_ok": production_ok,
        "ok": passed,
        "error": None if passed else "ohlc_or_technical_mismatch",
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair official history and independently verify OHLC/RSI/MACD/KD/ATR against Yahoo on official dates."
    )
    parser.add_argument("--codes", help="Comma-separated codes; defaults to Taiwan 50 components.")
    parser.add_argument("--months", type=int, default=15, help="Official monthly history window to refresh.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--write", action="store_true", help="Write official rows to history_price. Default is dry-run.")
    parser.add_argument(
        "--skip-official-refresh",
        action="store_true",
        help="Audit the current DB and Yahoo only; useful immediately after a successful official refresh.",
    )
    parser.add_argument("--yahoo-days", type=int, default=700)
    parser.add_argument("--rsi-tolerance", type=float, default=0.05)
    parser.add_argument("--close-tolerance", type=float, default=0.011)
    parser.add_argument("--technical-tolerance", type=float, default=0.05)
    parser.add_argument("--report", default=str(ROOT / "logs" / "rsi_validation" / "latest.json"))
    args = parser.parse_args()

    codes = _parse_codes(args.codes)
    months = _month_targets(args.months)
    started = time.time()
    if args.skip_official_refresh:
        official_rows, official_failures, official_no_trade_rows, successful_reports = [], [], [], []
    else:
        official_rows, official_failures, official_no_trade_rows, successful_reports = _fetch_official(
            codes,
            months,
            args.workers,
        )
    if successful_reports:
        with db() as conn:
            reference_dates = recent_market_reference_dates(
                conn,
                160,
                latest_completed_date=now_tpe().date().isoformat(),
            )
            master_rows = {
                str(row["code"]): dict(row)
                for row in conn.execute(
                    "SELECT code,first_seen_date,is_active FROM stock_master WHERE code IN ("
                    + ",".join("?" for _ in codes)
                    + ")",
                    codes,
                ).fetchall()
            }
        derived_no_trade_rows: list[dict[str, Any]] = []
        for report in successful_reports:
            master = master_rows.get(str(report["code"])) or {}
            if not bool(master.get("is_active")):
                continue
            derived_no_trade_rows.extend(
                derive_official_monthly_no_bar_evidence(
                    code=str(report["code"]),
                    market=str(report["market"]),
                    month=str(report["month"]),
                    official_rows=list(report["rows"]),
                    explicit_no_bar_rows=list(report["explicit_no_trade_rows"]),
                    market_reference_dates=reference_dates,
                    first_seen_date=master.get("first_seen_date"),
                    source=str(report["source"]),
                    source_url=str(report["source_url"]),
                )
            )
        existing_no_trade_keys = {
            (str(row.get("code") or ""), str(row.get("trade_date") or ""))
            for row in official_no_trade_rows
        }
        official_no_trade_rows.extend(
            row for row in derived_no_trade_rows
            if (str(row.get("code") or ""), str(row.get("trade_date") or ""))
            not in existing_no_trade_keys
        )
    first_month_by_code: dict[str, str] = {}
    for row in official_rows:
        code = str(row.get("code") or "")
        month = str(row.get("date") or "")[:7]
        if code and month and (code not in first_month_by_code or month < first_month_by_code[code]):
            first_month_by_code[code] = month
    skipped_prelisting = [
        failure for failure in official_failures
        if first_month_by_code.get(str(failure.get("code") or ""))
        and str(failure.get("month") or "") < first_month_by_code[str(failure.get("code") or "")]
    ]
    official_failures = [failure for failure in official_failures if failure not in skipped_prelisting]
    written = 0
    no_trade_written = 0
    if args.write and (official_rows or official_no_trade_rows):
        fetched_at = time.time()
        for row in official_rows:
            row["updated_at"] = fetched_at
            row["fetched_at"] = fetched_at
        with db() as conn:
            written = upsert_daily_ohlcv_rows(conn, official_rows)
            no_trade_written = upsert_verified_no_trade_dates(conn, official_no_trade_rows)
            conn.commit()

    coverage: list[dict[str, Any]] = []
    with db() as conn:
        for code in codes:
            coverage.append({"code": code, **history_date_coverage(conn, code, required_days=120)})

    comparisons: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                _compare_one,
                code,
                args.yahoo_days,
                args.rsi_tolerance,
                args.close_tolerance,
                args.technical_tolerance,
            ): code
            for code in codes
        }
        for idx, future in enumerate(as_completed(futures), start=1):
            code = futures[future]
            try:
                comparisons.append(future.result())
            except Exception as exc:
                comparisons.append({"code": code, "ok": False, "error": str(exc)})
            if idx % 10 == 0 or idx == len(codes):
                print(f"yahoo comparison progress {idx}/{len(codes)}", flush=True)
    comparisons.sort(key=lambda item: str(item.get("code")))
    adjustment_events = [
        event
        for comparison in comparisons
        for event in (comparison.get("split_events_applied") or [])
    ]
    adjustments_written = 0
    if args.write:
        with db() as conn:
            adjustments_written = replace_yahoo_rsi_split_adjustments(
                conn,
                codes,
                adjustment_events,
                verified_at=time.time(),
            )
            conn.commit()
    coverage.sort(key=lambda item: str(item.get("code")))
    coverage_ok = sum(bool(item.get("ready")) for item in coverage)
    yahoo_ok = sum(bool(item.get("ok")) for item in comparisons)
    production_ok = sum(bool(item.get("production_formula_ok")) for item in comparisons)
    overall_ok = not official_failures and coverage_ok == len(codes) and yahoo_ok == len(codes)
    report = {
        "ok": overall_ok,
        "writes_db": bool(args.write and (written or no_trade_written or adjustments_written)),
        "dry_run": not args.write,
        "official_refresh_skipped": bool(args.skip_official_refresh),
        "code_count": len(codes),
        "months": months,
        "official_rows_fetched": len(official_rows),
        "official_rows_written": written,
        "official_no_trade_rows_fetched": len(official_no_trade_rows),
        "official_no_trade_rows_written": no_trade_written,
        "rsi_split_adjustments_written": adjustments_written,
        "official_failures": official_failures,
        "official_skipped_prelisting": skipped_prelisting,
        "coverage_pass_count": coverage_ok,
        "yahoo_rsi_pass_count": yahoo_ok,
        "yahoo_technical_pass_count": yahoo_ok,
        "production_formula_pass_count": production_ok,
        "rsi_tolerance": args.rsi_tolerance,
        "close_tolerance": args.close_tolerance,
        "technical_tolerance": args.technical_tolerance,
        "duration_seconds": round(time.time() - started, 3),
        "coverage": coverage,
        "comparisons": comparisons,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "ok", "writes_db", "dry_run", "code_count", "official_rows_fetched", "official_rows_written",
        "coverage_pass_count", "yahoo_rsi_pass_count", "yahoo_technical_pass_count",
        "production_formula_pass_count", "duration_seconds"
    )}, ensure_ascii=False, indent=2), flush=True)
    print(f"report={report_path}", flush=True)
    return 0 if overall_ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
