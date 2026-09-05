from __future__ import annotations

import threading
from contextlib import closing
from typing import Any, Callable

from core.db import db
from repository.market_profile_repository import resolve_market_profile
from repository.history_repository import history_date_coverage
from repository.watchlist_repository import watchlist_contains
from services.daily_chip_momentum_service import refresh_daily_chip_momentum_for_codes
from services.tdcc_equity_concentration_service import update_tdcc_equity_concentration_for_codes


EnsureCompleteFunc = Callable[[list[str], int, str], Any]

_bootstrap_lock = threading.RLock()
_bootstrap_running: set[str] = set()
_bootstrap_queued: set[str] = set()
_ensure_complete_data: EnsureCompleteFunc | None = None


def configure_watchlist_bootstrap_service(ensure_complete_data_func: EnsureCompleteFunc) -> None:
    global _ensure_complete_data
    _ensure_complete_data = ensure_complete_data_func


def _clean_code(code: Any) -> str | None:
    value = str(code or "").strip().zfill(4)[:4]
    return value if value.isdigit() and len(value) == 4 else None


def _table_count(conn, table: str, code: str, code_col: str = "code") -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {code_col}=?", (code,)).fetchone()
        return int(row["c"] or 0)
    except Exception:
        return 0


def diagnose_watchlist_bootstrap_need(code: str) -> dict[str, Any]:
    clean = _clean_code(code)
    if not clean:
        return {"ok": False, "code": str(code), "ready": False, "missing": ["invalid_code"]}
    market_profile = resolve_market_profile(clean)
    with closing(db()) as conn:
        hist = _table_count(conn, "history_price", clean)
        eod = _table_count(conn, "eod_price", clean)
        valuation = _table_count(conn, "valuation", clean)
        institution = _table_count(conn, "institution_daily", clean)
        margin = _table_count(conn, "margin_daily", clean)
        foreign = _table_count(conn, "foreign_shareholding", clean)
        tdcc_summary = _table_count(conn, "tdcc_equity_summary", clean)
        tdcc_rows = _table_count(conn, "tdcc_holding_distribution", clean)
        daily_chip = _table_count(conn, "daily_chip_momentum", clean, "stock_id")
        history_coverage = history_date_coverage(conn, clean, required_days=120)
    missing: list[str] = []
    blocking_missing: list[str] = []
    nonblocking_missing: list[str] = []
    if hist < 120:
        missing.append("missing_history")
    if hist == 0 and eod == 0:
        missing.append("missing_price")
    if valuation == 0:
        missing.append("missing_valuation")
    if institution < 20:
        missing.append("missing_institution")
    if margin < 20:
        missing.append("missing_margin")
    if foreign < 20:
        missing.append("missing_foreign_shareholding")
    if tdcc_summary == 0 and tdcc_rows == 0:
        missing.append("missing_tdcc")
    price_available = hist > 0 or eod > 0
    technical_ready = hist >= 60 and price_available and bool(history_coverage.get("ready"))
    if hist < 60:
        blocking_missing.append("missing_history")
    if not history_coverage.get("ready"):
        missing.append("missing_recent_trading_dates")
        blocking_missing.append("missing_recent_trading_dates")
    if not price_available:
        blocking_missing.append("missing_price")
    for item in missing:
        if item not in blocking_missing:
            nonblocking_missing.append(item)
    unsupported_reason = None
    if not market_profile.get("bootstrap_supported"):
        unsupported_reason = market_profile.get("unsupported_reason") or "unsupported_market"
    return {
        "ok": True,
        "code": clean,
        "market_profile": market_profile,
        "market_type": market_profile.get("market_type"),
        "yahoo_symbol": market_profile.get("yahoo_symbol"),
        "yahoo_symbols": market_profile.get("yahoo_symbols"),
        "is_watchlist": watchlist_contains(clean),
        "ready": technical_ready and unsupported_reason is None,
        "complete_ready": not missing,
        "blocking_missing": blocking_missing,
        "nonblocking_missing": nonblocking_missing,
        "missing": missing,
        "unsupported_reason": unsupported_reason,
        "history_coverage": history_coverage,
        "tables": {
            "history_price": {"row_count": hist, "ready": hist >= 120},
            "eod_price": {"row_count": eod, "ready": eod > 0},
            "valuation": {"row_count": valuation, "ready": valuation > 0},
            "institution_daily": {"row_count": institution, "ready": institution >= 20},
            "margin_daily": {"row_count": margin, "ready": margin >= 20},
            "foreign_shareholding": {"row_count": foreign, "ready": foreign >= 20},
            "tdcc_equity": {"row_count": tdcc_summary + tdcc_rows, "ready": tdcc_summary > 0 or tdcc_rows > 0},
            "daily_chip_momentum": {"row_count": daily_chip, "ready": daily_chip > 0},
        },
        "readiness_groups": {
            "technical": {"ready": technical_ready, "blocking": True, "history_rows": hist, "price_available": price_available, "history_coverage": history_coverage},
            "valuation": {"ready": valuation > 0, "blocking": False},
            "chip": {"ready": institution >= 20 and margin >= 20 and foreign >= 20, "blocking": False},
            "tdcc": {"ready": tdcc_summary > 0 or tdcc_rows > 0, "blocking": False},
        },
    }


def build_watchlist_bootstrap_status(code: str) -> dict[str, Any]:
    need = diagnose_watchlist_bootstrap_need(code)
    clean = need.get("code")
    if not need.get("ok"):
        return {"bootstrap_status": "manual_required", "message": "股票代號格式無法辨識。", "readiness": need}
    if need.get("unsupported_reason"):
        return {"bootstrap_status": "unsupported", "message": "目前暫不支援此市場資料，先不顯示半成品分析。", "readiness": need}
    if need.get("ready"):
        return {"bootstrap_status": "already_ready", "message": "已加入自選股，完整分析資料已可使用。", "readiness": need}
    with _bootstrap_lock:
        queued = clean in _bootstrap_queued or clean in _bootstrap_running
    if queued:
        return {"bootstrap_status": "queued", "message": "已加入自選股，分析資料補齊中；稍後會自動重新檢查。", "readiness": need}
    return {"bootstrap_status": "manual_required", "message": "已加入自選股；完整分析需先執行資料補齊流程。", "readiness": need}


def bootstrap_watchlist_code(
    code: str,
    *,
    days: int = 180,
    mode: str = "watchlist",
    run_tdcc: bool = True,
    run_daily_chip: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    clean = _clean_code(code)
    if not clean:
        return {"ok": False, "code": str(code), "before": {}, "after": {}, "actions": [], "warnings": ["invalid_code"], "failed_steps": ["invalid_code"]}
    before = diagnose_watchlist_bootstrap_need(clean)
    actions: list[str] = []
    warnings: list[str] = []
    failed_steps: list[str] = []
    if before.get("ready"):
        return {
            "ok": True,
            "code": clean,
            "before": before,
            "after": before,
            "actions": [],
            "warnings": ["nonblocking data still missing"] if before.get("nonblocking_missing") else [],
            "failed_steps": [],
            "missing": before.get("missing") or [],
            "nonblocking_missing": before.get("nonblocking_missing") or [],
        }
    missing = set(before.get("missing") or [])
    if missing & {"missing_history", "missing_price", "missing_valuation", "missing_institution", "missing_margin", "missing_foreign_shareholding"}:
        actions.append("ensure_complete_data")
        if not dry_run:
            if _ensure_complete_data is None:
                warnings.append("ensure_complete_data is not configured")
                failed_steps.append("ensure_complete_data")
            else:
                try:
                    _ensure_complete_data([clean], int(days), "watchlist bootstrap")
                except Exception as exc:
                    warnings.append(f"ensure_complete_data failed: {exc}")
                    failed_steps.append("ensure_complete_data")
    if run_tdcc and "missing_tdcc" in missing:
        actions.append("tdcc_equity_concentration")
        if not dry_run:
            try:
                result = update_tdcc_equity_concentration_for_codes([clean], source="tdcc", days=int(days), dry_run=False)
                if int(result.get("success") or 0) <= 0:
                    warnings.append("TDCC rows unavailable for this code")
                    failed_steps.append("tdcc_equity_concentration")
            except Exception as exc:
                warnings.append(f"tdcc update failed: {exc}")
                failed_steps.append("tdcc_equity_concentration")
    if run_daily_chip:
        actions.append("daily_chip_momentum")
        if not dry_run:
            try:
                result = refresh_daily_chip_momentum_for_codes([clean], mode=mode, dry_run=False)
                if int(result.get("write_count") or 0) <= 0:
                    warnings.append("daily chip momentum not written")
                    failed_steps.append("daily_chip_momentum")
            except Exception as exc:
                warnings.append(f"daily chip momentum failed: {exc}")
                failed_steps.append("daily_chip_momentum")
    after = before if dry_run else diagnose_watchlist_bootstrap_need(clean)
    return {
        "ok": not failed_steps or bool(after.get("ready")),
        "code": clean,
        "before": before,
        "after": after,
        "actions": actions,
        "missing": after.get("missing") or [],
        "warnings": warnings,
        "failed_steps": failed_steps,
        "writes_db": not dry_run,
    }


def bootstrap_watchlist_codes(codes: list[str], **kwargs: Any) -> dict[str, Any]:
    results = []
    for code in codes:
        results.append(bootstrap_watchlist_code(code, **kwargs))
    success = sum(1 for item in results if item.get("ok"))
    failed_count = len(results) - success
    clean_codes = [str(item.get("code") or "") for item in results if item.get("code")]
    return {
        "ok": failed_count == 0,
        "total": len(results),
        "success": success,
        "failed_count": failed_count,
        "codes": clean_codes,
        "per_code": results,
        "results": results,
    }


def enqueue_watchlist_bootstrap(code: str, *, days: int = 180) -> dict[str, Any]:
    clean = _clean_code(code)
    if not clean:
        return {"bootstrap_status": "manual_required", "message": "股票代號格式無法辨識。"}
    need = diagnose_watchlist_bootstrap_need(clean)
    if need.get("unsupported_reason"):
        return {"bootstrap_status": "unsupported", "message": "目前暫不支援此市場資料，先不顯示半成品分析。", "readiness": need}
    if need.get("ready"):
        return {"bootstrap_status": "already_ready", "message": "已加入自選股，完整分析資料已可使用。", "readiness": need}
    if _ensure_complete_data is None:
        return {"bootstrap_status": "manual_required", "message": "已加入自選股；完整分析需先執行資料補齊流程。", "readiness": need}
    with _bootstrap_lock:
        if clean in _bootstrap_queued or clean in _bootstrap_running:
            status = "running" if clean in _bootstrap_running else "queued"
            return {"bootstrap_status": status, "message": "已加入自選股，分析資料補齊中；稍後會自動重新檢查。", "readiness": need}
        _bootstrap_queued.add(clean)

    def worker() -> None:
        with _bootstrap_lock:
            _bootstrap_queued.discard(clean)
            _bootstrap_running.add(clean)
        try:
            bootstrap_watchlist_code(clean, days=days, mode="watchlist", dry_run=False)
        finally:
            with _bootstrap_lock:
                _bootstrap_running.discard(clean)

    threading.Thread(target=worker, daemon=True).start()
    return {"bootstrap_status": "queued", "message": "已加入自選股，分析資料補齊中；稍後會自動重新檢查。", "readiness": need}
