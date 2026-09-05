from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
try:
    import truststore  # type: ignore
except Exception as exc:
    TRUSTSTORE_ENABLED = False
    TRUSTSTORE_ERROR = repr(exc)
else:
    try:
        truststore.inject_into_ssl()
        TRUSTSTORE_ENABLED = True
        TRUSTSTORE_ERROR = ""
    except Exception as exc:
        TRUSTSTORE_ENABLED = False
        TRUSTSTORE_ERROR = repr(exc)
import requests
from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from api.portfolio import router as portfolio_router
from api.config import configure_config_router, router as config_router
from api.image_analysis import router as image_analysis_router
from api.pages import configure_pages_router, router as pages_router
from api.status import router as status_router
from api.watchlist import configure_watchlist_router, router as watchlist_router
from adapter.mis import (
    fetch_mis_quotes_batch,
    get_current_session_evidence,
    get_mis_quote_cached,
    get_mis_quote_latest,
    get_mis_quote_latest_snapshot,
    prune_mis_quote_cache,
    start_mis_quote_daemon,
)
from adapter.finmind import (
    BROKER_FLOW_DATASET,
    _debug_broker_flow_source,
    finmind_get,
    get_finmind_token_disabled_reason,
)
from adapter.fugle import (
    FUGLE_QUOTE_TIMEOUT_SECONDS,
    extract_fugle_price_volume_rows,
    fetch_fugle_price_volume_network,
    fetch_fugle_quote_network,
    get_fugle_quote_cached,
)
from adapter.twse import (
    fetch_twse_eod_all,
    fetch_twse_stock_day_for_code,
    fetch_twse_valuation_all,
    refresh_twse_stock_day_codes,
    set_twse_cache_pruner,
)
from adapter.yahoo_history import (
    interpret_special_us_asset,
    upsert_yfinance_tw_history,
    upsert_yfinance_tw_valuation,
    yfinance_quote,
)
from auth.router import router as auth_router
from core.cache import (
    PRACTICAL_CACHE_MAXSIZE,
    PRACTICAL_CACHE_TTL_SECONDS,
    ROW_CACHE_MAXSIZE,
    ROW_CACHE_TTL_TW50_SECONDS,
    ROW_CACHE_TTL_WATCH_SECONDS,
    SCORE_CACHE_MAXSIZE,
    SCORE_CACHE_TTL_SECONDS,
    _practical_cache,
    _practical_cache_lock,
    _row_cache,
    _row_cache_lock,
    _score_cache,
    _score_cache_lock,
    prune_timed_cache,
)
from core.components import read_components
from core.config import (
    AUTO_REFRESH_MARKET_DATA_ON_START,
    AUTO_UPDATE_TW50_ON_START,
    COMPONENTS_FILE,
    DATA_DIR,
    DB_PATH,
    EOD_PAGE_REFRESH_SECONDS,
    FINMIND_BATCH_SIZE,
    FINMIND_BATCH_SLEEP_SECONDS,
    FINMIND_DELAY_SECONDS,
    FINMIND_HOURLY_SOFT_LIMIT,
    FINMIND_INCREMENTAL_DAYS,
    FINMIND_TOKEN,
    FUGLE_API_KEY,
    FUGLE_AUTO_UPDATE_MARKET_HOURS,
    FUGLE_WATCH_POLL_SECONDS,
    HEADERS,
    MA20_INVALID_DAYS_AFTER_EX,
    MIS_AUTO_UPDATE_MARKET_HOURS,
    MIS_CACHE_TTL_SECONDS,
    MIS_POLL_SECONDS,
    ROOT,
    TPEX_EX_DIVIDEND_CSV,
    TWSE_EX_DIVIDEND_CSV,
    YAHOO_REQUEST_SLEEP_SECONDS,
    mask_secret_text,
    safe_error,
)
from core.db import assert_db_integrity, db, init_db
from core.http import request_json
from core.market_foundation_schema import upsert_daily_ohlcv_rows
from core.market_session import (
    is_taiwan_market_holiday,
    is_taiwan_trading_day,
    market_is_open_now,
    normalize_list_mode,
    recent_market_date_for_eod,
    tw_market_session_now,
    us_market_session_now,
)
from core.status import get_status, set_status
from core.data_quality import DataQualityStatus, assess_component_freshness, assess_daily_ohlcv
from core.public_payload import sanitize_public_market_payload
from core.utils import (
    date_is_fresh_enough,
    fmt,
    iso_date_lag_days,
    normalize_date,
    now_tpe,
    parse_num,
    today_iso,
)
from core.valuation_normalizer import build_valuation_quality_payload, valuation_metric_display_value
from core.valuation_freshness import valuation_freshness_for_row
from analysis.support_resistance import (
    _extract_zone_bounds,
    _finite_float,
    _next_lower_support_text,
    _zone_bounds_text,
    _zone_low_high,
    basis_price,
    build_ohlcv_support_resistance_levels,
    choose_stop_loss_candidate,
    cluster_levels,
    fallback_support_resistance_text,
    format_zone_display,
    level_break_state,
    neutral_rsi_text,
    period_support_resistance_fields,
    price_bin_size,
    round_to_bin,
    zone_position,
)
from analysis.chip_cost import (
    chip_indicator_display,
    cost_display,
    cost_meta_text,
    cost_payload,
    is_no_event_cost,
    is_required_cost_ready,
    mark_cost_overlap,
)
from analysis.next_day_outlook import (
    _clamp_num,
    _confidence_value,
    _factor_payload,
    _prob_label,
    _source_age_decay_from_date,
    _source_age_decay_from_quote_time,
    factor_is_decision_usable,
    futures_night_factor,
    us_sentiment_factor,
    weighted_available_score,
)
from analysis.practical_status import (
    _practical_status_badge,
    calc_risk_reward_ratio,
    classify_practical_status_core,
    has_severe_warning,
)
from repository.twse_valuation_repository import get_twse_valuation
from repository.corporate_action_repository import upsert_legacy_corporate_action
from repository.market_profile_repository import resolve_market_profile
from repository.source_trace_repository import (
    audit_volume_units,
    required_local_rows,
    should_skip_dataset,
    source_trace_for_code_v2,
)

from repository.history_repository import (
    get_recent_corporate_action,
    history_date_coverage,
    history_rows_asc,
    latest_history_dates,
    normalize_history_row_volume,
    recent_market_reference_dates,
)
from repository.full_market_batch_repository import (
    latest_published_full_market_date,
    resolve_full_market_analysis_date,
)
from repository.rsi_adjustment_repository import (
    apply_rsi_split_adjustments,
    load_rsi_split_adjustments,
)
from analysis.technical import (
    calc_ma_from_rows,
    calc_pivot_from_rows,
    calc_rsi,
    component_states,
    historical_data_quality,
    technical_context_from_rows,
    _indicator_value_at,
)
from services.stock_detail_service import (
    build_technical_hints,
    explain_us_related_market,
    find_stock_item,
    technical_detail_from_rows,
)
from services.taiwan50_preload_service import refresh_incomplete_taiwan50_history
from services.dashboard_readiness_service import select_displayable_dashboard_items
from services.industry_profile_service import (
    build_peer_valuation_summary,
    configure_industry_profile_service,
    debug_industry_profile_all,
    debug_industry_profile_source,
    debug_theme_profile_source,
    inspect_industry_duplicate,
    inspect_industry_mapping,
    latest_industry_valuation_payload,
    latest_theme_profile_payload,
    local_industry_profile_source_status,
    update_industry_profiles_from_official,
    update_theme_profiles,
)
from services.daily_chip_momentum_service import (
    build_chip_momentum_payload,
    summarize_chip_momentum_for_quote,
)
from services.inner_outer_accumulation_service import build_inner_outer_accumulation_payload
from services.market_history_service import build_official_kline_payload
from services.estimated_chip_cost_service import get_canonical_cost_snapshot
from services.bot_market_data_service import build_canonical_close_batch_snapshot
from services.canonical_analysis_orchestrator import run_canonical_analysis
from services.canonical_question_analysis_service import run_canonical_question_analysis
from market.futures import futures_night_signal_for_stock
from repository.watchlist_repository import (
    get_watchlist_codes,
    get_watchlist_codes_unordered,
    get_watchlist_item,
    insert_watchlist_if_absent,
    list_watchlist_code_name_items,
    watchlist_contains,
)
from repository.taiwan50_close_batch_repository import latest_taiwan50_close_batch
from services.data_repair_service import (
    configure_data_repair_service,
    enqueue_data_repair,
    ensure_complete_data_for_codes,
    local_data_gaps,
)
from services.watchlist_bootstrap_service import (
    build_watchlist_bootstrap_status,
    configure_watchlist_bootstrap_service,
    diagnose_watchlist_bootstrap_need,
    enqueue_watchlist_bootstrap,
)
from services.price_volume_service import (
    capture_fugle_price_volume_snapshot,
    cleanup_price_volume_distribution,
    cleanup_price_volume_history,
    configure_price_volume_service,
    latest_price_volume_summary,
    price_volume_source_diagnostics,
    reconcile_price_volume_profile_for_code,
    unavailable_price_volume,
)
from services.tdcc_equity_concentration_service import (
    import_equity_rows_and_summarize,
    latest_equity_concentration_payload,
    load_equity_rows_from_source,
    parse_tdcc_open_data_csv,
    fetch_tdcc_holding_distribution_csv,
)

_scoring_import_exc = None
try:
    from scoring import score_stock, calculate_indicators, SYSTEM_VERSION as SCORING_SYSTEM_VERSION
except Exception as exc:
    _scoring_import_exc = exc
    score_stock = None
    calculate_indicators = None
    SCORING_SYSTEM_VERSION = "unavailable"
try:
    from scoring import _wilder_rsi as shared_wilder_rsi
except Exception:
    shared_wilder_rsi = None
try:
    from chip_cost_engine import compute_poc60
except Exception as exc:
    compute_poc60 = None
    logging.exception("chip_cost_engine import failed: %s", exc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_core()
    yield


app = FastAPI(title="Taiwan 50 Dashboard v2.44 Margin Logic, Market Divergence, External Hints", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(auth_router)
app.include_router(portfolio_router)
app.include_router(image_analysis_router)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_db_lock = threading.RLock()
_update_lock = threading.RLock()
_eod_update_running = False
_chip_update_running = False
_tdcc_equity_update_running = False
_price_volume_update_running = False


_fugle_refresh_lock = threading.RLock()
_fugle_refresh_running = False


def chip_update_is_running() -> bool:
    with _update_lock:
        return bool(_chip_update_running)


def prune_compute_caches() -> None:
    now_ts = time.time()
    with _score_cache_lock:
        prune_timed_cache(_score_cache, SCORE_CACHE_TTL_SECONDS * 3, SCORE_CACHE_MAXSIZE, now_ts=now_ts)
    with _practical_cache_lock:
        prune_timed_cache(_practical_cache, PRACTICAL_CACHE_TTL_SECONDS * 3, PRACTICAL_CACHE_MAXSIZE, now_ts=now_ts)
    with _row_cache_lock:
        prune_timed_cache(_row_cache, max(ROW_CACHE_TTL_TW50_SECONDS, ROW_CACHE_TTL_WATCH_SECONDS) * 3, ROW_CACHE_MAXSIZE, now_ts=now_ts)
    prune_mis_quote_cache()


set_twse_cache_pruner(prune_compute_caches)

configure_price_volume_service(
    db_lock=_db_lock,
    truststore_enabled=TRUSTSTORE_ENABLED,
    truststore_error=TRUSTSTORE_ERROR,
)
configure_industry_profile_service(db_lock=_db_lock)


def is_realtime_price_usable(price: Any, previous_close: Any = None) -> bool:
    """Return whether an intraday quote can safely override local close data."""
    p = parse_num(price)
    if p is None or p <= 0 or p != p:
        return False
    prev = parse_num(previous_close)
    if prev is not None and prev > 0:
        if abs(p - prev) / prev > 0.10:
            return False
    return True


def _quote_trade_time_age_seconds(quote: dict[str, Any] | None) -> float | None:
    text = str((quote or {}).get("trade_time") or "").strip()
    if not text:
        return None
    for fmt_text in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt_text).time()
            trade_dt = datetime.combine(now_tpe().date(), parsed, tzinfo=ZoneInfo("Asia/Taipei"))
            return (now_tpe() - trade_dt).total_seconds()
        except ValueError:
            continue
    try:
        parsed_dt = datetime.fromisoformat(text)
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=ZoneInfo("Asia/Taipei"))
        return (now_tpe() - parsed_dt.astimezone(ZoneInfo("Asia/Taipei"))).total_seconds()
    except Exception:
        return None


def _is_intraday_quote_fresh(quote: dict[str, Any] | None, max_age_seconds: int = 90) -> bool:
    age = _quote_trade_time_age_seconds(quote)
    if age is None:
        return False
    return 0 <= age <= max_age_seconds


def _positive_quote_level(quote: dict[str, Any] | None, key: str) -> float | None:
    value = parse_num((quote or {}).get(key))
    if value is None or value <= 0:
        return None
    return value


def _positive_cost_value(value: Any) -> float | None:
    parsed = parse_num(value)
    if parsed is None or parsed <= 0:
        return None
    try:
        if math.isnan(float(parsed)) or math.isinf(float(parsed)):
            return None
    except Exception:
        return None
    return float(parsed)


def _row_value(row: Any, key: str) -> Any:
    if not row:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return None


def _market_cost_basis_from_price_row(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    amount = _positive_cost_value(_row_value(row, "amount"))
    volume = _positive_cost_value(_row_value(row, "volume"))
    if amount is not None and volume is not None:
        avg_price = amount / volume
        if _positive_cost_value(avg_price) is not None:
            return {"label": "成交均價", "value": fmt(avg_price), "numeric_value": avg_price, "basis": "amount_div_volume"}
    open_price = _positive_cost_value(_row_value(row, "open"))
    high_price = _positive_cost_value(_row_value(row, "high"))
    low_price = _positive_cost_value(_row_value(row, "low"))
    close_price = _positive_cost_value(_row_value(row, "close"))
    if all(x is not None for x in [open_price, high_price, low_price, close_price]):
        basis = (open_price + high_price + low_price + close_price) / 4
        return {"label": "成交基準", "value": fmt(basis), "numeric_value": basis, "basis": "ohlc4"}
    if close_price is not None:
        return {"label": "收盤基準", "value": fmt(close_price), "numeric_value": close_price, "basis": "close"}
    return None


def build_market_cost_basis(
    hist: list[Any],
    eod: Any,
    *,
    quote_price: Any = None,
    use_intraday_quote: bool = False,
) -> dict[str, Any] | None:
    for row in list(hist or [])[:1] + ([eod] if eod else []):
        basis = _market_cost_basis_from_price_row(row)
        if basis:
            return basis
    price = _positive_cost_value(quote_price) if use_intraday_quote else None
    if price is not None:
        return {"label": "盤中成交基準", "value": fmt(price), "numeric_value": price, "basis": "intraday_last_price"}
    return None


def _normalize_local_stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def _format_tw50_close_batch_title(data_date: Any) -> str:
    text = str(data_date or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = text.replace("-", "/")
    if not text or text in {"—", "-", "一", "null", "undefined"}:
        return "收盤後台灣50大分析"
    return f"收盤後台灣50大分析（{text}）"


def latest_completed_tw50_close_date(conn: sqlite3.Connection, items: list[dict[str, str]]) -> str | None:
    del items  # retained for the public function contract
    return latest_published_full_market_date(conn)


def pct(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "--"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.{digits}f}%"



def display_text(v: Any, fallback: str = "資料暫缺") -> str:
    """Final API display guard: never send None/NaN/empty text to UI."""
    if v is None:
        return fallback
    try:
        if isinstance(v, float) and pd.isna(v):
            return fallback
    except Exception as exc:
        logging.debug("display_text NaN guard failed: %s", safe_error(exc))
    text = str(v).strip()
    if not text or text == "--" or text.lower() in {"none", "nan", "null", "undefined"}:
        return fallback
    return text


_RAW_DISPLAY_TEXT_MARKERS = (
    "numpy.float",
    "numpy.int",
    "object at 0x",
    "Traceback",
    "KeyError",
    "NoneType",
    "Infinity",
    "-Infinity",
)


def _looks_like_raw_display_text(text: str) -> bool:
    if not text:
        return False
    if text.strip() in {"'date'", '"date"'}:
        return True
    return any(marker in text for marker in _RAW_DISPLAY_TEXT_MARKERS)


def sanitize_api_value(value: Any, *, _depth: int = 0) -> Any:
    """Return JSON/display-safe values without leaking Python objects or exceptions."""
    if _depth > 16:
        return None
    if value is None:
        return None
    if isinstance(value, BaseException):
        logging.debug("sanitize_api_value dropped exception value: %s", safe_error(value))
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "nan", "null", "undefined", "inf", "infinity", "-inf", "-infinity"}:
            return None
        return None if _looks_like_raw_display_text(text) else text
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = sanitize_api_value(key, _depth=_depth + 1)
            key_text = str(clean_key if clean_key is not None else key)
            if key_text == "signal_strength" and isinstance(item, str) and item.strip().lower() == "none":
                out[key_text] = "none"
            else:
                out[key_text] = sanitize_api_value(item, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [sanitize_api_value(item, _depth=_depth + 1) for item in value]
    try:
        is_na = pd.isna(value)
        if isinstance(is_na, bool) and is_na:
            return None
    except Exception:
        pass
    try:
        item = value.item() if hasattr(value, "item") and callable(value.item) else None
        if item is not None and item is not value:
            return sanitize_api_value(item, _depth=_depth + 1)
    except Exception:
        pass
    try:
        json.dumps(value)
        return value
    except Exception:
        text = repr(value)
        return None if _looks_like_raw_display_text(text) else text


def sanitize_display_payload(payload: Any) -> Any:
    return sanitize_api_value(payload)


# TODO(accurate-data-source, 2026-06-19):
# Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
# Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
# TODO(watchlist-realtime-source, 2026-06-19):
# watchlist_realtime may use intraday price, but abnormal realtime values should not pollute technical indicators or status calculations.
# TODO(taiwan50-close-source, 2026-06-19):
# taiwan50_batch should prefer official close data and must not depend on TWSE MIS intraday price.
# ---------- TWSE OpenAPI ----------








# TODO(accurate-data-source, 2026-06-19):
# Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
# Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
# ---------- Fugle ----------











def refresh_fugle_quotes_background(items: list[dict[str, Any]]) -> None:
    # Refresh Fugle quotes in the background without blocking normal quote responses.
    global _fugle_refresh_running
    if not FUGLE_API_KEY:
        return
    with _fugle_refresh_lock:
        if _fugle_refresh_running:
            return
        _fugle_refresh_running = True
    updated_codes: list[str] = []
    checked_codes: list[str] = []
    try:
        codes = [str(x.get("code", "")).zfill(4) for x in items if x.get("code")]
        seen: set[str] = set()
        for code in codes:
            if code in seen:
                continue
            seen.add(code)
            checked_codes.append(code)
            # Skip network calls when the cache is still fresh.
            if get_fugle_quote_cached(code) is not None:
                continue
            result = fetch_fugle_quote_network(code)
            if result is not None:
                updated_codes.append(code)

        if updated_codes:
            with _row_cache_lock:
                for code in updated_codes:
                    _row_cache.pop(f"watchlist:{code}", None)
        set_status("fugle", "fresh", f"Fugle background quote refresh finished: {len(updated_codes)}/{len(checked_codes)} updated")
    finally:
        with _fugle_refresh_lock:
            _fugle_refresh_running = False

def parse_fugle_quote(data: dict[str, Any]) -> dict[str, Any]:
    # Parse conservative Fugle quote fields across response variants.
    price = parse_num(data.get("lastPrice") or data.get("closePrice") or data.get("price") or data.get("last"))
    change = parse_num(data.get("change") or data.get("priceChange"))
    change_pct = parse_num(data.get("changePercent") or data.get("changeRate"))
    high = parse_num(data.get("highPrice") or data.get("high"))
    low = parse_num(data.get("lowPrice") or data.get("low"))
    return {
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "high": high,
        "low": low,
        "source": "Fugle",
        "quote_source": "Fugle intraday/quote",
        "trade_time": None,
        "fetched_at": now_tpe().strftime("%H:%M:%S"),
        "is_realtime": True,
        "is_estimated_tick_volume": False,
    }

# TODO(accurate-data-source, 2026-06-19):
# Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
# Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
# ---------- FinMind ----------























def upsert_finmind_stock_data(code: str, days: int, mode: str) -> dict[str, Any]:
    # Update one stock from FinMind without wasting quota on already-fresh data.
    code = str(code).zfill(4)
    update_days = int(days if mode == "full" else min(days, FINMIND_INCREMENTAL_DAYS))
    end = now_tpe().date()
    target_s = recent_market_date_for_eod()
    start = end - timedelta(days=int(update_days * 1.7) + 10)
    start_s, end_s = start.isoformat(), end.isoformat()
    ts = time.time()
    stats = {"code": code, "api_calls": 0, "skipped": 0, "mode": mode, "days": update_days}

    # Price
    if should_skip_dataset("history_price", code, target_s, start_s, required_local_rows("history_price", mode, update_days)):
        stats["skipped"] += 1
        set_status(f"finmind_price_{code}", "fresh", f"{code} 歷史股價已足夠且最新，跳過 API")
    else:
        try:
            rows = finmind_get("TaiwanStockPrice", code, start_s, end_s)
            stats["api_calls"] += 1
            with _db_lock, closing(db()) as conn:
                normalized_rows: list[dict[str, Any]] = []
                for r in rows:
                    d = normalize_date(r.get("date"))
                    if not d:
                        continue
                    normalized_rows.append({
                        "date": d,
                        "code": code,
                        "open": parse_num(r.get("open")),
                        "high": parse_num(r.get("max")),
                        "low": parse_num(r.get("min")),
                        "close": parse_num(r.get("close")),
                        "volume": parse_num(r.get("Trading_Volume") or r.get("Trading_volume") or r.get("volume")),
                        "amount": parse_num(r.get("Trading_money") or r.get("Trading_Money") or r.get("amount")),
                        "volume_unit": "shares",
                        "source": "FinMind",
                        "source_quality": "FALLBACK",
                        "updated_at": ts,
                        "fetched_at": ts,
                    })
                written_rows = upsert_daily_ohlcv_rows(conn, normalized_rows)
                conn.commit()
            set_status(
                f"finmind_price_{code}",
                "fresh",
                f"{code} price rows accepted {written_rows}/{len(rows)} | {mode} {update_days}d",
            )
            time.sleep(FINMIND_DELAY_SECONDS)
        except Exception as exc:
            logging.exception("FinMind price update failed for %s", code)
            set_status(f"finmind_price_{code}", "stale", f"{code} price failed: {safe_error(exc)}")

    # Institution
    if should_skip_dataset("institution_daily", code, target_s, start_s, required_local_rows("institution_daily", mode, update_days)):
        stats["skipped"] += 1
        set_status(f"finmind_inst_{code}", "fresh", f"{code} institution data already fresh enough, skipped API")
    else:
        try:
            rows = finmind_get("TaiwanStockInstitutionalInvestorsBuySell", code, start_s, end_s)
            stats["api_calls"] += 1
            by_date: dict[str, dict[str, float]] = {}
            for r in rows:
                d = normalize_date(r.get("date"))
                if not d:
                    continue
                name = str(r.get("name") or r.get("type") or "")
                buy = parse_num(r.get("buy")) or 0.0
                sell = parse_num(r.get("sell")) or 0.0
                net = buy - sell
                item = by_date.setdefault(d, {"foreign": 0.0, "trust": 0.0, "dealer": 0.0})
                if "鎶曚俊" in name or "Investment" in name or "Trust" in name:
                    item["trust"] += net
                elif "澶栬硣" in name or "Foreign" in name:
                    item["foreign"] += net
                elif "鑷嚐" in name or "Dealer" in name:
                    item["dealer"] += net
            with _db_lock, closing(db()) as conn:
                for d, item in by_date.items():
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO institution_daily(
                            date,code,foreign_net,trust_net,dealer_net,source,updated_at,source_quality,fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (d, code, item["foreign"], item["trust"], item["dealer"], "FinMind", ts, "secondary", ts),
                    )
                conn.commit()
            set_status(f"finmind_inst_{code}", "fresh", f"{code} institution {len(by_date)} days | {mode} {update_days}d")
            time.sleep(FINMIND_DELAY_SECONDS)
        except Exception as exc:
            logging.exception("FinMind institution update failed for %s", code)
            set_status(f"finmind_inst_{code}", "stale", f"{code} institution failed: {safe_error(exc)}")

    # Margin
    if should_skip_dataset("margin_daily", code, target_s, start_s, required_local_rows("margin_daily", mode, update_days)):
        stats["skipped"] += 1
        set_status(f"finmind_margin_{code}", "fresh", f"{code} margin data already fresh enough, skipped API")
    else:
        try:
            rows = sorted(
                finmind_get("TaiwanStockMarginPurchaseShortSale", code, start_s, end_s),
                key=lambda r: normalize_date(r.get("date")) or "",
            )
            stats["api_calls"] += 1
            with _db_lock, closing(db()) as conn:
                prev_margin = None
                prev_short = None
                for r in rows:
                    d = normalize_date(r.get("date"))
                    if not d:
                        continue
                    mb = parse_num(r.get("MarginPurchaseTodayBalance") or r.get("margin_purchase_today_balance") or r.get("MarginPurchaseLimit"))
                    sb = parse_num(r.get("ShortSaleTodayBalance") or r.get("short_sale_today_balance") or r.get("ShortSaleLimit"))
                    # FinMind's MarginPurchaseBuy is gross buying, not the
                    # financing-balance delta.  Only consecutive balances can
                    # produce the legacy delta field without changing meaning.
                    md = (
                        mb - prev_margin
                        if mb is not None and prev_margin is not None
                        else None
                    )
                    sd = None
                    if sb is not None and prev_short is not None:
                        sd = sb - prev_short
                    conn.execute(
                        """
                        INSERT INTO margin_daily(
                            date,code,margin_delta,margin_balance,short_delta,short_balance,source,updated_at,source_quality,fetched_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(date,code) DO UPDATE SET
                            margin_delta=excluded.margin_delta,
                            margin_balance=excluded.margin_balance,
                            short_delta=excluded.short_delta,
                            short_balance=excluded.short_balance,
                            source=excluded.source,
                            updated_at=excluded.updated_at,
                            source_quality=excluded.source_quality,
                            fetched_at=excluded.fetched_at
                        WHERE COALESCE(margin_daily.source_quality,'')<>'official'
                        """,
                        (d, code, md, mb, sd, sb, "FinMind", ts, "secondary", ts),
                    )
                    if mb is not None:
                        prev_margin = mb
                    if sb is not None:
                        prev_short = sb
                conn.commit()
            set_status(f"finmind_margin_{code}", "fresh", f"{code} margin rows {len(rows)} | {mode} {update_days}d")
            time.sleep(FINMIND_DELAY_SECONDS)
        except Exception as exc:
            logging.exception("FinMind margin update failed for %s", code)
            set_status(f"finmind_margin_{code}", "stale", f"{code} margin failed: {safe_error(exc)}")

    # Foreign shareholding anchor for public foreign cumulative cost estimates.
    if should_skip_dataset("foreign_shareholding", code, target_s, start_s, required_local_rows("foreign_shareholding", mode, update_days)):
        stats["skipped"] += 1
        set_status(f"finmind_foreign_shareholding_{code}", "fresh", f"{code} foreign shareholding already fresh enough, skipped API")
    else:
        try:
            rows = finmind_get("TaiwanStockShareholding", code, start_s, end_s)
            stats["api_calls"] += 1
            count = 0
            with _db_lock, closing(db()) as conn:
                for r in rows:
                    d = normalize_date(r.get("date"))
                    shares = parse_num(r.get("ForeignInvestmentShares"))
                    if not d or shares is None:
                        continue
                    conn.execute("INSERT OR REPLACE INTO foreign_shareholding(date,code,ForeignInvestmentShares,source,updated_at) VALUES(?,?,?,?,?)",
                        (d, code, shares, "FinMind TaiwanStockShareholding", ts),
                    )
                    count += 1
                conn.commit()
            set_status(f"finmind_foreign_shareholding_{code}", "fresh", f"{code} foreign shareholding {count} rows | {mode} {update_days}d")
            time.sleep(FINMIND_DELAY_SECONDS)
        except Exception as exc:
            logging.exception("FinMind foreign shareholding update failed for %s", code)
            set_status(f"finmind_foreign_shareholding_{code}", "stale", f"{code} foreign shareholding failed: {safe_error(exc)}")

    return stats


# ---------- v2.42 multi-source data completion ----------










def background_ensure_complete_data(codes: list[str], days: int = 120, reason: str = "ensure complete data") -> None:
    global _chip_update_running
    with _update_lock:
        if _chip_update_running:
            set_status("data_ensure", "loading", "data completion task already running")
            return
        _chip_update_running = True
    try:
        ensure_complete_data_for_codes(codes, days=days, reason=reason)
    finally:
        with _update_lock:
            _chip_update_running = False


def update_finmind_codes(codes: list[str], days: int = 120, mode: str = "incremental", _owns_update_slot: bool = False) -> None:
    """FinMind background updater.

    Default mode is incremental to protect free quota. Full 120-day rebuild only runs when
    explicitly requested by the user.
    """
    global _chip_update_running
    codes = [str(c).zfill(4) for c in codes if str(c).strip()]
    mode = "full" if mode == "full" else "incremental"
    acquired_update_slot = False
    if not _owns_update_slot:
        with _update_lock:
            if _chip_update_running:
                set_status("finmind", "loading", "FinMind update task already running")
                return
            _chip_update_running = True
            acquired_update_slot = True
    try:
        total = len(codes)
        api_calls = 0
        skipped = 0
        set_status("finmind", "loading", f"FinMind {mode} update started: 0/{total} codes")
        for idx, code in enumerate(codes, start=1):
            set_status("finmind_progress", "loading", f"{mode} {idx}/{total}: {code} | API {api_calls} | skipped {skipped}")
            st = upsert_finmind_stock_data(code, days=days, mode=mode)
            api_calls += int(st.get("api_calls", 0))
            skipped += int(st.get("skipped", 0))
            prune_compute_caches()
            # Respect FinMind free-tier request limits.
            time.sleep(FINMIND_DELAY_SECONDS)
            if FINMIND_BATCH_SIZE > 0 and idx % FINMIND_BATCH_SIZE == 0 and idx < total:
                set_status("finmind_progress", "loading", f"batch pause {FINMIND_BATCH_SLEEP_SECONDS:g}s after {idx}/{total} | API {api_calls} | skipped {skipped}")
                time.sleep(FINMIND_BATCH_SLEEP_SECONDS)
        prune_compute_caches()
        set_status("finmind", "fresh", f"FinMind {mode} update complete: {total} codes | API {api_calls} | skipped {skipped}")
        set_status("finmind_progress", "fresh", f"completed {total}/{total} codes | API {api_calls} | skipped {skipped}")
    except Exception as exc:
        logging.exception("FinMind background update interrupted")
        set_status("finmind", "stale", f"FinMind update interrupted: {safe_error(exc)}")
    finally:
        if acquired_update_slot:
            with _update_lock:
                _chip_update_running = False

configure_data_repair_service(
    finmind_token=FINMIND_TOKEN,
    chip_update_is_running=chip_update_is_running,
    update_finmind_codes_func=update_finmind_codes,
    set_status_func=set_status,
    safe_error_func=safe_error,
    prune_compute_caches_func=prune_compute_caches,
)

# ---------- analysis ----------




def calc_cost(code: str, field: str, days: int = 20) -> tuple[float | None, dict[str, Any]]:
    # Public-data interval cost estimate for net buy / margin increase days.
    code = _normalize_local_stock_code(code)
    rows = latest_history_dates(code, limit=days + 20)
    if not rows:
        return None, {"quality": "missing", "reason": "無歷史收盤價", "valid_days": 0, "used_days": 0, "total_net": 0}
    closes = {r["date"]: r["close"] for r in rows if r["close"] is not None}
    analysis_as_of = str(rows[0]["date"])
    table = "institution_daily" if field.endswith("_net") else "margin_daily"
    with closing(db()) as conn:
        data = list(conn.execute(
            f"""
            SELECT date,{field} AS net
            FROM {table}
            WHERE TRIM(UPPER(code))=? AND date<=?
            ORDER BY date DESC
            LIMIT ?
            """,
            (code, analysis_as_of, days),
        ))
    valid_days = len(data)
    used = []
    total_net = 0.0
    total_amt = 0.0
    for r in data:
        net = r["net"]
        close = closes.get(r["date"])
        if net is not None and close is not None and net > 0:
            used.append((r["date"], float(close), float(net)))
            total_net += float(net)
            total_amt += float(close) * float(net)
    meta_base = {"valid_days": valid_days, "used_days": len(used), "total_net": round(total_net, 2), "window_days": days}
    if len(used) == 0 or total_net <= 0:
        no_reason = "no margin increase" if field == "margin_delta" else "no valid net-buy days"
        return None, {**meta_base, "quality": "missing", "reason": no_reason}
    cost = total_amt / total_net if total_net else None
    close_vals = [u[1] for u in used]
    if cost is None or cost < min(close_vals) - 0.01 or cost > max(close_vals) + 0.01:
        return None, {**meta_base, "quality": "error", "reason": "鎴愭湰鐣板父", "from": used[-1][0], "to": used[0][0]}
    # v2.27: one valid buy/increase day is still a real public-data estimate,
    # but it must be marked provisional rather than hidden.
    q = "low" if len(used) == 1 else ("medium" if len(used) == 2 else "high")
    return round(cost, 2), {**meta_base, "quality": q, "reason": "ok", "from": used[-1][0], "to": used[0][0]}








def calculate_public_chip_costs(
    code: str,
    limit: int = 60,
    *,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    # Canonical persisted institution estimates shared by dashboard, Bot and LINE.
    code = _normalize_local_stock_code(code)
    limit = max(20, min(int(limit), 60))
    canonical = get_canonical_cost_snapshot(code, as_of_date=as_of_date)
    analysis_as_of = canonical.get("trade_date")
    with closing(db()) as conn:
        if analysis_as_of:
            price_rows = list(conn.execute(
                """
                SELECT date, code, open, high, low, close, volume, amount
                FROM history_price
                WHERE TRIM(UPPER(code))=? AND close IS NOT NULL AND date<=?
                ORDER BY date DESC
                LIMIT ?
                """,
                (code, analysis_as_of, limit),
            ))
        else:
            price_rows = []

    price_data = [
        {
            "date": r["date"],
            "stock_id": r["code"],
            "open": r["open"],
            "max": r["high"],
            "min": r["low"],
            "close": r["close"],
            "Trading_Volume": r["volume"],
            "Trading_money": r["amount"],
        }
        for r in reversed(price_rows)
    ]
    if compute_poc60 is None:
        poc60 = {"value": None, "confidence": "unavailable", "note": "成交密集價計算元件不可用", "extra": {}}
    else:
        try:
            poc60 = compute_poc60(pd.DataFrame(price_data), 60).to_dict()
        except Exception as exc:
            logging.exception("price-volume reference calculation failed for %s", code)
            poc60 = {"value": None, "confidence": "unavailable", "note": safe_error(exc), "extra": {}}
    poc60.update(
        {
            "label": "成交密集價參考區",
            "is_institution_cost": False,
            "referee_eligible": False,
            "support_resistance_eligible": False,
            "can_override_main_status": False,
        }
    )
    return {**canonical, "poc60_estimate": poc60}






def fallback_sr_zones_from_rows(rows_asc: list[dict[str, Any]], current: float | None, periods: tuple[int, ...] = (20, 60)) -> dict[str, Any]:
    # Fallback support/resistance zones from available K-line rows.
    current = parse_num(current)
    out = {'support': None, 'resistance': None, 'supports': [], 'resistances': [], 'method': '澶氭棩楂樹綆榛?ATR淇濆簳浼扮畻'}
    if current is None or not rows_asc:
        return out
    # ATR estimate if indicators are available
    atr = None
    try:
        frame = pd.DataFrame(rows_asc)
        columns = ['date', 'open', 'high', 'low', 'close', 'volume'] + [
            column
            for column in ('rsi_close', 'technical_open', 'technical_high', 'technical_low', 'technical_close')
            if column in frame.columns
        ]
        df = frame[columns].copy()
        ind = calculate_indicators(df) if calculate_indicators else None
        if ind is not None and 'atr14' in ind.columns:
            v = ind['atr14'].iloc[-1]
            atr = float(v) if pd.notna(v) else None
    except Exception:
        atr = None

    def mk(price, label, side, period_label=''):
        price = parse_num(price)
        if price is None or price <= 0:
            return None
        strength = '弱'
        source = f'{period_label}{label} fallback'.strip()
        return {
            'price': round(float(price), 2),
            'zone_low': round(float(price), 2),
            'zone_high': round(float(price), 2),
            'label_price': fmt(price),
            'sources': [source],
            'raw_levels': [{'price': round(float(price), 2), 'source': source, 'status': 'fallback', 'score': 0.8}],
            'score': 0.8,
            'strength': strength,
            'distance_pct': round((float(price) - current) / current * 100, 2),
            'fallback': True,
            'side': side,
        }

    support_candidates = []
    resistance_candidates = []
    for n in periods:
        sample = rows_asc[-n:] if len(rows_asc) >= n else rows_asc
        lows = [parse_num(r.get('technical_low') if r.get('technical_low') is not None else r.get('low')) for r in sample]
        highs = [parse_num(r.get('technical_high') if r.get('technical_high') is not None else r.get('high')) for r in sample]
        lows = [x for x in lows if x is not None]
        highs = [x for x in highs if x is not None]
        if lows:
            p = min(lows)
            if p < current:
                support_candidates.append(mk(p, '低點', 'support', f'{n}日'))
        if highs:
            p = max(highs)
            if p > current:
                resistance_candidates.append(mk(p, '高點', 'resistance', f'{n}日'))
    # pick nearest valid candidate first, because main list needs actionable nearest levels
    support_candidates = [x for x in support_candidates if x]
    resistance_candidates = [x for x in resistance_candidates if x]
    support_candidates.sort(key=lambda x: abs((x.get('price') or current) - current))
    resistance_candidates.sort(key=lambda x: abs((x.get('price') or current) - current))
    if support_candidates:
        out['support'] = support_candidates[0]
        out['supports'] = support_candidates[:5]
    else:
        p = current - (1.5 * atr if atr else current * 0.03)
        z = mk(p, 'ATR/現價估算', 'support', '')
        out['support'] = z
        out['supports'] = [z] if z else []
    if resistance_candidates:
        out['resistance'] = resistance_candidates[0]
        out['resistances'] = resistance_candidates[:5]
    else:
        p = current + (1.5 * atr if atr else current * 0.03)
        z = mk(p, 'ATR/現價估算', 'resistance', '')
        out['resistance'] = z
        out['resistances'] = [z] if z else []
    return out


def sr_periods_detail(code: str, current_price: float | None, periods: tuple[int, ...] = (5, 10, 20, 60)) -> list[dict[str, Any]]:
    # Multi-period support/resistance summary for detail display.
    rows = history_rows_asc(code, limit=max(periods) + 5)
    current = parse_num(current_price) or (parse_num(rows[-1].get('close')) if rows else None)
    if current is None:
        return []

    def zone_payload(z: dict[str, Any] | None, pos: dict[str, Any]) -> dict[str, Any]:
        if not z:
            return {
                "value": None,
                "period": None,
                "label_price": None,
                "distance_pct": None,
                "signed_distance_pct": None,
                "strength": None,
            }
        return {
            "value": parse_num(z.get("price")),
            "period": None,
            "label_price": z.get("label_price") or fmt(z.get("price")),
            "distance_pct": pos.get("distance_pct"),
            "signed_distance_pct": z.get("signed_distance_pct"),
            "strength": z.get("strength"),
        }

    out = []
    for n in periods:
        detail = fallback_sr_zones_from_rows(rows[-n:] if len(rows) >= n else rows, current, periods=(n,))
        s = detail.get('support'); r = detail.get('resistance')
        sp = zone_position(current, s, 'support')
        rp = zone_position(current, r, 'resistance')
        s_payload = zone_payload(s, sp)
        r_payload = zone_payload(r, rp)
        out.append({
            'period': n,
            'label': f'{n}日',
            'support': s,
            'resistance': r,
            'support_display': format_zone_display('支撐', s, sp),
            'resistance_display': format_zone_display('賣壓', r, rp),
            'support_value': s_payload["value"],
            'resistance_value': r_payload["value"],
            'support_label_price': s_payload["label_price"],
            'resistance_label_price': r_payload["label_price"],
            'support_distance_pct': s_payload["distance_pct"],
            'resistance_distance_pct': r_payload["distance_pct"],
            'support_signed_distance_pct': s_payload["signed_distance_pct"],
            'resistance_signed_distance_pct': r_payload["signed_distance_pct"],
            'support_strength': s_payload["strength"],
            'resistance_strength': r_payload["strength"],
            'note': '多週期高低點與 ATR 估算',
        })
    return out


def calc_wave_cost(code: str, field: str, max_days: int = 60) -> tuple[float | None, dict[str, Any]]:
    # Estimate a recent wave cost from public net-buy rows.
    rows = latest_history_dates(code, limit=max_days + 30)
    if not rows:
        return None, {"quality":"missing", "reason":"無歷史收盤價", "used_days":0, "valid_days":0}
    closes = {r["date"]: r["close"] for r in rows if r["close"] is not None}
    analysis_as_of = str(rows[0]["date"])
    table = "institution_daily" if field.endswith("_net") else "margin_daily"
    with closing(db()) as conn:
        data_desc = list(
            conn.execute(
                f"""
                SELECT date,{field} AS net
                FROM {table}
                WHERE code=? AND date<=?
                ORDER BY date DESC
                LIMIT ?
                """,
                (code, analysis_as_of, max_days),
            )
        )
    if not data_desc:
        return None, {"quality":"missing", "reason":"missing source rows", "used_days":0, "valid_days":0}
    data = list(reversed(data_desc))  # old -> new
    nets = [parse_num(r["net"]) or 0.0 for r in data]
    positive = [n for n in nets if n > 0]
    avg_buy = (sum(positive) / len(positive)) if positive else 0.0
    min_effective_buy = max(avg_buy * 0.10, 1.0)  # 閬庡皬璨疯秴瑕栫偤闆滆▕

    reset_ratio_map = {"foreign_net": 0.35, "trust_net": 0.20, "dealer_net": 0.50}
    reset_ratio = reset_ratio_map.get(field, 0.30)
    anchor_idx = 0
    consecutive_sell = 0
    segment_buy_acc = 0.0
    sell_acc = 0.0
    for i, net in enumerate(nets):
        if net > 0 and net >= min_effective_buy:
            if consecutive_sell >= 3 or (segment_buy_acc > 0 and sell_acc >= segment_buy_acc * reset_ratio):
                anchor_idx = i
                segment_buy_acc = 0.0
            consecutive_sell = 0
            sell_acc = 0.0
            segment_buy_acc += net
        elif net < 0:
            consecutive_sell += 1
            sell_acc += abs(net)
        else:
            # Noise days do not enter cost, but they break consecutive selling.
            consecutive_sell = 0

    segment = data[anchor_idx:]
    used = []
    total_net = 0.0
    total_amt = 0.0
    for r in segment:
        net = parse_num(r["net"])
        close = closes.get(r["date"])
        if net is not None and close is not None and net > 0 and net >= min_effective_buy:
            used.append((r["date"], float(close), float(net)))
            total_net += float(net)
            total_amt += float(close) * float(net)
    meta = {"valid_days": len(segment), "used_days": len(used), "total_net": round(total_net, 2), "anchor_date": segment[0]["date"] if segment else None, "window_days": max_days, "min_effective_buy": round(min_effective_buy, 2), "reset_ratio": reset_ratio}
    if len(used) <= 1 or total_net <= 0:
        return None, {**meta, "quality":"low", "reason":"娉㈡妯ｆ湰涓嶈冻"}
    cost = total_amt / total_net
    close_vals = [u[1] for u in used]
    if cost < min(close_vals)-0.01 or cost > max(close_vals)+0.01:
        return None, {**meta, "quality":"error", "reason":"娉㈡鎴愭湰鐣板父", "from": used[0][0], "to": used[-1][0]}
    q = "medium" if len(used) == 2 else "high"
    return round(cost, 2), {**meta, "quality":q, "reason":"ok", "from": used[0][0], "to": used[-1][0]}


def chip_periods_detail(code: str, periods: tuple[int, ...] = (5, 10, 20, 60)) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    code = str(code).zfill(4)
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn)
        if not analysis_as_of:
            return []
        for n in periods:
            inst = conn.execute(
                """
                SELECT SUM(foreign_net) AS foreign_net,
                       SUM(trust_net) AS trust_net,
                       SUM(dealer_net) AS dealer_net
                FROM (
                    SELECT foreign_net,trust_net,dealer_net
                    FROM institution_daily
                    WHERE code=? AND date<=?
                    ORDER BY date DESC
                    LIMIT ?
                )
                """,
                (code, analysis_as_of, int(n)),
            ).fetchone()
            mar = conn.execute(
                """
                SELECT SUM(margin_delta) AS margin_delta,
                       SUM(short_delta) AS short_delta
                FROM (
                    SELECT margin_delta,short_delta
                    FROM margin_daily
                    WHERE code=? AND date<=?
                    ORDER BY date DESC
                    LIMIT ?
                )
                """,
                (code, analysis_as_of, int(n)),
            ).fetchone()
            out.append({
                "period": n,
                "label": f"{n}日",
                "foreign_net": round(float(inst["foreign_net"] or 0), 2) if inst else 0,
                "trust_net": round(float(inst["trust_net"] or 0), 2) if inst else 0,
                "dealer_net": round(float(inst["dealer_net"] or 0), 2) if inst else 0,
                "margin_delta": round(float(mar["margin_delta"] or 0), 2) if mar else 0,
                "short_delta": round(float(mar["short_delta"] or 0), 2) if mar else 0,
            })
    return out









def weighted_volume_profile(rows: list[dict[str, Any]], current: float, days: int, label: str) -> list[dict[str, Any]]:
    # Estimate volume profile from OHLCV weights; not tick-level volume-at-price.
    use = rows[-days:] if len(rows) > days else rows
    if len(use) < max(5, min(days, 10)):
        return []
    bin_size = price_bin_size(current)
    volume_by_bin: dict[float, float] = {}
    total_volume = 0.0
    for r in use:
        h = parse_num(r.get('high'))
        l = parse_num(r.get('low'))
        c = parse_num(r.get('close'))
        o = parse_num(r.get('open'))
        v = parse_num(r.get('volume'))
        if h is None or l is None or c is None or v is None or v <= 0:
            continue
        if h < l:
            h, l = l, h
        if h == l:
            b = round_to_bin(c, bin_size)
            volume_by_bin[b] = volume_by_bin.get(b, 0.0) + v
            total_volume += v
            continue
        typical = (h + l + c) / 3.0
        # Use a close-weighted mode unless the candle has an unusually long tail.
        mode_price = typical if abs(c - typical) / typical > 0.03 else (0.65 * c + 0.35 * typical)
        sigma = max((h - l) / 4.0, bin_size * 2)
        # Use tick-aware bins so high-priced and low-priced stocks keep reasonable resolution.
        start = round_to_bin(l, bin_size)
        end = round_to_bin(h, bin_size)
        prices = []
        x = start
        guard = 0
        while x <= end + 1e-9 and guard < 500:
            prices.append(round(x, 4))
            x += bin_size
            guard += 1
        if not prices:
            prices = [round_to_bin(c, bin_size)]
        weights = []
        for p in prices:
            w = math.exp(-0.5 * ((p - mode_price) / sigma) ** 2)
            if o is not None and abs(p - o) <= bin_size:
                w *= 1.08
            if abs(p - c) <= bin_size:
                w *= 1.15
            weights.append(w)
        sw = sum(weights) or 1.0
        for p, w in zip(prices, weights):
            volume_by_bin[p] = volume_by_bin.get(p, 0.0) + v * w / sw
        total_volume += v
    if not volume_by_bin or total_volume <= 0:
        return []
    raw = sorted(volume_by_bin.items(), key=lambda kv: kv[1], reverse=True)
    nodes = []
    for price, vol in raw:
        if any(abs(price - n['price']) <= bin_size * 1.5 for n in nodes):
            continue
        nodes.append({
            'price': round(price, 2),
            'source': f'{label}成交密集區',
            'volume_share': round(vol / total_volume * 100, 2),
            'base_score': 4.0 if days == 20 else (3.0 if days == 60 else 2.5),
        })
        if len(nodes) >= 8:
            break
    return nodes


def calc_volume_poc(code: str, current_price: float | None = None, days: int = 20) -> tuple[float | None, dict[str, Any]]:
    rows = history_rows_asc(code, limit=max(days, 60))
    if len(rows) < min(days, 10):
        return None, {"quality":"missing", "reason":"歷史K不足"}
    current = parse_num(current_price) or parse_num(rows[-1].get('close'))
    if current is None:
        return None, {"quality":"missing", "reason":"現價不足"}
    nodes = weighted_volume_profile(rows, current, days, f'{days}日')
    if not nodes:
        return None, {"quality":"missing", "reason":"成交密集區不足"}
    n = nodes[0]
    return n['price'], {"quality":"medium", "reason":"ok", "volume_share": n.get('volume_share'), "source":"OHLCV 權重 Volume Profile POC", "days": days}


def calc_main_force_cost(code: str, current_price: float | None = None) -> tuple[float | None, dict[str, Any]]:
    # Legacy transaction-density cost estimate.
    institutional: list[tuple[str, float, float, dict[str, Any]]] = []
    fw, fm = calc_wave_cost(code, 'foreign_net', 60)
    tw, tm = calc_wave_cost(code, 'trust_net', 60)
    dw, dm = calc_wave_cost(code, 'dealer_net', 20)
    poc, pm = calc_volume_poc(code, current_price, 20)
    current = parse_num(current_price)

    def add_inst(name: str, cost: float | None, weight: float, meta: dict[str, Any], dealer: bool = False) -> None:
        if cost is None:
            return
        q = (meta or {}).get('quality')
        if q in {'low', 'missing', 'error', None}:
            return
        if dealer and q != 'high':
            return
        if q == 'medium':
            weight *= 0.75
        institutional.append((name, float(cost), float(weight), meta or {}))

    add_inst('foreign wave', fw, 2.5, fm)
    add_inst('trust wave', tw, 4.0, tm)
    add_inst('dealer short-term', dw, 0.8, dm, dealer=True)

    poc_valid = poc is not None and (pm or {}).get('quality') not in {'low', 'missing', 'error'}
    poc_item = ('20日 POC', float(poc), 2.0, pm or {}) if poc_valid else None

    def weighted_avg(items: list[tuple[str, float, float, dict[str, Any]]]) -> float | None:
        if not items:
            return None
        total_w = sum(max(x[2], 0.01) for x in items)
        return sum(x[1] * max(x[2], 0.01) for x in items) / total_w

    sources = [{'name': n, 'cost': c, 'weight': w, 'meta': m} for n, c, w, m in institutional]
    if poc_item:
        sources.append({'name': poc_item[0], 'cost': poc_item[1], 'weight': poc_item[2], 'meta': poc_item[3]})

    if not sources:
        return None, {"quality": "missing", "reason": "insufficient wave and POC data", "sources": []}

    inst_avg = weighted_avg(institutional)
    inst_prices = [x[1] for x in institutional]
    inst_zone = None
    if inst_prices:
        inst_zone = {"low": round(min(inst_prices), 2), "high": round(max(inst_prices), 2), "representative": round(inst_avg, 2) if inst_avg else None}

    confluence_items = list(institutional)
    reason = "institutional cost estimate without POC confluence"
    quality = 'medium' if len(institutional) >= 2 else ('low' if len(institutional) == 1 else 'missing')
    representative = inst_avg
    confluence_zone = None

    if inst_avg is not None and poc_item and current:
        near = abs(poc_item[1] - inst_avg) / current <= 0.015
        if near:
            confluence_items.append(poc_item)
            representative = weighted_avg(confluence_items)
            prices = [x[1] for x in confluence_items]
            confluence_zone = {"low": round(min(prices), 2), "high": round(max(prices), 2), "representative": round(representative, 2) if representative else None}
            reason = "娉曚汉鎴愭湰鍗€鑸?0鏃OC鍏辨尟"
            quality = 'high' if len(confluence_items) >= 3 else 'medium'

    # If there is no institutional cost but POC exists, do not label it as main-force cost.
    if representative is None:
        return None, {
            "quality": "low" if poc_item else "missing",
            "reason": "鍍呮湁POC锛屾湭褰㈡垚娉曚汉鎴愭湰鍗€",
            "institution_cost_zone": inst_zone,
            "confluence_zone": None,
            "poc_20d": poc,
            "sources": sources,
        }

    return round(float(representative), 2), {
        "quality": quality,
        "reason": reason,
        "institution_cost_zone": inst_zone,
        "confluence_zone": confluence_zone,
        "poc_20d": poc,
        "sources": sources,
    }





def add_level(
    levels: list[dict[str, Any]],
    price: float | None,
    current: float,
    source: str,
    base_score: float,
    side: str | None = None,
    status: str = "active",
) -> None:
    if price is None or current is None or price <= 0:
        return
    if side is None:
        if price < current:
            side = 'support'
        elif price > current:
            side = 'resistance'
        else:
            return
    if side == 'support' and price >= current:
        return
    if side == 'resistance' and price <= current:
        return
    levels.append({'price': round(float(price), 2), 'source': source, 'base_score': float(base_score), 'side': side, 'status': status})


def add_cost_level(levels: list[dict[str, Any]], cost: float | None, meta: dict[str, Any], current: float, source: str, base_score: float, side: str | None = None) -> None:
    if cost is None:
        return
    q = (meta or {}).get('quality')
    if q not in {'high', 'medium'}:
        return
    add_level(levels, cost, current, source, base_score, side, status=f'quality:{q}')



def add_swing_low(levels: list[dict[str, Any]], level: float | None, current: float, rows: list[dict[str, Any]], label: str, score: float) -> None:
    if level is None:
        return
    st = level_break_state(level, rows, current)
    if current > level:
        if st == 'reclaimed':
            add_level(levels, level, current, f'{label} 跌破後站回觀察', score * 0.65, 'support', 'reclaimed_support')
        else:
            add_level(levels, level, current, label, score, 'support', 'active_support')
    elif current < level:
        add_level(levels, level, current, f'{label} 失守轉壓', score, 'resistance', 'broken_to_resistance')


def add_swing_high(levels: list[dict[str, Any]], level: float | None, current: float, rows: list[dict[str, Any]], label: str, score: float) -> None:
    if level is None:
        return
    st = level_break_state(level, rows, current)
    if current < level:
        add_level(levels, level, current, label, score, 'resistance', 'active_resistance')
    elif current > level:
        if st == 'reclaimed':
            add_level(levels, level, current, f'{label} 突破後回測觀察', score * 0.75, 'support', 'breakout_retest')
        else:
            add_level(levels, level, current, f'{label} 突破轉支撐', score * 0.85, 'support', 'broken_resistance_to_support')


def volume_bar_type(row: dict[str, Any]) -> str:
    o, h, l, c = parse_num(row.get('open')), parse_num(row.get('high')), parse_num(row.get('low')), parse_num(row.get('close'))
    if o is None or h is None or l is None or c is None or h <= l:
        return 'unknown'
    close_pos = (c - l) / (h - l)
    if c > o and close_pos >= 0.65:
        return 'accumulation'
    if c < o and close_pos <= 0.35:
        return 'distribution'
    return 'mixed'


def calc_support_resistance_detail(
    code: str,
    current_price: float | None = None,
    *,
    latest_k_date: str | None = None,
) -> dict[str, Any]:
    rows = history_rows_asc(code, limit=160, as_of_date=latest_k_date)
    if len(rows) < 10:
        current0 = parse_num(current_price) or (parse_num(rows[-1].get('close')) if rows else None)
        return fallback_sr_zones_from_rows(rows, current0)
    current = parse_num(current_price) or parse_num(rows[-1].get('close'))
    if current is None:
        return fallback_sr_zones_from_rows(rows, current)
    reference_k_date = normalize_date(latest_k_date) or normalize_date(rows[-1].get('date'))
    institution_freshness = {
        'ready': False,
        'status': 'background_only',
        'included_in_levels': False,
    }
    margin_freshness = {
        'ready': False,
        'status': 'background_only',
        'included_in_levels': False,
    }

    levels = build_ohlcv_support_resistance_levels(
        rows,
        current,
    )

    support_levels = cluster_levels([x for x in levels if x['side'] == 'support'], current)
    resistance_levels = cluster_levels([x for x in levels if x['side'] == 'resistance'], current)
    method_parts = ['OHLCV成交密集區', '技術關卡共振']
    detail = {
        'support': support_levels[0] if support_levels else None,
        'resistance': resistance_levels[0] if resistance_levels else None,
        'supports': support_levels[:5],
        'resistances': resistance_levels[:5],
        'method': ' + '.join(method_parts) + '｜法人與融資成本僅作背景，不納入支撐壓力',
        'component_freshness': {
            'latest_k_date': reference_k_date,
            'institution': institution_freshness,
            'margin': margin_freshness,
        },
    }
    if not detail['support'] or not detail['resistance']:
        fb = fallback_sr_zones_from_rows(rows, current)
        if not detail['support'] and fb.get('support'):
            detail['support'] = fb['support']; detail['supports'] = (fb.get('supports') or []) + detail.get('supports', [])
        if not detail['resistance'] and fb.get('resistance'):
            detail['resistance'] = fb['resistance']; detail['resistances'] = (fb.get('resistances') or []) + detail.get('resistances', [])
        detail['method'] += '｜不足處以多日高低點/ATR補足'
    return detail

def calc_support_resistance(
    code: str,
    current_price: float | None = None,
    *,
    latest_k_date: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    detail = calc_support_resistance_detail(code, current_price, latest_k_date=latest_k_date)
    s = detail.get('support')
    r = detail.get('resistance')
    def label(x: dict[str, Any] | None, kind: str) -> str:
        if not x:
            return f"{kind} --"
        label_price = x.get('label_price') or fmt(x.get('price'))
        strength = x.get('strength','')
        return f"{kind} {label_price}（{strength}）" if strength else f"{kind} {label_price}"
    return label(s, '支撐'), label(r, '賣壓'), detail


def chip_light(code: str, price: float | None, score_result: dict[str, Any] | None = None) -> tuple[str, str]:
    # Technical veto takes precedence over chip-light labels.
    if score_result and score_result.get('veto'):
        reasons = score_result.get('veto_reasons') or score_result.get('reasons') or ['技術否決']
        return "禁止", str(reasons[0])
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn)
        if analysis_as_of:
            inst = list(
                conn.execute(
                    "SELECT * FROM institution_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 5",
                    (code, analysis_as_of),
                )
            )
            mar = list(
                conn.execute(
                    "SELECT * FROM margin_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 5",
                    (code, analysis_as_of),
                )
            )
        else:
            inst = []
            mar = []
    if len(inst) < 3 or len(mar) < 3:
        return "警戒", "法人/融資資料不足，暫停正式數字判斷"
    foreign5 = sum(float(r["foreign_net"] or 0) for r in inst)
    trust5 = sum(float(r["trust_net"] or 0) for r in inst)
    margin5 = sum(float(r["margin_delta"] or 0) for r in mar)
    bal = parse_num(mar[0]["margin_balance"])
    if (bal is None or bal <= 0) and margin5 > 0:
        return "警戒", "融資餘額資料缺漏，無法驗證融資比例"
    bal = bal or 0
    if bal and bal < 200:
        margin_pct = 0.0
        margin_note = "融資餘額低，忽略小基數波動"
    else:
        margin_pct = (margin5 / bal * 100) if bal else 0.0
        margin_note = ""
    if foreign5 < 0 and margin_pct > 5:
        return "禁止", "外資轉賣、融資大增"
    if foreign5 < 0 or margin_pct > 5:
        return "警戒", "法人不同步或融資追價" + (f"｜{margin_note}" if margin_note else "")
    if foreign5 > 0 and trust5 >= 0 and margin_pct <= 2:
        return "安全", "法人偏多，融資未追高" + (f"｜{margin_note}" if margin_note else "")
    return "警戒", "籌碼尚未同步" + (f"｜{margin_note}" if margin_note else "")





def score_stock_cached(code: str, rows_asc: list[dict[str, Any]], cache_salt: str | None = None) -> dict[str, Any] | None:
    if score_stock is None or len(rows_asc) < 120:
        return None
    latest_date = rows_asc[-1].get('date') if rows_asc else None
    rsi_fingerprint = None
    if rows_asc and any('rsi_close' in row for row in rows_asc):
        rsi_fingerprint = tuple(
            round(float(value), 6) if value is not None else None
            for value in (row.get('rsi_close') for row in rows_asc[-120:])
        )
    cache_key = f"{str(code).zfill(4)}:{SCORING_SYSTEM_VERSION}"
    cached_marker = (latest_date, rsi_fingerprint, cache_salt)
    with _score_cache_lock:
        ts, cached_date, cached = _score_cache.get(cache_key, (0.0, None, None))
        if cached and cached_date == cached_marker and time.time() - ts < SCORE_CACHE_TTL_SECONDS:
            return cached
    try:
        frame = pd.DataFrame(rows_asc)
        columns = ['date', 'open', 'high', 'low', 'close', 'volume']
        columns.extend(
            column
            for column in ('rsi_close', 'technical_open', 'technical_high', 'technical_low', 'technical_close')
            if column in frame.columns
        )
        df = frame[columns].copy()
        result = score_stock(df)
        q = historical_data_quality(rows_asc)
        if result.get('data_level') == 'full' and q.get('level') != 'full':
            result['data_level'] = q.get('level')
            result.setdefault('reasons', []).append(f"歷史K缺口較多，降級為 {q.get('level')}")
        with _score_cache_lock:
            _score_cache[cache_key] = (time.time(), cached_marker, result)
            prune_timed_cache(_score_cache, SCORE_CACHE_TTL_SECONDS * 3, SCORE_CACHE_MAXSIZE)
        return result
    except Exception as exc:
        logging.exception("score_stock failed for %s", code)
        return {"data_valid": False, "data_level": "invalid", "veto": False, "signal": "技術評分失敗", "reason": safe_error(exc), "reasons": [safe_error(exc)], "scores": {"technical_total": 0}}



# ---------- v2.35 practical status / corporate actions ----------
def request_text(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, retries: int = 2, retry_wait: float = 3, timeout: float = 20) -> str:
    last = None
    for i in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers or HEADERS, timeout=timeout)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {mask_secret_text(resp.text[:240])}")
            if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
                resp.encoding = resp.apparent_encoding or 'utf-8'
            return resp.text
        except Exception as exc:
            last = exc
            if i < retries - 1:
                time.sleep(retry_wait)
    raise RuntimeError(safe_error(last))


def _csv_rows_from_text(text: str) -> list[dict[str, str]]:
    import io
    cleaned = text.replace('\ufeff', '').strip()
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return []
    # TWSE CSV often has a title row before the real header. Find the row containing code/date headers.
    header_idx = 0
    for i, ln in enumerate(lines[:20]):
        if ('代號' in ln or '股票代號' in ln or '證券代號' in ln) and ('日期' in ln or '除權' in ln or '除息' in ln):
            header_idx = i
            break
    reader = csv.DictReader(io.StringIO('\n'.join(lines[header_idx:])))
    return [dict(r) for r in reader]


def _row_get_any(row: dict[str, Any], keys: list[str]) -> Any:
    for k in keys:
        if k in row and str(row.get(k) or '').strip() not in {'', '--'}:
            return row.get(k)
    # fallback: fuzzy contain match for Chinese headers changing slightly
    for rk, rv in row.items():
        for k in keys:
            if k and k in str(rk) and str(rv or '').strip() not in {'', '--'}:
                return rv
    return None


def upsert_corporate_action_rows(rows: list[dict[str, Any]], source: str, is_confirmed: int = 0) -> int:
    ts = datetime.now().isoformat(timespec='seconds')
    count = 0
    with _db_lock, closing(db()) as conn:
        for r in rows:
            code = str(_row_get_any(r, ['股票代號', '證券代號', 'Code', 'code', '代號']) or '').strip()[:4]
            if not code.isdigit():
                continue
            d = normalize_date(_row_get_any(r, ['除權息日期', '除息日期', '除權日期', '日期', 'Date', 'date']))
            if not d:
                continue
            raw_type = str(_row_get_any(r, ['除權息', '類型', 'type', 'action_type']) or '').strip()
            if '權' in raw_type and '息' in raw_type:
                action_type = 'right_dividend'
            elif '權' in raw_type:
                action_type = 'right'
            elif '息' in raw_type:
                action_type = 'dividend'
            else:
                action_type = 'dividend'
            cash = parse_num(_row_get_any(r, ['現金股利', '現金股利(元)', 'cash_dividend']))
            stock = parse_num(_row_get_any(r, ['股票股利', '無償配股率', 'stock_dividend']))
            upsert_legacy_corporate_action(
                conn,
                code=code.zfill(4),
                action_date=d,
                action_type=action_type,
                cash_dividend=cash,
                stock_dividend=stock,
                source=source,
                is_confirmed=int(is_confirmed),
                updated_at=ts,
            )
            count += 1
        conn.commit()
    return count


def fetch_twse_ex_dividend_calendar() -> int:
    text = request_text(TWSE_EX_DIVIDEND_CSV, retries=2, retry_wait=3)
    rows = _csv_rows_from_text(text)
    return upsert_corporate_action_rows(rows, 'TWSE闋愬憡', 0)


def fetch_tpex_ex_dividend_calendar() -> int:
    # TPEx CSV endpoints may vary; try generic CSV/HTML export style and parse whatever rows are returned.
    text = request_text(TPEX_EX_DIVIDEND_CSV, retries=1, retry_wait=2)
    rows = _csv_rows_from_text(text)
    return upsert_corporate_action_rows(rows, 'TPEx闋愬憡', 0)


def fetch_finmind_dividend_result(days: int = 430) -> int:
    # Optional backup. If token / anonymous quota fails, caller will fallback to gap-detection.
    end = now_tpe().date()
    start = end - timedelta(days=days)
    total = 0
    codes = [x['code'] for x in read_components()]
    codes += get_watchlist_codes_unordered()
    seen = []
    for code in codes:
        code = str(code).zfill(4)
        if code in seen:
            continue
        seen.append(code)
        try:
            rows = finmind_get('TaiwanStockDividendResult', code, start.isoformat(), end.isoformat())
        except Exception:
            continue
        mapped = []
        for r in rows:
            d = normalize_date(r.get('date') or r.get('stock_and_cache_dividend_date') or r.get('CashExDividendTradingDate') or r.get('ex_dividend_date'))
            if not d:
                continue
            mapped.append({
                '股票代號': code,
                '除權息日期': d,
                '除權息': '息',
                '現金股利': r.get('CashEarningsDistribution') or r.get('cash_dividend') or r.get('cash_earnings_distribution'),
                '股票股利': r.get('StockEarningsDistribution') or r.get('stock_dividend') or r.get('stock_earnings_distribution'),
            })
        total += upsert_corporate_action_rows(mapped, 'FinMind confirmed', 1)
    return total


def update_corporate_actions() -> dict[str, Any]:
    success = False
    errors: list[str] = []
    total = 0
    try:
        n = fetch_twse_ex_dividend_calendar()
        total += n
        success = success or n > 0
    except Exception as e:
        errors.append(f'TWSE corporate action fetch failed: {safe_error(e)}')
    try:
        n = fetch_tpex_ex_dividend_calendar()
        total += n
        success = success or n > 0
    except Exception as e:
        errors.append(f'TPEx corporate action fetch failed: {safe_error(e)}')
    if not success:
        try:
            n = fetch_finmind_dividend_result()
            total += n
            success = success or n > 0
        except Exception as e:
            errors.append(f'FinMind corporate action fetch failed: {safe_error(e)}')
    set_status('corporate_actions', 'fresh' if success else 'stale', f'corporate action update rows {total}' + ('' if success else '; fallback unavailable'))
    return {'success': success, 'total': total, 'errors': errors, 'use_gap_fallback': not success}








def possible_ex_gap_fallback(rows_asc: list[dict[str, Any]]) -> bool:
    if len(rows_asc) < 25:
        return False
    last = rows_asc[-1]
    prev = rows_asc[-2]
    open_ = parse_num(last.get('open'))
    close = parse_num(last.get('close'))
    prev_close = parse_num(prev.get('close'))
    vols = [parse_num(r.get('volume')) for r in rows_asc[-21:-1]]
    vols = [v for v in vols if v]
    vol = parse_num(last.get('volume'))
    if not open_ or not prev_close or not vol or not vols:
        return False
    vol_ratio = vol / (sum(vols) / len(vols)) if vols else 0
    return abs(open_ - prev_close) / prev_close > 0.03 and vol_ratio >= 1.2





def latest_chip_note(code: str, latest_date: str | None) -> str:
    info = build_institution_info(code, latest_date)
    if not info.get('date'):
        return '籌碼資料待補'
    if not (info.get('data_quality') or {}).get('ready'):
        return str(info.get('note') or '籌碼資料延遲')
    return f"籌碼採用 {info.get('date')}" if latest_date and info.get('date') < latest_date else '籌碼最新'


def build_institution_info(code: str, latest_date: str | None = None) -> dict[str, Any]:
    # Build institutional direction, streaks, and latest source date.
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn, latest_date)
        rows = (
            conn.execute(
                """
                SELECT * FROM institution_daily
                WHERE code=? AND date<=?
                ORDER BY date DESC
                LIMIT 20
                """,
                (str(code).zfill(4), analysis_as_of),
            ).fetchall()
            if analysis_as_of
            else []
        )
    if not rows:
        quality = assess_component_freshness(None, latest_date, max_lag_days=3)
        return {
            'note': '法人資料待補',
            'raw_note': None,
            'date': None,
            'freshness': quality['status'],
            'source_delayed': False,
            'data_quality': quality,
        }

    row = rows[0]
    f = parse_num(row['foreign_net']) or 0
    t = parse_num(row['trust_net']) or 0
    d_net = parse_num(row['dealer_net']) or 0

    def streak(field: str, positive: bool = True) -> int:
        n = 0
        for rr in rows:
            v = parse_num(rr[field]) or 0
            if (v > 0 if positive else v < 0):
                n += 1
            else:
                break
        return n

    foreign_buy_streak = streak('foreign_net', True)
    foreign_sell_streak = streak('foreign_net', False)
    trust_buy_streak = streak('trust_net', True)
    trust_sell_streak = streak('trust_net', False)

    base_note = ''
    if f > 0 and t > 0:
        base_note = '外資投信同步買超'
    elif f < 0 and t < 0:
        base_note = '外資投信同步賣超'
    elif f < 0 and t > 0:
        base_note = '外資調節｜投信承接'
    elif f > 0 and t < 0:
        base_note = '外資買超｜投信調節'
    elif t > 0:
        base_note = '投信買超'
    elif f > 0:
        base_note = '外資買超'
    else:
        base_note = '法人動向普通'

    streak_notes: list[str] = []
    if trust_buy_streak >= 3:
        streak_notes.append(f'投信連買{trust_buy_streak}日')
    elif trust_sell_streak >= 3:
        streak_notes.append(f'投信連賣{trust_sell_streak}日')
    if foreign_buy_streak >= 3:
        streak_notes.append(f'外資連買{foreign_buy_streak}日')
    elif foreign_sell_streak >= 3:
        streak_notes.append(f'外資連賣{foreign_sell_streak}日')

    raw_note = '｜'.join(([base_note] if base_note else []) + streak_notes) or '法人動向普通'
    d = normalize_date(row['date']) if row['date'] else None
    quality = assess_component_freshness(d, latest_date, max_lag_days=3)
    freshness = quality['status']
    if quality['ready']:
        freshness = 'latest' if d == normalize_date(latest_date) else 'T-1/latest_available'
        note = raw_note
    else:
        note = f"法人資料延遲（{d or '無日期'}；K線 {normalize_date(latest_date) or '無日期'}），不納入當日判斷"
    return {
        'note': note,
        'raw_note': raw_note,
        'date': d,
        'freshness': freshness,
        'source_delayed': quality['status'] == DataQualityStatus.SOURCE_DELAYED.value,
        'data_quality': quality,
        'foreign_net': f,
        'trust_net': t,
        'dealer_net': d_net,
        'foreign_buy_streak': foreign_buy_streak,
        'foreign_sell_streak': foreign_sell_streak,
        'trust_buy_streak': trust_buy_streak,
        'trust_sell_streak': trust_sell_streak,
    }


def build_institution_note(code: str, latest_date: str | None = None) -> str:
    return str(build_institution_info(code, latest_date).get('note') or '法人資料待補')


def latest_margin_info(code: str, latest_date: str | None = None) -> dict[str, Any]:
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn, latest_date)
        row = (
            conn.execute(
                """
                SELECT * FROM margin_daily
                WHERE code=? AND date<=?
                ORDER BY date DESC
                LIMIT 1
                """,
                (str(code).zfill(4), analysis_as_of),
            ).fetchone()
            if analysis_as_of
            else None
        )
    if not row:
        quality = assess_component_freshness(None, latest_date, max_lag_days=3)
        return {
            'date': None,
            'freshness': quality['status'],
            'note': '融資融券資料待補',
            'raw_note': None,
            'source_delayed': False,
            'data_quality': quality,
        }
    d = normalize_date(row['date']) if row['date'] else None
    quality = assess_component_freshness(d, latest_date, max_lag_days=3)
    freshness = quality['status']
    if quality['ready']:
        freshness = 'latest' if d == normalize_date(latest_date) else 'T-1/latest_available'
    md = parse_num(row['margin_delta'])
    sd = parse_num(row['short_delta'])
    raw_note = f"融資變化 {fmt(md)}｜融券變化 {fmt(sd)}"
    note = raw_note if quality['ready'] else f"融資資料延遲（{d or '無日期'}；K線 {normalize_date(latest_date) or '無日期'}），不納入當日判斷"
    return {
        'date': d,
        'freshness': freshness,
        'note': note,
        'raw_note': raw_note,
        'source_delayed': quality['status'] == DataQualityStatus.SOURCE_DELAYED.value,
        'data_quality': quality,
        'margin_delta': md,
        'short_delta': sd,
    }


def build_valuation_tags(val: sqlite3.Row | None, price: float | None) -> list[str]:
    tags: list[str] = []
    if not val:
        return ['估值資料待補']
    pe = parse_num(val['pe'])
    pb = parse_num(val['pb'])
    dy = parse_num(val['dividend_yield'])
    if pe is not None and pe > 40:
        tags.append(f'PE偏高 {fmt(pe)}')
    if pb is None:
        tags.append('PB待補')
    elif pb > 6:
        tags.append(f'PB偏高 {fmt(pb)}')
    if dy is None:
        tags.append('殖利率待補')
    return tags[:3]


def twse_valuation_payload(code: str, row: dict[str, Any] | sqlite3.Row | None = None) -> dict[str, Any]:
    """Return official TWSE BWIBBU valuation payload for UI display.

    This helper is read-only. GET routes must not fetch/write valuation data.
    """
    data = dict(row) if row else get_twse_valuation(code)
    if not data:
        try:
            with closing(db()) as conn:
                legacy_row = conn.execute(
                    """
                    SELECT *
                    FROM valuation
                    WHERE code=?
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (str(code or "").strip().zfill(4)[:4],),
                ).fetchone()
        except sqlite3.OperationalError:
            legacy_row = None
        if legacy_row:
            legacy = dict(legacy_row)
            data = {
                "source": legacy.get("source") or "LOCAL_VALUATION",
                "source_status": "ok",
                "data_date": legacy.get("date"),
                "updated_at": legacy.get("updated_at"),
                "timezone": "Asia/Taipei",
                "pe_ratio": legacy.get("pe"),
                "pb_ratio": legacy.get("pb"),
                "dividend_yield": legacy.get("dividend_yield"),
                "dividend_year": None,
                "financial_year_quarter": None,
                "close_price": None,
                "reason": None,
            }
    if not data:
        empty_quality = build_valuation_quality_payload(None)
        return {
            "source": "TWSE_BWIBBU",
            "source_type": empty_quality.get("source_type"),
            "source_market": empty_quality.get("source_market"),
            "source_name": empty_quality.get("source_name"),
            "source_status": "not_found",
            "data_date": None,
            "updated_at": None,
            "timezone": "Asia/Taipei",
            "pe_ratio": None,
            "pb_ratio": None,
            "dividend_yield": None,
            "pe_status": empty_quality.get("pe_status"),
            "pb_status": empty_quality.get("pb_status"),
            "dividend_yield_status": empty_quality.get("dividend_yield_status"),
            "dividend_yield_source": empty_quality.get("dividend_yield_source"),
            "dividend_yield_unavailable_reason": empty_quality.get("dividend_yield_unavailable_reason"),
            "valuation_flags": empty_quality.get("suspicious_flags") or [],
            "suspicious_flags": empty_quality.get("suspicious_flags") or [],
            "parse_warnings": empty_quality.get("parse_warnings") or [],
            "valuation_quality": empty_quality,
            "freshness_status": "not_found",
            "market_type": None,
            "market_latest_date": None,
            "valuation_trade_day_gap": None,
            "price_date": None,
            "price_trade_day_gap": None,
            "stale_reason": None,
            "debug_reason": None,
            "dividend_year": None,
            "financial_year_quarter": None,
            "reason": "TWSE BWIBBU 無該股票估值資料",
        }
    quality = build_valuation_quality_payload(data)
    try:
        with closing(db()) as conn:
            freshness = valuation_freshness_for_row(conn, code, data)
    except sqlite3.OperationalError as exc:
        freshness = {
            "freshness_status": "cannot_verify",
            "source_status": "cannot_verify",
            "market_type": None,
            "market_latest_date": None,
            "valuation_trade_day_gap": None,
            "price_date": None,
            "price_trade_day_gap": None,
            "valuation_flags": ["valuation_freshness_check_failed"],
            "stale_reason": f"valuation freshness check failed: {exc}",
            "debug_reason": f"valuation freshness check failed: {exc}",
        }
    pe_value = valuation_metric_display_value(quality, "pe")
    pb_value = valuation_metric_display_value(quality, "pb")
    dividend_yield_value = valuation_metric_display_value(quality, "dividend_yield")
    valuation_flags = sorted(set((quality.get("suspicious_flags") or []) + (freshness.get("valuation_flags") or [])))
    return {
        "source": data.get("source") or "TWSE_BWIBBU",
        "source_type": quality.get("source_type"),
        "source_market": quality.get("source_market"),
        "source_name": quality.get("source_name"),
        "source_status": freshness.get("source_status") or data.get("source_status") or "ok",
        "data_date": data.get("data_date"),
        "updated_at": data.get("updated_at"),
        "timezone": data.get("timezone") or "Asia/Taipei",
        "pe_ratio": pe_value,
        "pb_ratio": pb_value,
        "dividend_yield": dividend_yield_value,
        "pe_status": quality.get("pe_status"),
        "pb_status": quality.get("pb_status"),
        "dividend_yield_status": quality.get("dividend_yield_status"),
        "dividend_yield_source": quality.get("dividend_yield_source"),
        "dividend_yield_unavailable_reason": quality.get("dividend_yield_unavailable_reason"),
        "valuation_flags": valuation_flags,
        "suspicious_flags": quality.get("suspicious_flags") or [],
        "parse_warnings": quality.get("parse_warnings") or [],
        "valuation_quality": quality,
        "freshness_status": freshness.get("freshness_status"),
        "market_type": freshness.get("market_type"),
        "market_latest_date": freshness.get("market_latest_date"),
        "valuation_trade_day_gap": freshness.get("valuation_trade_day_gap"),
        "price_date": freshness.get("price_date"),
        "price_trade_day_gap": freshness.get("price_trade_day_gap"),
        "stale_reason": freshness.get("stale_reason"),
        "debug_reason": freshness.get("debug_reason"),
        "dividend_year": data.get("dividend_year"),
        "financial_year_quarter": data.get("financial_year_quarter"),
        "close_price": parse_num(data.get("close_price")),
        "reason": data.get("reason"),
    }


def twse_valuation_summary(code: str) -> str:
    v = twse_valuation_payload(code)
    if v.get("source_status") != "ok":
        return ""
    return "｜".join(
        x
        for x in [
            (f"PE {fmt(v.get('pe_ratio'))}" if v.get("pe_ratio") is not None else ""),
            (f"PB {fmt(v.get('pb_ratio'))}" if v.get("pb_ratio") is not None else ""),
            (f"殖 {fmt(v.get('dividend_yield'))}" if v.get("dividend_yield") is not None else ""),
        ]
        if x
    )



def _classify_practical_status_core(data: dict) -> dict:
    return classify_practical_status_core(data)


def _canonicalize_web_practical_status(
    display_context: dict[str, Any] | None,
    canonical_snapshot: dict[str, Any] | None,
    current_price: float | None,
) -> dict[str, Any]:
    """Project the one canonical referee into the legacy Web response shape."""

    result = dict(display_context or {})
    snapshot = dict(canonical_snapshot or {})
    referee = dict(snapshot.get("referee") or {})
    if not referee:
        referee = {
            "decision_ready": False,
            "main_status": "資料不足",
            "main_reasons": ["canonical 盤後裁判資料尚未建立"],
            "reason_code": "canonical_snapshot_unavailable",
            "source": "canonical_close_batch_snapshot",
            "can_be_overridden_by_model": False,
        }
    main_status = str(referee.get("main_status") or "資料不足")
    reasons = [
        str(item)
        for item in list(referee.get("main_reasons") or [])
        if str(item).strip()
    ][:2]
    status_level, status_badge = _practical_status_badge(main_status)
    support_zone = referee.get("support_zone") if isinstance(referee.get("support_zone"), dict) else None
    resistance_zone = referee.get("resistance_zone") if isinstance(referee.get("resistance_zone"), dict) else None
    support_pos = zone_position(current_price, support_zone, "support")
    resistance_pos = zone_position(current_price, resistance_zone, "resistance")
    component_freshness = dict(referee.get("component_freshness") or {})
    institution_freshness = dict(component_freshness.get("institution") or {})
    margin_freshness = dict(component_freshness.get("margin") or {})
    reason_code = str(referee.get("reason_code") or "")
    missing_zone_message = {
        "recommendation_safety_hard_block": "安全條件否決，不提供進場區間",
        "no_trade": "當日無成交，不形成支撐／賣壓",
        "trading_halt": "停止交易，不形成支撐／賣壓",
        "no_regular_lot_ohlcv": "無一般交易資料，不形成支撐／賣壓",
        "no_ohlcv_residual_activity": "無有效一般交易，不形成支撐／賣壓",
        "inactive_official_universe": "非有效交易標的，不提供進場區間",
        "shared_support_resistance_not_ready": "支撐／賣壓結構尚未形成",
    }.get(reason_code, "支撐／賣壓結構尚未形成")
    return {
        **result,
        "main_status": main_status,
        "status_level": status_level,
        "status_badge": status_badge,
        "main_reasons": reasons,
        "decision_ready": bool(referee.get("decision_ready")),
        "reason_code": referee.get("reason_code"),
        "referee_source": referee.get("source"),
        "referee_version": referee.get("version"),
        "input_assembler_version": referee.get("input_assembler_version"),
        "recommendation_safety": dict(referee.get("recommendation_safety") or {}),
        "trading_state": dict(snapshot.get("trading_state") or {}),
        "analysis_contract_version": snapshot.get("analysis_contract_version"),
        "analysis_status": dict(snapshot.get("analysis_status") or {}),
        "can_be_overridden_by_model": False,
        "support_pos": support_pos,
        "resistance_pos": resistance_pos,
        "support_display": (
            format_zone_display("支撐", support_zone, support_pos)
            if support_zone
            else missing_zone_message
        ),
        "resistance_display": (
            format_zone_display("賣壓", resistance_zone, resistance_pos)
            if resistance_zone
            else missing_zone_message
        ),
        "institution_freshness": institution_freshness,
        "margin_freshness": margin_freshness,
        "institution_date": institution_freshness.get("source_date"),
        "margin_date": margin_freshness.get("source_date"),
        "chip_components_current": bool(
            institution_freshness.get("ready") and margin_freshness.get("ready")
        ),
    }


def _canonical_support_resistance_detail(
    canonical_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    snapshot = dict(canonical_snapshot or {})
    referee = dict(snapshot.get("referee") or {})
    return {
        "support": referee.get("support_zone"),
        "resistance": referee.get("resistance_zone"),
        "supports": [referee["support_zone"]] if isinstance(referee.get("support_zone"), dict) else [],
        "resistances": [referee["resistance_zone"]] if isinstance(referee.get("resistance_zone"), dict) else [],
        "method": referee.get("support_resistance_method") or "canonical 盤後裁判未形成完整支撐與賣壓",
        "semantics": referee.get("support_resistance_semantics"),
        "status": "ok" if referee.get("decision_ready") else "unavailable",
        "reason_code": referee.get("reason_code"),
        "analysis_contract_version": snapshot.get("analysis_contract_version"),
        "can_override_main_status": False,
    }


def _canonical_advisory_text(canonical_snapshot: dict[str, Any] | None) -> str:
    snapshot = dict(canonical_snapshot or {})
    advisory = dict(snapshot.get("advisory") or {})
    parts = [
        str(advisory.get(key) or "").strip()
        for key in ("headline", "buy_plan", "holder_plan", "invalidation")
    ]
    unique = list(dict.fromkeys(part for part in parts if part))
    if unique:
        return "\n".join(unique)
    referee = dict(snapshot.get("referee") or {})
    reasons = [str(item) for item in list(referee.get("main_reasons") or []) if str(item).strip()]
    return "；".join(reasons) or "canonical 盤後裁判資料不足，暫不判斷。"


def _canonical_technical_detail(
    canonical_snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Project canonical technical values into the existing detail-table shape."""

    technical = dict((canonical_snapshot or {}).get("technical") or {})
    if not technical.get("decision_ready"):
        return []
    rsi = dict(technical.get("rsi") or {})
    ma = dict(technical.get("moving_averages") or {})
    macd = dict(technical.get("macd") or {})
    kd = dict(technical.get("kd") or {})
    bollinger = dict(technical.get("bollinger") or {})

    def item(name: str, value: Any, explain: str) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, float):
            value = round(value, 4)
        return {
            "name": name,
            "value": value,
            "explain": explain,
            "risk": "與 LINE Bot 使用相同盤後技術快照；不得單獨覆蓋主結論。",
        }

    rows = [
        item("RSI5", rsi.get("rsi5"), "5 日相對強弱"),
        item("RSI10", rsi.get("rsi10"), "10 日相對強弱"),
        item("RSI14", rsi.get("rsi14"), "14 日相對強弱"),
        item("MA5", ma.get("ma5"), "5 日均線"),
        item("MA10", ma.get("ma10"), "10 日均線"),
        item("MA20", ma.get("ma20"), "20 日均線"),
        item("MA60", ma.get("ma60"), "60 日均線"),
        item("MACD DIF", macd.get("dif"), "MACD(12,26,9) DIF"),
        item("MACD Signal", macd.get("signal"), "MACD(12,26,9) 訊號線"),
        item("MACD Osc", macd.get("oscillator"), "MACD 柱狀體"),
        item("KD K", kd.get("k"), "KD(9,3,3) K 值"),
        item("KD D", kd.get("d"), "KD(9,3,3) D 值"),
        item("ATR14", technical.get("atr14"), "14 日平均真實波幅"),
        item("OBV", technical.get("obv"), "能量潮"),
        item("VOL MA20", technical.get("volume_ma20"), "20 日均量（股）"),
        item("布林上軌", bollinger.get("upper"), "20 日布林通道上軌"),
        item("布林中軌", bollinger.get("middle"), "20 日布林通道中軌"),
        item("布林下軌", bollinger.get("lower"), "20 日布林通道下軌"),
    ]
    return [row for row in rows if row is not None]


def _canonical_valuation_for_web(
    canonical_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    valuation = dict((canonical_snapshot or {}).get("valuation") or {})
    return {
        "available": bool(valuation.get("available")),
        "pe_ratio": valuation.get("pe_ratio"),
        "pb_ratio": valuation.get("pb_ratio"),
        "dividend_yield": valuation.get("dividend_yield_pct"),
        "data_date": valuation.get("trade_date"),
        "source": valuation.get("source"),
        "source_name": valuation.get("source"),
        "source_type": "official",
        "source_status": valuation.get("status"),
        "reason": valuation.get("reason"),
        "can_override_main_status": False,
    }


def _canonical_price_volume_for_web(
    canonical_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    snapshot = dict(canonical_snapshot or {})
    microstructure = dict(snapshot.get("microstructure_status") or {})
    levels = list(snapshot.get("price_levels") or [])
    ready = bool(microstructure.get("decision_ready"))
    display = dict(snapshot.get("scoped_price_volume_display") or {})
    scoped = not ready and display.get("available") is True and display.get("quality") == "scoped"
    display_ready = bool((ready or scoped) and levels)
    coverage = display.get("official_coverage_ratio")
    reason = (
        "限定交易範圍內已驗證；未達官方總成交量覆蓋門檻，僅顯示已取得的價量，不參與評分。"
        if scoped else "分價量已通過成交量核對。" if ready
        else "分價量資料尚未通過完整性或成交量核對，暫不顯示。"
    )
    max_lots = max((row.get("volume_lots") or 0 for row in levels), default=0)
    return {
        "available": display_ready,
        "display_available": display_ready,
        "analysis_eligible": ready,
        # Preserve the legacy full-quality flag; scoped display is explicit.
        "is_true_price_volume": bool(ready and levels),
        "status": "scoped" if scoped else microstructure.get("status"),
        "quality": "ok" if ready else "scoped" if scoped else "unavailable",
        "quality_reason": reason,
        "summary_text": reason,
        "trade_scope": {
            "regular_intraday": "regular_intraday",
            "fugle_captured_session": "captured_session",
        }.get(display.get("trade_scope")),
        "official_coverage_pct": round(coverage * 100, 4) if coverage is not None else None,
        "total_volume_lots": snapshot.get("captured_volume_lots"),
        "snapshot_time": snapshot.get("snapshot_time"),
        "price_level_count": snapshot.get("price_level_count", len(levels)),
        "price_levels_truncated": bool(snapshot.get("price_levels_truncated")),
        "profile_rows": [
            {
                "price": row.get("price"),
                "volume_lots": row.get("volume_lots"),
                "bar_pct": round((row.get("volume_lots") or 0) / max_lots * 100, 2) if max_lots else 0,
                "inner_lots": row.get("inner_lots"),
                "outer_lots": row.get("outer_lots"),
                "neutral_lots": row.get("neutral_lots"),
                "dominance": row.get("dominance"),
                "dominance_zh": row.get("dominance_zh"),
            }
            for row in levels if display_ready
        ],
        "flow_summary": snapshot.get("flow_summary"),
        "support_pressure": snapshot.get("support_pressure"),
        "trade_date": snapshot.get("trade_date"),
        "analysis_contract_version": snapshot.get("analysis_contract_version"),
        "can_override_main_status": False,
    }


def _canonical_next_day_for_web(
    canonical_snapshot: dict[str, Any] | None,
    display_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose the canonical advisory in the legacy next-day card without recalculation."""

    snapshot = dict(canonical_snapshot or {})
    advisory = dict(snapshot.get("advisory") or {})
    referee = dict(snapshot.get("referee") or {})
    global_context = dict(snapshot.get("global_market_context") or {})
    night_context = dict(snapshot.get("taifex_night_context") or {})
    institutional = dict(snapshot.get("institutional_context") or {})
    technical = dict(snapshot.get("technical") or {})
    available = bool(advisory.get("decision_ready"))
    return {
        "available": available,
        "model_version": advisory.get("version") or snapshot.get("analysis_contract_version"),
        "analysis_contract_version": snapshot.get("analysis_contract_version"),
        "label": referee.get("main_status") or "資料不足",
        "status": (snapshot.get("analysis_status") or {}).get("status"),
        "message": advisory.get("headline") or _canonical_advisory_text(snapshot),
        "opening_label": referee.get("main_status") or "資料不足",
        "sustainability_label": advisory.get("action_state") or "依條件觀察",
        "factors": {
            "us": {"reason": global_context.get("note")},
            "night": {"reason": night_context.get("note")},
            "chip": {"reason": institutional.get("note")},
            "tech": {"reason": technical.get("reason")},
        },
        "warnings": list(advisory.get("background_notes") or []),
        "display_gate": dict(display_gate or {}),
        "can_override_main_status": False,
    }


def classify_practical_status(code: str, rows_asc: list[dict[str, Any]], price: float | None, sr_detail: dict[str, Any], val: sqlite3.Row | None, latest_date: str | None, corporate_update_ok: bool | None = None) -> dict[str, Any]:
    ctx = technical_context_from_rows(rows_asc)
    close = float(price if price is not None else (ctx.get('close') or 0)) or None
    core_missing = [name for name, key in [('現價','close'),('RSI','rsi10'),('MA20','ma20'),('ATR','atr14')] if ctx.get(key) is None]
    support_zone = sr_detail.get('support') if sr_detail else None
    resistance_zone = sr_detail.get('resistance') if sr_detail else None
    support_pos = zone_position(close, support_zone, 'support')
    resistance_pos = zone_position(close, resistance_zone, 'resistance')
    ex_info = get_recent_corporate_action(code, latest_date or ctx.get('date') or today_iso(), days_before=3, days_after=MA20_INVALID_DAYS_AFTER_EX)
    ex_tags: list[str] = []
    if ex_info:
        dfa = int(ex_info.get('days_from_action') or 0)
        if dfa < 0:
            ex_tags.append('即將除權息，價格將調整')
        elif dfa == 0:
            ex_tags.append('今日除權息，技術線型需降權')
        elif 0 < dfa <= MA20_INVALID_DAYS_AFTER_EX:
            ex_tags.append('近期除權息，MA20與前低需降權')
        if not ex_info.get('is_confirmed'):
            ex_tags.append('除權息日期待確認')
    elif corporate_update_ok is False and possible_ex_gap_fallback(rows_asc):
        ex_tags.append('疑似除權息/跳空事件，技術破位需確認')
    reasons: list[str] = []
    warn_reasons: list[str] = []
    forbid_reasons: list[str] = []
    positive_reasons: list[str] = []
    rr_ratio: float | None = None
    rr_note: str | None = None
    rsi = ctx.get('rsi10'); ma20 = ctx.get('ma20'); ma60 = ctx.get('ma60'); atr14 = ctx.get('atr14')
    open_ = ctx.get('open'); high = ctx.get('high'); vol = ctx.get('volume'); vol_ma20 = ctx.get('vol_ma20')
    prev_low = ctx.get('prev_10d_low'); prev_close = ctx.get('prev_close')
    volume_ratio = (vol / vol_ma20) if vol and vol_ma20 else None
    ma20_reliable = not (ex_info and 0 <= int(ex_info.get('days_from_action') or 0) <= MA20_INVALID_DAYS_AFTER_EX)
    ma20_break_pct = ((ma20 - close) / ma20 * 100) if ma20 and close else 0
    prev_low_break_pct = ((prev_low - close) / prev_low * 100) if prev_low and close else 0
    raw_break = bool(ma20_reliable and close and ma20 and prev_low and volume_ratio and close < ma20 and ma20_break_pct >= 0.5 and close < prev_low and prev_low_break_pct >= 0.3 and volume_ratio >= 1.5)
    confirmed_support_break = raw_break
    if ex_tags and raw_break:
        confirmed_support_break = False
        warn_reasons.append('近期除權息，破位訊號需確認')
    if confirmed_support_break:
        forbid_reasons.append('放量跌破支撐')
    if support_pos['state'] == 'broken':
        warn_reasons.append('已跌破支撐區')
    stop_loss, stop_source = choose_stop_loss_candidate(close, support_zone, prev_low, ma20, atr14)
    stop_pct = ((close - stop_loss) / close * 100) if close and stop_loss else None
    atr_pct = (atr14 / close * 100) if close and atr14 else None
    stop_atr = (stop_pct / atr_pct) if stop_pct is not None and atr_pct else None
    if stop_pct is not None:
        if ex_tags and stop_pct > 8:
            warn_reasons.append('除權息後停損距離需重新確認')
        elif stop_pct > 8 and (stop_atr is None or stop_atr > 2.5):
            forbid_reasons.append(f'回檔空間過大（約 {stop_pct:.1f}%），暫不宜追高')
        elif stop_pct > 5 or (stop_atr is not None and stop_atr > 2.0):
            warn_reasons.append(f'離支撐還有約 {stop_pct:.1f}%，追高風險偏高')
    if support_pos['state'] == 'broken' and volume_ratio and volume_ratio >= 1.2:
        warn_reasons.append('跌破支撐且量能偏大')
    if resistance_pos['state'] == 'broken_up':
        if volume_ratio and volume_ratio >= 1.5 and open_ and close and close > open_:
            positive_reasons.append('放量突破賣壓區')
        elif volume_ratio and volume_ratio < 1.2:
            warn_reasons.append('無量突破賣壓區，需確認')
        elif open_ and close and close <= open_:
            warn_reasons.append('突破後收弱，留意假突破')
    is_black = bool(open_ and close and close < open_)
    upper_shadow_pct = ((high - max(open_, close)) / close * 100) if high and open_ and close else 0
    gap_up_black = bool(open_ and prev_close and close and open_ > prev_close * 1.015 and close < open_ and (volume_ratio or 0) >= 1.5)
    volume_spike_with_weakness = bool(volume_ratio and volume_ratio >= 2.5 and open_ and close and close <= open_)
    price_action_warning = bool(upper_shadow_pct >= 3 or volume_spike_with_weakness or ((volume_ratio or 0) >= 1.8 and is_black) or gap_up_black)
    rsi_hot = bool(rsi is not None and rsi >= 68)
    rsi_too_hot = bool(rsi is not None and rsi >= 80 and price_action_warning)
    if gap_up_black:
        warn_reasons.append('跳空高開收黑，追價風險提高')
    if rsi_too_hot:
        warn_reasons.append('RSI過熱且量價轉弱')
    elif rsi is not None and rsi >= 80:
        reasons.append('RSI高檔鈍化，勿追高')
    elif rsi_hot:
        reasons.append(f'RSI偏高 {rsi:.1f}')
    if close and ma20 and close < ma20 and ma20_reliable:
        warn_reasons.append('跌破月線，短線轉弱')
    if rsi is not None and rsi < 45:
        warn_reasons.append(f'RSI偏弱 {rsi:.1f}')
    osc = ctx.get('osc')
    if osc is not None and osc < 0:
        warn_reasons.append('上漲力道轉弱')
    inst_info = build_institution_info(code, latest_date)
    inst_note = str(inst_info.get('note') or '法人資料待補')
    margin_info = latest_margin_info(code, latest_date)
    inst_quality = inst_info.get('data_quality') or assess_component_freshness(
        inst_info.get('date'), latest_date, max_lag_days=3
    )
    margin_quality = margin_info.get('data_quality') or assess_component_freshness(
        margin_info.get('date'), latest_date, max_lag_days=3
    )
    inst_ready = bool(inst_quality.get('ready'))
    margin_ready = bool(margin_quality.get('ready'))
    md = margin_info.get('margin_delta') if margin_ready else None
    if md is not None and md > 0 and close and ma20 and close < ma20:
        warn_reasons.append('融資增加但股價偏弱')
    if inst_ready:
        if '同步賣超' in inst_note:
            warn_reasons.append(inst_note)
        elif inst_note not in {'法人動向普通', '法人資料待補'}:
            reasons.append(inst_note)
    val_tags = build_valuation_tags(val, close)
    if any('偏高' in x for x in val_tags):
        warn_reasons.append(val_tags[0])
    for t in ex_tags:
        warn_reasons.append(t)
    rr_ratio, rr_note = calc_risk_reward_ratio(close, stop_loss, resistance_zone, resistance_pos)
    support_low, support_high = _extract_zone_bounds(support_zone)
    resistance_low, resistance_high = _extract_zone_bounds(resistance_zone)
    core_data = {
        "current_price": close,
        "previous_close": ctx.get('prev_close'),
        "ma20": ma20,
        "ma20_3days_ago": _indicator_value_at(rows_asc, 'ma20', -4),
        "ma60": ma60,
        "rsi": ctx.get('rsi14') if ctx.get('rsi14') is not None else rsi,
        "macd_osc": ctx.get('osc'),
        "macd_osc_prev": _indicator_value_at(rows_asc, 'osc', -2),
        "atr": atr14,
        "volume": vol,
        "volume_avg_20d": vol_ma20,
        "low_10d": prev_low,
        "support_zone_upper": support_high,
        "support_zone_lower": support_low,
        "resistance_zone_upper": resistance_high if resistance_high is not None else resistance_low,
    }
    core_result = _classify_practical_status_core(core_data)
    main_status = str(core_result.get("status") or "資料不足")
    main_reasons = [str(x) for x in (core_result.get("reasons") or []) if str(x).strip()][:2]
    if main_status == "資料不足" and not main_reasons and core_missing:
        main_reasons = [f"核心資料不足：{','.join(core_missing)}"]
    level, badge = _practical_status_badge(main_status)
    return {
        'main_status': main_status, 'status_level': level, 'status_badge': badge,
        'main_reasons': main_reasons, 'risk_tags': val_tags, 'institution_note': inst_note,
        'institution_info': inst_info, 'institution_date': inst_info.get('date'),
        'margin_info': margin_info, 'margin_date': margin_info.get('date'),
        'institution_freshness': inst_quality, 'margin_freshness': margin_quality,
        'chip_components_current': bool(inst_ready and margin_ready),
        'risk_reward_ratio': rr_ratio,
        'support_pos': support_pos, 'resistance_pos': resistance_pos,
        'support_display': format_zone_display('支撐', support_zone, support_pos),
        'resistance_display': format_zone_display('賣壓', resistance_zone, resistance_pos),
        'stop_loss_candidate': stop_loss, 'stop_loss_source': stop_source, 'stop_loss_distance_pct': stop_pct, 'stop_loss_distance_atr': stop_atr,
        'ex_dividend': ex_info, 'ex_tags': ex_tags,
    }


def classify_practical_status_cached(
    code: str,
    rows_asc: list[dict[str, Any]],
    price: float | None,
    sr_detail: dict[str, Any],
    val: sqlite3.Row | None,
    latest_date: str | None,
    corporate_update_ok: bool | None = None,
) -> dict[str, Any]:
    price_key = round(float(price), 4) if price is not None else None
    key = f"{str(code).zfill(4)}:{latest_date or ''}:{price_key}:{corporate_update_ok}"
    now = time.time()
    with _practical_cache_lock:
        cached = _practical_cache.get(key)
        if cached and cached[1] == latest_date and cached[2] == price_key and now - cached[0] < PRACTICAL_CACHE_TTL_SECONDS:
            return copy.deepcopy(cached[3])
    result = classify_practical_status(code, rows_asc, price, sr_detail, val, latest_date, corporate_update_ok)
    with _practical_cache_lock:
        _practical_cache[key] = (time.time(), latest_date, price_key, copy.deepcopy(result))
        prune_timed_cache(_practical_cache, PRACTICAL_CACHE_TTL_SECONDS * 3, PRACTICAL_CACHE_MAXSIZE)
    return result


def should_write_state_history() -> bool:
    now = now_tpe()
    return now.hour >= 15


def save_stock_state(code: str, latest_date: str | None, status: dict[str, Any], display_signal: str, close: float | None, support_text: str, resistance_text: str) -> None:
    if not should_write_state_history():
        return
    calc_date = latest_date or today_iso()
    with _db_lock, closing(db()) as conn:
        conn.execute(
            "INSERT INTO stock_state_history(code,calc_date,calc_ts,main_status,status_level,display_signal,close,support_text,resistance_text,reasons_json) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code,calc_date) DO UPDATE SET calc_ts=excluded.calc_ts, main_status=excluded.main_status, status_level=excluded.status_level, display_signal=excluded.display_signal, close=excluded.close, support_text=excluded.support_text, resistance_text=excluded.resistance_text, reasons_json=excluded.reasons_json",
            (str(code).zfill(4), calc_date, time.time(), status.get('main_status'), int(status.get('status_level') or 0), display_signal, close, support_text, resistance_text, json.dumps(status.get('main_reasons', []), ensure_ascii=False)),
        )
        conn.commit()


def get_previous_stock_state(code: str, current_date: str | None) -> dict[str, Any] | None:
    if not current_date:
        current_date = today_iso()
    with closing(db()) as conn:
        r = conn.execute("SELECT * FROM stock_state_history WHERE code=? AND calc_date < ? ORDER BY calc_date DESC LIMIT 1", (str(code).zfill(4), current_date)).fetchone()
        return dict(r) if r else None


def count_consecutive_status(code: str, status: str) -> int:
    with closing(db()) as conn:
        rows = conn.execute("SELECT main_status FROM stock_state_history WHERE code=? ORDER BY calc_date DESC LIMIT 30", (str(code).zfill(4),)).fetchall()
    count = 0
    for r in rows:
        if r['main_status'] == status:
            count += 1
        else:
            break
    return count


def build_status_change(code: str, current_status: dict[str, Any], latest_date: str | None) -> str:
    cur = str(current_status.get("main_status") or "").strip()
    if not cur:
        return ""
    prev = get_previous_stock_state(code, latest_date)
    if not prev:
        return "new"
    prev_status = str(prev.get("main_status") or "").strip()
    if not prev_status or prev_status in {"0", "None", "null"}:
        return "new"
    if prev_status != cur:
        return f"status changed: {prev_status} -> {cur}"
    consec = count_consecutive_status(code, cur)
    return f"status stable: {cur} {consec}d"

def _market_proxy_state(code: str) -> dict[str, Any] | None:
    # Infer local proxy market state from historical K-line data.
    rows = history_rows_asc(code, 80)
    if len(rows) < 25:
        return None
    latest_date = normalize_date(rows[-1].get('date'))
    expected_date = recent_market_date_for_eod()
    is_fresh = bool(latest_date and latest_date >= expected_date)
    closes = [parse_num(r.get('close')) for r in rows if parse_num(r.get('close')) is not None]
    if len(closes) < 25:
        return None
    close = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else None
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    day_pct = ((close - prev) / prev * 100) if (is_fresh and prev) else None
    if close > ma20 and (ma60 is None or close >= ma60 * 0.98):
        state = 'bullish'
    elif ma60 is not None and close < ma60 * 0.97:
        state = 'bearish'
    elif close < ma20 * 0.99:
        state = 'weak'
    else:
        state = 'neutral'
    return {'code': code, 'state': state, 'close': close, 'ma20': ma20, 'ma60': ma60, 'day_pct': day_pct, 'latest_date': latest_date, 'expected_date': expected_date, 'fresh': is_fresh}


def classify_market_environment() -> dict[str, Any]:
    # First-stage broad-market proxy and divergence check.
    p0050 = _market_proxy_state('0050')
    p2330 = _market_proxy_state('2330')

    # Broad-market divergence: ETF proxy and 2330 proxy disagree.
    if p0050 and p2330:
        s1, s2 = p0050['state'], p2330['state']
        opposite = (s1 in {'bullish', 'neutral'} and s2 in {'weak', 'bearish'}) or (s1 in {'weak', 'bearish'} and s2 == 'bullish')
        d1, d2 = p0050.get('day_pct'), p2330.get('day_pct')
        wide_gap = bool(d1 is not None and d2 is not None and abs(d1 - d2) >= 1.5 and (d1 * d2 < 0))
        if opposite or wide_gap:
            return {
                'market_state': 'divergent',
                'market_label': f"指數分化：0050 {s1}、2330 {s2}，需分開判斷個股",
                'status_modifier': '保守觀察',
                'status_badge': 'warning',
            }

    for proxy, label in [(p0050, '0050 ETF proxy'), (p2330, '2330權值電子proxy')]:
        if not proxy:
            continue
        state = proxy['state']
        if state == 'bullish':
            return {'market_state': 'bullish', 'market_label': f'{label}偏多，僅作外部環境參考', 'status_modifier': None, 'status_badge': 'positive'}
        if state == 'bearish':
            return {'market_state': 'bearish', 'market_label': f'{label}偏空，系統風險升高', 'status_modifier': '警戒加重', 'status_badge': 'warning_heavy'}
        if state == 'weak':
            return {'market_state': 'weak', 'market_label': f'{label}偏弱，降低追價意願', 'status_modifier': '保守觀察', 'status_badge': 'warning'}
        return {'market_state': 'neutral', 'market_label': f'{label}中性，不加不扣', 'status_modifier': None, 'status_badge': 'neutral'}

    return {'market_state': 'no_data', 'market_label': '大盤proxy待確認', 'status_modifier': None, 'status_badge': 'neutral'}


def apply_external_adjustment(practical: dict[str, Any], stock_type: str = 'normal') -> dict[str, Any]:
    ext: list[str] = []
    env = classify_market_environment()
    state = env.get('market_state')
    final_status = practical.get('main_status')
    badge = practical.get('status_badge')
    modifier = None

    if state in {'bearish', 'weak', 'divergent'}:
        ext.append(env.get('market_label'))
        if final_status == '警戒':
            modifier = '警戒加重' if state == 'bearish' else '警戒，留意市場走弱'
            badge = 'warning_heavy' if state in {'bearish', 'weak'} else 'warning'
        elif final_status == '可觀察':
            modifier = '保守觀察'
            badge = 'warning'
        elif final_status == '偏多但不追價' and state in {'bearish', 'weak', 'divergent'}:
            modifier = '偏多但外部逆風'
            badge = 'warning'
    elif state == 'bullish':
        ext.append(env.get('market_label'))

    return {'final_status': final_status, 'status_modifier': modifier, 'status_badge': badge, 'external_notes': [x for x in ext if x], 'market_env': env}





def build_action_hint(
    main_status: str,
    support_pos: dict[str, Any],
    resistance_pos: dict[str, Any],
    status_change: str,
    practical: dict[str, Any],
    external: dict[str, Any] | None = None,
    sr_detail: dict[str, Any] | None = None,
    price: float | None = None,
    chip_reason: str | None = None,
    simple_sr: dict[str, Any] | None = None,
    sr_periods: list[dict[str, Any]] | None = None,
) -> str:
    support_zone = (sr_detail or {}).get("support") or {}
    resistance_zone = (sr_detail or {}).get("resistance") or {}
    support_text = _zone_bounds_text(support_zone)
    resistance_text = _zone_bounds_text(resistance_zone)
    next_support = _next_lower_support_text(sr_detail, support_zone, price)
    price_text = fmt(price) if price is not None else "現價待更新"
    support_low, support_high = _zone_low_high(support_zone)
    res_low, _ = _zone_low_high(resistance_zone)

    def _clean_level_text(value: Any) -> str:
        text = str(value or "").strip()
        return text if text and text != "None" else "--"

    def _period_level(kind: str) -> str:
        rows = sr_periods or []
        for row in reversed(rows):
            display = row.get("support_display") if kind == "support" else row.get("resistance_display")
            label = row.get("label") or (f"{row.get('period')}日" if row.get("period") else "主要")
            text = _clean_level_text(display)
            if text not in {"--", "支撐 --", "賣壓 --"}:
                return f"{label} {text}"
        fallback = support_text if kind == "support" else resistance_text
        prefix = "綜合支撐區" if kind == "support" else "綜合賣壓區"
        return f"{prefix} {_clean_level_text(fallback)}"

    today_support = _clean_level_text((simple_sr or {}).get("today_support"))
    support_5d = _clean_level_text((simple_sr or {}).get("support_5d"))
    today_resistance = _clean_level_text((simple_sr or {}).get("today_resistance"))
    resistance_5d = _clean_level_text((simple_sr or {}).get("resistance_5d"))
    remote_support = _period_level("support")
    remote_resistance = _period_level("resistance")
    near_support = _clean_level_text(format_zone_display("支撐", support_zone, support_pos)) if support_zone else "--"
    near_resistance = _clean_level_text(format_zone_display("賣壓", resistance_zone, resistance_pos)) if resistance_zone else "--"

    support_break = today_support if today_support != "--" else (fmt(support_low) if support_low is not None else support_text)
    resistance_break = today_resistance if today_resistance != "--" else (fmt(res_low) if res_low is not None else resistance_text)

    position_notes: list[str] = [
        f"現價 {price_text}。",
        f"今日低點參考：{today_support}；5日區間低點：{support_5d}；近端支撐區：{near_support}。",
        f"今日高點參考：{today_resistance}；5日區間高點：{resistance_5d}；近端賣壓區：{near_resistance}。",
    ]
    if remote_support != "--" or remote_resistance != "--":
        position_notes.append(f"遠端支撐參考：{remote_support}；遠端突破觀察位：{remote_resistance}。")
    if support_zone:
        if support_pos.get("state") == "broken":
            position_notes.append(f"已跌破今日低點參考 {support_break}，短線支撐轉弱，下一層先看 {next_support}。")
        else:
            position_notes.append(f"跌破今日低點參考 {support_break} 代表短線支撐轉弱；若再跌破5日區間低點 {support_5d}，需回頭看近端支撐區。")
    else:
        position_notes.append("下方尚無可靠共振支撐，需等待新K線確認。")
    if resistance_zone:
        if resistance_pos.get("state") == "broken_up":
            position_notes.append(f"已站回今日高點參考 {resistance_break} 上方，短線賣壓轉弱，後續目標需用新高或 ATR 重新估算。")
        else:
            position_notes.append(f"站回今日高點參考 {resistance_break} 代表短線賣壓轉弱；若能站上近端賣壓區，壓力才算進一步解除。")
    else:
        position_notes.append("上方暫無可靠共振賣壓，延伸時改看短均線與 ATR。")

    if chip_reason:
        position_notes.append(f"籌碼/量價：{chip_reason}。")

    def with_external_hint(text: str) -> str:
        mod = (external or {}).get('status_modifier')
        if mod == '保守觀察':
            return text + ' 大盤或外部環境偏弱，降低追價意願，等市場回穩後再評估。'
        if mod in {'警戒加重', '警戒，留意市場走弱'}:
            return text + ' 外部環境走弱，需提高風險控管，避免在弱勢環境中追價。'
        if mod == '偏多但外部逆風':
            return text + ' 外部環境偏逆風，即使個股偏多也不宜追價。'
        return text
    base = "".join(position_notes)
    if practical.get('ex_tags'):
        return base + '近期有除權息影響，MA20、RSI、前低與停損距離需保守解讀。'
    if main_status == '警戒' and support_pos.get('state') == 'broken':
        return with_external_hint(base + '結論：支撐已破，未重新站回支撐區前不視為買點。')
    if main_status == '警戒' and support_pos.get('state') == 'inside':
        return with_external_hint(base + '結論：先看支撐是否守住，量價轉強才有觀察價值。')
    if main_status == '偏多但不追價':
        if practical.get('chip_components_current'):
            chip_followup = '法人籌碼若呈現分歧，代表有人承接但也有人調節，後續需觀察能否站回主要賣壓之上。'
        else:
            chip_followup = '法人／融資未與K線同步，補齊前只保留價格與技術結論，不以舊籌碼確認當日方向。'
        return with_external_hint(base + '結論：目前技術結構仍偏多，但現價接近上方賣壓或風險報酬偏低，因此不適合追價。' + chip_followup)
    if main_status == '禁止':
        reasons_txt = '｜'.join([str(x) for x in (practical.get('main_reasons') or [])])
        if support_pos.get('state') == 'broken' or '跌破支撐' in reasons_txt or '放量跌破支撐' in reasons_txt:
            return base + '結論：放量或有效跌破支撐，短線不進場。'
        if '停損距離' in reasons_txt or '回檔空間過大' in reasons_txt or '離支撐還有' in reasons_txt:
            return base + '結論：回檔空間過大，等價格靠近支撐或重新形成較近防守點。'
        return base + '結論：已觸發風險條件，不列為買進觀察。'
    if main_status in {'可觀察', '偏多但不追價'} and resistance_pos.get('state') == 'no_data':
        return base + '結論：可追蹤，但沒有上方共振目標時要用短均線或 ATR 管控。'
    if main_status == '可觀察':
        return with_external_hint(base + '結論：條件可追蹤，優先等接近支撐或放量站上賣壓。')
    if main_status == '中性':
        return with_external_hint(base + '結論：目前多空沒有明確優勢，區間內以支撐/賣壓反應為主。')
    return with_external_hint(base + '結論：暫不強判買賣，先依上述價位控管。')


def _chip_component_gate(states: dict[str, Any]) -> dict[str, Any]:
    """Describe whether persisted chip components may support current-day text."""

    latest_k_date = normalize_date(states.get('latest_k_date'))
    inst_quality = states.get('institution_freshness') or assess_component_freshness(
        states.get('institution_date'), latest_k_date, max_lag_days=3
    )
    margin_quality = states.get('margin_freshness') or assess_component_freshness(
        states.get('margin_date'), latest_k_date, max_lag_days=3
    )
    entries = (
        ('法人', int(states.get('inst_count') or 0), inst_quality),
        ('融資', int(states.get('margin_count') or 0), margin_quality),
    )
    notices: list[str] = []
    has_delayed = False
    has_missing = False
    for label, count, quality in entries:
        status = quality.get('status')
        source_date = quality.get('source_date')
        as_of_date = quality.get('as_of_date') or latest_k_date
        if status == DataQualityStatus.SOURCE_DELAYED.value:
            has_delayed = True
            notices.append(f"{label}資料延遲（{source_date or '無日期'}；K線 {as_of_date or '無日期'}）")
        elif status == DataQualityStatus.MISSING.value:
            has_missing = True
            notices.append(f"{label}資料缺漏（來源 {source_date or '無日期'}；K線 {as_of_date or '無日期'}）")
        elif count < 3:
            notices.append(f"{label}樣本不足（{count}筆；來源 {source_date or '無日期'}）")

    freshness_ready = bool(inst_quality.get('ready') and margin_quality.get('ready'))
    interpretation_ready = bool(
        freshness_ready
        and int(states.get('inst_count') or 0) >= 3
        and int(states.get('margin_count') or 0) >= 3
    )
    full_ready = bool(
        freshness_ready
        and int(states.get('inst_count') or 0) >= 20
        and int(states.get('margin_count') or 0) >= 20
    )
    if has_delayed:
        quality_label = '籌碼資料延遲'
    elif has_missing:
        quality_label = '籌碼資料待補'
    elif not full_ready:
        quality_label = '部分待補'
    else:
        quality_label = '資料完整'
    return {
        'interpretation_ready': interpretation_ready,
        'full_ready': full_ready,
        'quality_label': quality_label,
        'notice': '｜'.join(notices) if notices else '',
        'institution_freshness': inst_quality,
        'margin_freshness': margin_quality,
    }


def _chip_list_display(
    gate: dict[str, Any],
    score_result: dict[str, Any] | None,
    light: str,
    light_reason: str,
) -> dict[str, str]:
    """Fail closed for list fields without changing the technical verdict."""

    if gate.get('interpretation_ready'):
        return {
            'light': light,
            'light_reason': light_reason,
            'action_reason': light_reason,
            'risk_summary': light_reason,
        }
    notice = str(gate.get('notice') or '籌碼資料不足，不納入當日判斷')
    technical_veto = bool(score_result and score_result.get('veto'))
    return {
        'light': light if technical_veto else '警戒',
        'light_reason': light_reason if technical_veto else notice,
        'action_reason': notice,
        'risk_summary': '｜'.join(x for x in [light_reason if technical_veto else None, notice] if x),
    }


def _build_row_uncached(
    item: dict[str, str],
    mode: str,
    *,
    persist_state: bool = True,
    source_type: str = "unknown",
) -> dict[str, Any]:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    # TODO(taiwan50-close-source, 2026-06-19):
    # taiwan50_batch should prefer official close data and must not depend on TWSE MIS intraday price.
    # TODO(watchlist-realtime-source, 2026-06-19):
    # watchlist_realtime may use intraday price, but abnormal realtime values should not pollute technical indicators or status calculations.
    code = item["code"]
    name = _clean_detail_name(item.get("name"))
    if not name:
        resolved_item = find_stock_item(code)
        if resolved_item:
            name = _clean_detail_name(resolved_item.get("name"))
    source_type = str(source_type or "unknown")
    quote = None
    realtime_source_types = {"watchlist_realtime", "detail_realtime"}
    batch_source_types = {"taiwan50_batch", "detail_batch"}
    watchlist_intraday = bool(mode == "watchlist" and source_type in realtime_source_types and market_is_open_now())
    if source_type in realtime_source_types and mode == "watchlist" and watchlist_intraday:
        quote = get_mis_quote_cached(code) or get_mis_quote_latest(code) or get_mis_quote_latest_snapshot(code)
    # latest EOD / history fallback
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn)
        eod = (
            conn.execute(
                "SELECT * FROM eod_price WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, analysis_as_of),
            ).fetchone()
            if analysis_as_of
            else None
        )
        hist = (
            [
                row
                for row in conn.execute(
                    """
                    SELECT * FROM history_price
                    WHERE code=? AND date<=?
                    ORDER BY date DESC
                    LIMIT 260
                    """,
                    (code, analysis_as_of),
                ).fetchall()
                if assess_daily_ohlcv(dict(row))["ready"]
            ]
            if analysis_as_of
            else []
        )
        val = (
            conn.execute(
                "SELECT * FROM valuation WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, analysis_as_of),
            ).fetchone()
            if analysis_as_of
            else None
        )
        rsi_history_coverage = history_date_coverage(conn, code, required_days=120)
        rsi_split_events = load_rsi_split_adjustments(conn, code)
    try:
        canonical_daily = build_canonical_close_batch_snapshot(
            code,
            trade_date=analysis_as_of,
            include_levels=source_type in {"detail_batch", "detail_realtime"},
            analysis_mode="close_batch",
            allow_live_quote_fetch=False,
            # The price/technical basis remains the latest sealed close, while
            # the current Web request must see corporate-action facts already
            # available to the shared LINE path at request time.
            analysis_cutoff=now_tpe().isoformat(timespec="seconds"),
        )
    except Exception:
        logging.exception("canonical close-batch snapshot failed for %s", code)
        canonical_daily = {
            "trade_date": analysis_as_of,
            "analysis_contract_version": "canonical-close-batch-analysis-v1",
            "analysis_status": {
                "status": "insufficient_data",
                "complete": False,
                "decision_ready": False,
                "reason_code": "canonical_snapshot_unavailable",
            },
            "referee": {
                "decision_ready": False,
                "main_status": "資料不足",
                "main_reasons": ["canonical 盤後裁判資料尚未建立"],
                "reason_code": "canonical_snapshot_unavailable",
                "source": "canonical_close_batch_snapshot",
                "can_be_overridden_by_model": False,
            },
        }
    states = component_states(code, eod, hist)
    chip_gate = _chip_component_gate(states)
    bootstrap_readiness = diagnose_watchlist_bootstrap_need(code) if mode == "watchlist" else None
    bootstrap_needed = bool(bootstrap_readiness and not bootstrap_readiness.get("ready"))
    bootstrap_status = (
        "already_ready"
        if bootstrap_readiness and bootstrap_readiness.get("ready")
        else ("manual_required" if bootstrap_readiness else "not_applicable")
    )
    hist_is_newer = bool(hist and (not eod or str(hist[0]["date"] or "") >= str(eod["date"] or "")))
    history_price = ((hist[0]["close"] if hist_is_newer else None) or (eod["close"] if eod else None) or (hist[0]["close"] if hist else None))
    history_source = ((hist[0]["source"] if hist_is_newer else None) or (eod["source"] if eod else None) or (hist[0]["source"] if hist else None) or "快取/歷史")
    raw_quote_price = quote.get("price") if quote and quote.get("price") is not None else None
    quote_fresh = bool(_is_intraday_quote_fresh(quote)) if watchlist_intraday else bool(quote)
    realtime_price_usable = bool(watchlist_intraday and quote_fresh and is_realtime_price_usable(raw_quote_price, history_price))
    quote_price = raw_quote_price if realtime_price_usable else None
    if watchlist_intraday:
        price = quote_price if quote_price is not None else history_price
    elif source_type in batch_source_types:
        price = history_price
    else:
        price = history_price
    if quote_price is not None and watchlist_intraday:
        row_price_source = (quote.get("quote_source") or quote.get("source") or "TWSE MIS") if quote else "TWSE MIS"
        row_quality = DataQualityStatus.OK.value
        technical_source = "intraday_estimated"
    elif history_price is not None:
        row_price_source = history_source
        row_quality = DataQualityStatus.SOURCE_DELAYED.value if watchlist_intraday and raw_quote_price is not None else DataQualityStatus.OK.value
        technical_source = "history_price"
    else:
        row_price_source = "unknown"
        row_quality = DataQualityStatus.MISSING.value
        technical_source = "unknown"
    row_meta = {
        "source_type": source_type,
        "price_source": row_price_source,
        "technical_source": technical_source,
        "data_quality": row_quality,
        "data_trade_date": (hist[0]["date"] if hist else None) or (eod["date"] if eod else None),
        "fetched_at": now_tpe().isoformat(timespec="seconds"),
        "uses_mis_intraday": bool(quote_price is not None and watchlist_intraday),
        "mis_price_usable": bool(realtime_price_usable),
        "mis_quote_fresh": bool(quote_fresh),
        "mis_quote_age_seconds": _quote_trade_time_age_seconds(quote) if watchlist_intraday else None,
        "previous_close_source": history_source if history_price is not None else "unknown",
    }
    rows_asc_for_score = apply_rsi_split_adjustments(
        [dict(r) for r in reversed(hist)],
        rsi_split_events,
    )
    change_pct = quote.get("change_pct") if quote and quote_price is not None and quote.get("change_pct") is not None else None
    eod_is_latest = bool(eod and (not hist or str(eod["date"] or "") >= str(hist[0]["date"] or "")))
    if change_pct is None and (not watchlist_intraday or quote_price is None) and eod_is_latest and eod["change_value"] is not None and eod["close"] and (eod["close"] - eod["change_value"]):
        prev = eod["close"] - eod["change_value"]
        change_pct = eod["change_value"] / prev * 100
    if change_pct is None and (not watchlist_intraday or quote_price is None) and hist_is_newer and len(rows_asc_for_score) >= 2:
        cur_row = rows_asc_for_score[-1]
        prev_row = rows_asc_for_score[-2]
        cur = parse_num(cur_row.get("technical_close") or cur_row.get("rsi_close") or cur_row.get("close"))
        prev = parse_num(prev_row.get("technical_close") or prev_row.get("rsi_close") or prev_row.get("close"))
        if cur is not None and prev not in (None, 0):
            change_pct = (cur - prev) / prev * 100
    closes = [
        float(r["rsi_close"])
        for r in reversed(rows_asc_for_score)
        if r.get("rsi_close") is not None
    ]
    intraday_estimated = bool(watchlist_intraday and quote_price is not None)
    if intraday_estimated:
        if hist and str(hist[0]["date"] or "") == today_iso():
            closes = list(closes)
            closes[0] = float(quote_price)
        else:
            closes = [float(quote_price)] + closes
    rsi5 = calc_rsi(closes, 5)
    rsi10 = calc_rsi(closes, 10)
    rsi14 = calc_rsi(closes, 14)
    if not rsi_history_coverage.get("ready"):
        rsi5 = None
        rsi10 = None
        rsi14 = None
    rows_asc_completed_for_display = rows_asc_for_score
    if watchlist_intraday:
        rows_asc_completed_for_display = [
            dict(r) for r in rows_asc_for_score
            if str(r.get("date") or "") != today_iso()
        ]
    score_cache_salt = None
    if intraday_estimated and rows_asc_for_score:
        base = dict(rows_asc_for_score[-1])
        prev_close = parse_num(base.get("close"))
        if str(base.get("date") or "") == today_iso():
            virtual = base
        else:
            virtual = dict(base)
            virtual["date"] = today_iso()
            virtual["open"] = prev_close if prev_close is not None else quote_price
            virtual["volume"] = quote.get("cumulative_volume") if quote and quote.get("cumulative_volume") is not None else base.get("volume")
        virtual["close"] = quote_price
        virtual["rsi_close"] = quote_price
        virtual["high"] = max([x for x in [parse_num(virtual.get("high")), prev_close, quote_price] if x is not None])
        virtual["low"] = min([x for x in [parse_num(virtual.get("low")), prev_close, quote_price] if x is not None])
        virtual["technical_open"] = virtual.get("open")
        virtual["technical_high"] = virtual.get("high")
        virtual["technical_low"] = virtual.get("low")
        virtual["technical_close"] = quote_price
        if str(base.get("date") or "") == today_iso():
            rows_asc_for_score[-1] = virtual
        else:
            rows_asc_for_score.append(virtual)
        score_cache_salt = f"mis:{quote.get('trade_time') or ''}:{quote.get('fetched_at') or ''}:{quote_price}"
    # Legacy scoring is not executed on publishable Web rows.  The shared
    # close-batch referee below is the only source of a user-facing verdict.
    score_result = None
    chip_costs = calculate_public_chip_costs(code, as_of_date=analysis_as_of)
    chip_foreign = chip_costs.get("foreign_cost_estimate", {})
    chip_trust = chip_costs.get("trust_buy_cost_estimate", {})
    chip_poc60 = chip_costs.get("poc60_estimate", {})
    supp, resist, sr_detail = "--", "--", {}
    # Legacy day/5-day lows and highs were previously exposed as another
    # "support/resistance" algorithm.  Keep the canonical referee zones as the
    # only publishable support/resistance values across Web, Bot and LINE.
    simple_sr: dict[str, Any] = {}
    light = "muted"
    light_reason = "等待 canonical 盤後裁判"
    latest_date = (hist[0]["date"] if hist_is_newer else None) or (eod["date"] if eod else None) or (hist[0]["date"] if hist else None)
    # Normal lists use readiness gates; debug/force paths may still show missing values.
    basis = basis_price(price, eod, hist)
    market_cost_basis = build_market_cost_basis(
        hist,
        eod,
        quote_price=quote_price,
        use_intraday_quote=bool(watchlist_intraday and quote_fresh),
    )
    inst_cost_txt = "｜".join(x for x in [
        f"外 {chip_indicator_display(chip_foreign)}" if chip_indicator_display(chip_foreign) else "",
        f"投 {chip_indicator_display(chip_trust)}" if chip_indicator_display(chip_trust) else "",
    ] if x)
    inst_cost_title = "｜".join(
        str(item.get("note") or "") for item in (chip_foreign, chip_trust) if item
    )
    wave_cost_txt = ""
    wave_cost_title = "舊版波段成本已停用；所有正式介面統一使用近期增量部位均價推估。"
    margin_add_est_txt = ""
    margin_add_est_title = "融資成本單位與放款金額尚未完成官方驗證，正式介面不提供推估值。"
    risk_summary = "等待 canonical 盤後裁判"
    price_source = (quote.get("quote_source") or quote.get("source")) if quote and quote_price is not None else row_meta["price_source"]
    source = price_source or ("等待MIS" if watchlist_intraday else history_source)
    data_txt = f"{states['text']}｜{source}"
    corp_ok = None
    try:
        st = get_status().get('corporate_actions')
        corp_ok = bool(st and st.get('status') == 'fresh')
    except Exception:
        corp_ok = None
    display_context: dict[str, Any] = {}
    canonical_ohlcv = dict(canonical_daily.get("ohlcv") or {})
    canonical_price = parse_num(canonical_ohlcv.get("close"))
    canonical_trading_status = str((canonical_daily.get("trading_state") or {}).get("status") or "")
    formal_price = (
        None
        if canonical_trading_status in {
            "no_trade",
            "trading_halt",
            "no_regular_lot_ohlcv",
            "no_ohlcv_residual_activity",
        }
        else canonical_price if canonical_price is not None else price
    )
    practical = _canonicalize_web_practical_status(
        display_context,
        canonical_daily,
        formal_price,
    )
    canonical_status = str(practical.get("main_status") or "資料不足")
    light = canonical_status
    light_reason = "｜".join(practical.get("main_reasons") or []) or canonical_status
    risk_summary = light_reason
    sr_detail = _canonical_support_resistance_detail(canonical_daily)
    price_volume = _canonical_price_volume_for_web(canonical_daily)
    canonical_background_notes = [
        str((canonical_daily.get(key) or {}).get("note") or "").strip()
        for key in (
            "global_market_context",
            "taifex_night_context",
            "official_event_context",
            "external_event_context",
            "news_radar_context",
        )
    ]
    external = {
        "final_status": practical.get("main_status"),
        "status_badge": practical.get("status_badge"),
        "status_modifier": None,
        "external_notes": [note for note in canonical_background_notes if note],
        "market_env": (canonical_daily.get("global_market_context") or {}).get("stance"),
    }
    status_change = build_status_change(code, practical, latest_date)
    display_signal = "｜".join([external.get("final_status") or practical.get("main_status") or "資料不足"] + list(practical.get("main_reasons") or [])[:2])
    support_display = practical.get('support_display') or supp
    resistance_display = practical.get('resistance_display') or resist
    external_display = "｜".join((external.get("external_notes") or []) + (practical.get("ex_tags") or []))
    action_hint = _canonical_advisory_text(canonical_daily)
    if persist_state:
        try:
            save_stock_state(
                code,
                canonical_daily.get("trade_date") or latest_date,
                practical,
                display_signal,
                formal_price,
                support_display,
                resistance_display,
            )
        except Exception:
            logging.exception('save state failed for %s', code)
    canonical_valuation = _canonical_valuation_for_web(canonical_daily)
    valuation_summary = "｜".join(
        text
        for text in (
            f"PE {fmt(canonical_valuation.get('pe_ratio'))}" if canonical_valuation.get("pe_ratio") is not None else "",
            f"PB {fmt(canonical_valuation.get('pb_ratio'))}" if canonical_valuation.get("pb_ratio") is not None else "",
            f"殖 {fmt(canonical_valuation.get('dividend_yield'))}" if canonical_valuation.get("dividend_yield") is not None else "",
        )
        if text
    )
    chip_momentum_summary = summarize_chip_momentum_for_quote(code) if mode == "watchlist" else None
    return {
        "code": code,
        "name": name or (eod["name"] if eod else ""),
        "price": display_text(
            "當日無成交"
            if (canonical_daily.get("trading_state") or {}).get("status") == "no_trade"
            else fmt(formal_price),
            "補資料中" if bootstrap_needed else "需更新價格",
        ),
        "change_pct": display_text(
            "" if (canonical_daily.get("trading_state") or {}).get("status") == "no_trade" else pct(change_pct),
            "",
        ),
        "quote_source": canonical_ohlcv.get("source") or price_source,
        "price_source": canonical_ohlcv.get("source") or price_source,
        "history_source": history_source,
        "data_date": canonical_daily.get("trade_date") or row_meta["data_trade_date"],
        "quote_time": quote.get("trade_time") if quote else None,
        "quote_fetched_at": quote.get("fetched_at") if quote else None,
        "quote_age_seconds": quote.get("quote_age_seconds") if quote else None,
        "cumulative_volume": quote.get("cumulative_volume") if quote else None,
        "volume_delta_since_last_poll": quote.get("volume_delta_since_last_poll") if quote else None,
        "is_realtime": bool(quote_price is not None and quote and quote.get("is_realtime")),
        "is_estimated_tick_volume": quote.get("is_estimated_tick_volume") if quote else False,
        "intraday_estimated": intraday_estimated,
        "bootstrap_needed": bootstrap_needed,
        "bootstrap_status": bootstrap_status,
        "bootstrap_readiness": bootstrap_readiness,
        "unsupported_reason": None,
        "rsi": neutral_rsi_text(
            ((canonical_daily.get("technical") or {}).get("rsi") or {}).get("rsi5"),
            ((canonical_daily.get("technical") or {}).get("rsi") or {}).get("rsi10"),
        ),
        "rsi5": ((canonical_daily.get("technical") or {}).get("rsi") or {}).get("rsi5"),
        "rsi10": ((canonical_daily.get("technical") or {}).get("rsi") or {}).get("rsi10"),
        "rsi14": ((canonical_daily.get("technical") or {}).get("rsi") or {}).get("rsi14"),
        "rsi_data_quality": rsi_history_coverage,
        "legacy_signal": None,
        "legacy_signal_disabled": True,
        "data_quality": ("資料補齊中" if bootstrap_needed else ("需更新K線" if states["hist_count"] < 30 else chip_gate['quality_label'])),
        "stock_analysis": display_text(display_signal, "資料整理中"),
        "main_status": practical.get('main_status'),
        "status_level": practical.get('status_level'),
        "status_badge": "muted" if bootstrap_needed else (external.get('status_badge') or practical.get('status_badge')),
        "main_reasons": practical.get('main_reasons', []),
        "referee": canonical_daily.get("referee"),
        "advisory": canonical_daily.get("advisory"),
        "recommendation_safety": canonical_daily.get("recommendation_safety") or (canonical_daily.get("referee") or {}).get("recommendation_safety"),
        "trading_state": canonical_daily.get("trading_state"),
        "analysis_status": canonical_daily.get("analysis_status"),
        "analysis_contract_version": canonical_daily.get("analysis_contract_version"),
        "microstructure_status": canonical_daily.get("microstructure_status"),
        "canonical_technical": canonical_daily.get("technical"),
        "canonical_valuation": canonical_daily.get("valuation"),
        "institutional_context": canonical_daily.get("institutional_context"),
        "global_market_context": canonical_daily.get("global_market_context"),
        "taifex_night_context": canonical_daily.get("taifex_night_context"),
        "official_event_context": canonical_daily.get("official_event_context"),
        "external_event_context": canonical_daily.get("external_event_context"),
        "news_radar_context": canonical_daily.get("news_radar_context"),
        "canonical_price_volume": price_volume,
        "decision_audit": canonical_daily.get("decision_audit"),
        "institution_date": practical.get('institution_date'),
        "margin_date": practical.get('margin_date'),
        "chip_date_text": (f"法人 {practical.get('institution_date')}｜融資 {practical.get('margin_date')}" if (practical.get('institution_date') or practical.get('margin_date')) else ""),
        "support_display": "資料補齊後顯示" if bootstrap_needed else display_text(support_display, "支撐 需先更新日K"),
        "resistance_display": "資料補齊後顯示" if bootstrap_needed else display_text(resistance_display, "賣壓 需先更新日K"),
        "today_support": None,
        "today_resistance": None,
        "support_5d": None,
        "resistance_5d": None,
        "legacy_period_support_resistance_disabled": True,
        "external_display": display_text(external_display, ""),
        "external_modifier": external.get("status_modifier"),
        "market_env": external.get("market_env"),
        "status_change": display_text(status_change, ""),
        "price_volume_summary": display_text(price_volume.get("summary_text"), price_volume.get("quality_reason", "")),
        "price_volume_grade": price_volume.get("grade"),
        "price_volume_score": price_volume.get("total_score"),
        "price_volume_quality": price_volume.get("quality"),
        "price_volume_status": price_volume.get("status"),
        "action_hint": "資料補齊中；完成後會顯示觀察建議。" if bootstrap_needed else display_text(action_hint, "請先確認支撐、賣壓與量價是否同步。"),
        "ex_dividend": practical.get('ex_dividend'),
        "signal": "資料補齊中" if bootstrap_needed else display_text(display_signal, "資料整理中"),
        "market_cost_basis": None if bootstrap_needed else market_cost_basis,
        "inst_cost": "" if bootstrap_needed else inst_cost_txt,
        "foreign_cost": "" if bootstrap_needed else chip_indicator_display(chip_foreign),
        "trust_cost": "" if bootstrap_needed else chip_indicator_display(chip_trust),
        "margin_cost": "" if bootstrap_needed else (margin_add_est_txt or ""),  # backward compatible
        "margin_add_est": "" if bootstrap_needed else (margin_add_est_txt or ""),
        "main_force_cost": "" if bootstrap_needed else chip_indicator_display(chip_poc60),  # deprecated alias
        "price_volume_reference": "" if bootstrap_needed else chip_indicator_display(chip_poc60),
        "chip_cost_indicators": chip_costs,
        "foreign_cost_estimate": chip_foreign,
        "trust_buy_cost_estimate": chip_trust,
        "poc60_estimate": chip_poc60,
        "wave_cost": wave_cost_txt,
        "risk_summary": risk_summary,
        "sr": "資料補齊後顯示" if bootstrap_needed else f"{display_text(support_display, '支撐 需先更新日K')}｜{display_text(resistance_display, '賣壓 需先更新日K')}",
        "chip_light": light,
        "data_status": data_txt,
        "_needs_repair": bool(states["hist_count"] < 30 or not chip_gate['full_ready']),
        "_repair_reason": (
            f"K{states['hist_count']}@{states.get('latest_k_date') or 'missing'}/"
            f"法人{states['inst_count']}@{states.get('institution_date') or 'missing'}/"
            f"融資{states['margin_count']}@{states.get('margin_date') or 'missing'}"
        ),
        "pepb": valuation_summary,
        "chip_momentum_summary": chip_momentum_summary,
        "inst_cost_title": inst_cost_title,
        "wave_cost_title": wave_cost_title,
        "margin_cost_title": margin_add_est_title,  # backward compatible
        "margin_add_est_title": margin_add_est_title,
        "main_force_title": chip_poc60.get("note") or "成交密集價參考區",
        "price_volume_reference_title": chip_poc60.get("note") or "成交密集價參考區",
        "foreign_cost_title": chip_foreign.get("note") or "外資近期增量部位均價推估不可用",
        "trust_cost_title": chip_trust.get("note") or "投信近期增量部位均價推估不可用",
        "chip_cost_disclaimer": "",
        "chip_cost_input_issues": chip_costs.get("input_issues", []),
        "sr_title": sr_detail.get('method','') + '｜支撐來源：' + ('、'.join(sr_detail.get('support',{}).get('sources',[])) if sr_detail.get('support') else '無') + '｜賣壓來源：' + ('、'.join(sr_detail.get('resistance',{}).get('sources',[])) if sr_detail.get('resistance') else '無') + '｜主價為區間代表值；詳細保留 raw_levels。',
    }


def build_row(
    item: dict[str, str],
    mode: str,
    *,
    persist_state: bool = True,
    source_type: str = "unknown",
) -> dict[str, Any]:
    code = item["code"]
    ttl = ROW_CACHE_TTL_WATCH_SECONDS if mode == "watchlist" else ROW_CACHE_TTL_TW50_SECONDS
    source_type = str(source_type or "unknown")
    try:
        with closing(db()) as conn:
            publication_date = latest_published_full_market_date(conn)
    except Exception:
        publication_date = None
    key = (
        f"{mode}:{source_type}:{code}:"
        f"{'persist' if persist_state else 'readonly'}:{publication_date or 'unpublished'}"
    )
    now = time.time()
    with _row_cache_lock:
        cached = _row_cache.get(key)
        if cached and now - cached[0] < ttl:
            return sanitize_display_payload(dict(cached[1]))
    row = sanitize_display_payload(_build_row_uncached(item, mode, persist_state=persist_state, source_type=source_type))
    with _row_cache_lock:
        _row_cache[key] = (time.time(), dict(row))
        prune_timed_cache(_row_cache, max(ROW_CACHE_TTL_TW50_SECONDS, ROW_CACHE_TTL_WATCH_SECONDS) * 3, ROW_CACHE_MAXSIZE)
    return row


def warm_row_cache(codes: list[str], mode: str) -> None:
    mode = normalize_list_mode(mode)
    items_by_code: dict[str, dict[str, str]] = {}
    if mode == "tw50":
        items_by_code = {str(x.get("code", "")).zfill(4): {"code": str(x.get("code", "")).zfill(4), "name": x.get("name", "")} for x in read_components()}
    else:
        items_by_code = {
            str(r["code"]).zfill(4): {"code": str(r["code"]).zfill(4), "name": r["name"]}
            for r in list_watchlist_code_name_items()
        }
    warmed = 0
    for code in [str(c).zfill(4) for c in codes if str(c).strip()]:
        item = items_by_code.get(code)
        if not item:
            continue
        try:
            build_row(
                item,
                mode,
                source_type=("taiwan50_batch" if mode == "tw50" else "watchlist_realtime"),
            )
            warmed += 1
        except Exception:
            logging.exception("Failed to warm row cache for %s:%s", mode, code)
    if warmed:
        set_status(f"row_cache_{mode}", "fresh", f"{mode} 列表快取已預熱 {warmed} 檔")


# ---------- background tasks ----------
def background_eod_update() -> None:
    global _eod_update_running
    with _update_lock:
        if _eod_update_running:
            return
        _eod_update_running = True
    try:
        set_status("tw50_official_history", "loading", "台灣50官方日線預載中")
        reconcile_target_date: str | None = None
        try:
            eod_date, eod_written = fetch_twse_eod_all()
            if eod_written > 0:
                reconcile_target_date = str(eod_date)
        except Exception as exc:
            logging.exception("TWSE EOD update failed")
            set_status("twse_eod", "stale", f"TWSE OpenAPI 今日資料延遲，使用快取：{safe_error(exc)}")
        try:
            preload = refresh_incomplete_taiwan50_history(read_components())
            if preload.get("ready"):
                if preload.get("target_date"):
                    reconcile_target_date = str(preload.get("target_date"))
                set_status(
                    "tw50_official_history",
                    "fresh",
                    f"台灣50官方日線完整：{preload.get('checked')}/{preload.get('checked')}，日期 {preload.get('target_date')}",
                )
            else:
                set_status(
                    "tw50_official_history",
                    "stale",
                    f"台灣50官方日線仍缺 {len(preload.get('remaining_incomplete') or [])} 檔，日期 {preload.get('target_date')}",
                )
        except Exception as exc:
            logging.exception("Taiwan 50 official history preload failed")
            set_status("tw50_official_history", "stale", f"台灣50官方日線預載失敗：{safe_error(exc)}")
        try:
            fetch_twse_valuation_all()
        except Exception as exc:
            logging.exception("TWSE valuation update failed")
            set_status("twse_valuation", "stale", f"TWSE OpenAPI 估值資料延遲：{safe_error(exc)}")
        try:
            update_corporate_actions()
        except Exception as exc:
            logging.exception("Corporate actions update failed")
            set_status("corporate_actions", "stale", f"除權息資料延遲：{safe_error(exc)}")
        # Phase 2 of price-volume capture: only after official same-day OHLCV is
        # available may a provisional Fugle snapshot become VALIDATED/scorable.
        reconciled = 0
        rejected = 0
        if reconcile_target_date:
            reconcile_codes = sorted({
                *(str(item.get("code") or "").zfill(4) for item in read_components()),
                *(str(code).zfill(4) for code in get_watchlist_codes()),
            })
            for code in reconcile_codes:
                try:
                    result = reconcile_price_volume_profile_for_code(
                        code,
                        reconcile_target_date,
                    )
                    if result.get("status") == "validated":
                        reconciled += 1
                    elif result.get("status") not in {"unavailable", "source_delayed", "awaiting_official_eod"}:
                        rejected += 1
                except Exception:
                    rejected += 1
                    logging.exception("Price-volume EOD reconciliation failed for %s", code)
        if reconciled or rejected:
            set_status(
                "price_volume_reconcile",
                "fresh" if reconciled and not rejected else "stale",
                f"分價量盤後核對：通過 {reconciled} 檔，拒絕 {rejected} 檔",
            )
    finally:
        with _update_lock:
            _eod_update_running = False


def background_chip_update(codes: list[str], days: int = 120, mode: str = "incremental") -> None:
    if mode == "full":
        background_ensure_complete_data(codes, days=days, reason="完整補齊多來源資料")
    else:
        update_finmind_codes(codes, days=days, mode=mode)


def background_price_volume_update(
    codes: list[str],
    session_evidence: dict[str, Any] | None = None,
) -> None:
    global _price_volume_update_running
    clean_codes = [str(c).zfill(4) for c in codes if str(c or "").strip()]
    ok_count = 0
    validated_count = 0
    fail_count = 0
    last_message = ""
    try:
        if not clean_codes:
            return
        evidence = session_evidence or get_current_session_evidence(["0050", "2330"])
        last_evidence_at = time.monotonic()
        if not evidence.get("ok"):
            set_status(
                "price_volume",
                "stale",
                "分價量未更新：" + str(evidence.get("reason") or evidence.get("status") or "無法證明目前為有效交易時段"),
            )
            return
        accepted_evidence = evidence.get("accepted_evidence") or []
        verified_date = str((accepted_evidence[0] if accepted_evidence else {}).get("source_date") or "")
        if not verified_date:
            set_status("price_volume", "stale", "分價量未更新：TWSE MIS 缺少可驗證的交易日期")
            return
        set_status("price_volume", "loading", f"分價量盤中快照開始：0/{len(clean_codes)}")
        for idx, code in enumerate(clean_codes, start=1):
            try:
                # A large Taiwan-50 batch can cross the close. Re-prove the
                # session at least once per minute and stop before another API call.
                if time.monotonic() - last_evidence_at >= 60:
                    evidence = get_current_session_evidence(["0050", "2330"])
                    last_evidence_at = time.monotonic()
                    accepted_evidence = evidence.get("accepted_evidence") or []
                    current_date = str((accepted_evidence[0] if accepted_evidence else {}).get("source_date") or "")
                    if not evidence.get("ok") or current_date != verified_date:
                        remaining = len(clean_codes) - idx + 1
                        fail_count += remaining
                        last_message = "交易時段證據已失效，停止後續 Fugle 呼叫"
                        break
                payload = fetch_fugle_price_volume_network(code)
                if not payload:
                    fetch_status = get_status().get("price_volume") or {}
                    fetch_message = str(fetch_status.get("message") or "").strip()
                    fail_count += 1
                    last_message = fetch_message or f"{code} Fugle 沒有回傳可用資料"
                else:
                    result = capture_fugle_price_volume_snapshot(
                        code,
                        payload,
                        expected_date=verified_date,
                    )
                    if result.get("ok"):
                        reconcile = reconcile_price_volume_profile_for_code(
                            code,
                            verified_date,
                            required_days=30,
                        )
                        ok_count += 1
                        if reconcile.get("status") == "validated":
                            validated_count += 1
                            last_message = (
                                f"{code} 保存 {result.get('price_level_count')} 個真實 Fugle 價位；"
                                "已通過官方盤後量核對"
                            )
                        else:
                            last_message = (
                                f"{code} 保存 {result.get('price_level_count')} 個真實 Fugle 價位；"
                                "待官方盤後成交量核對"
                            )
                    else:
                        fail_count += 1
                        last_message = f"{code} 未寫入：{result.get('quality_reason') or result.get('status')}"
            except Exception as exc:
                fail_count += 1
                last_message = f"{code} 更新失敗：{safe_error(exc)}"
                logging.exception("Fugle price-volume capture failed for %s", code)
            set_status(
                "price_volume",
                "loading",
                f"分價量快照 {idx}/{len(clean_codes)}：成功 {ok_count}｜失敗 {fail_count}｜{last_message}",
            )
        prune_compute_caches()
        status_name = "fresh" if validated_count == len(clean_codes) else "stale"
        set_status(
            "price_volume",
            status_name,
            f"分價量快照完成：保存 {ok_count}/{len(clean_codes)}，已核對 {validated_count}/{len(clean_codes)}，失敗 {fail_count}。{last_message}",
        )
    finally:
        with _update_lock:
            _price_volume_update_running = False


def startup_core() -> None:
    if not AUTO_REFRESH_MARKET_DATA_ON_START:
        if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
            raise RuntimeError(
                "Safe startup requires an existing non-empty database; "
                "automatic database initialization is disabled."
            )
        assert_db_integrity()
        logging.info(
            "Safe startup is read-only; schema migration and market-data refresh are disabled."
        )
        return
    init_db()
    start_mis_quote_daemon()
    # The launcher must see this state before the worker starts, otherwise an
    # already-populated but stale DB can pass readiness during the thread race.
    set_status("tw50_official_history", "loading", "台灣50官方日線預載中")
    if score_stock is None or calculate_indicators is None:
        set_status("scoring_module", "stale", f"技術評分模組載入失敗：{safe_error(_scoring_import_exc)}")
    # migrate old watchlist json if present
    old = DATA_DIR / "watchlist.json"
    if old.exists():
        try:
            raw = json.loads(old.read_text(encoding="utf-8"))
            rows = raw.get("items", raw) if isinstance(raw, dict) else raw
            for i, r in enumerate(rows or []):
                insert_watchlist_if_absent(str(r.get("code", "")).zfill(4), r.get("name", ""), i, time.time())
        except Exception as exc:
            logging.warning("legacy watchlist import failed: %s", safe_error(exc))
    threading.Thread(target=background_eod_update, daemon=True).start()
    # 啟動後優先補自選股，不自動先跑台灣50，避免自選股等待 50 檔更新。
    watch_codes = get_watchlist_codes()
    if watch_codes:
        threading.Thread(target=background_chip_update, args=(watch_codes, FINMIND_INCREMENTAL_DAYS, "incremental"), daemon=True).start()
    if AUTO_UPDATE_TW50_ON_START:
        comps = [r["code"] for r in read_components()]
        threading.Thread(target=background_chip_update, args=(comps, FINMIND_INCREMENTAL_DAYS, "incremental"), daemon=True).start()
    else:
        set_status("tw50_autoupdate", "stale", "台灣50啟動自動更新已關閉；自選股優先。需要台灣50時請切換分頁或按更新按鈕。")


configure_pages_router(ROOT)
configure_config_router(truststore_enabled=TRUSTSTORE_ENABLED, truststore_error=TRUSTSTORE_ERROR)
configure_data_repair_service(
    finmind_token=FINMIND_TOKEN,
    chip_update_is_running=chip_update_is_running,
    update_finmind_codes_func=update_finmind_codes,
    set_status_func=set_status,
    safe_error_func=safe_error,
    prune_compute_caches_func=prune_compute_caches,
    update_corporate_actions_func=update_corporate_actions,
    warm_row_cache_func=warm_row_cache,
)
configure_watchlist_bootstrap_service(background_ensure_complete_data)
configure_watchlist_router(enqueue_watchlist_bootstrap)
app.include_router(pages_router)
app.include_router(config_router)
app.include_router(status_router)
app.include_router(watchlist_router)


def _legacy_api_config_unrouted() -> dict[str, Any]:
    finmind_token_disabled_reason = get_finmind_token_disabled_reason()
    return {
        "fugle_enabled": bool(clean_api_token(os.getenv("FUGLE_API_KEY", ""))),
        "fugle_key_hint": token_decode_hint(os.getenv("FUGLE_API_KEY", "")),
        "truststore_enabled": TRUSTSTORE_ENABLED,
        "truststore_error": TRUSTSTORE_ERROR,
        "finmind_enabled": bool(clean_api_token(os.getenv("FINMIND_TOKEN", ""))) and not bool(finmind_token_disabled_reason),
        "finmind_token_disabled_reason": finmind_token_disabled_reason,
        "env_note": "修改 .env 後請關閉伺服器視窗，再重新執行「啟動台股分析系統.cmd」才會套用到背景任務。",
        "mis_quote_enabled": MIS_AUTO_UPDATE_MARKET_HOURS,
        "mis_poll_seconds": MIS_POLL_SECONDS,
        "mis_cache_ttl_seconds": MIS_CACHE_TTL_SECONDS,
        "mis_source": "TWSE MIS getStockInfo.jsp",
        "fugle_watch_poll_seconds": FUGLE_WATCH_POLL_SECONDS,
        "fugle_quote_timeout_seconds": FUGLE_QUOTE_TIMEOUT_SECONDS,
        "eod_refresh_seconds": EOD_PAGE_REFRESH_SECONDS,
        "finmind_hourly_soft_limit": FINMIND_HOURLY_SOFT_LIMIT,
        "row_cache_ttl_tw50_seconds": ROW_CACHE_TTL_TW50_SECONDS,
        "finmind_incremental_days": FINMIND_INCREMENTAL_DAYS,
        "finmind_batch_size": FINMIND_BATCH_SIZE,
        "finmind_batch_sleep_seconds": FINMIND_BATCH_SLEEP_SECONDS,
        "yahoo_request_sleep_seconds": YAHOO_REQUEST_SLEEP_SECONDS,
        "truststore_enabled": TRUSTSTORE_ENABLED,
        "truststore_error": TRUSTSTORE_ERROR,
        "fugle_auto_update_market_hours": FUGLE_AUTO_UPDATE_MARKET_HOURS,
        "auto_update_tw50_on_start": AUTO_UPDATE_TW50_ON_START,
        "tw_market_session": tw_market_session_now(),
        "db_path": str(DB_PATH),
        "persistent_db": True,
        "update_notes": {
            "twse_close": "TWSE OpenAPI 收盤資料約 13:50 後更新；失敗會使用快取。",
            "institution": "法人資料盤後陸續更新；FinMind/TWSE 資料可能較晚。",
            "margin_lending": "融資融券/借券資料通常晚間或隔日補齊。",
        },
    }


@app.post("/api/analysis")
def api_canonical_analysis(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Web transport for the one shared, write-authorized analysis use case."""

    try:
        if str(payload.get("query") or "").strip():
            return run_canonical_question_analysis(
                query=str(payload.get("query") or ""),
                delivery_channel="web",
                conversation_context=(
                    payload.get("conversation_context")
                    if isinstance(payload.get("conversation_context"), dict)
                    else {}
                ),
                trade_date=str(payload.get("trade_date") or "") or None,
                analysis_cutoff=str(payload.get("analysis_cutoff") or "") or None,
                request_received_at=str(payload.get("request_received_at") or "") or None,
                profile=str(payload.get("profile") or "focused"),
            )
        return run_canonical_analysis(
            code=str(payload.get("code") or ""),
            delivery_channel="web",
            trade_date=str(payload.get("trade_date") or "") or None,
            analysis_cutoff=str(payload.get("analysis_cutoff") or "") or None,
            request_received_at=str(payload.get("request_received_at") or "") or None,
            conversation_context_digest=(
                str(payload.get("conversation_context_digest") or "") or None
            ),
            profile=str(payload.get("profile") or "focused"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/update/eod")
def api_update_eod() -> dict[str, Any]:
    prune_compute_caches()
    threading.Thread(target=background_eod_update, daemon=True).start()
    return {"ok": True, "message": "TWSE OpenAPI 盤後資料背景更新中"}


@app.post("/api/update/chip")
def api_update_chip(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    prune_compute_caches()
    mode = normalize_list_mode(payload.get("mode", "tw50"))
    if mode == "watchlist":
        codes = get_watchlist_codes()
    else:
        codes = [r["code"] for r in read_components()]
    update_mode = "full" if payload.get("update_mode") == "full" else "incremental"
    default_days = 120 if update_mode == "full" else FINMIND_INCREMENTAL_DAYS
    days = int(payload.get("days", default_days))
    threading.Thread(target=background_chip_update, args=(codes, days, update_mode), daemon=True).start()
    label = "多來源完整補齊 120 日" if update_mode == "full" else f"快速更新最近 {days} 天"
    src = "TWSE / FinMind / Yahoo" if update_mode == "full" else "FinMind"
    return {"ok": True, "message": f"{src} {label} 背景執行中：{len(codes)} 檔"}


@app.post("/api/update/ensure-complete")
def api_update_ensure_complete(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    # Run bundled repair path: TWSE + FinMind + Yahoo fallback.
    prune_compute_caches()
    mode = normalize_list_mode(payload.get("mode", "tw50"))
    if mode == "watchlist":
        codes = get_watchlist_codes()
    else:
        codes = [r["code"] for r in read_components()]
    days = int(payload.get("days", 120))
    threading.Thread(target=background_ensure_complete_data, args=(codes, days, "manual ensure complete"), daemon=True).start()
    return {"ok": True, "message": f"TWSE / FinMind / Yahoo 多來源完整補齊背景執行中：{len(codes)} 檔", "codes": len(codes)}


def _tdcc_update_codes(payload: dict[str, Any]) -> list[str]:
    code = str(payload.get("code") or "").strip()
    if code:
        return [code.zfill(4)[:4]]
    mode = normalize_list_mode(payload.get("mode", "watchlist"))
    if mode == "watchlist":
        return get_watchlist_codes()
    return [r["code"] for r in read_components()]


def background_tdcc_equity_update(codes: list[str], days: int, source: str) -> None:
    global _tdcc_equity_update_running
    codes = [str(x).zfill(4)[:4] for x in codes if str(x).strip()]
    set_status("tdcc_equity", "loading", f"TDCC 股權集中度更新中：{len(codes)} 檔")
    inserted = 0
    summaries = 0
    errors: list[str] = []
    try:
        rows_by_source: list[dict[str, Any]] = []
        source_key = (source or "auto").lower()
        if source_key in {"auto", "tdcc"}:
            try:
                tdcc_rows = parse_tdcc_open_data_csv(fetch_tdcc_holding_distribution_csv())
                wanted = set(codes)
                rows_by_source.extend([r for r in tdcc_rows if r.get("code") in wanted])
                if source_key == "tdcc" and not rows_by_source:
                    errors.append("TDCC CSV 解析成功，但指定股票沒有資料。")
            except Exception as exc:
                errors.append("TDCC failed: " + safe_error(exc))
        missing = [c for c in codes if not any(r.get("code") == c for r in rows_by_source)]
        if source_key in {"auto", "finmind"} and missing:
            for code in missing:
                result = load_equity_rows_from_source(code, days=days, source="finmind")
                if result.get("ok"):
                    rows_by_source.extend(result.get("rows") or [])
                else:
                    errors.extend(result.get("errors") or [f"{code} FinMind no usable rows"])
        with closing(db()) as conn:
            result = import_equity_rows_and_summarize(conn, rows_by_source, codes)
            conn.commit()
        inserted = int(result.get("rows") or 0)
        summaries = int(result.get("summaries") or 0)
        prune_compute_caches()
        status = "fresh" if summaries else "stale"
        msg = f"TDCC 股權集中度更新完成：分級 {inserted} 筆，摘要 {summaries} 檔"
        if errors:
            msg += "；部分來源失敗：" + "；".join(errors[:3])
        set_status("tdcc_equity", status, msg)
    except Exception as exc:
        logging.exception("TDCC equity concentration update failed")
        set_status("tdcc_equity", "error", "TDCC 股權集中度更新失敗：" + safe_error(exc))
    finally:
        with _update_lock:
            _tdcc_equity_update_running = False


@app.post("/api/debug/tdcc-equity-source")
def api_debug_tdcc_equity_source(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    code = str(payload.get("code") or "").strip().zfill(4)[:4]
    if not code or not code.isdigit():
        return {"ok": False, "error": "請提供 4 碼股票代號"}
    source = str(payload.get("source") or "auto").strip().lower()
    days = int(payload.get("days", 180))
    result = load_equity_rows_from_source(code, days=days, source=source)
    rows = result.get("rows") or []
    sample = rows[:3]
    return {
        "ok": bool(result.get("ok")),
        "code": code,
        "source": result.get("source"),
        "row_count": len(rows),
        "sample": sample,
        "errors": result.get("errors") or [],
        "debug": result.get("debug") or {},
        "writes_db": False,
    }


@app.post("/api/debug/broker-flow-source")
def api_debug_broker_flow_source(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    code = str(payload.get("code") or "").strip().zfill(4)[:4]
    if not code or not code.isdigit():
        return {
            "ok": False,
            "dataset": BROKER_FLOW_DATASET,
            "source": "FinMind",
            "can_implement_broker_flow_concentration": False,
            "reason": "invalid_code",
            "writes_db": False,
            "next_step": "Provide a 4-digit stock code.",
        }
    source = str(payload.get("source") or "finmind").strip().lower()
    if source != "finmind":
        return {
            "ok": False,
            "dataset": BROKER_FLOW_DATASET,
            "source": source,
            "code": code,
            "can_implement_broker_flow_concentration": False,
            "reason": "source must be finmind",
            "writes_db": False,
            "next_step": "This phase only validates FinMind TaiwanStockTradingDailyReport.",
        }
    days = max(1, min(int(payload.get("days", 5) or 5), 10))
    return _debug_broker_flow_source(code, days)


def _industry_profile_update_codes(payload: dict[str, Any]) -> list[str]:
    code = str(payload.get("code") or "").strip()
    if code:
        return [code.zfill(4)[:4]]
    mode = normalize_list_mode(payload.get("mode", "watchlist"))
    if mode == "watchlist":
        return get_watchlist_codes()
    return [r["code"] for r in read_components()]


@app.post("/api/debug/industry-profile-source")
def api_debug_industry_profile_source(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    source = str(payload.get("source") or "auto").strip().lower()
    mode = str(payload.get("mode") or "").strip().lower()
    if mode == "inspect_duplicate":
        return inspect_industry_duplicate(str(payload.get("code") or ""), source)
    if mode == "inspect_industry_mapping":
        return inspect_industry_mapping(source)
    if mode == "all":
        return debug_industry_profile_all(source)
    code = normalize_industry_profile_code(payload.get("code"))
    if not code:
        return {"ok": False, "error": "請提供 4 碼股票代號", "writes_db": False}
    return debug_industry_profile_source(code, source)


@app.post("/api/update/industry-profile")
def api_update_industry_profile(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return update_industry_profiles_from_official(payload)


@app.post("/api/debug/theme-profile-source")
def api_debug_theme_profile_source(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return debug_theme_profile_source(payload)


@app.post("/api/update/theme-profile")
def api_update_theme_profile(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return update_theme_profiles(payload)


@app.post("/api/update/tdcc-equity-concentration")
def api_update_tdcc_equity_concentration(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    global _tdcc_equity_update_running
    codes = _tdcc_update_codes(payload)
    source = str(payload.get("source") or "auto").strip().lower()
    days = int(payload.get("days", 180))
    if source not in {"auto", "tdcc", "finmind"}:
        return {"ok": False, "message": "source 僅支援 auto / tdcc / finmind"}
    with _update_lock:
        if _tdcc_equity_update_running:
            return {"ok": False, "message": "TDCC 股權集中度背景更新已在執行中"}
        _tdcc_equity_update_running = True
    threading.Thread(target=background_tdcc_equity_update, args=(codes, days, source), daemon=True).start()
    return {"ok": True, "message": "TDCC 股權集中度背景更新中", "codes": len(codes), "source": source}


@app.post("/api/update/price-volume")
def api_update_price_volume(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    global _price_volume_update_running
    mode = normalize_list_mode(payload.get("mode", "watchlist"))
    code = str(payload.get("code") or "").strip()
    if code:
        codes = [code.zfill(4)]
    elif mode == "watchlist":
        codes = get_watchlist_codes()
    else:
        codes = [r["code"] for r in read_components()]
    if not codes:
        return {"ok": False, "status": "empty", "message": "沒有可更新的股票", "codes": 0, "writes_db": False}
    with _update_lock:
        if _price_volume_update_running:
            return {
                "ok": False,
                "status": "already_running",
                "message": "分價量背景更新已在執行中",
                "codes": len(codes),
                "writes_db": False,
            }
    evidence = get_current_session_evidence(["0050", "2330"])
    if not evidence.get("ok"):
        reason = str(evidence.get("reason") or evidence.get("status") or "無法驗證目前交易時段")
        return {
            "ok": False,
            "status": evidence.get("status") or "source_delayed",
            "message": f"分價量未更新：{reason}",
            "codes": len(codes),
            "writes_db": False,
            "market_evidence": evidence,
        }
    with _update_lock:
        if _price_volume_update_running:
            return {
                "ok": False,
                "status": "already_running",
                "message": "分價量背景更新已在執行中",
                "codes": len(codes),
                "writes_db": False,
            }
        _price_volume_update_running = True
    try:
        threading.Thread(
            target=background_price_volume_update,
            args=(codes, evidence),
            daemon=True,
        ).start()
    except Exception:
        with _update_lock:
            _price_volume_update_running = False
        raise
    return {
        "ok": True,
        "status": "started",
        "message": f"Fugle 分價量背景更新中：{len(codes)} 檔",
        "codes": len(codes),
        "market_evidence": evidence,
    }


@app.get("/api/debug/price-volume/{code}")
def api_debug_price_volume(code: str) -> dict[str, Any]:
    code = str(code).zfill(4)
    diag = price_volume_source_diagnostics(code)
    with closing(db()) as conn:
        score_row = conn.execute(
            "SELECT * FROM price_volume_score_daily WHERE code=? ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
    if score_row:
        score = dict(score_row)
        score_status = {
            "available": True,
            "status": score.get("status"),
            "quality": score.get("quality"),
            "quality_reason": score.get("quality_reason"),
            "coverage_days": score.get("coverage_days"),
            "required_days": score.get("required_days"),
            "source_hint": diag.get("source_hint"),
            "profile_rows": diag.get("profile_rows"),
            "usable_rows": diag.get("usable_rows"),
            "date": score.get("date"),
            "source_name": score.get("source_name"),
            "source_level": score.get("source_level"),
            "total_score": score.get("total_score"),
            "grade": score.get("grade"),
        }
        needs_update = False
        reason = None
    else:
        score_status = {
            "available": False,
            "status": "not_computed",
            "quality": "missing",
            "quality_reason": "No persisted price-volume score row. This read-only debug endpoint does not compute or write data; use POST /api/update/price-volume.",
            "coverage_days": None,
            "required_days": None,
            "source_hint": diag.get("source_hint"),
            "profile_rows": diag.get("profile_rows"),
            "usable_rows": diag.get("usable_rows"),
        }
        needs_update = True
        reason = "missing persisted price-volume score"
    return {
        "ok": True,
        "code": code,
        "read_only": True,
        "needs_update": needs_update,
        "reason": reason,
        "update_endpoint": "POST /api/update/price-volume",
        "diagnostics": diag,
        "score_status": score_status,
    }


@app.get("/api/debug/volume_units")
def api_debug_volume_units(codes: str = "2330,2449,2317") -> dict[str, Any]:
    # Audit-only endpoint; it never migrates or modifies stored volume.
    code_list = [c.strip().zfill(4) for c in str(codes or "").split(",") if c.strip()] or ["2330", "2449", "2317"]
    with closing(db()) as conn:
        return audit_volume_units(conn, code_list)


@app.get("/api/debug/rsi_check/{code}")
def api_debug_rsi_check(code: str) -> dict[str, Any]:
    # Compare app.py calc_rsi with scoring.py Wilder RSI.
    code = str(code).zfill(4)
    rows_desc = latest_history_dates(code, limit=260)
    rows_asc = list(reversed([dict(r) for r in rows_desc]))
    closes_desc = [r["close"] for r in rows_desc if r.get("close") is not None]
    app_rsi5 = calc_rsi(closes_desc, 5)
    app_rsi10 = calc_rsi(closes_desc, 10)
    scoring_rsi5 = None
    scoring_rsi10 = None
    try:
        from scoring import _wilder_rsi  # type: ignore
        close_series = pd.Series([float(r["close"]) for r in rows_asc if r.get("close") is not None], dtype="float64")
        if len(close_series) >= 11:
            rsi5_s = _wilder_rsi(close_series, 5).dropna()
            rsi10_s = _wilder_rsi(close_series, 10).dropna()
            scoring_rsi5 = round(float(rsi5_s.iloc[-1]), 2) if not rsi5_s.empty else None
            scoring_rsi10 = round(float(rsi10_s.iloc[-1]), 2) if not rsi10_s.empty else None
    except Exception as exc:
        logging.exception("RSI debug check failed for %s", code)
        return {"code": code, "ok": False, "error": safe_error(exc)}
    def same(a, b):
        return a is not None and b is not None and abs(float(a) - float(b)) <= 0.01
    return {
        "code": code,
        "rows": len(rows_asc),
        "app": {"rsi5": app_rsi5, "rsi10": app_rsi10},
        "scoring": {"rsi5": scoring_rsi5, "rsi10": scoring_rsi10},
        "consistent": {"rsi5": same(app_rsi5, scoring_rsi5), "rsi10": same(app_rsi10, scoring_rsi10)},
        "note": "app.py calc_rsi 已共用 scoring.py 的 Wilder RSI；consistent 應保持 true。",
    }


@app.get("/api/debug/cost/{code}")
def api_debug_cost(code: str) -> dict[str, Any]:
    """Expose the same canonical cost contract used by Web, Bot and LINE.

    This route intentionally does not execute any legacy 20-day, wave, margin,
    dealer or synthetic main-force cost formula.  The OHLCV price-volume
    reference remains a separately labelled, non-institutional observation.
    """
    code = str(code).zfill(4)
    snapshot = calculate_public_chip_costs(code)
    return {
        "code": code,
        "trade_date": snapshot.get("trade_date"),
        "contract_version": snapshot.get("contract_version"),
        "formula_version": snapshot.get("formula_version"),
        "costs": snapshot.get("costs"),
        "estimated_costs": snapshot.get("estimated_costs"),
        "price_volume_reference": snapshot.get("poc60_estimate"),
        "main_force_branch_cost": snapshot.get("main_force_branch_cost"),
        "can_override_main_status": False,
    }


@app.get("/api/debug/outlook_shadow/{code}")
def api_debug_outlook_shadow(code: str) -> dict[str, Any]:
    """Return Calendar-B shadow-only outlook context when explicitly enabled."""

    env_name = str(os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    if env_name == "production":
        return {"enabled": False, "reason": "disabled in production"}
    enabled = str(os.getenv("ENABLE_SHADOW_OUTLOOK", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"enabled": False, "reason": "ENABLE_SHADOW_OUTLOOK is false"}
    from outlook.shadow_builder import build_shadow_outlook_context

    return build_shadow_outlook_context(code).to_dict()




DETAIL_LOCAL_READY_THRESHOLD = 120
US_RELATION_MAPPING_LOCATION = "review_src/us_relations.py:US_RELATION_MAP"


def _clean_detail_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "undefined", "nan"}:
        return ""
    return text


def _tw50_code_set() -> set[str]:
    return {str(item.get("code", "")).strip().zfill(4)[:4] for item in read_components() if item.get("code")}


def _resolve_detail_source_context(
    code: str,
    requested_source: str | None,
    *,
    is_watchlist: bool,
    history_rows: int,
    history_coverage_ready: bool,
) -> dict[str, Any]:
    clean = str(code or "").strip().zfill(4)[:4]
    requested = str(requested_source or "").strip().lower()
    tw50_codes = _tw50_code_set()
    is_tw50 = clean in tw50_codes
    local_ready = int(history_rows or 0) >= DETAIL_LOCAL_READY_THRESHOLD and bool(history_coverage_ready)
    tw50_membership_source = "core.components.read_components" if is_tw50 else None
    if requested in {"tw50", "taiwan50", "taiwan_50"}:
        resolved = "tw50" if is_tw50 or local_ready else "missing"
    elif requested in {"watchlist", "watch"}:
        resolved = "watchlist" if is_watchlist else ("local" if local_ready else "missing")
    elif is_tw50:
        resolved = "tw50"
    elif is_watchlist:
        resolved = "watchlist"
    elif local_ready:
        resolved = "local"
    else:
        resolved = "missing"
    detail_mode = "watchlist" if resolved == "watchlist" else "tw50"
    return {
        "requested_source": requested or None,
        "resolved_detail_source": resolved,
        "detail_mode": detail_mode,
        "is_tw50": is_tw50,
        "is_watchlist": is_watchlist,
        "local_detail_ready": local_ready,
        "local_history_rows": int(history_rows or 0),
        "local_ready_threshold": DETAIL_LOCAL_READY_THRESHOLD,
        "tw50_membership_source": tw50_membership_source,
        "writes_db": False,
    }


# ---------- stock detail page / US related market ----------
# 美股關聯表已移到 us_relations.py；沒有明確對應就回空，不使用通用模板。
try:
    from us_relations import related_us_assets_for_code, relation_coverage, US_RELATION_MAP as PRECISE_US_RELATION_MAP
except Exception as _us_rel_exc:
    PRECISE_US_RELATION_MAP = {}
    def related_us_assets_for_code(code: str) -> dict[str, Any]:
        return {
            "category": "未分類",
            "note": "美股關聯表載入失敗：" + safe_error(_us_rel_exc),
            "last_reviewed": None,
            "assets": [],
        }
    def relation_coverage(codes: list[str]) -> dict[str, Any]:
        return {"missing": codes, "error": safe_error(_us_rel_exc)}








def related_us_assets_from_theme_key(key: Any) -> list[dict[str, Any]]:
    text = str(key or "").strip()
    if not text:
        return []

    def us_asset(ticker: str, name: str, typ: str, relevance: str, weight: float, reason: str) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "name": name,
            "market": "US",
            "type": typ,
            "relevance": relevance,
            "weight": float(weight),
            "reason": reason,
            "usage": "僅作海外情緒參考，不可單獨推翻主結論。",
        }

    if any(k in text for k in ["通訊網路", "網通", "軌道衛星", "衛星"]):
        return [
            us_asset("IYZ", "iShares U.S. Telecommunications ETF", "通訊服務ETF", "medium", 0.55, "通訊服務類股情緒參考。"),
            us_asset("XLC", "Communication Services Select Sector SPDR", "通訊服務ETF", "low", 0.35, "大型通訊與媒體平台情緒參考。"),
            us_asset("ARKX", "ARK Space Exploration & Innovation ETF", "航太衛星ETF", "low", 0.3, "衛星與航太題材情緒參考。"),
            us_asset("IRDM", "Iridium Communications", "衛星通訊公司", "low", 0.25, "衛星通訊情緒參考。"),
        ]
    if any(k in text for k in ["半導體", "IC設計", "晶圓", "封測", "先進封裝"]):
        return [
            us_asset("SOXX", "iShares Semiconductor ETF", "半導體ETF", "medium", 0.6, "半導體族群情緒參考。"),
            us_asset("SMH", "VanEck Semiconductor ETF", "半導體ETF", "medium", 0.6, "半導體族群情緒參考。"),
            us_asset("NVDA", "NVIDIA", "AI半導體", "low", 0.3, "AI半導體情緒參考。"),
        ]
    if any(k in text for k in ["AI伺服器", "伺服器ODM", "電子代工"]):
        return [
            us_asset("SMCI", "Super Micro Computer", "AI伺服器", "medium", 0.55, "AI伺服器需求情緒參考。"),
            us_asset("DELL", "Dell Technologies", "伺服器品牌", "low", 0.35, "伺服器與企業硬體需求參考。"),
            us_asset("NVDA", "NVIDIA", "AI平台需求", "low", 0.35, "AI平台出貨情緒參考。"),
        ]
    if any(k in text for k in ["貨櫃航運", "航運"]):
        return [
            us_asset("ZIM", "ZIM Integrated Shipping", "貨櫃航運同業", "medium", 0.65, "全球貨櫃航運同業情緒參考。"),
            us_asset("BOAT", "SonicShares Global Shipping ETF", "全球航運ETF", "low", 0.4, "全球航運族群情緒參考。"),
        ]
    if any(k in text for k in ["航空", "高鐵", "陸運"]):
        return [
            us_asset("JETS", "U.S. Global Jets ETF", "航空ETF", "medium", 0.55, "航空與旅運需求情緒參考。"),
            us_asset("DAL", "Delta Air Lines", "航空公司", "low", 0.3, "航空同業情緒參考。"),
        ]
    if any(k in text for k in ["金融", "銀行", "壽險", "金控"]):
        return [
            us_asset("XLF", "Financial Select Sector SPDR", "金融ETF", "medium", 0.55, "金融類股情緒參考。"),
            us_asset("KBE", "SPDR S&P Bank ETF", "銀行ETF", "low", 0.35, "銀行類股情緒參考。"),
        ]
    return []

def analyze_us_related_direction(us_assets: list[dict[str, Any]]) -> dict[str, Any]:
    # Estimate next-session sentiment from explicitly mapped US proxies only.
    session = us_market_session_now()
    usable: list[dict[str, Any]] = []
    for asset in us_assets or []:
        quote = asset.get("quote") or {}
        if not quote.get("ok"):
            continue
        change_pct = parse_num(quote.get("change_pct"))
        weight = parse_num(asset.get("weight"))
        if change_pct is None or weight is None or weight <= 0:
            continue
        relevance = str(asset.get("relevance") or "").lower()
        if "high" in relevance:
            relevance_mult = 1.15
        elif "low" in relevance:
            relevance_mult = 0.75
        else:
            relevance_mult = 1.0
        usable.append({
            "ticker": asset.get("ticker"),
            "name": asset.get("name"),
            "type": asset.get("type"),
            "change_pct": change_pct,
            "weight": weight * relevance_mult,
            "raw_weight": weight,
        })
    if not usable:
        return {
            "available": False,
            "probability_up": None,
            "probability_down": None,
            "label": "美股關聯報價不足",
            "confidence": "低",
            "market_session": session,
            "reasons": ["目前關聯美股沒有可用漲跌幅，系統不產生推估數字。"],
            "caveat": "缺少來源數據時不以猜測值補齊。",
        }

    total_weight = sum(x["weight"] for x in usable)
    weighted_change = sum(x["change_pct"] * x["weight"] for x in usable) / total_weight
    positive_weight = sum(x["weight"] for x in usable if x["change_pct"] > 0)
    negative_weight = sum(x["weight"] for x in usable if x["change_pct"] < 0)
    positive_ratio = positive_weight / total_weight if total_weight else 0.0
    score = weighted_change * 11.0 + (positive_ratio - 0.5) * 36.0
    if score >= 12:
        label = "偏多"
    elif score <= -12:
        label = "偏空"
    else:
        label = "中性"
    if len(usable) >= 4 and total_weight >= 1.5 and (session["is_open"] or session["is_extended"]) and abs(weighted_change) >= 0.25:
        confidence = "中高"
    elif len(usable) >= 2 and total_weight >= 0.8:
        confidence = "中"
    else:
        confidence = "低"
    contributors = sorted(usable, key=lambda x: abs(x["change_pct"] * x["weight"]), reverse=True)[:3]
    contrib_text = "、".join(f"{x['ticker']} {x['change_pct']:+.2f}%" for x in contributors)
    reasons = [
        f"關聯美股加權漲跌幅 {weighted_change:+.2f}%，正向權重 {positive_ratio * 100:.0f}%。",
        f"主要貢獻標的：{contrib_text}。" if contrib_text else "沒有足夠的主要貢獻標的。",
        f"{session['label']}；海外報價可能延遲，僅作情緒輸入。",
    ]
    return {
        "available": True,
        "probability_up": None,
        "probability_down": None,
        "probability_calibrated": False,
        "calibration_status": "not_walk_forward_calibrated",
        "sentiment_score": round(score, 2),
        "weighted_change_pct": round(weighted_change, 2),
        "positive_weight_ratio": round(positive_ratio * 100, 0),
        "negative_weight_ratio": round(negative_weight / total_weight * 100, 0) if total_weight else None,
        "label": label,
        "confidence": confidence,
        "sample_size": len(usable),
        "market_session": session,
        "top_contributors": contributors,
        "reasons": reasons,
        "caveat": "這是海外關聯行情的定性情緒標籤；尚未完成走勢外校準，不輸出漲跌機率。仍需合併台股籌碼、K 線、支撐賣壓與大盤狀態。",
    }


NEXT_DAY_OUTLOOK_VERSION = "2026.08.27-missing-factor-gate-v3"










def chip_factor_for_stock(code: str, practical: dict[str, Any] | None = None, price: float | None = None) -> dict[str, Any]:
    code = str(code).zfill(4)
    practical = practical or {}
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn)
        if analysis_as_of:
            inst = list(
                conn.execute(
                    "SELECT * FROM institution_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 5",
                    (code, analysis_as_of),
                )
            )
            mar = list(
                conn.execute(
                    "SELECT * FROM margin_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 5",
                    (code, analysis_as_of),
                )
            )
        else:
            inst = []
            mar = []
    if len(inst) < 3 or len(mar) < 3:
        return _factor_payload(
            "個股籌碼",
            0.0,
            False,
            "低",
            "FinMind/TWSE institution + margin",
            None,
            "法人或融資資料少於 3 日，不納入隔日展望",
            decay=0.0,
            freshness="missing",
        )
    foreign3 = sum(float(r["foreign_net"] or 0) for r in inst[:3])
    trust3 = sum(float(r["trust_net"] or 0) for r in inst[:3])
    dealer3 = sum(float(r["dealer_net"] or 0) for r in inst[:3])
    margin5 = sum(float(r["margin_delta"] or 0) for r in mar[:5])
    short5 = sum(float(r["short_delta"] or 0) for r in mar[:5])
    bal = parse_num(mar[0]["margin_balance"]) or 0.0
    short_bal = parse_num(mar[0]["short_balance"]) or 0.0
    margin_pct = (margin5 / bal * 100.0) if bal > 0 else None
    short_pct = (short5 / short_bal * 100.0) if short_bal > 0 else None
    score = 0.0
    reasons: list[str] = []
    if foreign3 > 0 and trust3 > 0:
        score += 15
        reasons.append("外資與投信近3日同步買超")
    elif foreign3 < 0 and trust3 < 0:
        score -= 15
        reasons.append("外資與投信近3日同步賣超")
    else:
        score += 6 if foreign3 > 0 else -6 if foreign3 < 0 else 0
        score += 5 if trust3 > 0 else -5 if trust3 < 0 else 0
        reasons.append("法人近3日方向分歧")
    score += 2 if dealer3 > 0 else -2 if dealer3 < 0 else 0
    latest_change_pct = None
    try:
        with closing(db()) as conn:
            analysis_as_of = resolve_full_market_analysis_date(conn)
            h = (
                list(
                    conn.execute(
                        "SELECT close FROM history_price WHERE code=? AND date<=? ORDER BY date DESC LIMIT 2",
                        (code, analysis_as_of),
                    )
                )
                if analysis_as_of
                else []
            )
        if len(h) >= 2 and h[0]["close"] is not None and h[1]["close"]:
            latest_change_pct = (float(h[0]["close"]) - float(h[1]["close"])) / float(h[1]["close"]) * 100.0
    except Exception:
        latest_change_pct = None
    if margin_pct is None and margin5 > 0:
        score -= 6
        reasons.append("融資餘額缺漏但融資增加，先降權")
    elif margin_pct is not None:
        if margin_pct > 2 and (latest_change_pct or 0) < 0:
            score -= 10
            reasons.append(f"股價走弱但融資增加 {margin_pct:.1f}%")
        elif margin_pct > 5:
            score -= 8
            reasons.append(f"融資近5日增加 {margin_pct:.1f}%")
        elif margin_pct < -2:
            score += 5
            reasons.append(f"融資近5日下降 {abs(margin_pct):.1f}%")
    if short_pct is not None and short_pct > 5:
        score += 6
        reasons.append(f"融券近5日增加 {short_pct:.1f}%，具軋空參考")
    # Do not feed the referee's already-composed conclusion back into the raw
    # chip factor.  Doing so double-counts technical/chip evidence and creates
    # a circular score dependency.
    latest_date = max(str(inst[0]["date"] or ""), str(mar[0]["date"] or ""))
    decay, freshness, _ = _source_age_decay_from_date(latest_date)
    reason = "；".join(reasons[:4]) or "籌碼中性"
    confidence = "高" if len(inst) >= 5 and len(mar) >= 5 and freshness == "fresh" else "中"
    return _factor_payload(
        "個股籌碼",
        _clamp_num(score, -25.0, 25.0),
        True,
        confidence,
        "FinMind/TWSE 三大法人與融資融券",
        latest_date,
        reason,
        decay=decay,
        freshness=freshness,
        raw_score=_clamp_num(score, -25.0, 25.0),
    ) | {
        "foreign3": round(foreign3, 0),
        "trust3": round(trust3, 0),
        "dealer3": round(dealer3, 0),
        "margin5": round(margin5, 0),
        "margin_pct": round(margin_pct, 2) if margin_pct is not None else None,
        "short_pct": round(short_pct, 2) if short_pct is not None else None,
    }


def tech_factor_for_stock(rows_asc: list[dict[str, Any]], practical: dict[str, Any] | None, sr_detail: dict[str, Any] | None) -> dict[str, Any]:
    practical = practical or {}
    sr_detail = sr_detail or {}
    ctx = technical_context_from_rows(rows_asc)
    close = parse_num(ctx.get("close"))
    if close is None:
        return _factor_payload("技術續航濾鏡", 0.0, False, "低", "TWSE/FinMind OHLCV", None, "缺少收盤價", decay=0.0, freshness="missing")
    score = 0.0
    reasons: list[str] = []
    ma20 = parse_num(ctx.get("ma20"))
    ma5 = parse_num(ctx.get("ma5"))
    ma10 = parse_num(ctx.get("ma10"))
    rsi10 = parse_num(ctx.get("rsi10"))
    open_ = parse_num(ctx.get("open"))
    volume = parse_num(ctx.get("volume"))
    vol_ma20 = parse_num(ctx.get("vol_ma20"))
    if ma20:
        if close > ma20:
            score += 6
            reasons.append("收盤站上MA20")
        else:
            score -= 8
            reasons.append("收盤跌破MA20")
    if ma5 and ma10:
        score += 4 if close > ma5 and close > ma10 else -3
    if rsi10 is not None:
        if 50 <= rsi10 <= 68:
            score += 4
        elif rsi10 < 45:
            score -= 5
        elif rsi10 >= 80:
            score -= 4
    volume_ratio = (volume / vol_ma20) if volume and vol_ma20 else None
    if volume_ratio and open_:
        if close > open_ and volume_ratio >= 1.2:
            score += 5
            reasons.append("放量收紅")
        elif close < open_ and volume_ratio >= 1.5:
            score -= 6
            reasons.append("放量收黑")
    support_pos = practical.get("support_pos") or {}
    resistance_pos = practical.get("resistance_pos") or {}
    if support_pos.get("state") == "broken":
        score -= 10
        reasons.append("跌破支撐區")
    if resistance_pos.get("state") in {"inside", "below"} and parse_num(resistance_pos.get("distance_pct")) is not None:
        if float(resistance_pos.get("distance_pct")) <= 2:
            score -= 6
            reasons.append("接近賣壓區")
    latest_date = ctx.get("date")
    decay, freshness, _ = _source_age_decay_from_date(latest_date)
    return _factor_payload(
        "技術續航濾鏡",
        _clamp_num(score, -20.0, 20.0),
        True,
        "中",
        "TWSE/FinMind OHLCV + 支撐賣壓",
        latest_date,
        "；".join(reasons[:4]) or "技術面中性",
        decay=decay,
        freshness=freshness,
        raw_score=_clamp_num(score, -20.0, 20.0),
    )


def synthesize_next_day_outlook(
    code: str,
    us_forecast: dict[str, Any],
    us_assets: list[dict[str, Any]],
    futures_night: dict[str, Any],
    practical: dict[str, Any],
    rows_asc: list[dict[str, Any]],
    sr_detail: dict[str, Any],
    price: float | None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    us_factor = us_sentiment_factor(us_forecast, us_assets)
    night_factor = futures_night_factor(futures_night)
    chip_factor = chip_factor_for_stock(code, practical, price)
    tech_factor = tech_factor_for_stock(rows_asc, practical, sr_detail)
    factors = {"us": us_factor, "night": night_factor, "chip": chip_factor, "tech": tech_factor}
    base_weights = {"us": 0.28, "night": 0.37, "chip": 0.25, "tech": 0.10}
    composition = weighted_available_score(
        factors,
        base_weights,
        minimum_coverage_ratio=0.60,
        minimum_factor_count=2,
        required_groups=(("us", "night"), ("chip", "tech")),
    )
    opening_composition = weighted_available_score(
        factors,
        {"us": 0.43, "night": 0.57},
        minimum_coverage_ratio=0.43,
        minimum_factor_count=1,
    )
    sustainability_composition = weighted_available_score(
        factors,
        {"chip": 0.72, "tech": 0.28},
        minimum_coverage_ratio=0.60,
        minimum_factor_count=1,
    )
    total_score = composition["score"]
    opening_score = opening_composition["score"]
    sustainability_score = sustainability_composition["score"]
    warnings: list[str] = []
    external_composition = weighted_available_score(
        factors,
        {"us": 0.45, "night": 0.55},
        minimum_coverage_ratio=0.45,
        minimum_factor_count=1,
    )
    external_score = external_composition["score"]
    if external_score is not None and total_score is not None and external_score >= 18 and chip_factor["score"] <= -12:
        warnings.append("外部情緒偏多但個股籌碼偏空，嚴防開高走低，不宜追價")
        total_score = min(total_score, 8.0)
    if external_score is not None and external_score <= -18 and chip_factor["score"] >= 12:
        warnings.append("外部情緒偏空但個股籌碼抗跌，開低後需觀察承接")
    if us_factor["score"] >= 14 and night_factor["score"] <= -10:
        warnings.append("美股偏多但台灣夜盤未跟上，本土資金態度分歧")
    if chip_factor.get("foreign3", 0) < 0 and chip_factor.get("trust3", 0) < 0 and (chip_factor.get("margin5") or 0) > 0:
        warnings.append("三大法人偏賣、融資增加，短線籌碼較凌亂，追價容易遇到賣壓")
    if total_score is not None and tech_factor["score"] <= -12 and total_score > 10:
        warnings.append("技術面破位或接近強壓，限制偏多解讀")
        total_score = min(total_score, 10.0)
    signs = [
        1 if f["score"] > 3 else -1 if f["score"] < -3 else 0
        for f in [us_factor, night_factor, chip_factor, tech_factor]
        if factor_is_decision_usable(f)
    ]
    if signs and all(x >= 0 for x in signs) and any(x > 0 for x in signs):
        consistency = "一致偏多"
    elif signs and all(x <= 0 for x in signs) and any(x < 0 for x in signs):
        consistency = "一致偏空"
    elif any(x > 0 for x in signs) and any(x < 0 for x in signs):
        consistency = "分歧"
    else:
        consistency = "中性"
    source_conf = [
        _confidence_value(f.get("confidence"))
        for f in [us_factor, night_factor, chip_factor, tech_factor]
        if factor_is_decision_usable(f)
    ]
    available_count = len(source_conf)
    if not composition["available"]:
        confidence = "低"
        warnings.append("可用因子覆蓋不足，隔日方向暫不判斷")
    elif available_count >= 3 and min(source_conf or [0]) >= 2 and consistency != "分歧" and not warnings:
        confidence = "中高"
    elif available_count >= 2:
        confidence = "中"
    else:
        confidence = "低"
    def score_label(value: float | None) -> str:
        if value is None:
            return "資料不足"
        if value >= 12:
            return "偏多"
        if value <= -12:
            return "偏空"
        return "中性"

    out = {
        "available": bool(composition["available"]),
        "model_version": NEXT_DAY_OUTLOOK_VERSION,
        "probability_up": None,
        "probability_down": None,
        "opening_probability": None,
        "sustainability_probability": None,
        "probability_calibrated": False,
        "calibration_status": "not_walk_forward_calibrated",
        "label": score_label(total_score),
        "opening_label": score_label(opening_score),
        "sustainability_label": score_label(sustainability_score),
        "confidence": confidence,
        "consistency": consistency,
        "total_score": round(total_score, 2) if total_score is not None else None,
        "opening_score": round(opening_score, 2) if opening_score is not None else None,
        "sustainability_score": round(sustainability_score, 2) if sustainability_score is not None else None,
        "weights": composition["effective_weights"],
        "base_weights": base_weights,
        "weight_coverage": composition,
        "opening_weight_coverage": opening_composition,
        "sustainability_weight_coverage": sustainability_composition,
        "factors": factors,
        "warnings": warnings,
        "summary": f"開盤{score_label(opening_score)}｜續航{score_label(sustainability_score)}｜訊號{consistency}",
        "caveat": "隔日展望尚未完成走勢外校準，因此只顯示定性方向，不輸出機率；它不能取代主狀態與風控。",
        "sources": [
            {
                "key": factor_key,
                "name": factors[factor_key].get("name"),
                "source": factors[factor_key].get("source"),
                "date": factors[factor_key].get("date"),
                "freshness": factors[factor_key].get("freshness"),
                "confidence": factors[factor_key].get("confidence"),
                "available": factors[factor_key].get("available"),
            }
            for factor_key in ["us", "night", "chip", "tech"]
        ],
    }
    if persist:
        record_next_day_outlook(code, out)
    return out


def next_day_outlook_gate() -> dict[str, Any]:
    # Gate previous-night outlook visibility by Taiwan market session.
    session = tw_market_session_now()
    state = session.get("session")
    if state == "holiday":
        return {
            "show": True,
            "status": "market_holiday_reference",
            "label": "台股休市，沿用最近完整資料",
            "message": "今日為台灣國定假日或臨時停止交易日，以下內容以最近完整交易日、最新可得海外行情與期貨盤後資料，作為下一個交易日開盤前參考。",
            "next_update_time": "下一個交易日收盤後約 15:30 起",
            "session": session,
        }
    if state == "regular":
        return {
            "show": False,
            "status": "regular_session_expired",
            "label": "台股盤中，隔日展望暫停顯示",
            "message": "台股已開盤，昨夜美股與期貨夜盤只適合解釋開盤，不再作為盤中展望。下一次隔日展望會在收盤資料與期貨盤後資料更新後重新計算。",
            "next_update_time": "收盤後約 15:30 起",
            "session": session,
        }
    if state == "closing_buffer":
        return {
            "show": False,
            "status": "closing_data_pending",
            "label": "收盤資料整理中",
            "message": "台股剛收盤，盤後資料尚在更新；目前不沿用上一輪隔日展望，避免誤判。",
            "next_update_time": "約 15:30 後",
            "session": session,
        }
    return {
        "show": True,
        "status": "available_window",
        "label": "可顯示隔日展望",
        "message": "",
        "next_update_time": None,
        "session": session,
    }


def record_next_day_outlook(code: str, outlook: dict[str, Any]) -> None:
    try:
        calc_date = recent_market_date_for_eod()
        f = outlook.get("factors") or {}
        with closing(db()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO next_day_outlook_daily(calc_date,code,generated_at,model_version,probability_up,opening_probability,sustainability_probability,label,opening_label,sustainability_label,confidence,consistency,total_score,us_score,night_score,chip_score,tech_score,warnings_json,sources_json,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    calc_date, str(code).zfill(4), time.time(), outlook.get("model_version"),
                    outlook.get("probability_up"), outlook.get("opening_probability"), outlook.get("sustainability_probability"),
                    outlook.get("label"), outlook.get("opening_label"), outlook.get("sustainability_label"),
                    outlook.get("confidence"), outlook.get("consistency"), outlook.get("total_score"),
                    (f.get("us") or {}).get("score"), (f.get("night") or {}).get("score"),
                    (f.get("chip") or {}).get("score"), (f.get("tech") or {}).get("score"),
                    json.dumps(outlook.get("warnings") or [], ensure_ascii=False),
                    json.dumps(outlook.get("sources") or [], ensure_ascii=False),
                    json.dumps(outlook, ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception:
        logging.exception("Failed to record next-day outlook for %s", code)











@app.get("/api/stock/{code}/detail")
def api_stock_detail(code: str, source: str | None = None) -> dict[str, Any]:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    code = str(code).strip().zfill(4)[:4]
    item = find_stock_item(code)
    with closing(db()) as conn:
        analysis_as_of = resolve_full_market_analysis_date(conn)
        val = (
            conn.execute(
                "SELECT * FROM valuation WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, analysis_as_of),
            ).fetchone()
            if analysis_as_of
            else None
        )
        eod = (
            conn.execute(
                "SELECT * FROM eod_price WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, analysis_as_of),
            ).fetchone()
            if analysis_as_of
            else None
        )
        hist = (
            [
                row
                for row in conn.execute(
                    """
                    SELECT * FROM history_price
                    WHERE code=? AND date<=?
                    ORDER BY date DESC
                    LIMIT 260
                    """,
                    (code, analysis_as_of),
                ).fetchall()
                if assess_daily_ohlcv(dict(row))["ready"]
            ]
            if analysis_as_of
            else []
        )
        data_sources, data_source_issues = source_trace_for_code_v2(
            conn,
            code,
            as_of_date=analysis_as_of,
        )
        is_watch = watchlist_contains(code)
        detail_history_coverage = history_date_coverage(conn, code, required_days=120)
        detail_rsi_split_events = load_rsi_split_adjustments(conn, code)
    if not item:
        return sanitize_public_market_payload(
            sanitize_display_payload({"ok": False, "error": f"找不到股票 {code}"})
        )
    display_name = _clean_detail_name(item.get("name")) or code
    source_context = _resolve_detail_source_context(
        code,
        source,
        is_watchlist=is_watch,
        history_rows=len([r for r in hist if r["close"] is not None]),
        history_coverage_ready=bool(detail_history_coverage.get("ready")),
    )
    detail_mode = source_context["detail_mode"]
    readiness = data_readiness_for_items([{"code": item["code"], "name": display_name}], detail_mode, enqueue_missing=False)
    if not readiness.get("ready"):
        bootstrap_readiness = diagnose_watchlist_bootstrap_need(code)
        readiness_groups = bootstrap_readiness.get("readiness_groups") or {}
        technical_ready = bool((readiness_groups.get("technical") or {}).get("ready") or bootstrap_readiness.get("ready"))
        valuation_ready = bool((readiness_groups.get("valuation") or {}).get("ready"))
        chip_ready = bool((readiness_groups.get("chip") or {}).get("ready"))
        tdcc_ready = bool((readiness_groups.get("tdcc") or {}).get("ready"))
        overall_ready = bool(
            (technical_ready or source_context.get("local_detail_ready"))
            and not bootstrap_readiness.get("blocking_missing")
            and not bootstrap_readiness.get("unsupported_reason")
        )
        merged_readiness = {
            **(readiness or {}),
            "overall_ready": overall_ready,
            "technical_ready": bool(technical_ready or source_context.get("local_detail_ready")),
            "valuation_ready": valuation_ready,
            "chip_ready": chip_ready,
            "tdcc_ready": tdcc_ready,
            "blocking_missing": bootstrap_readiness.get("blocking_missing") or [],
            "nonblocking_missing": bootstrap_readiness.get("nonblocking_missing") or [],
            "bootstrap": bootstrap_readiness,
            "detail_source_context": source_context,
        }
        if overall_ready:
            readiness = merged_readiness
        else:
            if is_watch:
                status_payload = build_watchlist_bootstrap_status(code)
                bootstrap_status = status_payload.get("bootstrap_status", "manual_required")
                message = status_payload.get("message") or "分析資料補齊中；完成後會顯示完整詳細分析。"
                onboarding_available = False
                onboarding_action = None
            else:
                unsupported = bool(bootstrap_readiness.get("unsupported_reason"))
                bootstrap_status = "unsupported" if unsupported else "not_started"
                message = (
                    "目前暫不支援此市場資料，先不顯示半成品分析。"
                    if unsupported
                    else "這檔股票尚未加入自選股。加入後，系統會開始補齊分析資料。"
                )
                onboarding_available = not unsupported
                onboarding_action = "add_watchlist_and_bootstrap" if onboarding_available else None
            return sanitize_public_market_payload(sanitize_display_payload({
                "ok": False,
                "code": code,
                "name": display_name,
                "error": message,
                "message": message,
                "is_watchlist": is_watch,
                "is_tw50": source_context.get("is_tw50"),
                "resolved_detail_source": source_context.get("resolved_detail_source"),
                "detail_source_context": source_context,
                "bootstrap_needed": not overall_ready,
                "bootstrap_status": bootstrap_status,
                "onboarding_available": onboarding_available,
                "onboarding_action": onboarding_action,
                "retry_after_seconds": 5 if bootstrap_status in {"queued", "running", "not_started"} else None,
                "readiness": merged_readiness,
            }))
    # 詳細頁也必須符合嚴格數字欄位門檻；未補齊時不顯示半成品。
    detail_source_type = "detail_realtime" if detail_mode == "watchlist" else "detail_batch"
    base_row = build_row(
        {"code": item["code"], "name": display_name},
        detail_mode,
        persist_state=False,
        source_type=detail_source_type,
    )
    rows_asc = apply_rsi_split_adjustments(
        [dict(r) for r in reversed(hist)],
        detail_rsi_split_events,
    )
    kline_history = build_official_kline_payload(code, hist, limit=120)
    price = parse_num(base_row.get("price")) or (eod["close"] if eod else None)
    detail_k_dates = [normalize_date(eod['date'])] if eod and eod['date'] else []
    detail_k_dates.extend(normalize_date(row['date']) for row in hist if row['date'])
    detail_latest_k_date = max((value for value in detail_k_dates if value), default=None)
    display_context: dict[str, Any] = {}
    canonical_daily = {
        "analysis_contract_version": base_row.get("analysis_contract_version"),
        "analysis_status": base_row.get("analysis_status"),
        "trading_state": base_row.get("trading_state"),
        "referee": base_row.get("referee"),
        "advisory": base_row.get("advisory"),
        "recommendation_safety": base_row.get("recommendation_safety"),
        "microstructure_status": base_row.get("microstructure_status"),
        "technical": base_row.get("canonical_technical"),
        "valuation": base_row.get("canonical_valuation"),
        "institutional_context": base_row.get("institutional_context"),
        "global_market_context": base_row.get("global_market_context"),
        "taifex_night_context": base_row.get("taifex_night_context"),
        "official_event_context": base_row.get("official_event_context"),
        "external_event_context": base_row.get("external_event_context"),
        "news_radar_context": base_row.get("news_radar_context"),
        "decision_audit": base_row.get("decision_audit"),
        "trade_date": base_row.get("data_date"),
    }
    tech = _canonical_technical_detail(canonical_daily)
    tech_hints = list(
        dict.fromkeys(
            str(item)
            for item in [
                *((canonical_daily.get("advisory") or {}).get("evidence") or []),
                *((canonical_daily.get("advisory") or {}).get("background_notes") or []),
            ]
            if str(item).strip()
        )
    )[:8]
    formal_price = parse_num((base_row.get("referee") or {}).get("current_price")) or parse_num(base_row.get("price"))
    practical = _canonicalize_web_practical_status(
        display_context,
        canonical_daily,
        formal_price,
    )
    sr_detail = _canonical_support_resistance_detail(canonical_daily)
    price_volume = dict(base_row.get("canonical_price_volume") or {})
    inner_outer_accumulation = build_inner_outer_accumulation_payload(code)
    equity_concentration = latest_equity_concentration_payload(
        code,
        as_of_date=detail_latest_k_date,
    )
    canonical_background_notes = [
        str((canonical_daily.get(key) or {}).get("note") or "").strip()
        for key in (
            "global_market_context",
            "taifex_night_context",
            "official_event_context",
            "external_event_context",
            "news_radar_context",
        )
    ]
    external = {
        "final_status": practical.get("main_status"),
        "status_badge": practical.get("status_badge"),
        "status_modifier": None,
        "external_notes": [note for note in canonical_background_notes if note],
        "market_env": (canonical_daily.get("global_market_context") or {}).get("stance"),
    }
    status_change = build_status_change(code, practical, (eod['date'] if eod else (hist[0]['date'] if hist else None)))
    sr_periods: list[dict[str, Any]] = []
    action_hint = _canonical_advisory_text(canonical_daily)
    chip_costs = base_row.get("chip_cost_indicators") or calculate_public_chip_costs(
        code,
        as_of_date=detail_latest_k_date,
    )
    valuation = _canonical_valuation_for_web(canonical_daily)
    theme_profile = latest_theme_profile_payload(code)
    industry_valuation = latest_industry_valuation_payload(code)
    valuation_summary = build_peer_valuation_summary(industry_valuation)
    valuation_flags = valuation.get("suspicious_flags") or valuation.get("valuation_flags") or []
    if (
        valuation.get("dividend_yield_status") == "dividend_yield_suspicious"
        or "dividend_yield_unit_or_field_shift_suspicious" in valuation_flags
    ):
        valuation_summary = " ".join(
            x
            for x in [
                valuation_summary,
                "殖利率資料待確認，暫不納入同業比較。",
            ]
            if x
        )
    chip_momentum = build_chip_momentum_payload(code)
    global_context = dict(canonical_daily.get("global_market_context") or {})
    global_tickers = list(global_context.get("component_tickers") or [])
    us_assets = [
        {
            "ticker": ticker,
            "name": ticker,
            "type": "canonical_global_context",
            "reason": global_context.get("note"),
            "usage": "盤後背景資料，不覆蓋個股主結論。",
            "quote": {"ok": False},
        }
        for ticker in global_tickers
    ]
    us_forecast = {
        "available": bool(global_context.get("available")),
        "direction": global_context.get("stance"),
        "score": global_context.get("score"),
        "reason": global_context.get("note"),
        "formula_version": global_context.get("formula_version"),
        "can_override_main_status": False,
    }
    us_related_payload = {
        "category": global_context.get("industry_code"),
        "note": global_context.get("note"),
        "last_reviewed": global_context.get("market_date"),
        "available": bool(global_context.get("available")),
        "mapping_location": "canonical_close_batch_snapshot.global_market_context",
        "mapping_key": global_context.get("industry_code"),
        "assets": us_assets,
        "analysis_contract_version": canonical_daily.get("analysis_contract_version"),
        "can_override_main_status": False,
    }
    success_bootstrap_readiness = diagnose_watchlist_bootstrap_need(code)
    success_groups = success_bootstrap_readiness.get("readiness_groups") or {}
    success_readiness = {
        "overall_ready": True,
        "technical_ready": bool((success_groups.get("technical") or {}).get("ready") or success_bootstrap_readiness.get("ready")),
        "valuation_ready": bool((success_groups.get("valuation") or {}).get("ready")),
        "chip_ready": bool((success_groups.get("chip") or {}).get("ready")),
        "tdcc_ready": bool((success_groups.get("tdcc") or {}).get("ready")),
        "bootstrap": success_bootstrap_readiness,
        "detail_source_context": source_context,
    }
    outlook_gate = next_day_outlook_gate()
    night_context = dict(canonical_daily.get("taifex_night_context") or {})
    night_direction = {
        "positive": "bullish",
        "negative": "bearish",
        "neutral": "neutral",
    }.get(str(night_context.get("stance") or ""), "unavailable")
    futures_night = {
        "available": bool(night_context.get("available") and outlook_gate.get("show")),
        "source": "canonical_close_batch_snapshot.taifex_night_context",
        "can_override_main_status": False,
        "confidence": "context_only" if night_context.get("available") else "none",
        "direction": night_direction,
        "label": night_context.get("note") or outlook_gate.get("label"),
        "items": [],
        "weighted_change_pct": night_context.get("score"),
        "quality_reason": night_context.get("note") or outlook_gate.get("message"),
        "next_update_time": outlook_gate.get("next_update_time"),
        "display_gate": outlook_gate,
        "analysis_contract_version": canonical_daily.get("analysis_contract_version"),
    }
    next_day_outlook = _canonical_next_day_for_web(canonical_daily, outlook_gate)
    if not outlook_gate.get("show"):
        next_day_outlook.update(
            {
                "available": False,
                "label": outlook_gate.get("label"),
                "message": outlook_gate.get("message"),
                "next_update_time": outlook_gate.get("next_update_time"),
            }
        )
    return sanitize_public_market_payload(sanitize_display_payload({
        "ok": True,
        "code": code,
        "name": display_name,
        "is_watchlist": is_watch,
        "is_tw50": source_context.get("is_tw50"),
        "resolved_detail_source": source_context.get("resolved_detail_source"),
        "detail_source_context": source_context,
        "bootstrap_needed": False,
        "bootstrap_status": "already_ready",
        "onboarding_available": False,
        "onboarding_action": None,
        "readiness": success_readiness,
        "base": base_row,
        "kline_history": kline_history,
        "score_signal": {
            "signal": base_row.get('stock_analysis') or "資料不足",
            "main_status": practical.get('main_status'),
            "status_badge": external.get('status_badge') or practical.get('status_badge'),
            "main_reasons": practical.get('main_reasons', []),
            "institution_date": practical.get('institution_date'),
            "margin_date": practical.get('margin_date'),
            "external_notes": external.get('external_notes', []),
            "external_modifier": external.get("status_modifier"),
            "market_env": external.get("market_env"),
            "status_change": status_change,
            "action_hint": action_hint,
            "price_volume_summary": price_volume.get("summary_text") or price_volume.get("quality_reason"),
            "price_volume_grade": price_volume.get("grade"),
            "price_volume_score": price_volume.get("total_score"),
            "price_volume_quality": price_volume.get("quality"),
            "price_volume_status": price_volume.get("status"),
            "inner_outer_accumulation": inner_outer_accumulation,
            "ex_dividend": practical.get('ex_dividend'),
            "blockers": ((canonical_daily.get("advisory") or {}).get("low_zone_assessment") or {}).get("blocking_reasons", []),
            "warnings": (canonical_daily.get("advisory") or {}).get("background_notes", []),
            "states": canonical_daily.get("analysis_status") or {},
            "stop_loss_candidate": None,
            "target_price": None,
            "risk_reward_ratio": ((canonical_daily.get("advisory") or {}).get("low_zone_assessment") or {}).get("reward_risk_ratio"),
            "legacy_risk_reward_ratio": None,
        },
        "technical": tech,
        "tech_hints": tech_hints,
        "valuation": valuation,
        "theme_profile": theme_profile,
        "industry_valuation": industry_valuation,
        "valuation_summary": valuation_summary,
        "chip_momentum": chip_momentum,
        "valuation_source": {
            "date": valuation.get("data_date"),
            "source": valuation.get("source"),
            "source_type": valuation.get("source_type"),
            "source_market": valuation.get("source_market"),
            "source_name": valuation.get("source_name"),
            "source_status": valuation.get("source_status"),
            "updated_at": valuation.get("updated_at"),
            "timezone": valuation.get("timezone"),
        },
        "data_sources": data_sources,
        "data_source_issues": data_source_issues,
        "costs": {
            "market_cost_basis": base_row.get("market_cost_basis"),
            "foreign_cost_estimate": chip_costs.get("foreign_cost_estimate", {}),
            "trust_buy_cost_estimate": chip_costs.get("trust_buy_cost_estimate", {}),
            "poc60_estimate": {
                "label": "成交密集價參考區",
                "value": base_row.get("price_volume_reference"),
                "available": _positive_cost_value(base_row.get("price_volume_reference")) is not None,
                "source_meta": chip_costs.get("poc60_estimate", {}),
                "note": base_row.get("price_volume_reference_title"),
            },
            "disclaimer": chip_costs.get("disclaimer", ""),
            "input_issues": chip_costs.get("input_issues", []),
            "foreign_20d": {**chip_costs.get("foreign_cost_estimate", {}), "deprecated_alias": True},
            "trust_20d": {**chip_costs.get("trust_buy_cost_estimate", {}), "deprecated_alias": True},
            "dealer_20d": {"value": None, "available": False, "status": "unavailable", "note": "正式介面未提供另一套自營商成本公式。"},
            "main_force_zone": chip_costs.get("main_force_branch_cost", {}),
            "price_volume_reference": chip_costs.get("poc60_estimate", {}),
        },
        "support_resistance": sr_detail,
        "price_volume": price_volume,
        "inner_outer_accumulation": inner_outer_accumulation,
        "equity_concentration": equity_concentration,
        "sr_periods": sr_periods,
        "chip_periods": chip_periods_detail(code),
        "practical": practical,
        "support_display": display_text(practical.get('support_display'), "支撐 需先更新日K"),
        "resistance_display": display_text(practical.get('resistance_display'), "賣壓 需先更新日K"),
        "external_display": display_text('｜'.join((external.get('external_notes') or []) + (practical.get('ex_tags') or [])), ""),
        "status_change": display_text(status_change, ""),
        "action_hint": display_text(action_hint, "請先確認支撐、賣壓與量價是否同步。"),
        "us_related": us_related_payload,
        "us_forecast": us_forecast,
        "us_summary": explain_us_related_market(us_assets),
        "futures_night": futures_night,
        "next_day_outlook": next_day_outlook,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }))





# ---------- v2.41 true data readiness gate ----------
def _latest_price_and_history_for_readiness(
    conn: sqlite3.Connection,
    code: str,
    *,
    as_of_date: str | None = None,
) -> tuple[float | None, sqlite3.Row | None, list[sqlite3.Row]]:
    code = str(code).zfill(4)
    selected_date = resolve_full_market_analysis_date(conn, as_of_date)
    if not selected_date:
        return None, None, []
    eod = conn.execute(
        "SELECT * FROM eod_price WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, selected_date),
    ).fetchone()
    hist = [
        row
        for row in conn.execute(
            """
            SELECT * FROM history_price
            WHERE code=? AND date<=?
            ORDER BY date DESC
            LIMIT 260
            """,
            (code, selected_date),
        ).fetchall()
        if assess_daily_ohlcv(dict(row))["ready"]
    ]
    price = None
    if hist and hist[0]["close"] is not None and (not eod or str(hist[0]["date"] or "") >= str(eod["date"] or "")):
        price = parse_num(hist[0]["close"])
    if price is None and eod and eod["close"] is not None:
        price = parse_num(eod["close"])
    if price is None and hist and hist[0]["close"] is not None:
        price = parse_num(hist[0]["close"])
    return price, eod, hist


def _technical_readiness_from_history(
    hist_desc: list[sqlite3.Row],
    history_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    closes_desc = [parse_num(r["close"]) for r in hist_desc if parse_num(r["close"]) is not None]
    rows_asc = list(reversed([dict(r) for r in hist_desc]))
    out = {
        "hist_count": len(closes_desc),
        "rsi5": calc_rsi(closes_desc, 5),
        "rsi10": calc_rsi(closes_desc, 10),
        "rsi14": calc_rsi(closes_desc, 14),
        "ma20_ready": len(closes_desc) >= 20,
        "ma60_ready": len(closes_desc) >= 60,
        "atr14_ready": False,
        "macd_ready": False,
        "kd_ready": False,
        "indicator_error": None,
        "history_coverage": history_coverage or {},
    }
    if calculate_indicators and len(rows_asc) >= 60:
        try:
            df = pd.DataFrame(rows_asc)[["date", "open", "high", "low", "close", "volume"]].copy()
            ind = calculate_indicators(df)
            if ind is not None and not ind.empty:
                last = ind.iloc[-1]
                out["atr14_ready"] = bool("atr14" in ind.columns and pd.notna(last.get("atr14")))
                out["macd_ready"] = bool(("macd_diff" in ind.columns and pd.notna(last.get("macd_diff"))) or ("dif" in ind.columns and pd.notna(last.get("dif"))))
                out["kd_ready"] = bool(("k" in ind.columns and pd.notna(last.get("k"))) and ("d" in ind.columns and pd.notna(last.get("d"))))
        except Exception as exc:
            out["indicator_error"] = safe_error(exc)
    else:
        # 涓嶈畵 scoring.py 缂哄皯鏅傞€犳垚瑾ゅ垽锛涙槑纰烘绀洪渶瑕佹洿澶欿绶?濂椾欢銆?        if len(rows_asc) >= 60:
            out["indicator_error"] = "calculate_indicators 灏氭湭杓夊叆"
    out["rsi_ready"] = out["rsi5"] is not None and out["rsi10"] is not None and out["rsi14"] is not None
    coverage_ready = bool((history_coverage or {}).get("ready"))
    out["technical_ready"] = bool(coverage_ready and out["hist_count"] >= 120 and out["rsi_ready"] and out["ma20_ready"] and out["ma60_ready"] and out["atr14_ready"])
    return out


def _support_resistance_readiness(
    canonical_referee: dict[str, Any],
    canonical_analysis_status: dict[str, Any],
) -> dict[str, Any]:
    """Classify two-sided, one-sided, and absent canonical OHLCV structure."""

    support = canonical_referee.get("support_zone")
    resistance = canonical_referee.get("resistance_zone")
    support_ready = isinstance(support, dict)
    resistance_ready = isinstance(resistance, dict)
    complete = bool(support_ready and resistance_ready)
    partial = bool(support_ready != resistance_ready)
    not_applicable = bool(
        canonical_analysis_status.get("status") == "blocked"
        and canonical_analysis_status.get("complete")
    )
    status = (
        "ok"
        if complete
        else "partial"
        if partial
        else "not_applicable"
        if not_applicable
        else "insufficient_data"
    )
    warning = None
    if partial:
        missing_label = "支撐" if not support_ready else "賣壓"
        warning = {
            "component": "support_resistance",
            "status": "partial",
            "reason": f"{missing_label}區尚未形成可靠數值；保留可用單側結構但不產生主結論",
        }
    return {
        "ready_for_display": bool(complete or partial or not_applicable),
        "partial": partial,
        "warning": warning,
        "detail": {
            "support": support,
            "resistance": resistance,
            "method": canonical_referee.get("support_resistance_method"),
            "status": status,
            "reason_code": canonical_referee.get("reason_code"),
            "component_coverage": {
                "support": support_ready,
                "resistance": resistance_ready,
            },
            "source": "canonical_close_batch_snapshot.referee",
        },
    }


def data_readiness_for_items(items: list[dict[str, Any]], mode: str = "tw50", *, enqueue_missing: bool = False) -> dict[str, Any]:
    # True data readiness check for normal display rows.
    checked = 0
    passed = 0
    front_close_mode = normalize_list_mode(mode) == "tw50"
    failed: list[dict[str, Any]] = []
    summary = {
        "price_ready": 0,
        "kline_ready": 0,
        "technical_ready": 0,
        "support_resistance_ready": 0,
        "chip_ready": 0,
        "foreign_shareholding_ready": 0,
        "valuation_available": 0,
        "valuation_incomplete": 0,
        "numeric_cells_ready": 0,
        "source_trace_ready": 0,
        "us_relation_mapped": 0,
    }
    repair_full: list[str] = []
    repair_chip: list[str] = []
    codes = [str(x.get("code", "")).zfill(4) for x in items]
    watchlist_mode = normalize_list_mode(mode) == "watchlist"
    with closing(db()) as conn:
        target_s = resolve_full_market_analysis_date(conn)
        reference_dates = recent_market_reference_dates(
            conn,
            required_days=120,
            latest_completed_date=target_s,
        )
        volume_audit = audit_volume_units(conn, codes[:8] or ["2330", "2317", "2454"])
        history_volume_unit = (
            volume_audit.get("tables", {})
            .get("history_price", {})
            .get("inference", {})
            .get("unit")
        )
        if watchlist_mode:
            volume_unit_ok = history_volume_unit == "shares"
        else:
            volume_unit_ok = bool(volume_audit.get("unit_consistent") and history_volume_unit == "shares")
        for item in items:
            code = str(item.get("code", "")).zfill(4)
            name = item.get("name", "")
            checked += 1
            issues: list[str] = []
            market_profile = resolve_market_profile(code)
            details: dict[str, Any] = {"code": code, "name": name, "market_profile": market_profile}
            price, eod, hist = _latest_price_and_history_for_readiness(
                conn,
                code,
                as_of_date=target_s,
            )
            history_coverage = history_date_coverage(
                conn,
                code,
                required_days=120,
                reference_dates=reference_dates,
            )
            hist_count = len([r for r in hist if r["close"] is not None])
            latest_jump_pct = None
            latest_jump_suspicious = False
            if len(hist) >= 2 and hist[0]["close"] is not None and hist[1]["close"] not in (None, 0):
                try:
                    latest_jump_pct = (float(hist[0]["close"]) - float(hist[1]["close"])) / float(hist[1]["close"]) * 100
                    latest_jump_suspicious = abs(latest_jump_pct) > 18
                except Exception:
                    latest_jump_pct = None
            inst_count = conn.execute(
                "SELECT COUNT(*) AS c FROM institution_daily WHERE code=? AND date<=?",
                (code, target_s),
            ).fetchone()["c"]
            margin_count = conn.execute(
                "SELECT COUNT(*) AS c FROM margin_daily WHERE code=? AND date<=?",
                (code, target_s),
            ).fetchone()["c"]
            foreign_holding_count = conn.execute(
                "SELECT COUNT(*) AS c FROM foreign_shareholding WHERE code=? AND date<=?",
                (code, target_s),
            ).fetchone()["c"]
            inst_latest = conn.execute(
                "SELECT date FROM institution_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, target_s),
            ).fetchone()
            margin_latest = conn.execute(
                "SELECT date FROM margin_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, target_s),
            ).fetchone()
            foreign_holding_latest = conn.execute(
                "SELECT date FROM foreign_shareholding WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, target_s),
            ).fetchone()
            val = conn.execute(
                "SELECT * FROM valuation WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, target_s),
            ).fetchone()
            source_trace, source_issues = source_trace_for_code_v2(
                conn,
                code,
                as_of_date=target_s,
            )
            latest_price_date = max([d for d in [(eod["date"] if eod else None), (hist[0]["date"] if hist else None)] if d], default=None)
            latest_hist_date = (hist[0]["date"] if hist else None)
            details.update({
                "price": price,
                "latest_price_date": latest_price_date,
                "latest_history_date": latest_hist_date,
                "latest_institution_date": inst_latest["date"] if inst_latest else None,
                "latest_margin_date": margin_latest["date"] if margin_latest else None,
                "latest_foreign_shareholding_date": foreign_holding_latest["date"] if foreign_holding_latest else None,
                "expected_recent_market_date": target_s,
                "latest_history_jump_pct": round(latest_jump_pct, 2) if latest_jump_pct is not None else None,
                "history_rows": hist_count,
                "institution_rows": inst_count,
                "margin_rows": margin_count,
                "foreign_shareholding_rows": foreign_holding_count,
                "source_trace": source_trace,
                "history_coverage": history_coverage,
            })
            supplemental_source_issues = [
                issue
                for issue in source_issues
                if "foreign_shareholding" in str(issue).lower()
                or "foreign shareholding" in str(issue).lower()
            ]
            blocking_source_issues = [
                issue for issue in source_issues if issue not in supplemental_source_issues
            ]
            if supplemental_source_issues:
                details.setdefault("data_quality_warnings", []).append(
                    {
                        "field": "foreign_shareholding",
                        "status": "supplemental_unavailable",
                        "reason": "；".join(str(item) for item in supplemental_source_issues),
                        "blocking": False,
                    }
                )
            if blocking_source_issues:
                issues.extend(blocking_source_issues)
            else:
                summary["source_trace_ready"] += 1
            if price is not None:
                summary["price_ready"] += 1
            else:
                issues.append("缺最新價格")
            if latest_price_date and str(latest_price_date) < str(target_s) and not front_close_mode:
                issues.append(f"價格日期過舊：{latest_price_date}，預期 {target_s}")
            if hist_count >= 120:
                summary["kline_ready"] += 1
            else:
                issues.append(f"歷史K線不足：{hist_count}/120")
                repair_full.append(code)
            if not history_coverage.get("ready"):
                issues.append(
                    f"近期K線交易日缺漏：{history_coverage.get('observed_days')}/{history_coverage.get('required_days')}"
                )
                repair_full.append(code)
            if latest_hist_date and str(latest_hist_date) < str(target_s) and not front_close_mode:
                issues.append(f"K線日期過舊：{latest_hist_date}，預期 {target_s}")
                repair_full.append(code)
            if latest_jump_suspicious:
                issues.append(f"最新K線跳動異常：{latest_jump_pct:.2f}%，需改用其他來源覆核")
                repair_full.append(code)
            try:
                canonical_readiness = build_canonical_close_batch_snapshot(
                    code,
                    trade_date=target_s,
                    include_levels=False,
                    analysis_mode="close_batch",
                    allow_live_quote_fetch=False,
                )
            except Exception as exc:
                canonical_readiness = {
                    "analysis_contract_version": "canonical-close-batch-analysis-v1",
                    "analysis_status": {
                        "status": "insufficient_data",
                        "complete": False,
                        "decision_ready": False,
                        "reason_code": "canonical_snapshot_unavailable",
                    },
                    "technical": {
                        "decision_ready": False,
                        "reason": safe_error(exc),
                    },
                    "referee": {
                        "decision_ready": False,
                        "main_status": "資料不足",
                        "main_reasons": ["canonical 盤後裁判資料尚未建立"],
                        "reason_code": "canonical_snapshot_unavailable",
                    },
                }
            canonical_analysis_status = dict(canonical_readiness.get("analysis_status") or {})
            canonical_referee = dict(canonical_readiness.get("referee") or {})
            canonical_technical = dict(canonical_readiness.get("technical") or {})
            details["analysis_contract_version"] = canonical_readiness.get("analysis_contract_version")
            details["analysis_status"] = canonical_analysis_status
            details["referee"] = canonical_referee
            tech = {
                "technical_ready": bool(canonical_technical.get("decision_ready")),
                "rsi_ready": bool((canonical_technical.get("rsi") or {}).get("rsi14") is not None),
                "ma20_ready": bool((canonical_technical.get("moving_averages") or {}).get("ma20") is not None),
                "ma60_ready": bool((canonical_technical.get("moving_averages") or {}).get("ma60") is not None),
                "atr14_ready": canonical_technical.get("atr14") is not None,
                "macd_ready": bool((canonical_technical.get("macd") or {}).get("oscillator") is not None),
                "kd_ready": bool((canonical_technical.get("kd") or {}).get("k") is not None),
                "hist_count": canonical_technical.get("input_row_count"),
                "indicator_error": None if canonical_technical.get("decision_ready") else canonical_technical.get("reason"),
                "formula_version": canonical_technical.get("formula_version"),
                "source": "canonical_close_batch_snapshot.technical",
            }
            details["technical"] = tech
            if tech.get("technical_ready"):
                summary["technical_ready"] += 1
            else:
                if not tech.get("rsi_ready"):
                    issues.append("RSI5/10/14 無法完整計算")
                if not tech.get("ma20_ready") or not tech.get("ma60_ready"):
                    issues.append("MA20/MA60 無法完整計算")
                if not tech.get("atr14_ready"):
                    issues.append("ATR14 無法計算")
                if tech.get("indicator_error"):
                    issues.append(f"技術指標計算限制：{tech.get('indicator_error')}")
            support_structure = _support_resistance_readiness(
                canonical_referee,
                canonical_analysis_status,
            )
            details["support_resistance"] = support_structure["detail"]
            if support_structure["ready_for_display"]:
                summary["support_resistance_ready"] += 1
                if support_structure["warning"]:
                    details.setdefault("data_quality_warnings", []).append(
                        support_structure["warning"]
                    )
            else:
                issues.append("支撐/賣壓缺可靠數值")
            margin_min_count = 20
            if latest_hist_date and margin_latest and margin_count >= 5:
                margin_start_lag = iso_date_lag_days(latest_hist_date, margin_latest["date"])
                earliest_margin = conn.execute("SELECT date FROM margin_daily WHERE code=? ORDER BY date ASC LIMIT 1", (code,)).fetchone()
                if earliest_margin and earliest_margin["date"]:
                    margin_window_lag = iso_date_lag_days(latest_hist_date, earliest_margin["date"])
                    if margin_window_lag is not None and margin_window_lag <= 10:
                        margin_min_count = margin_count
            chip_ready = bool(inst_count >= 20 and margin_count >= margin_min_count)
            if chip_ready:
                # === Phase Calendar-D3B missing_data_quality minimal fix start ===
                # In Taiwan 50/front-close mode, institution and margin data can
                # be legitimately delayed relative to the latest K-line because
                # those sources are published later or may lag during holidays.
                # Treat sufficient-but-delayed rows as source_delayed metadata
                # instead of blocking formal next_day_outlook. Truly missing
                # rows still remain blocking below.
                delayed_chip_notes: list[dict[str, Any]] = []
                # === Phase Calendar-D3B missing_data_quality minimal fix end ===
                # chip data should not be older than the latest K-line when it is expected to be available.
                stale_chip = False
                if latest_hist_date and inst_latest and not date_is_fresh_enough(inst_latest["date"], latest_hist_date, max_lag_days=3):
                    if front_close_mode:
                        # === Phase Calendar-D3B missing_data_quality minimal fix start ===
                        delayed_chip_notes.append({
                            "field": "institution_daily",
                            "status": "source_delayed",
                            "date": inst_latest["date"],
                            "anchor_date": latest_hist_date,
                            "reason": "法人資料可能 T+1 或因休市/來源延遲沿用最近已公布資料",
                        })
                        # === Phase Calendar-D3B missing_data_quality minimal fix end ===
                    else:
                        stale_chip = True
                        issues.append(f"法人資料日期過舊：{inst_latest['date']}，K線最新 {latest_hist_date}")
                if latest_hist_date and margin_latest and not date_is_fresh_enough(margin_latest["date"], latest_hist_date, max_lag_days=3):
                    if front_close_mode:
                        # === Phase Calendar-D3B missing_data_quality minimal fix start ===
                        delayed_chip_notes.append({
                            "field": "margin_daily",
                            "status": "source_delayed",
                            "date": margin_latest["date"],
                            "anchor_date": latest_hist_date,
                            "reason": "融資融券資料可能晚於價格資料公布，前收模式沿用最近已公布資料",
                        })
                        # === Phase Calendar-D3B missing_data_quality minimal fix end ===
                    else:
                        stale_chip = True
                        issues.append(f"融資資料日期過舊：{margin_latest['date']}，K線最新 {latest_hist_date}")
                if delayed_chip_notes:
                    details.setdefault("data_quality_warnings", []).extend(delayed_chip_notes)
                if not stale_chip:
                    summary["chip_ready"] += 1
            else:
                issues.append(f"法人/融資資料不足：法人{inst_count}/20、融資{margin_count}/{margin_min_count}")
                repair_chip.append(code)
            # Foreign shareholding is supplemental only; the canonical v3
            # incremental-cost model intentionally has no holding anchor.
            foreign_holding_ready = bool(foreign_holding_count >= 20 and foreign_holding_latest)
            if foreign_holding_ready and latest_hist_date and not date_is_fresh_enough(foreign_holding_latest["date"], latest_hist_date, max_lag_days=5):
                if front_close_mode:
                    # === Phase Calendar-D3B missing_data_quality minimal fix start ===
                    details.setdefault("data_quality_warnings", []).append({
                        "field": "foreign_shareholding",
                        "status": "source_delayed",
                        "date": foreign_holding_latest["date"],
                        "anchor_date": latest_hist_date,
                        "reason": "外資持股屬非每日穩定更新資料，前收模式可沿用最近原始 anchor",
                    })
                    # === Phase Calendar-D3B missing_data_quality minimal fix end ===
                else:
                    foreign_holding_ready = False
                    details.setdefault("data_quality_warnings", []).append({
                        "field": "foreign_shareholding",
                        "status": "source_delayed",
                        "reason": f"外資持股資料日期過舊：{foreign_holding_latest['date']}，K線最新 {latest_hist_date}；不影響 canonical 成本",
                    })
            if foreign_holding_ready:
                summary["foreign_shareholding_ready"] += 1
            else:
                details.setdefault("data_quality_warnings", []).append({
                    "field": "foreign_shareholding",
                    "status": "supplemental_unavailable",
                    "reason": f"外資持股資料 {foreign_holding_count}/20；canonical v3 不使用持股 anchor",
                })
            canonical_valuation = dict(canonical_readiness.get("valuation") or {})
            valuation_ready = bool(canonical_valuation.get("available"))
            details["valuation"] = canonical_valuation
            if valuation_ready:
                summary["valuation_available"] += 1
            else:
                summary["valuation_incomplete"] += 1
                issues.append("估值數字不完整：PB/殖利率必須都有可靠值；PE 若官方未揭露則不顯示")
                details["valuation_note"] = "估值欄位不得用猜測；PB/殖利率缺任一項即阻擋正式列表，PE 為官方可得時才顯示"
            numeric_issues: list[str] = []
            try:
                # Readiness only audits the canonical persisted costs.  The
                # separate POC calculation is display-only and must not make
                # the Taiwan 50 readiness pass slower or block the list.
                chip_costs = get_canonical_cost_snapshot(code)
                required_costs = [
                    ("外資近期增量部位均價推估", chip_costs.get("foreign_cost_estimate", {}), False),
                    ("投信近期增量部位均價推估", chip_costs.get("trust_buy_cost_estimate", {}), False),
                ]
                for label, item, required in required_costs:
                    value = (item or {}).get("value")
                    confidence = (item or {}).get("confidence", "invalid")
                    if value is None or confidence in {"invalid", "none"}:
                        if required:
                            note = str((item or {}).get("note") or "資料不足")
                            details.setdefault("numeric_warnings", []).append(f"{label}未納入：{note}")
                details["numeric_cells"] = {
                    "foreign_cost_estimate": chip_costs.get("foreign_cost_estimate"),
                    "trust_buy_cost_estimate": chip_costs.get("trust_buy_cost_estimate"),
                    "input_issues": chip_costs.get("input_issues", []),
                }
            except Exception as exc:
                numeric_issues.append(f"chip cost readiness check failed: {safe_error(exc)}")
            if numeric_issues:
                details.setdefault("numeric_warnings", []).extend(numeric_issues)
            else:
                summary["numeric_cells_ready"] += 1
            global_context = dict(canonical_readiness.get("global_market_context") or {})
            details["global_market_context"] = global_context
            if global_context.get("available") and global_context.get("coverage_count"):
                summary["us_relation_mapped"] += 1
            else:
                details.setdefault("data_quality_warnings", []).append({
                    "field": "us_relation",
                    "status": "unavailable",
                    "reason": "canonical global-market mapping is unavailable; this is informational and does not block local detail analysis",
                })
            blocking_issues = issues
            if watchlist_mode:
                blocking_markers = (
                    "缺最新價格",
                    "歷史K線不足",
                    "K線日期過舊",
                    "最新K線跳動異常",
                    "RSI5/10/14",
                    "MA20/MA60",
                    "ATR14",
                    "技術指標計算限制",
                    "支撐/賣壓缺可靠數值",
                )
                blocking_issues = [msg for msg in issues if any(marker in msg for marker in blocking_markers)]
                nonblocking_issues = [msg for msg in issues if msg not in blocking_issues]
                if nonblocking_issues:
                    details["nonblocking_issues"] = nonblocking_issues
            if blocking_issues:
                details["issues"] = blocking_issues
                if blocking_issues != issues:
                    details["all_issues"] = issues
                failed.append(details)
            else:
                passed += 1
    # volume unit is global; if it fails, the dataset is not ready for strong volume-based signals.
    global_issues = []
    if not target_s:
        global_issues.append("尚無全市場完整交易日 publication marker，盤後分析暫停")
    if not volume_unit_ok:
        global_issues.append("volume 單位尚未確認為 shares，OBV/量比/成交金額/流動性暫不作強判斷")
    ready = checked > 0 and passed == checked and not global_issues
    if enqueue_missing:
        # v2.42: use the strongest bundled repair path, not FinMind-only repair.
        repair_targets = sorted(set(repair_full) | set(repair_chip) | {x["code"] for x in failed})
        if repair_targets:
            threading.Thread(target=background_ensure_complete_data, args=(repair_targets, 120, "資料完整性檢查自動多來源補齊"), daemon=True).start()
    return {
        "mode": mode,
        "ready": ready,
        "checked": checked,
        "pass_count": passed,
        "fail_count": len(failed),
        "requirements": {
            "min_history_rows": 120,
            "require_price": True,
            "require_rsi_5_10_14": True,
            "require_ma20_ma60_atr14": True,
            "require_at_least_one_support_or_resistance_from_kline": True,
            "require_chip_20d": True,
            "require_numeric_cost_cells": False,
            "costs_are_background_only": True,
            "require_source_trace": True,
            "valuation_required": True,
            "valuation_fields": ["pb", "dividend_yield"],
            "pe_optional_when_official_source_does_not_publish": True,
            "us_quotes_required": False,
            "front_close_mode": front_close_mode,
        },
        "summary": summary,
        "global_issues": global_issues,
        "volume_audit": volume_audit,
        "failed": failed[:50],
        "failed_codes": [x["code"] for x in failed],
        "auto_repair_queued": bool(enqueue_missing and failed),
        "note": "這是嚴格數字欄位完整性檢查；tw50 使用前收盤/最近可得收盤資料，不要求盤中 MIS。force=1 僅供 debug。",
    }


@app.get("/api/debug/data-readiness")
def api_debug_data_readiness(mode: str = "tw50", auto_repair: int = 0) -> dict[str, Any]:
    mode = normalize_list_mode(mode)
    if mode == "watchlist":
        items = list_watchlist_code_name_items()
    else:
        items = read_components()
    result = data_readiness_for_items(items, mode, enqueue_missing=False)
    auto_repair_requested = bool(auto_repair)
    result.update({
        "read_only": True,
        "auto_repair_requested": auto_repair_requested,
        "auto_repair_executed": False,
        "repair_endpoint": "POST /api/update/ensure-complete",
        "update_endpoint": "POST /api/update/ensure-complete",
        "suggested_endpoint": "POST /api/update/ensure-complete",
        "generic_repair_endpoint": "POST /api/update/ensure-complete",
    })
    if auto_repair_requested:
        result["reason"] = "GET debug endpoint is read-only; use POST /api/update/ensure-complete to trigger repair."
    return result


@app.get("/api/debug/us-relations/coverage")
def api_us_relations_coverage() -> dict[str, Any]:
    codes = [x.get("code", "") for x in read_components()]
    return relation_coverage(codes)


@app.get("/api/debug/ui-completeness")
def api_debug_ui_completeness(mode: str = "tw50") -> dict[str, Any]:
    # Debug visible row completeness for blank/NaN/undefined fields.
    mode = normalize_list_mode(mode)
    items = read_components() if mode != "watchlist" else list_watchlist_code_name_items()
    readiness = data_readiness_for_items(items, mode, enqueue_missing=False) if items else None
    partial_ready = False
    hidden_codes: list[str] = []
    if readiness is not None and not readiness.get("ready"):
        failed_codes = set(readiness.get("failed_codes") or [])
        can_show_partial = (
            not readiness.get("global_issues")
            and int(readiness.get("pass_count") or 0) > 0
            and failed_codes
        )
        if can_show_partial:
            partial_ready = True
            hidden_codes = sorted(failed_codes)
            items = [x for x in items if str(x.get("code", "")).zfill(4) not in failed_codes]
        else:
            volume_audit = readiness.get("volume_audit") if isinstance(readiness, dict) else None
            volume_warning = volume_audit.get("warning") if isinstance(volume_audit, dict) else None
            return {
                "mode": mode,
                "checked": 0,
                "issue_count": 1,
                "issues": [{
                    "field": "readiness",
                    "note": "資料完整性未通過，主列表正常不應輸出半成品 rows；請先看 readiness.failed。",
                }],
                "readiness": readiness,
                "partial_ready": False,
                "hidden_codes": sorted(failed_codes),
                "skipped_row_check": True,
                "volume_audit": volume_audit,
                "volume_warning": volume_warning,
                "ok": False,
                "visible_rows_ok": False,
            }
    required = ["code", "name", "price", "stock_analysis", "support_display", "resistance_display", "main_status"]
    bad_values = {"", "--", "nan", "none", "null", "undefined"}
    issues: list[dict[str, Any]] = []
    checked = 0
    for item in items:
        try:
            row = build_row(
                item,
                mode,
                persist_state=False,
                source_type=("taiwan50_batch" if mode == "tw50" else "watchlist_realtime"),
            )
            checked += 1
            row_issues = []
            for k in required:
                v = row.get(k)
                sv = str(v).strip().lower() if v is not None else ""
                if sv in bad_values:
                    row_issues.append({"field": k, "value": row.get(k)})
            if not display_text(row.get("rsi"), ""):
                row_issues.append({"field": "rsi", "value": row.get("rsi"), "note": "RSI椤ず鏂囧瓧缂哄け"})
            if row.get("legacy_signal") and row.get("legacy_signal") == row.get("stock_analysis"):
                row_issues.append({"field": "legacy_signal", "value": row.get("legacy_signal"), "note": "涓诲垪琛ㄧ枒浼间粛浣跨敤 legacy signal"})
            if row_issues:
                issues.append({"code": row.get("code"), "name": row.get("name"), "issues": row_issues})
        except Exception as exc:
            issues.append({"code": item.get("code"), "name": item.get("name"), "error": safe_error(exc)})
    volume_audit = None
    try:
        with closing(db()) as conn:
            sample_codes = [str(x.get("code", "")).zfill(4) for x in (items[:6] if items else read_components()[:6])]
            volume_audit = audit_volume_units(conn, sample_codes or ["2330", "2317", "2454"])
    except Exception as exc:
        volume_audit = {"warning": f"volume 單位自檢失敗：{safe_error(exc)}", "unit_consistent": None}
    volume_warning = volume_audit.get("warning") if isinstance(volume_audit, dict) else None
    return {
        "mode": mode,
        "checked": checked,
        "issue_count": len(issues),
        "issues": issues,
        "readiness": readiness,
        "partial_ready": partial_ready,
        "hidden_codes": hidden_codes,
        "skipped_row_check": False,
        "volume_audit": volume_audit,
        "volume_warning": volume_warning,
        "ok": len(issues) == 0 and not bool(volume_warning),
        "visible_rows_ok": len(issues) == 0 and not bool(volume_warning),
    }


@app.get("/api/quotes/watchlist")
def api_quotes_watchlist(q: str = "", force: int = 0) -> dict[str, Any]:
    # 前端自選股專用；不先載入台灣50，也不等待台灣50資料。
    return api_quotes(mode="watchlist", q=q, force=force)

@app.get("/api/quotes")
def api_quotes(mode: str = "watchlist", q: str = "", force: int = 0) -> dict[str, Any]:
    mode = normalize_list_mode(mode)
    if mode == "watchlist":
        # TODO(watchlist-realtime-source, 2026-06-19):
        # watchlist_realtime may use intraday price, but abnormal realtime values should not pollute technical indicators or status calculations.
        items = list_watchlist_code_name_items()
    else:
        # TODO(taiwan50-close-source, 2026-06-19):
        # taiwan50_batch should prefer official close data and must not depend on TWSE MIS intraday price.
        items = read_components()
    close_batch = None
    if mode == "tw50":
        try:
            with closing(db()) as conn:
                close_batch = latest_taiwan50_close_batch(conn)
        except Exception:
            logging.exception("failed to read Taiwan50 close-batch metadata")
    if q and mode == "watchlist":
        query = q.strip()
        items = [x for x in items if query in x["code"] or query in x.get("name", "")]

    readiness = None
    # Keep data readiness strict; only individually complete rows may display.
    # force=1 僅供 debug/開發檢視，不作一般 UI 預設。
    if items and not force and mode != "watchlist":
        readiness = data_readiness_for_items(items, mode, enqueue_missing=False)
        if not readiness.get("ready"):
            failed_codes = set(readiness.get("failed_codes") or [])
            if mode == "watchlist":
                can_show_partial = (
                    not readiness.get("global_issues")
                    and int(readiness.get("pass_count") or 0) > 0
                    and failed_codes
                )
                if can_show_partial:
                    items = [x for x in items if str(x.get("code", "")).zfill(4) not in failed_codes]
                else:
                    return {
                        "page": "watchlist",
                        "update_mode": "realtime_or_intraday",
                        "is_realtime": market_is_open_now(),
                        "market_session": tw_market_session_now().get("state"),
                        "data_time": now_tpe().strftime("%H:%M:%S"),
                        "timezone": "Asia/Taipei",
                        "mode": mode,
                        "rows": [],
                        "count": 0,
                        "readiness": readiness,
                        "ready": False,
                        "partial_ready": False,
                        "hidden_codes": sorted(failed_codes),
                        "message": "自選股正在預補必要數字；資料未全齊前不顯示半成品列表。完成後會一次顯示完整資料。",
                        "statuses": get_status(),
                        "tw_market_session": tw_market_session_now(),
                        "now": now_tpe().isoformat(),
                    }

    display_items = select_displayable_dashboard_items(items, readiness)
    partial_ready = bool(display_items and readiness and not readiness.get("ready"))
    rows = [
        build_row(
            x,
            mode,
            persist_state=False,
            source_type=("taiwan50_batch" if mode == "tw50" else "watchlist_realtime"),
        )
        for x in display_items
    ]
    if mode != "watchlist":
        # 台灣50預設用 RSI5 由低到高排序；RSI 缺值排最後。
        rows.sort(key=lambda r: (r.get("rsi5") is None, float(r.get("rsi5") if r.get("rsi5") is not None else 9999)))
    payload = {
        "mode": mode,
        "rows": rows,
        "count": len(rows),
        "readiness": readiness,
        "ready": bool(readiness.get("ready")) if readiness is not None else True,
        "display_ready": bool(rows),
        "rsi_ready": all(
            bool((row.get("rsi_data_quality") or {}).get("ready"))
            and row.get("rsi5") is not None
            and row.get("rsi10") is not None
            and row.get("rsi14") is not None
            for row in rows
        ),
        "partial_ready": partial_ready,
        "hidden_codes": sorted(set((readiness or {}).get("failed_codes") or [])),
        "message": (
            f"已顯示 {len(rows)} 檔可用分析；另 {(readiness or {}).get('fail_count', 0)} 檔暫不顯示。"
            if partial_ready
            else ("台灣50使用前收盤/最近可得收盤資料，不依賴盤中 MIS。若 readiness 有 failed_codes，代表仍有欄位需人工確認。" if mode == "tw50" else "")
        ),
        "statuses": get_status(),
        "tw_market_session": tw_market_session_now(),
        "now": now_tpe().isoformat(),
    }
    if mode == "tw50":
        run = (close_batch or {}).get("run") if close_batch else None
        close_batch_data_date = None
        try:
            with closing(db()) as conn:
                close_batch_data_date = latest_completed_tw50_close_date(conn, items)
        except Exception:
            logging.exception("failed to resolve Taiwan50 completed close date")
        if not close_batch_data_date:
            close_batch_data_date = run.get("data_date") if run else None
        payload.update({
            "page": "taiwan50",
            "title": _format_tw50_close_batch_title(close_batch_data_date),
            "data_date": close_batch_data_date,
            "updated_at": run.get("updated_at") if run else None,
            "timezone": "Asia/Taipei",
            "update_mode": "close_batch",
            "is_realtime": False,
            "retention_days": 200,
            "next_expected_update": "下一個交易日收盤後",
            "empty_batch": not bool(run),
            "items": (close_batch or {}).get("items", []) if run else [],
        })
        if not run:
            payload["message"] = payload.get("message") or "尚未建立收盤後批次資料"
    else:
        session = tw_market_session_now()
        payload.update({
            "page": "watchlist",
            "update_mode": "realtime_or_intraday",
            "is_realtime": market_is_open_now(),
            "market_session": session.get("state"),
            "data_time": now_tpe().strftime("%H:%M:%S"),
            "timezone": "Asia/Taipei",
        })
    return sanitize_display_payload(payload)

































