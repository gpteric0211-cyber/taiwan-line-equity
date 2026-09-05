from __future__ import annotations

"""Yahoo / yfinance read-only quote adapter.

This module owns only external Yahoo / yfinance quote fetch behavior and the
small in-memory cache needed for that fetch path.

It does not:
- import app.py or FastAPI
- write the database
- call set_status()
- start background tasks or threads
- compute scoring or shape API responses
"""

import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger(__name__).exception("Failed to inject truststore for Yahoo adapter")

from core.config import configure_yfinance_cache, safe_error
from core.http import request_json
from core.utils import parse_num
from repository.market_profile_repository import yahoo_symbols_for_code


US_MARKET_CACHE_TTL_SECONDS = int(os.getenv("US_MARKET_CACHE_TTL_SECONDS", "120"))
YAHOO_REQUEST_TIMEOUT_SECONDS = 10
YAHOO_REQUEST_RETRIES = 2
YAHOO_SYMBOL_DELAY_SECONDS = 0.8
YAHOO_MAX_BODY_BYTES = 2_000_000
_us_market_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_us_market_lock = threading.RLock()
_yahoo_request_lock = threading.RLock()
_last_symbol_request_ts: dict[str, float] = {}


def normalize_tw_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    text = re.sub(r"\.(TW|TWO)$", "", text)
    return text.strip()


def _sleep_for_symbol(symbol: str) -> None:
    clean = normalize_tw_symbol(symbol)
    with _yahoo_request_lock:
        last = _last_symbol_request_ts.get(clean, 0.0)
        wait = YAHOO_SYMBOL_DELAY_SECONDS - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _last_symbol_request_ts[clean] = time.time()


def _yahoo_request(url: str, *, symbol: str = "", expect_json: bool = False) -> Any:
    headers = {
        "User-Agent": "Mozilla/5.0 TaiwanStockDashboard/1.0",
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    }
    clean_symbol = normalize_tw_symbol(symbol)
    last_error = None
    for attempt in range(YAHOO_REQUEST_RETRIES + 1):
        try:
            if clean_symbol:
                _sleep_for_symbol(clean_symbol)
            resp = requests.get(url, headers=headers, timeout=YAHOO_REQUEST_TIMEOUT_SECONDS)
            body = resp.content[:YAHOO_MAX_BODY_BYTES]
            if resp.status_code in {403, 429}:
                logging.warning(
                    "Yahoo request throttled symbol=%s url_type=%s status=%s retry=%s",
                    clean_symbol,
                    "json_endpoint" if expect_json else "html_fallback",
                    resp.status_code,
                    attempt,
                )
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            if expect_json:
                return resp.json()
            encoding = resp.encoding or resp.apparent_encoding or "utf-8"
            return body.decode(encoding, errors="replace")
        except Exception as exc:
            last_error = exc
            logging.warning(
                "Yahoo request failed symbol=%s url_type=%s retry=%s error=%s",
                clean_symbol,
                "json_endpoint" if expect_json else "html_fallback",
                attempt,
                safe_error(exc),
            )
            if attempt < YAHOO_REQUEST_RETRIES:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(safe_error(last_error or "Yahoo request failed"))


def _empty_yahoo_rows(symbol: str, source_type: str, reason: str) -> dict[str, Any]:
    return {
        "symbol": normalize_tw_symbol(symbol),
        "ok": False,
        "rows": [],
        "source_status": "unavailable",
        "source_type": source_type,
        "reason": reason,
    }


def fetch_yahoo_time_sales(symbol: str) -> dict[str, Any]:
    """Fetch Yahoo 5-second aggregated quote history when available.

    Yahoo does not provide official per-trade tick data here.  Returned rows are
    only accepted when the response has parseable timestamp, price, and volume.
    """
    clean = normalize_tw_symbol(symbol)
    last_reason = ""
    for ticker in yahoo_symbols_for_code(clean):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=5s"
            data = _yahoo_request(url, symbol=clean, expect_json=True)
            result = ((data.get("chart") or {}).get("result") or [None])[0] or {}
            timestamps = result.get("timestamp") or []
            quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            rows = []
            for ts, price, volume in zip(timestamps, closes, volumes):
                price_num = parse_num(price)
                volume_num = parse_num(volume)
                if price_num is None or volume_num is None:
                    continue
                rows.append({
                    "time": datetime.fromtimestamp(int(ts), tz=ZoneInfo("Asia/Taipei")).strftime("%H:%M:%S"),
                    "price": float(price_num),
                    "change": None,
                    "volume": int(float(volume_num)),
                })
            if rows:
                return {
                    "symbol": clean,
                    "yahoo_symbol": ticker,
                    "ok": True,
                    "rows": rows,
                    "source_status": "ok",
                    "source_type": "json_endpoint",
                    "reason": "",
                }
            last_reason = "Yahoo JSON response has no valid 5-second rows"
        except Exception as exc:
            last_reason = safe_error(exc)
            continue
    return _empty_yahoo_rows(clean, "json_endpoint", last_reason or "Yahoo Taiwan symbol unavailable")


def fetch_yahoo_volume_profile(symbol: str) -> dict[str, Any]:
    """Fetch Yahoo price-volume distribution if a parseable source is available.

    Current Yahoo chart JSON does not expose a reliable volume-by-price profile.
    This function returns an explicit unavailable state instead of inventing
    rows from OHLCV data.
    """
    clean = normalize_tw_symbol(symbol)
    last_reason = ""
    for ticker in yahoo_symbols_for_code(clean):
        try:
            # Probe the standard chart endpoint only to verify availability; do not
            # reinterpret time bars as volume-by-price distribution.
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=5m"
            data = _yahoo_request(url, symbol=clean, expect_json=True)
            result = ((data.get("chart") or {}).get("result") or [None])[0] or {}
            if result:
                return _empty_yahoo_rows(clean, "json_endpoint", "Yahoo chart JSON has no verified volume-profile fields")
            last_reason = "Yahoo chart response is empty"
        except Exception as exc:
            last_reason = safe_error(exc)
            continue
    return _empty_yahoo_rows(clean, "json_endpoint", last_reason or "Yahoo Taiwan symbol unavailable")


def fetch_yfinance_quote(ticker: str) -> dict[str, Any]:
    ticker = str(ticker).strip().upper()
    now = time.time()
    base_out: dict[str, Any] = {
        "ticker": ticker,
        "ok": False,
        "price": None,
        "previous_close": None,
        "change": None,
        "change_pct": None,
        "currency": "USD",
        "date": None,
        "market_state": None,
        "regular_market_time": None,
        "exchange_timezone": None,
        "error": "尚未取得報價",
        "source": "Yahoo Finance chart",
    }
    with _us_market_lock:
        cached = _us_market_cache.get(ticker)
        if cached and now - cached[0] < US_MARKET_CACHE_TTL_SECONDS:
            out = dict(base_out)
            out.update(dict(cached[1]))
            return out
    out = dict(base_out)
    chart_error = None
    try:
        data = request_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"range": "5d", "interval": "1d"},
            retries=1,
            timeout=15,
        )
        result = ((data.get("chart") or {}).get("result") or [None])[0] or {}
        meta = result.get("meta") or {}
        indicators = result.get("indicators") or {}
        quote = ((indicators.get("quote") or [None])[0] or {})
        closes = [parse_num(x) for x in (quote.get("close") or [])]
        closes = [x for x in closes if x is not None]
        timestamps = result.get("timestamp") or []
        last_hist_close = closes[-1] if closes else None
        prev_hist_close = closes[-2] if len(closes) >= 2 else None
        market_state = str(meta.get("marketState") or "").upper()
        last_price = parse_num(meta.get("regularMarketPrice")) or last_hist_close
        prev_close = parse_num(
            meta.get("regularMarketPreviousClose")
            or meta.get("previousClose")
            or meta.get("chartPreviousClose")
        )
        if last_price is not None:
            if market_state in {"REGULAR", "PRE", "POST", "PREPRE", "POSTPOST"}:
                if last_hist_close is not None:
                    prev_close = last_hist_close
            elif last_hist_close is not None:
                last_price = last_hist_close
                if prev_hist_close is not None:
                    prev_close = prev_hist_close
        if prev_close is None and prev_hist_close is not None:
            prev_close = prev_hist_close
        change = None
        change_pct = None
        if last_price is not None and prev_close not in (None, 0):
            change = last_price - prev_close
            change_pct = change / prev_close * 100
        last_dt = None
        if timestamps:
            try:
                last_dt = datetime.fromtimestamp(int(timestamps[-1]), tz=ZoneInfo("UTC")).date().isoformat()
            except Exception:
                last_dt = None
        if last_price is not None:
            out.update({
                "ok": True,
                "price": round(last_price, 4),
                "previous_close": round(prev_close, 4) if prev_close is not None else None,
                "change": round(change, 4) if change is not None else None,
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "currency": meta.get("currency") or "USD",
                "date": last_dt,
                "market_state": meta.get("marketState"),
                "regular_market_time": meta.get("regularMarketTime"),
                "exchange_timezone": meta.get("exchangeTimezoneName"),
                "error": None,
                "source": "Yahoo Finance chart",
            })
            with _us_market_lock:
                _us_market_cache[ticker] = (time.time(), dict(out))
            return out
    except Exception as exc:
        chart_error = safe_error(exc)
    try:
        import yfinance as yf  # type: ignore
        configure_yfinance_cache(yf)
        tk = yf.Ticker(ticker)
        hist = tk.history(period="5d", interval="1d", auto_adjust=False)
        fast = getattr(tk, "fast_info", {}) or {}
        last_price = None
        prev_close = None
        currency = None
        last_dt = None

        def _to_float(v):
            try:
                if v is None:
                    return None
                return float(v)
            except Exception:
                return None

        try:
            last_price = _to_float(fast.get("last_price") or fast.get("lastPrice") or fast.get("regular_market_price"))
            prev_close = _to_float(fast.get("previous_close") or fast.get("previousClose"))
            currency = fast.get("currency")
        except Exception as exc:
            logging.warning("Yahoo fast_info parse failed for %s: %s", ticker, safe_error(exc))
        if hist is not None and not hist.empty:
            closes = [_to_float(x) for x in hist["Close"].dropna().tolist()]
            closes = [x for x in closes if x is not None]
            if closes:
                last_hist_close = closes[-1]
                prev_hist_close = closes[-2] if len(closes) >= 2 else None
                if last_price is None:
                    last_price = last_hist_close
                if last_price is not None and last_hist_close is not None and abs(last_price - last_hist_close) > 1e-9:
                    prev_close = last_hist_close
                elif prev_close is None and prev_hist_close is not None:
                    prev_close = prev_hist_close
            try:
                last_dt = str(hist.index[-1].date()) if hasattr(hist.index[-1], "date") else str(hist.index[-1])
            except Exception:
                last_dt = None
        change = None
        change_pct = None
        if last_price is not None and prev_close not in (None, 0):
            change = last_price - prev_close
            change_pct = change / prev_close * 100
        out.update({
            "ok": bool(last_price is not None),
            "price": round(last_price, 4) if last_price is not None else None,
            "previous_close": round(prev_close, 4) if prev_close is not None else None,
            "change": round(change, 4) if change is not None else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "currency": currency or "USD",
            "date": last_dt,
            "market_state": None,
            "regular_market_time": None,
            "exchange_timezone": None,
            "error": None if last_price is not None else "Yahoo Finance 暫無可用價格",
            "source": "Yahoo Finance / yfinance",
        })
    except Exception as exc:
        err = safe_error(exc) or "Yahoo Finance 抓取失敗"
        if chart_error:
            err = f"chart API 失敗：{chart_error}；yfinance 失敗：{err}"
        out.update({"ok": False, "error": err})
    with _us_market_lock:
        _us_market_cache[ticker] = (time.time(), dict(out))
    return out
