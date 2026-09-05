from __future__ import annotations

from contextlib import closing
from typing import Any

from core.utils import normalize_date
from repository.market_microstructure_repository import codes_with_price_volume_distribution
from repository.market_analytics_repository import active_stock_codes
from core.db import db
from repository.watchlist_repository import get_watchlist_codes
from services.price_volume_service import (
    capture_fugle_price_volume_snapshot,
    reconcile_price_volume_profile_for_code,
)


def recover_missing_price_volume_from_persisted_trades(
    trade_date: str,
    *,
    codes: list[str] | None = None,
    verified_official_trade_date: str | None = None,
) -> dict[str, Any]:
    """Rebuild missing or incompatible regular-session bins from exact trades.

    This is a lossless group-by of already captured Fugle time-and-sales rows.
    It does not estimate volume, direction, odd lots, or after-hours trades.
    Existing validated distributions are never replaced.  A provisional or
    rejected distribution is replaced only when its total differs from the
    complete persisted regular-session trade total by more than 5%.
    """

    target_date = normalize_date(trade_date)
    if not target_date:
        return {"ok": False, "status": "invalid_date", "recovered_count": 0, "results": []}
    selected_codes = sorted({str(code).zfill(4) for code in (codes or []) if str(code or "").strip()})
    delayed_terminal_pagination_allowed = bool(
        normalize_date(verified_official_trade_date) == target_date
    )
    with closing(db()) as conn:
        trade_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fugle_intraday_trades'"
        ).fetchone()
        capture_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fugle_intraday_capture_runs'"
        ).fetchone()
        if not trade_table or not capture_table:
            return {"ok": True, "status": "skipped_no_persisted_trades", "recovered_count": 0, "results": []}
        params: list[Any] = [target_date]
        code_filter = ""
        if selected_codes:
            placeholders = ",".join("?" for _ in selected_codes)
            code_filter = f" AND t.code IN ({placeholders})"
            params.extend(selected_codes)
        rows = conn.execute(
            f"""
            SELECT t.code,t.price,SUM(COALESCE(t.size,0)) AS volume_lots,
                   MAX(r.snapshot_time) AS capture_snapshot_time
            FROM fugle_intraday_trades t
            JOIN fugle_intraday_capture_runs r
              ON r.code=t.code AND r.trade_date=t.trade_date
             AND r.endpoint='trades' AND UPPER(COALESCE(r.source,''))='FUGLE'
            WHERE t.trade_date=? AND UPPER(COALESCE(t.source,''))='FUGLE'
              AND t.trade_time >= '09:00:00'
              AND t.trade_time <= '13:30:00.999999'
              AND r.normalized_row_count > 0
              AND r.stored_row_count = r.normalized_row_count
              AND UPPER(COALESCE(r.data_quality,'')) IN (
                    'SESSION_COMPLETE','PAGINATION_COMPLETE_SESSION_UNVERIFIED'
              )
              {code_filter}
            GROUP BY t.code,t.price
            HAVING SUM(COALESCE(t.size,0)) > 0
            ORDER BY t.code,t.price
            """,
            params,
        ).fetchall()
        existing_rows = conn.execute(
            """
            SELECT stock_id,
                   SUM(COALESCE(volume_lots,0)) AS volume_lots,
                   MAX(CASE WHEN UPPER(COALESCE(data_quality,source_quality,'')) IN ('VALIDATED','SCOPED_VALIDATED')
                            THEN 1 ELSE 0 END) AS has_validated
            FROM price_volume_distribution
            WHERE trade_date=?
            GROUP BY stock_id
            """,
            (target_date,),
        ).fetchall()
    all_profiles: dict[str, list[dict[str, Any]]] = {}
    capture_times: dict[str, str] = {}
    for row in rows:
        row_code = str(row["code"])
        all_profiles.setdefault(row_code, []).append(
            {"price": float(row["price"]), "volume": int(row["volume_lots"])}
        )
        if str(row["capture_snapshot_time"] or "").strip():
            capture_times[row_code] = str(row["capture_snapshot_time"]).strip()
    existing_by_code = {str(row["stock_id"]): dict(row) for row in existing_rows}
    profiles: dict[str, list[dict[str, Any]]] = {}
    recovery_reasons: dict[str, str] = {}
    for code, profile in all_profiles.items():
        persisted = existing_by_code.get(code)
        if not persisted:
            profiles[code] = profile
            recovery_reasons[code] = "missing_distribution"
            continue
        if bool(persisted.get("has_validated")):
            continue
        exact_lots = sum(int(row["volume"]) for row in profile)
        existing_lots = int(persisted.get("volume_lots") or 0)
        diff_pct = (
            abs(existing_lots - exact_lots) / exact_lots * 100.0
            if exact_lots > 0
            else 0.0
        )
        if diff_pct > 5.0:
            profiles[code] = profile
            recovery_reasons[code] = "trade_distribution_mismatch"
    results: list[dict[str, Any]] = []
    for code, profile in profiles.items():
        capture_kwargs: dict[str, Any] = {
            "expected_date": target_date,
            "verified_snapshot_time": capture_times.get(code),
        }
        if delayed_terminal_pagination_allowed:
            capture_kwargs["allow_delayed_terminal_pagination"] = True
        item = capture_fugle_price_volume_snapshot(
            code,
            {"date": target_date, "symbol": code, "data": profile},
            **capture_kwargs,
        )
        results.append({
            "code": code,
            "ok": bool(item.get("ok")),
            "status": item.get("status"),
            "recovery_reason": recovery_reasons.get(code),
            "price_level_count": item.get("price_level_count"),
            "writes_db": bool(item.get("writes_db")),
        })
    recovered = sum(1 for item in results if item["ok"] and item["writes_db"])
    replaced = sum(
        1
        for item in results
        if item["ok"]
        and item["writes_db"]
        and item.get("recovery_reason") == "trade_distribution_mismatch"
    )
    return {
        "ok": all(item["ok"] for item in results),
        "status": "ok" if results and recovered == len(results) else ("skipped_no_candidates" if not results else "partial"),
        "target_date": target_date,
        "candidate_count": len(profiles),
        "recovered_count": recovered,
        "replaced_incompatible_count": replaced,
        "results": results,
    }


def official_result_date(result: dict[str, Any]) -> str | None:
    verified_date = normalize_date(result.get("verified_trade_date"))
    if verified_date:
        return verified_date
    dates = {
        normalize_date(item.get("data_date"))
        for item in result.get("official_sources") or []
        if str(item.get("status") or "OK").upper() == "OK" and item.get("data_date")
    }
    dates.discard(None)
    if len(dates) == 1:
        return next(iter(dates))
    # A requested date is not publication evidence.  In particular, when all
    # official sources are SOURCE_DELAYED, never relabel that request as an
    # aligned official result date.  Callers backed by an exact-date persisted
    # batch must pass ``verified_trade_date`` explicitly.
    return None


def reconcile_watchlist_price_volume_after_official_update(
    result: dict[str, Any],
    *,
    required_days: int = 30,
) -> dict[str, Any]:
    """Reconcile only same-day captured watchlist distributions after official EOD."""

    target_date = official_result_date(result)
    if not target_date:
        return {
            "ok": False,
            "status": "source_delayed",
            "reason": "official TWSE/TPEx result does not have one aligned data date",
            "target_date": None,
            "candidate_count": 0,
            "validated_count": 0,
            "retryable_count": 0,
            "rejected_count": 0,
        }
    watchlist_codes = sorted({str(code).zfill(4) for code in get_watchlist_codes() if str(code or "").strip()})
    candidates = codes_with_price_volume_distribution(watchlist_codes, target_date)
    if not candidates:
        return {
            "ok": True,
            "status": "skipped_no_same_day_capture",
            "reason": "no watchlist price-volume distribution exists for the official data date",
            "target_date": target_date,
            "candidate_count": 0,
            "validated_count": 0,
            "retryable_count": 0,
            "rejected_count": 0,
            "results": [],
        }

    rows: list[dict[str, Any]] = []
    validated = 0
    scoped_validated = 0
    retryable = 0
    rejected = 0
    for code in candidates:
        try:
            item = reconcile_price_volume_profile_for_code(
                code,
                target_date,
                required_days=required_days,
            )
        except Exception:
            item = {
                "ok": False,
                "status": "failed",
                "quality_reason": "price-volume reconciliation raised an internal error",
                "writes_db": False,
            }
        status = str(item.get("status") or "unavailable")
        rows.append(
            {
                "code": code,
                "status": status,
                "ok": bool(item.get("ok")),
                "volume_diff_pct": item.get("volume_diff_pct"),
                "score_available": bool(item.get("score_available")),
                "writes_db": bool(item.get("writes_db")),
            }
        )
        if status == "validated":
            validated += 1
        elif status == "scoped_validated":
            scoped_validated += 1
        elif status in {"source_delayed", "awaiting_official_eod"}:
            retryable += 1
        else:
            rejected += 1
    complete = validated + scoped_validated == len(candidates)
    return {
        "ok": complete,
        "status": "ok" if complete else ("source_delayed" if retryable and not rejected else "partial"),
        "reason": "same-day captured distributions reconciled against official close volume" if complete else "one or more captured distributions were not validated",
        "target_date": target_date,
        "candidate_count": len(candidates),
        "validated_count": validated,
        "scoped_validated_count": scoped_validated,
        "capture_validated_count": validated + scoped_validated,
        "retryable_count": retryable,
        "rejected_count": rejected,
        "results": rows,
    }


def reconcile_full_market_price_volume_after_official_update(
    result: dict[str, Any],
    *,
    required_days: int = 30,
) -> dict[str, Any]:
    """Reconcile every same-day captured active listed/OTC stock distribution."""

    target_date = official_result_date(result)
    if not target_date:
        return {
            "ok": False,
            "status": "source_delayed",
            "reason": "official TWSE/TPEx result does not have one aligned data date",
            "target_date": None,
            "candidate_count": 0,
            "validated_count": 0,
            "retryable_count": 0,
            "rejected_count": 0,
        }
    with closing(db()) as conn:
        universe = active_stock_codes(conn)
        no_trade_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_no_trade_dates'"
        ).fetchone()
        no_trade_columns = ("code", "market", "reason", "source", "source_quality")
        raw_no_trade_rows = (
            conn.execute(
                "SELECT code,market,reason,source,source_quality FROM stock_no_trade_dates WHERE trade_date=?",
                (target_date,),
            ).fetchall()
            if no_trade_table
            else []
        )
        no_trade_rows = [
            dict(row) if hasattr(row, "keys") else dict(zip(no_trade_columns, row))
            for row in raw_no_trade_rows
        ]
        no_trade_codes = {str(row["code"]) for row in no_trade_rows}
        history_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_price'"
        ).fetchone()
        official_observed_codes = {
            str(row[0])
            for row in (
                conn.execute(
                    """
                    SELECT code
                    FROM history_price
                    WHERE date=?
                      AND LOWER(COALESCE(source_quality,''))='official'
                      AND COALESCE(volume,0)>0
                    """,
                    (target_date,),
                ).fetchall()
                if history_table
                else []
            )
        }
    universe_set = set(universe)
    official_observed_codes &= universe_set
    official_unobserved_codes = sorted(
        universe_set - official_observed_codes - no_trade_codes
    )
    required_universe = sorted(official_observed_codes - no_trade_codes)
    if universe and not official_observed_codes:
        return {
            "ok": False,
            "status": "source_delayed",
            "reason": "official result date exists but no official positive-volume history rows were persisted",
            "target_date": target_date,
            "universe_count": len(universe),
            "official_no_trade_count": len(no_trade_codes),
            "official_no_trade_codes": sorted(no_trade_codes),
            "official_no_trade_details": sorted(no_trade_rows, key=lambda row: str(row["code"])),
            "official_unobserved_count": len(official_unobserved_codes),
            "official_unobserved_codes": official_unobserved_codes,
            "required_trading_stock_count": 0,
            "candidate_count": 0,
            "validated_count": 0,
            "retryable_count": 0,
            "rejected_count": 0,
            "missing_capture_count": 0,
        }
    candidates = codes_with_price_volume_distribution(required_universe, target_date)
    if not candidates:
        return {
            "ok": not required_universe,
            "status": "skipped_no_required_trading_stocks" if not required_universe else "source_delayed",
            "reason": (
                "no positive-volume official stocks require supplemental capture"
                if not required_universe
                else "no full-market price-volume rows exist for the official data date"
            ),
            "target_date": target_date,
            "universe_count": len(universe),
            "official_no_trade_count": len(no_trade_codes),
            "official_no_trade_codes": sorted(no_trade_codes),
            "official_no_trade_details": sorted(no_trade_rows, key=lambda row: str(row["code"])),
            "official_unobserved_count": len(official_unobserved_codes),
            "official_unobserved_codes": official_unobserved_codes,
            "required_trading_stock_count": len(required_universe),
            "candidate_count": 0,
            "validated_count": 0,
            "retryable_count": 0,
            "rejected_count": 0,
            "missing_capture_count": len(required_universe),
            "operational_complete": not required_universe,
            "decision_ready_count": 0,
            "distribution_validated_count": 0,
            "scoped_validated_count": 0,
            "capture_validated_count": 0,
        }
    rows: list[dict[str, Any]] = []
    validated = 0
    scoped_validated = 0
    decision_ready = 0
    retryable = 0
    rejected = 0
    for code in candidates:
        try:
            item = reconcile_price_volume_profile_for_code(
                code,
                target_date,
                required_days=required_days,
            )
        except Exception:
            item = {
                "ok": False,
                "status": "failed",
                "quality_reason": "price-volume reconciliation raised an internal error",
                "writes_db": False,
            }
        status = str(item.get("status") or "unavailable")
        rows.append(
            {
                "code": code,
                "status": status,
                "ok": bool(item.get("ok")),
                "volume_diff_pct": item.get("volume_diff_pct"),
                "score_available": bool(item.get("score_available")),
                "score_status": item.get("score_status"),
                "score_quality_reason": item.get("score_quality_reason"),
                "coverage_days": item.get("coverage_days"),
                "required_days": item.get("required_days"),
                "writes_db": bool(item.get("writes_db")),
            }
        )
        if status == "validated":
            validated += 1
            if item.get("score_available"):
                decision_ready += 1
        elif status == "scoped_validated":
            scoped_validated += 1
        elif status in {"source_delayed", "awaiting_official_eod", "awaiting_complete_trade_capture"}:
            retryable += 1
        else:
            rejected += 1
    missing_capture_count = max(len(required_universe) - len(candidates), 0)
    capture_validated = validated + scoped_validated
    complete = capture_validated == len(required_universe)
    operational_complete = missing_capture_count == 0 and retryable == 0
    return {
        "ok": complete,
        "status": "ok" if complete else ("source_delayed" if retryable and not rejected else "partial"),
        "reason": (
            "all required stocks have same-day price-volume data validated for their declared scope"
            if complete
            else "full-market price-volume capture is incomplete or not fully validated"
        ),
        "target_date": target_date,
        "universe_count": len(universe),
        "official_no_trade_count": len(no_trade_codes),
        "official_no_trade_codes": sorted(no_trade_codes),
        "official_no_trade_details": sorted(no_trade_rows, key=lambda row: str(row["code"])),
        "official_unobserved_count": len(official_unobserved_codes),
        "official_unobserved_codes": official_unobserved_codes,
        "required_trading_stock_count": len(required_universe),
        "candidate_count": len(candidates),
        "missing_capture_count": missing_capture_count,
        "validated_count": validated,
        "scoped_validated_count": scoped_validated,
        "capture_validated_count": capture_validated,
        "retryable_count": retryable,
        "rejected_count": rejected,
        "operational_complete": operational_complete,
        "decision_ready_count": decision_ready,
        "distribution_validated_count": validated,
        "results": rows,
    }
