from __future__ import annotations

import time
import threading
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from adapter.yahoo import fetch_yfinance_quote
from core.config import HEADERS, YAHOO_REQUEST_SLEEP_SECONDS, configure_yfinance_cache, safe_error
from core.date_utils import now_tpe
from core.db import db
from core.http import request_json
from core.market_foundation_schema import upsert_daily_ohlcv_rows
from core.market_session import recent_market_date_for_eod
from core.status import set_status
from core.utils import normalize_date, parse_num
from repository.market_profile_repository import yahoo_symbols_for_code

TAIPEI = ZoneInfo("Asia/Taipei")
_yahoo_db_lock = threading.RLock()


def yahoo_tw_symbols(code: str, market_type: str | None = None) -> list[str]:
    code = str(code).zfill(4)
    return yahoo_symbols_for_code(code, market_type)


def fetch_yahoo_chart_tw_history_rows(
    code: str,
    *,
    days: int = 600,
    market_type: str | None = None,
) -> dict[str, Any]:
    """Fetch Yahoo chart daily rows for independent, read-only auditing.

    Yahoo may normalize historical prices around split/stock-dividend events.
    Callers must align these rows to official trading dates before comparison.
    """

    code = str(code).zfill(4)
    start_day = now_tpe().date() - timedelta(days=max(60, int(days)))
    end_day = now_tpe().date() + timedelta(days=1)
    period1 = int(datetime(start_day.year, start_day.month, start_day.day, tzinfo=TAIPEI).timestamp())
    period2 = int(datetime(end_day.year, end_day.month, end_day.day, tzinfo=TAIPEI).timestamp())
    errors: list[str] = []
    for sym in yahoo_tw_symbols(code, market_type):
        try:
            data = request_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={
                    "period1": period1,
                    "period2": period2,
                    "interval": "1d",
                    "events": "div,splits,capitalGains",
                },
                headers=HEADERS,
                retries=2,
                retry_wait=1,
                timeout=30,
            )
            result = ((data.get("chart") or {}).get("result") or [None])[0]
            if not result:
                errors.append(f"{sym}: no chart result")
                continue
            timestamps = result.get("timestamp") or []
            quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            rows: list[dict[str, Any]] = []
            for idx, raw_ts in enumerate(timestamps):
                try:
                    trade_date = datetime.fromtimestamp(int(raw_ts), tz=TAIPEI).date().isoformat()
                except Exception:
                    continue
                close = parse_num(closes[idx] if idx < len(closes) else None)
                if close is not None:
                    rows.append({
                        "date": trade_date,
                        "open": parse_num(opens[idx] if idx < len(opens) else None),
                        "high": parse_num(highs[idx] if idx < len(highs) else None),
                        "low": parse_num(lows[idx] if idx < len(lows) else None),
                        "close": close,
                        "volume": parse_num(volumes[idx] if idx < len(volumes) else None),
                    })
            rows.sort(key=lambda row: str(row["date"]))
            split_events: list[dict[str, Any]] = []
            raw_splits = ((result.get("events") or {}).get("splits") or {})
            for raw in raw_splits.values():
                try:
                    event_date = datetime.fromtimestamp(int(raw.get("date")), tz=TAIPEI).date().isoformat()
                except Exception:
                    continue
                numerator = parse_num(raw.get("numerator"))
                denominator = parse_num(raw.get("denominator"))
                if numerator is None or denominator is None or numerator <= 0 or denominator <= 0:
                    continue
                split_events.append({
                    "code": code,
                    "event_date": event_date,
                    "numerator": numerator,
                    "denominator": denominator,
                    "pre_event_factor": denominator / numerator,
                    "source": "Yahoo Finance chart split event",
                })
            split_events.sort(key=lambda item: str(item["event_date"]))
            if rows:
                return {"code": code, "symbol": sym, "ok": True, "rows": rows, "row_count": len(rows), "split_events": split_events, "error": None}
            errors.append(f"{sym}: no valid closes")
        except Exception as exc:
            errors.append(f"{sym}: {safe_error(exc)}")
    return {"code": code, "symbol": None, "ok": False, "rows": [], "row_count": 0, "split_events": [], "error": "; ".join(errors) or "Yahoo chart unavailable"}


def upsert_yahoo_chart_tw_history(code: str, days: int = 180, market_type: str | None = None) -> dict[str, Any]:
    # Direct Yahoo chart fallback when yfinance/curl is blocked.
    code = str(code).zfill(4)
    out = {"code": code, "source": None, "rows": 0, "ok": False, "error": None}
    start_day = now_tpe().date() - timedelta(days=max(180, int(days * 2)))
    end_day = now_tpe().date() + timedelta(days=1)
    period1 = int(datetime(start_day.year, start_day.month, start_day.day, tzinfo=TAIPEI).timestamp())
    period2 = int(datetime(end_day.year, end_day.month, end_day.day, tzinfo=TAIPEI).timestamp())
    ts = time.time()
    for sym in yahoo_tw_symbols(code, market_type):
        try:
            data = request_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"period1": period1, "period2": period2, "interval": "1d"},
                headers=HEADERS,
                retries=2,
                retry_wait=1,
                timeout=30,
            )
            result = ((data.get("chart") or {}).get("result") or [None])[0]
            if not result:
                continue
            timestamps = result.get("timestamp") or []
            quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            normalized_rows: list[dict[str, Any]] = []
            count = 0
            with _yahoo_db_lock, closing(db()) as conn:
                for idx, raw_ts in enumerate(timestamps):
                    try:
                        d = datetime.fromtimestamp(int(raw_ts), tz=TAIPEI).date().isoformat()
                    except Exception:
                        continue
                    close_v = parse_num(closes[idx] if idx < len(closes) else None)
                    if close_v is None:
                        continue
                    normalized_rows.append({
                        "date": d,
                        "code": code,
                        "open": parse_num(opens[idx] if idx < len(opens) else None),
                        "high": parse_num(highs[idx] if idx < len(highs) else None),
                        "low": parse_num(lows[idx] if idx < len(lows) else None),
                        "close": close_v,
                        "volume": parse_num(volumes[idx] if idx < len(volumes) else None),
                        "amount": None,
                        "volume_unit": "shares",
                        "source": f"Yahoo Finance chart {sym}",
                        "source_quality": "FALLBACK",
                        "updated_at": ts,
                        "fetched_at": ts,
                        "market": "listed" if sym.endswith(".TW") else "otc",
                    })
                count = upsert_daily_ohlcv_rows(conn, normalized_rows)
                conn.commit()
            if count:
                out.update({"source": sym, "rows": count, "ok": True})
                set_status(f"yahoo_history_{code}", "fresh", f"{code} Yahoo chart rows {count} via {sym}")
                return out
        except Exception as exc:
            out["error"] = safe_error(exc)
            continue
    out["error"] = out.get("error") or "Yahoo chart symbols unavailable"
    set_status(f"yahoo_history_{code}", "stale", f"{code} Yahoo chart history failed: {out['error']}")
    return out


def upsert_yfinance_tw_history(code: str, days: int = 180, market_type: str | None = None) -> dict[str, Any]:
    # Fill history_price using Yahoo/yfinance when primary sources are incomplete.
    code = str(code).zfill(4)
    out = {"code": code, "source": None, "rows": 0, "ok": False, "error": None}
    try:
        import yfinance as yf  # type: ignore
        configure_yfinance_cache(yf)
        period = f"{max(180, int(days * 2))}d"
        ts = time.time()
        for attempt_idx, sym in enumerate(yahoo_tw_symbols(code, market_type)):
            if attempt_idx > 0:
                time.sleep(YAHOO_REQUEST_SLEEP_SECONDS)
            try:
                tk = yf.Ticker(sym)
                hist = tk.history(period=period, auto_adjust=False)
                if hist is None or hist.empty:
                    continue
                count = 0
                with _yahoo_db_lock, closing(db()) as conn:
                    normalized_rows: list[dict[str, Any]] = []
                    for idx, row in hist.iterrows():
                        d = normalize_date(str(idx.date() if hasattr(idx, 'date') else idx))
                        if not d:
                            continue
                        close_v = parse_num(row.get('Close'))
                        if close_v is None:
                            continue
                        normalized_rows.append({
                            "date": d,
                            "code": code,
                            "open": parse_num(row.get('Open')),
                            "high": parse_num(row.get('High')),
                            "low": parse_num(row.get('Low')),
                            "close": close_v,
                            "volume": parse_num(row.get('Volume')),
                            "amount": None,
                            "volume_unit": "shares",
                            "source": f"Yahoo Finance {sym}",
                            "source_quality": "FALLBACK",
                            "updated_at": ts,
                            "fetched_at": ts,
                            "market": "listed" if sym.endswith(".TW") else "otc",
                        })
                    count = upsert_daily_ohlcv_rows(conn, normalized_rows)
                    conn.commit()
                if count > 0:
                    out.update({"source": sym, "rows": count, "ok": True})
                    set_status(f"yahoo_history_{code}", "fresh", f"{code} Yahoo姝峰彶K绶氳榻?{count} 绛嗭綔{sym}")
                    return out
            except Exception as exc:
                out["error"] = safe_error(exc)
                continue
        if not out.get("ok"):
            chart_out = upsert_yahoo_chart_tw_history(code, days=days, market_type=market_type)
            if chart_out.get("ok"):
                return chart_out
            out["error"] = out.get("error") or chart_out.get("error") or "Yahoo Taiwan symbol unavailable"
            set_status(f"yahoo_history_{code}", "stale", f"{code} Yahoo姝峰彶K绶氳榻婂け鏁楋細{out['error']}")
        return out
    except Exception as exc:
        chart_out = upsert_yahoo_chart_tw_history(code, days=days, market_type=market_type)
        if chart_out.get("ok"):
            return chart_out
        out["error"] = safe_error(exc) or chart_out.get("error")
        set_status(f"yahoo_history_{code}", "stale", f"{code} Yahoo姝峰彶K绶氳榻婂け鏁楋細{out['error']}")
        return out


def upsert_yfinance_tw_valuation(code: str, market_type: str | None = None) -> dict[str, Any]:
    """Fill valuation PE/PB/EPS only when official TWSE/TPEx data is absent.

    Existing official exchange metrics are never downgraded or overwritten by
    Yahoo. Values are stored with a source label and no value is invented.
    """
    code = str(code).zfill(4)
    out = {"code": code, "ok": False, "source": None, "pe": None, "pb": None, "eps": None, "dividend_yield": None, "error": None}
    try:
        with closing(db()) as conn:
            official = conn.execute(
                """
                SELECT * FROM valuation
                WHERE code=?
                  AND (
                      UPPER(COALESCE(source,'')) LIKE 'TWSE%'
                      OR UPPER(COALESCE(source,'')) LIKE 'TPEX%'
                  )
                ORDER BY date DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        if official:
            out.update({
                "ok": True,
                "source": official["source"],
                "pe": official["pe"],
                "pb": official["pb"],
                "eps": official["eps"],
                "dividend_yield": official["dividend_yield"],
                "error": "official_valuation_preserved",
            })
            set_status(
                f"yahoo_valuation_{code}",
                "fresh",
                f"{code} official valuation preserved; Yahoo fallback skipped",
            )
            return out
        import yfinance as yf  # type: ignore
        configure_yfinance_cache(yf)
        ts = time.time()
        data_date = recent_market_date_for_eod()
        for attempt_idx, sym in enumerate(yahoo_tw_symbols(code, market_type)):
            if attempt_idx > 0:
                time.sleep(YAHOO_REQUEST_SLEEP_SECONDS)
            try:
                tk = yf.Ticker(sym)
                info = {}
                try:
                    info = tk.get_info() or {}
                except Exception:
                    info = getattr(tk, 'info', {}) or {}
                pe = parse_num(info.get('trailingPE') or info.get('forwardPE'))
                pb = parse_num(info.get('priceToBook'))
                eps = parse_num(info.get('trailingEps') or info.get('forwardEps'))
                dy = parse_num(info.get('dividendYield'))
                if dy is not None and dy < 1:
                    dy = dy * 100
                if pe is None and eps:
                    # If Yahoo has EPS but not PE, compute PE from latest local price/Yahoo price.
                    price = None
                    try:
                        fi = getattr(tk, 'fast_info', {}) or {}
                        price = parse_num(fi.get('last_price') or fi.get('lastPrice'))
                    except Exception:
                        price = None
                    if price and eps > 0:
                        pe = price / eps
                if pe is None and pb is None and eps is None and dy is None:
                    continue
                with _yahoo_db_lock, closing(db()) as conn:
                    # Merge with existing official TWSE valuation when Yahoo only fills EPS.
                    prev = conn.execute("SELECT * FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
                    m_dy = dy if dy is not None else (prev['dividend_yield'] if prev and prev['dividend_yield'] is not None else None)
                    m_pe = pe if pe is not None else (prev['pe'] if prev and prev['pe'] is not None else None)
                    m_pb = pb if pb is not None else (prev['pb'] if prev and prev['pb'] is not None else None)
                    conn.execute(
                        "INSERT OR REPLACE INTO valuation(date,code,dividend_yield,pe,pb,source,updated_at,eps,eps_source) VALUES(?,?,?,?,?,?,?,?,?)",
                        (data_date, code, m_dy, m_pe, m_pb, f"Yahoo Finance {sym}", ts, eps, f"Yahoo Finance {sym}" if eps is not None else None),
                    )
                    conn.commit()
                out.update({"ok": True, "source": sym, "pe": pe, "pb": pb, "eps": eps, "dividend_yield": dy})
                set_status(f"yahoo_valuation_{code}", "fresh", f"{code} Yahoo浼板€艰榻婏綔{sym}")
                return out
            except Exception as exc:
                out["error"] = safe_error(exc)
                continue
        if not out.get('ok'):
            out['error'] = out.get('error') or 'Yahoo鐒′及鍊?EPS璩囨枡'
            set_status(f"yahoo_valuation_{code}", "stale", f"{code} Yahoo浼板€艰榻婂け鏁楋細{out['error']}")
        return out
    except Exception as exc:
        out['error'] = safe_error(exc)
        set_status(f"yahoo_valuation_{code}", "stale", f"{code} Yahoo浼板€艰榻婂け鏁楋細{safe_error(exc)}")
        return out


def yfinance_quote(ticker: str) -> dict[str, Any]:
    return fetch_yfinance_quote(ticker)


def interpret_special_us_asset(ticker: str, change_pct: Any) -> str:
    # Human-readable interpretation for special US proxy assets.
    try:
        cp = float(change_pct)
    except Exception:
        return ""
    t = str(ticker).upper()
    if t == "TLT":
        if cp <= -1:
            return "TLT下跌通常代表長債殖利率上升，海外債券估值與匯率風險需留意。"
        if cp >= 1:
            return "TLT上漲通常代表長債殖利率下降，海外債券估值壓力可能緩和。"
    if t == "UUP":
        if cp >= 1:
            return "美元偏強，需留意台幣匯率、外資金流與避險成本。"
        if cp <= -1:
            return "美元偏弱，通常對新興市場與台股資金情緒較友善。"
    if t == "USO":
        if cp >= 2:
            return "油價上漲，航運燃油成本與石化原料成本壓力可能升高。"
        if cp <= -2:
            return "油價下跌，有助航運燃油成本壓力緩和，但也可能反映需求降溫。"
    if t == "BDRY":
        if cp >= 3:
            return "乾散貨運價上漲，運價景氣情緒偏正面；但與貨櫃航運不同，僅低權重參考。"
        if cp <= -3:
            return "乾散貨運價下跌，運價景氣情緒偏弱；但與貨櫃航運不同，僅低權重參考。"
    if t == "BOAT":
        return "BOAT為全球航運ETF且流動性較低，若報價缺漏或異常需獨立檢查，只作低權重情緒參考。"
    if t == "SMCI":
        return "SMCI波動性高且曾有財務/治理疑慮，只作AI伺服器情緒參考，不宜單獨依賴。"
    return ""
