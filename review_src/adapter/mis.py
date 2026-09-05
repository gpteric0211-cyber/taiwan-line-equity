from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any, Callable

from core.cache import _row_cache, _row_cache_lock
from core.config import (
    HEADERS,
    MIS_AUTO_UPDATE_MARKET_HOURS,
    MIS_CACHE_TTL_SECONDS,
    MIS_POLL_SECONDS,
    MIS_SESSION_EVIDENCE_CODES,
    MIS_SESSION_EVIDENCE_MAX_AGE_SECONDS,
    TWSE_MIS_STOCK_INFO,
    safe_error,
)
from core.db import db
from core.http import request_json
from core.market_calendar_cache import taiwan_market_day_status
from core.market_session import market_is_open_now
from core.status import set_status
from core.utils import now_tpe, parse_num, today_iso
from repository.watchlist_repository import get_watchlist_codes

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger(__name__).exception("Failed to inject truststore for MIS adapter")

_mis_quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_mis_lock = threading.RLock()
MIS_QUOTE_CACHE_MAXSIZE = int(os.getenv("MIS_QUOTE_CACHE_MAXSIZE", "600"))
_mis_daemon_started = False
_mis_db_lock = threading.RLock()


def mis_headers() -> dict[str, str]:
    return {
        **HEADERS,
        "Referer": "https://mis.twse.com.tw/",
        "Origin": "https://mis.twse.com.tw",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }


def _mis_channels_for_codes(codes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        code = str(raw or "").strip().zfill(4)
        if not re.fullmatch(r"\d{4}", code):
            continue
        for prefix in ("tse", "otc"):
            ch = f"{prefix}_{code}.tw"
            if ch not in seen:
                seen.add(ch)
                out.append(ch)
    return out


def _mis_code_from_row(row: dict[str, Any]) -> str | None:
    for key in ("c", "ch", "key"):
        text = str(row.get(key) or "")
        m = re.search(r"(\d{4})", text)
        if m:
            return m.group(1)
    return None


def _mis_parse_volume(raw: Any) -> int | None:
    value = parse_num(raw)
    if value is None:
        return None
    # MIS v is normally accumulated trading units/lots. Store shares so it is
    # comparable with TWSE/FinMind volume columns.
    return int(round(value * 1000))


def _normalize_mis_source_date(value: Any) -> str | None:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _mis_tlong_datetime(value: Any, *, as_of: datetime) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        raw = float(text)
    except (TypeError, ValueError):
        return None
    seconds = raw / 1000.0 if abs(raw) >= 100_000_000_000 else raw
    if abs(seconds) < 1_000_000_000:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=as_of.tzinfo)
    except (OSError, OverflowError, ValueError):
        return None


def mis_source_date_evidence(row: dict[str, Any], *, as_of: datetime | None = None) -> dict[str, Any]:
    """Validate dates explicitly supplied by TWSE MIS (``d`` and/or ``tlong``)."""

    now = as_of or now_tpe()
    raw_d = str(row.get("d") or "").strip()
    raw_tlong = str(row.get("tlong") or "").strip()
    date_from_d = _normalize_mis_source_date(raw_d) if raw_d else None
    tlong_at = _mis_tlong_datetime(raw_tlong, as_of=now) if raw_tlong else None
    date_from_tlong = tlong_at.date().isoformat() if tlong_at else None
    if raw_d and not date_from_d:
        return {"ok": False, "reason": "invalid_mis_d", "source_date": None}
    if raw_tlong and not tlong_at:
        return {"ok": False, "reason": "invalid_mis_tlong", "source_date": None}
    source_dates = {value for value in (date_from_d, date_from_tlong) if value}
    if not source_dates:
        return {"ok": False, "reason": "missing_mis_d_and_tlong", "source_date": None}
    if len(source_dates) != 1:
        return {
            "ok": False,
            "reason": "mis_d_tlong_date_conflict",
            "source_date": None,
            "date_from_d": date_from_d,
            "date_from_tlong": date_from_tlong,
        }
    source_date = next(iter(source_dates))
    if source_date != now.date().isoformat():
        return {
            "ok": False,
            "reason": "mis_source_date_not_today",
            "source_date": source_date,
            "expected_date": now.date().isoformat(),
        }
    return {
        "ok": True,
        "reason": "current_source_date",
        "source_date": source_date,
        "date_from_d": date_from_d,
        "date_from_tlong": date_from_tlong,
        "tlong_at": tlong_at.isoformat(timespec="seconds") if tlong_at else None,
        "date_fields": [field for field, value in (("d", date_from_d), ("tlong", date_from_tlong)) if value],
    }


def _mis_trade_datetime(row: dict[str, Any], source_date: str, *, as_of: datetime) -> datetime | None:
    raw = str(row.get("t") or "").strip()
    if not raw:
        return None
    try:
        trade_time = datetime.strptime(raw, "%H:%M:%S").time()
        return datetime.combine(datetime.fromisoformat(source_date).date(), trade_time, tzinfo=as_of.tzinfo)
    except ValueError:
        return None


def parse_mis_quote(
    row: dict[str, Any],
    previous: dict[str, Any] | None = None,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any] | None:
    now = as_of or now_tpe()
    source_evidence = mis_source_date_evidence(row, as_of=now)
    if not source_evidence.get("ok"):
        return None
    price = parse_num(row.get("z"))
    # During opening/illiquid moments z may be "-" while b/a exist; do not guess
    # from bid/ask. Fall back to previous close only for display if no trade yet.
    prev_close = parse_num(row.get("y"))
    open_price = parse_num(row.get("o"))
    high = parse_num(row.get("h"))
    low = parse_num(row.get("l"))
    if price is None:
        return None
    change = None
    if prev_close not in (None, 0):
        change = price - float(prev_close)
    change_pct = None
    if change is not None and prev_close not in (None, 0):
        change_pct = float(change) / float(prev_close) * 100
    cumulative_volume = _mis_parse_volume(row.get("v"))
    prev_cum = parse_num((previous or {}).get("cumulative_volume"))
    volume_delta = None
    if cumulative_volume is not None and prev_cum is not None:
        delta = int(cumulative_volume - int(prev_cum))
        volume_delta = delta if delta >= 0 else None
    trade_time = str(row.get("t") or "").strip() or None
    fetched_at = now.strftime("%H:%M:%S")
    return {
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "high": high,
        "low": low,
        "open": open_price,
        "prev_close": prev_close,
        "cumulative_volume": cumulative_volume,
        "volume_delta_since_last_poll": volume_delta,
        "trade_time": trade_time,
        "quote_date": source_evidence["source_date"],
        "source_date_fields": source_evidence.get("date_fields") or [],
        "source_tlong": source_evidence.get("tlong_at"),
        "fetched_at": fetched_at,
        "quote_source": "TWSE MIS",
        "source": "TWSE MIS",
        "is_realtime": True,
        "is_estimated_tick_volume": True,
    }


def get_current_session_evidence(
    codes: list[str] | tuple[str, ...] | None = None,
    *,
    as_of: datetime | None = None,
    max_age_seconds: int | None = None,
    requester: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read TWSE MIS without DB/cache writes and prove the current session is live."""

    now = as_of or now_tpe()
    day_status = taiwan_market_day_status(now.date())
    base: dict[str, Any] = {
        "ok": False,
        "status": "SOURCE_DELAYED",
        "writes_db": False,
        "updates_cache": False,
        "as_of": now.isoformat(timespec="seconds"),
        "calendar": day_status,
        "request_count": 0,
        "accepted_evidence": [],
        "rejections": [],
    }
    if not day_status.get("is_trading_day"):
        return {
            **base,
            "status": "MARKET_CLOSED",
            "clean_skip": True,
            "reason": str(day_status.get("reason") or "market_closed"),
        }
    if not day_status.get("verified"):
        return {
            **base,
            "status": "CALENDAR_UNVERIFIED",
            "clean_skip": False,
            "reason": "official_calendar_year_unavailable",
        }
    now_minutes = now.hour * 60 + now.minute
    if not 9 * 60 <= now_minutes <= 13 * 60 + 30:
        return {
            **base,
            "status": "OUTSIDE_CURRENT_SESSION",
            "clean_skip": True,
            "reason": "current time is outside TWSE regular session 09:00-13:30",
        }

    evidence_codes = sorted(
        {
            str(code or "").strip().zfill(4)
            for code in (codes or MIS_SESSION_EVIDENCE_CODES)
            if str(code or "").strip().isdigit()
        }
    )
    channels = _mis_channels_for_codes(evidence_codes)
    base["evidence_codes"] = evidence_codes
    if not channels:
        return {**base, "reason": "no_valid_mis_evidence_codes"}

    get_json = requester or request_json
    rows: list[dict[str, Any]] = []
    try:
        for index in range(0, len(channels), 50):
            chunk = channels[index:index + 50]
            payload = get_json(
                TWSE_MIS_STOCK_INFO,
                params={"ex_ch": "|".join(chunk), "json": "1", "delay": "0", "_": int(now.timestamp() * 1000)},
                headers=mis_headers(),
                retries=1,
                timeout=8,
            )
            base["request_count"] += 1
            payload_rows = payload.get("msgArray") if isinstance(payload, dict) else None
            if isinstance(payload_rows, list):
                rows.extend(row for row in payload_rows if isinstance(row, dict))
    except Exception as exc:
        return {**base, "reason": "mis_request_failed", "error": safe_error(exc)}

    max_age = max(60, int(max_age_seconds or MIS_SESSION_EVIDENCE_MAX_AGE_SECONDS))
    allowed_future_seconds = 120
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        code = _mis_code_from_row(row)
        if not code or code not in evidence_codes:
            continue
        date_evidence = mis_source_date_evidence(row, as_of=now)
        if not date_evidence.get("ok"):
            rejected.append({"code": code, "reason": date_evidence.get("reason")})
            continue
        if parse_num(row.get("z")) is None:
            rejected.append({"code": code, "reason": "missing_current_trade_price_z"})
            continue
        trade_at = _mis_trade_datetime(row, str(date_evidence["source_date"]), as_of=now)
        if not trade_at:
            rejected.append({"code": code, "reason": "missing_current_session_time"})
            continue
        source_field = "d+t"
        source_at = trade_at
        age_seconds = (now - source_at).total_seconds()
        source_minutes = source_at.hour * 60 + source_at.minute
        if not 9 * 60 <= source_minutes <= 13 * 60 + 35:
            rejected.append({"code": code, "reason": "source_time_outside_regular_session"})
            continue
        if age_seconds < -allowed_future_seconds:
            rejected.append({"code": code, "reason": "source_time_in_future"})
            continue
        if age_seconds > max_age:
            rejected.append({"code": code, "reason": "stale_current_session_evidence", "age_seconds": round(age_seconds, 1)})
            continue
        accepted.append(
            {
                "code": code,
                "source_date": date_evidence["source_date"],
                "source_time": source_at.isoformat(timespec="seconds"),
                "source_time_field": source_field,
                "age_seconds": round(max(age_seconds, 0.0), 1),
            }
        )

    base["accepted_evidence"] = accepted
    base["rejections"] = rejected
    base["response_rows"] = len(rows)
    base["max_age_seconds"] = max_age
    if not accepted:
        return {**base, "reason": "no_current_session_mis_evidence"}
    return {
        **base,
        "ok": True,
        "status": "OK",
        "clean_skip": False,
        "reason": "current_session_confirmed_by_twse_mis",
    }


def persist_mis_quote_snapshots(quotes: dict[str, dict[str, Any]]) -> None:
    if not quotes:
        return
    snap_dt = now_tpe()
    snapshot_ts = snap_dt.isoformat(timespec="seconds")
    cutoff_date = (snap_dt.date() - timedelta(days=60)).isoformat()
    rows = []
    for code, quote in quotes.items():
        quote_date = str(quote.get("quote_date") or "").strip()
        if quote_date != snap_dt.date().isoformat():
            continue
        rows.append((
            snapshot_ts,
            quote_date,
            str(code).zfill(4),
            quote.get("price"),
            quote.get("change_pct"),
            quote.get("cumulative_volume"),
            quote.get("volume_delta_since_last_poll"),
            quote.get("trade_time"),
            quote.get("fetched_at"),
            quote.get("quote_source") or quote.get("source") or "TWSE MIS",
            1 if quote.get("is_estimated_tick_volume") else 0,
        ))
    if not rows:
        return
    try:
        with _mis_db_lock, closing(db()) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO mis_quote_snapshot(
                    snapshot_ts,snapshot_date,code,price,change_pct,cumulative_volume,
                    volume_delta_since_last_poll,trade_time,fetched_at,quote_source,
                    is_estimated_tick_volume
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
            conn.execute("DELETE FROM mis_quote_snapshot WHERE snapshot_date < ?", (cutoff_date,))
            conn.commit()
    except Exception:
        logging.exception("persist MIS quote snapshots failed")


def prune_mis_quote_cache(*, maxsize: int = MIS_QUOTE_CACHE_MAXSIZE) -> int:
    """Keep today's last valid MIS quote, prune older or excessive symbols."""
    today = today_iso()
    removed = 0
    with _mis_lock:
        for code, value in list(_mis_quote_cache.items()):
            quote = value[1] if isinstance(value, tuple) and len(value) > 1 and isinstance(value[1], dict) else {}
            quote_date = str(quote.get("quote_date") or quote.get("date") or "").strip()[:10]
            if quote_date != today:
                _mis_quote_cache.pop(code, None)
                removed += 1
        if maxsize > 0 and len(_mis_quote_cache) > maxsize:
            overflow = len(_mis_quote_cache) - maxsize
            sorted_items = sorted(_mis_quote_cache.items(), key=lambda kv: float((kv[1] or (0,))[0] or 0))
            for key, _ in sorted_items[:overflow]:
                _mis_quote_cache.pop(key, None)
                removed += 1
    return removed


def fetch_mis_quotes_batch(
    codes: list[str],
    *,
    persist: bool = True,
    update_cache: bool = True,
) -> dict[str, dict[str, Any]]:
    clean_codes = sorted({str(c or "").strip().zfill(4) for c in codes if str(c or "").strip()})
    channels = _mis_channels_for_codes(clean_codes)
    if not channels:
        return {}
    updated: dict[str, dict[str, Any]] = {}
    # Use conservative chunks because each stock is queried as tse+otc and the
    # endpoint is URL based.
    for i in range(0, len(channels), 50):
        chunk = channels[i:i + 50]
        payload = request_json(
            TWSE_MIS_STOCK_INFO,
            params={"ex_ch": "|".join(chunk), "json": "1", "delay": "0", "_": int(time.time() * 1000)},
            headers=mis_headers(),
            retries=1,
            timeout=8,
        )
        rows = payload.get("msgArray") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        with _mis_lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = _mis_code_from_row(row)
                if not code or code not in clean_codes:
                    continue
                previous = (_mis_quote_cache.get(code) or (None, None))[1] if update_cache else None
                if isinstance(previous, dict) and str(previous.get("quote_date") or "") != today_iso():
                    previous = None
                quote = parse_mis_quote(row, previous if isinstance(previous, dict) else None)
                if not quote:
                    continue
                if update_cache:
                    _mis_quote_cache[code] = (time.time(), quote)
                updated[code] = quote
        if update_cache:
            prune_mis_quote_cache()
    if updated and persist:
        persist_mis_quote_snapshots(updated)
        with _row_cache_lock:
            _row_cache.clear()
        set_status("mis_quote", "fresh", f"TWSE MIS 即時快照更新 {len(updated)}/{len(clean_codes)} 檔，{now_tpe().strftime('%H:%M:%S')}")
    elif not updated and persist:
        set_status(
            "mis_quote",
            "stale",
            "TWSE MIS 已回應，但沒有來源日期為今日且含成交價 z 的有效報價；不以本機日期、買賣價或高低價猜測",
        )
    return updated


def get_mis_quote_cached(code: str) -> dict[str, Any] | None:
    code = str(code or "").strip().zfill(4)
    now = time.time()
    with _mis_lock:
        cached = _mis_quote_cache.get(code)
        if cached and now - cached[0] <= MIS_CACHE_TTL_SECONDS:
            quote = dict(cached[1])
            if str(quote.get("quote_date") or "") != today_iso():
                return None
            quote["quote_age_seconds"] = round(now - cached[0], 2)
            quote["is_realtime"] = True
            return quote
    return None


def get_mis_quote_latest(code: str) -> dict[str, Any] | None:
    """Return the latest valid MIS quote even after the short realtime TTL.

    Watchlist intraday display must not fall back to historical close simply
    because TWSE MIS temporarily returns z='-' or a poll is delayed. Keep the
    last actual MIS trade price and mark it as cached/stale instead.
    """
    code = str(code or "").strip().zfill(4)
    now = time.time()
    with _mis_lock:
        cached = _mis_quote_cache.get(code)
        if cached:
            quote = dict(cached[1])
            if str(quote.get("quote_date") or "") != today_iso():
                return None
            quote["quote_age_seconds"] = round(now - cached[0], 2)
            quote["is_realtime"] = False
            quote["stale_reason"] = "MIS latest valid trade retained; no newer valid z yet"
            return quote
    return None


def get_mis_quote_latest_snapshot(code: str) -> dict[str, Any] | None:
    code = str(code or "").strip().zfill(4)
    try:
        with closing(db()) as conn:
            row = conn.execute(
                """
                SELECT * FROM mis_quote_snapshot
                WHERE code=? AND snapshot_date=?
                ORDER BY snapshot_ts DESC
                LIMIT 1
                """,
                (code, today_iso()),
            ).fetchone()
        if not row:
            return None
        quote = {
            "price": row["price"],
            "change_pct": row["change_pct"],
            "cumulative_volume": row["cumulative_volume"],
            "volume_delta_since_last_poll": row["volume_delta_since_last_poll"],
            "trade_time": row["trade_time"],
            "quote_date": row["snapshot_date"],
            "fetched_at": row["fetched_at"],
            "quote_source": row["quote_source"] or "TWSE MIS",
            "source": row["quote_source"] or "TWSE MIS",
            "is_realtime": False,
            "is_estimated_tick_volume": bool(row["is_estimated_tick_volume"]),
            "stale_reason": "Loaded from today's persisted MIS snapshot",
        }
        return quote if quote["price"] is not None else None
    except Exception:
        logging.exception("load latest MIS quote snapshot failed")
        return None


def mis_quote_daemon() -> None:
    idle_reported = False
    while True:
        try:
            if MIS_AUTO_UPDATE_MARKET_HOURS and market_is_open_now():
                watch_codes = get_watchlist_codes()
                codes = sorted({str(c).zfill(4) for c in watch_codes})
                fetch_mis_quotes_batch(codes)
                idle_reported = False
                time.sleep(max(2.0, MIS_POLL_SECONDS))
            else:
                if not idle_reported:
                    set_status("mis_quote", "stale", "TWSE MIS idle outside market hours; using latest stored data")
                    idle_reported = True
                time.sleep(30)
        except Exception as exc:
            logging.exception("TWSE MIS quote daemon failed")
            set_status("mis_quote", "stale", f"TWSE MIS quote daemon failed: {safe_error(exc)}")
            time.sleep(max(10.0, MIS_POLL_SECONDS))


def start_mis_quote_daemon() -> None:
    global _mis_daemon_started
    if _mis_daemon_started:
        return
    _mis_daemon_started = True
    threading.Thread(target=mis_quote_daemon, daemon=True).start()
