from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import closing
from typing import Any

from adapter.twse import fetch_twse_eod_all, fetch_twse_valuation_all, refresh_twse_stock_day_codes
from adapter.yahoo_history import upsert_yfinance_tw_history, upsert_yfinance_tw_valuation
from core.components import read_components
from core.config import YAHOO_REQUEST_SLEEP_SECONDS
from core.db import db
from core.market_session import latest_verified_market_date, recent_market_date_for_eod
from repository.history_repository import history_date_coverage
from repository.market_profile_repository import resolve_market_profile
from repository.watchlist_repository import list_watchlist_code_name_items
from services.tpex_history_service import refresh_tpex_history_codes
from services.tpex_valuation_service import refresh_tpex_valuation_codes

_repair_lock = threading.RLock()
_repair_pending: dict[str, tuple[int, str]] = {}
_repair_running = False
_finmind_token: str | None = None
_chip_update_is_running: Callable[[], bool] = lambda: False
_update_finmind_codes: Callable[..., None] | None = None
_set_status: Callable[[str, str, str], None] = lambda *_args, **_kwargs: None
_safe_error: Callable[[Exception], str] = lambda exc: repr(exc)
_prune_compute_caches: Callable[[], None] = lambda: None
_update_corporate_actions: Callable[[], Any] = lambda: None
_warm_row_cache: Callable[[list[str], str], None] = lambda *_args, **_kwargs: None


def configure_data_repair_service(
    *,
    finmind_token: str | None,
    chip_update_is_running: Callable[[], bool],
    update_finmind_codes_func: Callable[..., None],
    set_status_func: Callable[[str, str, str], None],
    safe_error_func: Callable[[Exception], str],
    prune_compute_caches_func: Callable[[], None],
    update_corporate_actions_func: Callable[[], Any] | None = None,
    warm_row_cache_func: Callable[[list[str], str], None] | None = None,
) -> None:
    global _finmind_token, _chip_update_is_running, _update_finmind_codes, _set_status, _safe_error, _prune_compute_caches, _update_corporate_actions, _warm_row_cache
    _finmind_token = finmind_token
    _chip_update_is_running = chip_update_is_running
    _update_finmind_codes = update_finmind_codes_func
    _set_status = set_status_func
    _safe_error = safe_error_func
    _prune_compute_caches = prune_compute_caches_func
    if update_corporate_actions_func is not None:
        _update_corporate_actions = update_corporate_actions_func
    if warm_row_cache_func is not None:
        _warm_row_cache = warm_row_cache_func

def enqueue_data_repair(codes: list[str], days: int, mode: str, reason: str) -> None:
    # Queue background FinMind repair without blocking read APIs.
    global _repair_running
    if not _finmind_token:
        return
    clean_codes = [str(c).zfill(4) for c in codes if str(c).strip()]
    if not clean_codes:
        return
    with _repair_lock:
        for code in clean_codes:
            old = _repair_pending.get(code)
            if old is None or days > old[0] or mode == "full":
                _repair_pending[code] = (int(days), "full" if mode == "full" else "incremental")
        if _repair_running:
            return
        _repair_running = True
    threading.Thread(target=_data_repair_worker, args=(reason,), daemon=True).start()


def _data_repair_worker(reason: str) -> None:
    global _repair_running
    try:
        while True:
            with _repair_lock:
                if not _repair_pending:
                    _repair_running = False
                    return
                pending_snapshot = list(_repair_pending.items())
                items = list(_repair_pending.items())[:5]
                for code, _ in items:
                    _repair_pending.pop(code, None)
            codes = [code for code, _ in items]
            days = max(v[0] for _, v in pending_snapshot)
            mode = "full" if any(v[1] == "full" for _, v in pending_snapshot) else "incremental"
            _set_status("auto_repair", "loading", f"{reason}: {len(codes)} codes | {mode} {days}d")
            while _chip_update_is_running():
                time.sleep(3)
            if _update_finmind_codes is None:
                raise RuntimeError("data repair service is not configured")
            _update_finmind_codes(codes, days=days, mode=mode)
            time.sleep(1)
    except Exception as exc:
        logging.exception("auto data repair worker failed")
        _set_status("auto_repair", "stale", f"鑷嫊瑁滆硣鏂欏け鏁楋細{_safe_error(exc)}")
        with _repair_lock:
            _repair_running = False


def local_data_gaps(code: str, min_history_rows: int = 120) -> dict[str, Any]:
    code = str(code).zfill(4)
    with closing(db()) as conn:
        hist_count = conn.execute("SELECT COUNT(*) AS c FROM history_price WHERE code=? AND close IS NOT NULL", (code,)).fetchone()["c"]
        eod_row = conn.execute("SELECT * FROM eod_price WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        hist_row = conn.execute("SELECT * FROM history_price WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        hist_prev = conn.execute("SELECT * FROM history_price WHERE code=? AND close IS NOT NULL ORDER BY date DESC LIMIT 1 OFFSET 1", (code,)).fetchone()
        price_row = hist_row if hist_row and (not eod_row or str(hist_row["date"] or "") >= str(eod_row["date"] or "")) else eod_row
        target_date = latest_verified_market_date(conn)
        history_coverage = history_date_coverage(
            conn,
            code,
            required_days=min_history_rows,
        )
        latest_price_date = str(price_row["date"]) if price_row and price_row["date"] else None
        latest_history_date = str(hist_row["date"]) if hist_row and hist_row["date"] else None
        latest_jump_pct = None
        latest_jump_suspicious = False
        if hist_row and hist_prev and hist_row["close"] is not None and hist_prev["close"] not in (None, 0):
            latest_jump_pct = (float(hist_row["close"]) - float(hist_prev["close"])) / float(hist_prev["close"]) * 100
            latest_jump_suspicious = abs(latest_jump_pct) > 18
        latest_is_verified_no_trade = target_date in set(history_coverage.get("verified_no_trade_dates") or [])
        price_fresh = bool(
            latest_price_date
            and (latest_price_date >= target_date or latest_is_verified_no_trade)
        )
        history_fresh = bool(history_coverage.get("ready"))
        inst_count = conn.execute("SELECT COUNT(*) AS c FROM institution_daily WHERE code=?", (code,)).fetchone()["c"]
        margin_count = conn.execute("SELECT COUNT(*) AS c FROM margin_daily WHERE code=?", (code,)).fetchone()["c"]
        val = conn.execute("SELECT * FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        val_ok = bool(val and val["pb"] is not None and val["dividend_yield"] is not None)
        return {
            "code": code,
            "expected_market_date": target_date,
            "latest_price_date": latest_price_date,
            "latest_history_date": latest_history_date,
            "latest_history_jump_pct": round(latest_jump_pct, 2) if latest_jump_pct is not None else None,
            "latest_history_jump_suspicious": latest_jump_suspicious,
            "price_ready": bool(price_row and price_row['close'] is not None and price_fresh and not latest_jump_suspicious),
            "history_rows": hist_count,
            "history_ready": history_fresh and not latest_jump_suspicious,
            "history_coverage": history_coverage,
            "institution_rows": inst_count,
            "institution_ready": inst_count >= 20,
            "margin_rows": margin_count,
            "margin_ready": margin_count >= 20,
            "valuation_ready": val_ok,
        }


def ensure_complete_data_for_codes(codes: list[str], days: int = 120, reason: str = "ensure complete data") -> dict[str, Any]:
    """Synchronous worker target: try all configured public sources in priority order.

    Order: TWSE/TPEx official price history -> FinMind chip/margin -> Yahoo TW history/valuation fallback.
    We still do not invent missing official chip data; if FinMind cannot provide institution/margin, readiness stays false.
    """
    codes = [str(c).zfill(4) for c in codes if str(c).strip()]
    profiles = {code: resolve_market_profile(code) for code in codes}
    listed_codes = [code for code in codes if profiles[code].get("market_type") == "listed"]
    otc_codes = [code for code in codes if profiles[code].get("market_type") == "otc"]
    unknown_codes = [code for code in codes if profiles[code].get("market_type") == "unknown"]
    # Unknown codes are probed against both official markets.  They are never
    # guessed into Yahoo .TW/.TWO fallback until the market is resolved.
    twse_candidate_codes = [*listed_codes, *unknown_codes]
    tpex_candidate_codes = [*otc_codes, *unknown_codes]
    stats = {"codes": len(codes), "market_profiles": profiles, "otc_codes": otc_codes, "unknown_codes": unknown_codes, "tpex_history": None, "tpex_valuation": None, "finmind": [], "yahoo_history": [], "yahoo_valuation": []}
    _prune_compute_caches()
    _set_status("data_ensure", "loading", f"{reason}: starting TWSE / TPEx / FinMind / Yahoo multi-source update for {len(codes)} codes")
    try:
        try:
            fetch_twse_eod_all()
        except Exception as exc:
            _set_status("twse_eod", "stale", f"TWSE EOD update failed: {_safe_error(exc)}")
        try:
            name_map = {str(x.get("code", "")).zfill(4): x.get("name", "") for x in read_components()}
            for r in list_watchlist_code_name_items():
                key = str(r["code"]).zfill(4)
                name_map[key] = r["name"] or name_map.get(key, "")
            refresh_twse_stock_day_codes([{"code": c, "name": name_map.get(c, "")} for c in twse_candidate_codes], target_date=recent_market_date_for_eod())
        except Exception as exc:
            _set_status("twse_stock_day", "stale", f"TWSE stock day update failed: {_safe_error(exc)}")
        if tpex_candidate_codes:
            try:
                # Eight monthly reports cover more than 120 normal trading
                # sessions while preserving official per-stock no-trade rows.
                stats["tpex_history"] = refresh_tpex_history_codes(tpex_candidate_codes, months=8)
            except Exception as exc:
                stats["tpex_history"] = {"ok": False, "error": _safe_error(exc)}
                _set_status("tpex_stock_history", "stale", f"TPEx stock history update failed: {_safe_error(exc)}")
        try:
            fetch_twse_valuation_all()
        except Exception as exc:
            _set_status("twse_valuation", "stale", f"TWSE浼板€艰榻婂け鏁楋細{_safe_error(exc)}")
        if tpex_candidate_codes:
            try:
                stats["tpex_valuation"] = refresh_tpex_valuation_codes(tpex_candidate_codes)
            except Exception as exc:
                stats["tpex_valuation"] = {"ok": False, "error": _safe_error(exc)}
                _set_status("tpex_valuation", "stale", f"TPEx valuation update failed: {_safe_error(exc)}")
        try:
            _update_corporate_actions()
        except Exception as exc:
            _set_status("corporate_actions", "stale", f"corporate actions update failed: {_safe_error(exc)}")
        # FinMind remains the only bundled source for institution/margin.
        # background_ensure_complete_data() already owns the chip update slot,
        # so do not let the nested FinMind updater treat that as a competing job.
        _update_finmind_codes(codes, days=days, mode="full", _owns_update_slot=True)
        for idx, code in enumerate(codes, 1):
            yahoo_called = False
            market_type = profiles.get(code, {}).get("market_type")
            gaps = local_data_gaps(code, min_history_rows=days)
            if market_type == "unknown":
                if not gaps.get('history_ready') or not gaps.get('price_ready'):
                    stats['yahoo_history'].append({
                        "code": code,
                        "ok": False,
                        "rows": 0,
                        "error": "market_unresolved_official_sources_only",
                    })
            elif not gaps.get('history_ready') or not gaps.get('price_ready'):
                stats['yahoo_history'].append(upsert_yfinance_tw_history(code, days=max(days, 180), market_type=market_type))
                yahoo_called = True
            gaps = local_data_gaps(code, min_history_rows=days)
            if not gaps.get('valuation_ready') and market_type != "unknown":
                stats['yahoo_valuation'].append(upsert_yfinance_tw_valuation(code, market_type=market_type))
                yahoo_called = True
            if yahoo_called:
                _prune_compute_caches()
                time.sleep(YAHOO_REQUEST_SLEEP_SECONDS)
            _set_status("data_ensure_progress", "loading", f"multi-source update {idx}/{len(codes)}: {code}")
        _prune_compute_caches()
        _warm_row_cache(codes, "tw50")
        _warm_row_cache(codes, "watchlist")
        final_gaps = {code: local_data_gaps(code, min_history_rows=days) for code in codes}
        blocking_codes = [
            code for code, gaps in final_gaps.items()
            if not gaps.get("history_ready") or not gaps.get("price_ready")
        ]
        stats["final_gaps"] = final_gaps
        stats["blocking_codes"] = blocking_codes
        final_status = "fresh" if not blocking_codes else "stale"
        _set_status(
            "data_ensure",
            final_status,
            f"{reason} complete: {len(codes) - len(blocking_codes)}/{len(codes)} price histories ready",
        )
    except Exception as exc:
        logging.exception("ensure complete data failed")
        _set_status("data_ensure", "stale", f"{reason} failed: {_safe_error(exc)}")
    return stats

