from __future__ import annotations

import re
import threading
import time
from contextlib import closing
from typing import Any, Callable

import logging

from core.config import HEADERS, TWSE_BWIBBU_ALL, TWSE_STOCK_DAY_ALL, TWSE_STOCK_DAY_BY_CODE, safe_error
from core.data_quality import assess_daily_ohlcv
from core.db import db
from core.http import request_json
from core.market_foundation_schema import upsert_daily_ohlcv_rows
from core.market_session import recent_market_date_for_eod
from core.status import set_status
from core.utils import normalize_date, parse_num

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger(__name__).exception("Failed to inject truststore for TWSE adapter")

_twse_db_lock = threading.RLock()
_cache_pruner: Callable[[], None] | None = None


def set_twse_cache_pruner(pruner: Callable[[], None] | None) -> None:
    global _cache_pruner
    _cache_pruner = pruner


def _prune_compute_caches() -> None:
    if _cache_pruner is not None:
        _cache_pruner()


def fetch_twse_eod_all() -> tuple[str, int]:
    data = request_json(TWSE_STOCK_DAY_ALL, retries=3, retry_wait=5)
    if not isinstance(data, list) or not data:
        raise RuntimeError("TWSE STOCK_DAY_ALL returned no rows")
    # STOCK_DAY_ALL is frequently undated.  Never stamp an inferred calendar
    # date onto a payload: on holidays, typhoon closures, or source delay that
    # would turn the previous trading day's prices into a fictitious new bar.
    inferred_dates = [normalize_date(r.get("Date")) for r in data if isinstance(r, dict)]
    inferred_dates = [d for d in inferred_dates if d]
    if not inferred_dates:
        raise RuntimeError("TWSE STOCK_DAY_ALL has no explicit data date; refusing inferred-date write")
    data_date = max(inferred_dates)
    ts = time.time()
    count = 0
    with _twse_db_lock, closing(db()) as conn:
        history_candidates: list[dict[str, Any]] = []
        for row in data:
            code_raw = str(row.get("Code") or row.get("stock_id") or "").strip()
            if not re.fullmatch(r"\d{4}", code_raw):
                continue
            code = code_raw
            name = str(row.get("Name") or row.get("name") or "").strip()
            d = normalize_date(row.get("Date"))
            if not d or d != data_date:
                continue
            open_ = parse_num(row.get("OpeningPrice"))
            high = parse_num(row.get("HighestPrice"))
            low = parse_num(row.get("LowestPrice"))
            close = parse_num(row.get("ClosingPrice"))
            vol = parse_num(row.get("TradeVolume"))
            amount = parse_num(row.get("TradeValue"))
            change_value = parse_num(row.get("Change"))
            tx = parse_num(row.get("Transaction"))
            history_row = {
                "date": d,
                "code": code.zfill(4),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": vol,
                "amount": amount,
                "volume_unit": "shares",
                "source": "TWSE OpenAPI",
                "source_quality": "official",
                "updated_at": ts,
                "fetched_at": ts,
                "market": "listed",
            }
            if not assess_daily_ohlcv(history_row)["ready"]:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO eod_price VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d, code.zfill(4), name, open_, high, low, close, vol, amount, change_value, tx, "TWSE OpenAPI", ts),
            )
            history_candidates.append(history_row)
            count += 1
        history_written = upsert_daily_ohlcv_rows(conn, history_candidates)
        conn.commit()
    if history_written != count:
        logging.info(
            "TWSE EOD parsed %s official rows; wrote %s and preserved/rejected %s "
            "through centralized OHLCV quality/source-priority gates",
            count,
            history_written,
            count - history_written,
        )
    set_status("twse_eod", "fresh", f"TWSE OpenAPI official rows updated {history_written}, date {data_date}")
    _prune_compute_caches()
    return data_date, history_written


def fetch_twse_stock_day_for_code(code: str, name: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    """Refresh one stock's latest monthly daily K from TWSE official STOCK_DAY.

    The all-market OpenAPI endpoint often lags after close.  This endpoint is
    per-stock but usually updates earlier and matches the historical quote
    tables used by public securities sites for OHLC, volume, amount and PE
    validation.
    """
    code = str(code or "").strip().zfill(4)
    if not re.fullmatch(r"\d{4}", code):
        return {"code": code, "ok": False, "error": "invalid code"}
    target = normalize_date(target_date) or recent_market_date_for_eod()
    params = {
        "response": "json",
        "date": target.replace("-", ""),
        "stockNo": code,
    }
    data = request_json(
        TWSE_STOCK_DAY_BY_CODE,
        params=params,
        headers={**HEADERS, "Referer": "https://www.twse.com.tw/", "Accept": "application/json, text/javascript, */*; q=0.01"},
        retries=3,
        retry_wait=2.5,
        timeout=15,
    )
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"code": code, "ok": False, "error": "TWSE STOCK_DAY returned no rows"}
    latest = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 9:
            continue
        d = normalize_date(row[0])
        if not d:
            continue
        if latest is None or d > latest[0]:
            latest = (d, row)
    if not latest:
        return {"code": code, "ok": False, "error": "TWSE STOCK_DAY has no parsable date"}
    d, row = latest
    if d < target:
        return {"code": code, "ok": False, "date": d, "target_date": target, "error": "latest row is older than target"}
    ts = time.time()
    open_ = parse_num(row[3])
    high = parse_num(row[4])
    low = parse_num(row[5])
    close = parse_num(row[6])
    volume = parse_num(row[1])
    amount = parse_num(row[2])
    change_value = parse_num(row[7])
    transactions = parse_num(row[8])
    latest_history_row = {
        "date": d,
        "code": code,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }
    latest_quality = assess_daily_ohlcv(latest_history_row)
    if not latest_quality["ready"]:
        return {"code": code, "ok": False, "date": d, "error": latest_quality["reason"]}
    with _twse_db_lock, closing(db()) as conn:
        if not name:
            prev_name = conn.execute("SELECT name FROM eod_price WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            name = prev_name["name"] if prev_name and prev_name["name"] else ""
        conn.execute(
            """
            INSERT INTO eod_price(
                date,code,name,open,high,low,close,volume,amount,
                change_value,transactions,source,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date,code) DO UPDATE SET
                name=excluded.name,open=excluded.open,high=excluded.high,
                low=excluded.low,close=excluded.close,volume=excluded.volume,
                amount=excluded.amount,change_value=excluded.change_value,
                transactions=excluded.transactions,source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (d, code, name or "", open_, high, low, close, volume, amount, change_value, transactions, "TWSE STOCK_DAY", ts),
        )
        monthly_candidates: list[dict[str, Any]] = []
        for monthly_row in rows:
            if not isinstance(monthly_row, list) or len(monthly_row) < 9:
                continue
            monthly_date = normalize_date(monthly_row[0])
            monthly_close = parse_num(monthly_row[6])
            if not monthly_date or monthly_close is None or monthly_date > target:
                continue
            monthly_candidates.append({
                "date": monthly_date,
                "code": code,
                "open": parse_num(monthly_row[3]),
                "high": parse_num(monthly_row[4]),
                "low": parse_num(monthly_row[5]),
                "close": monthly_close,
                "volume": parse_num(monthly_row[1]),
                "amount": parse_num(monthly_row[2]),
                "volume_unit": "shares",
                "source": "TWSE STOCK_DAY",
                "source_quality": "official",
                "updated_at": ts,
                "fetched_at": ts,
                "market": "listed",
            })
        history_rows = upsert_daily_ohlcv_rows(conn, monthly_candidates)
        conn.commit()
    return {
        "code": code,
        "ok": True,
        "date": d,
        "close": close,
        "volume": volume,
        "amount": amount,
        "history_rows": history_rows,
    }


def fetch_twse_stock_month_rows(code: str, month_date: str) -> dict[str, Any]:
    """Read one calendar month's official daily OHLCV without writing DB."""

    code = str(code or "").strip().zfill(4)
    target = normalize_date(month_date)
    if not re.fullmatch(r"\d{4}", code):
        return {"code": code, "month": target, "ok": False, "rows": [], "error": "invalid code"}
    if not target:
        return {"code": code, "month": None, "ok": False, "rows": [], "error": "invalid month_date"}
    data = request_json(
        TWSE_STOCK_DAY_BY_CODE,
        params={"response": "json", "date": target.replace("-", "")[:6] + "01", "stockNo": code},
        headers={**HEADERS, "Referer": "https://www.twse.com.tw/", "Accept": "application/json, text/javascript, */*; q=0.01"},
        retries=3,
        retry_wait=1.0,
        timeout=20,
    )
    raw_rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(raw_rows, list):
        return {"code": code, "month": target[:7], "ok": False, "rows": [], "error": "TWSE STOCK_DAY returned no row list"}
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) < 9:
            continue
        trade_date = normalize_date(raw[0])
        close = parse_num(raw[6])
        if not trade_date or close is None:
            continue
        rows.append({
            "date": trade_date,
            "code": code,
            "open": parse_num(raw[3]),
            "high": parse_num(raw[4]),
            "low": parse_num(raw[5]),
            "close": close,
            "volume": parse_num(raw[1]),
            "amount": parse_num(raw[2]),
            "volume_unit": "shares",
            "source": "TWSE STOCK_DAY",
            "source_quality": "OK",
            "market": "listed",
        })
    rows.sort(key=lambda row: str(row["date"]))
    return {
        "code": code,
        "month": target[:7],
        "ok": bool(rows),
        "rows": rows,
        "row_count": len(rows),
        "error": None if rows else str(data.get("stat") or "TWSE STOCK_DAY returned no valid rows"),
    }


def refresh_twse_stock_day_codes(items: list[dict[str, Any]] | list[str], target_date: str | None = None, sleep_seconds: float = 0.15) -> dict[str, Any]:
    target = normalize_date(target_date) or recent_market_date_for_eod()
    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip().zfill(4)
            name = str(item.get("name") or "")
        else:
            code = str(item or "").strip().zfill(4)
            name = ""
        if re.fullmatch(r"\d{4}", code):
            normalized.append({"code": code, "name": name})
    stats = {"target_date": target, "total": len(normalized), "updated": 0, "failed": []}
    for idx, item in enumerate(normalized, 1):
        try:
            res = fetch_twse_stock_day_for_code(item["code"], item.get("name"), target)
            if res.get("ok"):
                stats["updated"] += 1
            else:
                stats["failed"].append(res)
        except Exception as exc:
            stats["failed"].append({"code": item["code"], "error": safe_error(exc)})
        if sleep_seconds and idx < len(normalized):
            time.sleep(sleep_seconds)
    set_status("twse_stock_day", "fresh" if stats["updated"] else "stale", f"TWSE 鍊嬭偂鏃鏇存柊 {stats['updated']}/{stats['total']} 妾旓紝鐩 {target}")
    _prune_compute_caches()
    return stats


def fetch_twse_valuation_all() -> int:
    data = request_json(TWSE_BWIBBU_ALL, retries=3, retry_wait=5)
    if not isinstance(data, list) or not data:
        raise RuntimeError("TWSE BWIBBU_ALL returned no rows")
    inferred_dates = [normalize_date(r.get("Date")) for r in data if isinstance(r, dict)]
    inferred_dates = [d for d in inferred_dates if d]
    if not inferred_dates:
        raise RuntimeError("TWSE BWIBBU_ALL has no explicit data date; refusing inferred-date write")
    data_date = max(inferred_dates)
    ts = time.time()
    count = 0
    with _twse_db_lock, closing(db()) as conn:
        for row in data:
            code_raw = str(row.get("Code") or "").strip()
            if not re.fullmatch(r"\d{4}", code_raw):
                continue
            code = code_raw
            dy = parse_num(row.get("DividendYield"))
            pe = parse_num(row.get("PEratio"))
            pb = parse_num(row.get("PBratio"))
            conn.execute(
                "INSERT OR REPLACE INTO valuation(date,code,dividend_yield,pe,pb,source,updated_at,eps,eps_source) VALUES(?,?,?,?,?,?,?,?,?)",
                (data_date, code.zfill(4), dy, pe, pb, "TWSE OpenAPI", ts, None, None),
            )
            count += 1
        conn.commit()
    set_status("twse_valuation", "fresh", f"TWSE OpenAPI valuation updated {count} rows, date {data_date}")
    _prune_compute_caches()
    return count


def normalize_twse_stock_day_all_row(row: dict[str, Any], data_date: str | None = None) -> dict[str, Any] | None:
    code_raw = str(row.get("Code") or row.get("stock_id") or "").strip()
    if not re.fullmatch(r"\d{4}", code_raw):
        return None
    close = parse_num(row.get("ClosingPrice"))
    row_date = normalize_date(row.get("Date"))
    if close is None or not row_date or (data_date and row_date != data_date):
        return None
    return {
        "date": row_date,
        "code": code_raw,
        "name": str(row.get("Name") or row.get("name") or "").strip(),
        "open": parse_num(row.get("OpeningPrice")),
        "high": parse_num(row.get("HighestPrice")),
        "low": parse_num(row.get("LowestPrice")),
        "close": close,
        "volume": parse_num(row.get("TradeVolume")),
        "amount": parse_num(row.get("TradeValue")),
        "volume_unit": "shares",
        "market": "listed",
        "source": "TWSE_OFFICIAL",
        "source_quality": "OK",
        "raw": row,
    }


def fetch_twse_stock_day_all_rows() -> dict[str, Any]:
    """Fetch TWSE official all-market daily OHLCV rows without writing DB."""
    out = {
        "ok": False,
        "source": "TWSE_OFFICIAL",
        "url": TWSE_STOCK_DAY_ALL,
        "rows": 0,
        "valid_rows": 0,
        "items": [],
        "columns": [],
        "error": None,
    }
    try:
        data = request_json(TWSE_STOCK_DAY_ALL, retries=2, retry_wait=2, timeout=20)
        rows = data if isinstance(data, list) else []
        inferred_dates = [normalize_date(r.get("Date")) for r in rows if isinstance(r, dict)]
        inferred_dates = [d for d in inferred_dates if d]
        if not inferred_dates:
            out.update({
                "status": "source_delayed",
                "error": "TWSE STOCK_DAY_ALL has no explicit data date; inferred dates are forbidden",
                "rows": len(rows),
                "columns": list(rows[0].keys()) if rows else [],
            })
            return out
        data_date = max(inferred_dates)
        items = [
            item
            for item in (normalize_twse_stock_day_all_row(row, data_date) for row in rows if isinstance(row, dict))
            if item
        ]
        out.update({
            "ok": bool(items),
            "rows": len(rows),
            "valid_rows": len(items),
            "items": items,
            "columns": list(rows[0].keys()) if rows else [],
            "data_date": data_date,
        })
    except Exception as exc:
        out["error"] = safe_error(exc)
    return out
