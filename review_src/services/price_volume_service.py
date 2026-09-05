from __future__ import annotations

import sqlite3
import threading
import time
import os
import json
import logging
import math
from contextlib import closing
from typing import Any

from adapter.fugle import extract_fugle_price_volume_rows
from core.config import clean_api_token, safe_error, token_decode_hint
from core.data_quality import (
    assess_persisted_price_volume_reconciliation,
    assess_post_close_snapshot,
)
from core.db import db
from core.fugle_intraday_schema import ensure_fugle_intraday_schema, upsert_fugle_price_volume
from core.status import get_status, set_status
from core.utils import fmt, normalize_date, now_tpe, parse_num
from repository.full_market_batch_repository import resolve_full_market_analysis_date

try:
    from price_volume import (
        SYSTEM_VERSION as PRICE_VOLUME_SYSTEM_VERSION,
        SOURCE_LEVELS as PRICE_VOLUME_SOURCE_LEVELS,
        canonical_hash as pv_canonical_hash,
        evaluate_profile as pv_evaluate_profile,
        merge_profiles as pv_merge_profiles,
        normalize_price as pv_normalize_price,
        normalize_profile_rows as pv_normalize_profile_rows,
        price_tick as pv_price_tick,
    )
except Exception:
    PRICE_VOLUME_SYSTEM_VERSION = "unavailable"
    PRICE_VOLUME_SOURCE_LEVELS = {}
    pv_canonical_hash = None
    pv_evaluate_profile = None
    pv_merge_profiles = None
    pv_normalize_price = None
    pv_normalize_profile_rows = None
    pv_price_tick = None

PRICE_VOLUME_USABLE_QUALITIES = {
    "high",
    "high_mixed",
    "medium_mixed",
}

PRICE_VOLUME_SCORE_QUALITY = "high"
PRICE_VOLUME_SCORE_STATUS = "ok"
PRICE_VOLUME_DISTRIBUTION_OK_QUALITIES = {"validated"}

_db_lock = threading.RLock()
_truststore_enabled = False
_truststore_error = ""


def configure_price_volume_service(*, db_lock: threading.RLock, truststore_enabled: bool = False, truststore_error: str = "") -> None:
    global _db_lock, _truststore_enabled, _truststore_error
    _db_lock = db_lock
    _truststore_enabled = bool(truststore_enabled)
    _truststore_error = str(truststore_error or "")

def price_volume_source_diagnostics(code: str | None = None) -> dict[str, Any]:
    code = str(code or "").zfill(4) if code else None
    with closing(db()) as conn:
        params: tuple[Any, ...] = (code,) if code else tuple()
        where = "WHERE code=?" if code else ""
        total = conn.execute(f"SELECT COUNT(*) c FROM price_volume_profile_daily {where}", params).fetchone()["c"]
        usable = conn.execute(
            f"SELECT COUNT(*) c FROM price_volume_profile_daily {where} "
            + ("AND" if where else "WHERE")
            + " quality IN ('high','high_mixed','medium_mixed')",
            params,
        ).fetchone()["c"]
        latest = conn.execute(
            f"SELECT date,code,source_name,quality,quality_reason,total_volume_shares,eod_volume_shares,volume_diff_pct,price_level_count "
            f"FROM price_volume_profile_daily {where} ORDER BY date DESC, fetched_at DESC LIMIT 1",
            params,
        ).fetchone()
        by_quality = [
            dict(r) for r in conn.execute(
                f"SELECT quality,COUNT(*) c FROM price_volume_profile_daily {where} GROUP BY quality",
                params,
            ).fetchall()
        ]
    status = get_status().get("price_volume") or {}
    key_hint = token_decode_hint(os.getenv("FUGLE_API_KEY", ""))
    source_hint = "Fugle key is configured, but no usable price-volume rows are available."
    if not clean_api_token(os.getenv("FUGLE_API_KEY", "")):
        source_hint = "Fugle API key is not configured. Add a valid key in .env."
    if status.get("message"):
        source_hint += f" Last status: {status.get('message')}"
    return {
        "code": code,
        "profile_rows": total,
        "usable_rows": usable,
        "latest": dict(latest) if latest else None,
        "quality_counts": by_quality,
        "status": status,
        "truststore_enabled": _truststore_enabled,
        "truststore_error": _truststore_error,
        "fugle_key_present": bool(clean_api_token(os.getenv("FUGLE_API_KEY", ""))),
        "fugle_key_hint": key_hint,
        "source_hint": source_hint,
    }


def latest_eod_volume(conn: sqlite3.Connection, code: str, d: str | None = None) -> tuple[float | None, str | None]:
    analysis_date = resolve_full_market_analysis_date(conn, d)
    if analysis_date:
        for table in ("history_price", "eod_price"):
            row = conn.execute(
                f"SELECT date,volume FROM {table} WHERE code=? AND date=? LIMIT 1",
                (code, analysis_date),
            ).fetchone()
            if row and row["volume"] is not None:
                return parse_num(row["volume"]), row["date"]
        return None, None
    return None, None


def cleanup_price_volume_history(conn: sqlite3.Connection, code: str, *, keep_rows: int = 200) -> int:
    """Keep recent price-volume profile/score rows per stock to bound DB growth."""
    code = str(code).zfill(4)
    keep_rows = max(1, int(keep_rows))
    removed = 0
    for table in ("price_volume_profile_daily", "price_volume_score_daily"):
        cutoff_row = conn.execute(
            f"""
            SELECT date FROM {table}
            WHERE code=?
            ORDER BY date DESC
            LIMIT 1 OFFSET ?
            """,
            (code, keep_rows - 1),
        ).fetchone()
        if not cutoff_row:
            continue
        cur = conn.execute(
            f"DELETE FROM {table} WHERE code=? AND date < ?",
            (code, cutoff_row["date"]),
        )
        removed += int(cur.rowcount or 0)
    return removed


def cleanup_price_volume_distribution(conn: sqlite3.Connection, code: str, *, keep_days: int = 200) -> int:
    """Keep recent true daily price-volume distribution rows per stock."""
    code = str(code).zfill(4)
    keep_days = max(1, int(keep_days))
    cutoff_row = conn.execute(
        """
        SELECT trade_date
        FROM (
            SELECT DISTINCT trade_date
            FROM price_volume_distribution
            WHERE stock_id=?
            ORDER BY trade_date DESC
            LIMIT 1 OFFSET ?
        )
        """,
        (code, keep_days - 1),
    ).fetchone()
    if not cutoff_row:
        return 0
    cur = conn.execute(
        "DELETE FROM price_volume_distribution WHERE stock_id=? AND trade_date < ?",
        (code, cutoff_row["trade_date"]),
    )
    return int(cur.rowcount or 0)


def unavailable_price_volume(status: str, quality: str, reason: str, *, required_days: int = 30) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "status": status,
        "quality": quality,
        "quality_reason": reason,
        "coverage_days": None,
        "required_days": required_days,
    }


def upsert_price_volume_profile_from_fugle(
    code: str,
    payload: dict[str, Any],
    *,
    expected_date: str | None = None,
) -> dict[str, Any]:
    if not pv_normalize_profile_rows or not pv_canonical_hash:
        return {"ok": False, "status": "module_unavailable", "reason": "price_volume module unavailable"}
    code = str(code).zfill(4)
    source_rows, payload_date = extract_fugle_price_volume_rows(payload)
    trade_date = payload_date or normalize_date(payload.get("date"))
    expected_date = normalize_date(expected_date) if expected_date else None
    if not trade_date:
        return {
            "ok": False,
            "status": "source_delayed",
            "quality": "source_delayed",
            "quality_reason": "Fugle payload has no explicit trade date; no row was written",
            "writes_db": False,
        }
    if expected_date and trade_date != expected_date:
        return {
            "ok": False,
            "status": "source_delayed",
            "quality": "source_delayed",
            "date": trade_date,
            "expected_date": expected_date,
            "quality_reason": f"Fugle payload date {trade_date} does not match verified session date {expected_date}",
            "writes_db": False,
        }
    profile, issues = pv_normalize_profile_rows(source_rows, volume_unit="lots")
    if not profile:
        return {
            "ok": False,
            "status": "source_parse_error",
            "quality": "invalid",
            "date": trade_date,
            "quality_reason": "Fugle payload contains no usable price/volume rows",
            "issues": issues,
            "writes_db": False,
        }
    source_hash = pv_canonical_hash({"source": "Fugle intraday volumes", "date": trade_date, "code": code, "profile": profile})
    total_volume = sum(float(r["volume"]) for r in profile)
    with _db_lock, closing(db()) as conn:
        eod_volume, _ = latest_eod_volume(conn, code, trade_date)
        diff_pct = None
        quality = "high"
        quality_reason = "Fugle intraday price-volume normalized to legal price bins and shares"
        if eod_volume and eod_volume > 0:
            diff_pct = abs(total_volume - float(eod_volume)) / float(eod_volume) * 100
            if diff_pct > 5:
                quality = "volume_mismatch"
                quality_reason = f"Fugle price-volume differs from same-day official volume by {diff_pct:.2f}%"
        else:
            quality = "source_delayed"
            quality_reason = "No same-day K-line volume available for reconciliation"
        if issues:
            quality_reason += "; " + "; ".join(issues[:3])
        existing = conn.execute(
            "SELECT quality FROM price_volume_profile_daily WHERE code=? AND date=?",
            (code, trade_date),
        ).fetchone()
        if (
            existing
            and str(existing["quality"] or "").lower() == "high"
            and quality != "high"
        ):
            return {
                "ok": True,
                "status": "preserved_validated",
                "date": trade_date,
                "quality": "high",
                "quality_reason": "Existing validated Fugle profile was preserved",
                "usable_for_score": True,
                "price_level_count": len(profile),
                "writes_db": False,
            }
        conn.execute("INSERT OR REPLACE INTO price_volume_profile_daily(date,code,source_level,source_name,source_hash,trade_scope,volume_unit,total_volume_shares,eod_volume_shares,volume_diff_pct,price_level_count,min_price,max_price,profile_json,quality,quality_reason,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade_date, code, int(PRICE_VOLUME_SOURCE_LEVELS.get("fugle_intraday_volumes", 3)),
                "Fugle intraday volumes", source_hash, "regular_intraday", "shares",
                total_volume, eod_volume, diff_pct, len(profile),
                min((r["price"] for r in profile), default=None),
                max((r["price"] for r in profile), default=None),
                json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
                quality, quality_reason, time.time(),
            ),
        )
        cleanup_price_volume_history(conn, code, keep_rows=200)
        conn.commit()
    return {
        "ok": quality in {"high", "high_mixed", "medium_mixed"},
        "date": trade_date,
        "quality": quality,
        "quality_reason": quality_reason,
        "volume_diff_pct": diff_pct,
        "usable_for_score": quality in {"high", "high_mixed", "medium_mixed"},
        "price_level_count": len(profile),
        "writes_db": True,
    }


def _pv_tick(price: float) -> float:
    if pv_price_tick:
        return float(pv_price_tick(float(price)))
    p = float(price)
    if p < 10:
        return 0.01
    if p < 50:
        return 0.05
    if p < 100:
        return 0.1
    if p < 500:
        return 0.5
    if p < 1000:
        return 1.0
    return 5.0


def _pv_norm(price: float) -> float:
    if pv_normalize_price:
        return float(pv_normalize_price(float(price)))
    tick = _pv_tick(float(price))
    return round(round(float(price) / tick) * tick, 4)


def reconstruct_price_volume_profile_from_ohlcv(row: sqlite3.Row | dict[str, Any]) -> list[dict[str, float]]:
    # Build a transparent, source-labeled volume profile from public OHLCV fallback.
    open_ = parse_num(row["open"])
    high = parse_num(row["high"])
    low = parse_num(row["low"])
    close = parse_num(row["close"])
    volume = parse_num(row["volume"])
    if close is None or volume is None or volume <= 0:
        return []
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close)
    if low is None:
        low = min(open_, close)
    high, low = max(float(high), float(low), float(open_), float(close)), min(float(high), float(low), float(open_), float(close))
    if high <= 0 or low <= 0 or high < low:
        return []
    if abs(high - low) < 1e-9:
        return [{"price": _pv_norm(close), "volume": float(volume)}]

    tick = _pv_tick((high + low) / 2)
    raw_steps = max(1, int(round((high - low) / tick)))
    max_bins = 401
    if raw_steps > max_bins:
        step = (high - low) / max_bins
        n = max_bins + 1
    else:
        step = tick
        n = raw_steps + 1
    typical = (high + low + close) / 3
    body_low, body_high = sorted([float(open_), float(close)])
    width = max(high - low, tick)
    bins: dict[float, float] = {}
    for i in range(n):
        price = low + step * i
        distance = abs(price - typical) / width
        weight = max(0.08, 1.0 - distance * 1.25)
        if body_low <= price <= body_high:
            weight += 0.45
        if abs(price - close) <= max(tick, width * 0.08):
            weight += 0.75
        if abs(price - open_) <= max(tick, width * 0.06):
            weight += 0.25
        norm_price = _pv_norm(price)
        bins[norm_price] = bins.get(norm_price, 0.0) + weight
    total_weight = sum(bins.values())
    if total_weight <= 0:
        return []
    return [{"price": float(k), "volume": float(v) / total_weight * float(volume)} for k, v in sorted(bins.items()) if v > 0]


def ensure_ohlcv_reconstructed_price_volume(conn: sqlite3.Connection, code: str, *, limit: int = 240) -> int:
    if not pv_normalize_profile_rows or not pv_canonical_hash:
        return 0
    code = str(code).zfill(4)
    rows = list(conn.execute("SELECT date,code,open,high,low,close,volume,source FROM history_price WHERE code=? AND close IS NOT NULL AND volume IS NOT NULL AND volume > 0 ORDER BY date DESC LIMIT ?",
        (code, int(limit)),
    ).fetchall())
    inserted = 0
    now_ts = time.time()
    for row in rows:
        existing = conn.execute(
            "SELECT quality FROM price_volume_profile_daily WHERE code=? AND date=?",
            (code, row["date"]),
        ).fetchone()
        if existing and str(existing["quality"] or "") in PRICE_VOLUME_USABLE_QUALITIES and str(existing["quality"]) != "medium_ohlcv_reconstructed":
            continue
        raw_profile = reconstruct_price_volume_profile_from_ohlcv(row)
        profile, issues = pv_normalize_profile_rows(raw_profile, volume_unit="shares")
        if not profile:
            continue
        total_volume = sum(float(r["volume"]) for r in profile)
        eod_volume, _ = latest_eod_volume(conn, code, row["date"])
        diff_pct = abs(total_volume - float(eod_volume)) / float(eod_volume) * 100 if eod_volume else None
        source_name = "OHLCV reconstructed volume profile"
        source_hash = pv_canonical_hash({
            "source": source_name,
            "date": row["date"],
            "code": code,
            "ohlcv": {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "source": row["source"],
            },
            "profile": profile,
        })
        reason = "Public OHLCV reconstructed volume profile; reconciled to daily volume, not tick-level volume-at-price"
        if issues:
            reason += "; " + "; ".join(issues[:3])
        conn.execute("INSERT OR REPLACE INTO price_volume_profile_daily(date,code,source_level,source_name,source_hash,trade_scope,volume_unit,total_volume_shares,eod_volume_shares,volume_diff_pct,price_level_count,min_price,max_price,profile_json,quality,quality_reason,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["date"], code, int(PRICE_VOLUME_SOURCE_LEVELS.get("ohlcv_reconstructed", PRICE_VOLUME_SOURCE_LEVELS.get("ohlcv_estimated", 5))),
                source_name, source_hash, "regular_ohlcv_reconstructed", "shares",
                total_volume, eod_volume, diff_pct, len(profile),
                min((r["price"] for r in profile), default=None),
                max((r["price"] for r in profile), default=None),
                json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
                "medium_ohlcv_reconstructed", reason, now_ts,
            ),
        )
        inserted += 1
    if inserted:
        cleanup_price_volume_history(conn, code, keep_rows=200)
        conn.commit()
        set_status("price_volume", "fresh", f"OHLCV reconstructed price-volume filled {code}: {inserted} rows")
    return inserted


def price_volume_profiles_for_code(conn: sqlite3.Connection, code: str, limit: int = 240) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM price_volume_profile_daily WHERE code=? AND quality IN ('high','high_mixed','medium_mixed') AND UPPER(source_name) LIKE '%FUGLE%' ORDER BY date DESC LIMIT ?",
        (str(code).zfill(4), int(limit)),
    ).fetchall())


def _upsert_price_volume_score_outcome(
    conn: sqlite3.Connection,
    *,
    date: str,
    code: str,
    close: float,
    source: sqlite3.Row | dict[str, Any],
    outcome: dict[str, Any],
) -> None:
    """Persist a ready score or an explicit current-day non-ready outcome."""

    source_row = dict(source)
    values: dict[str, Any] = {
        "date": date,
        "code": code,
        "system_version": PRICE_VOLUME_SYSTEM_VERSION,
        "close": close,
        "source_name": source_row.get("source_name"),
        "source_level": source_row.get("source_level"),
        "source_hash": source_row.get("source_hash"),
        "coverage_days": outcome.get("coverage_days"),
        "required_days": outcome.get("required_days"),
        "quality": outcome.get("quality"),
        "quality_reason": outcome.get("quality_reason"),
        "status": outcome.get("status"),
        "main_peak_price": outcome.get("main_peak_price"),
        "main_peak_distance_pct": outcome.get("main_peak_distance_pct"),
        "overhead_pressure_pct": outcome.get("overhead_pressure_pct"),
        "top3_concentration_pct": outcome.get("top3_concentration_pct"),
        "recent_peak_strength_pct": outcome.get("recent_peak_strength_pct"),
        "behavior_state": outcome.get("behavior_state"),
        "weighted_cost": outcome.get("weighted_cost"),
        "cost_state": outcome.get("cost_state"),
        "position_score": outcome.get("position_score"),
        "pressure_score": outcome.get("pressure_score"),
        "concentration_score": outcome.get("concentration_score"),
        "recent_peak_score": outcome.get("recent_peak_score"),
        "behavior_score": outcome.get("behavior_score"),
        "total_score": outcome.get("total_score"),
        "grade": outcome.get("grade"),
        "support_json": json.dumps(outcome.get("support_peaks") or [], ensure_ascii=False),
        "resistance_json": json.dumps(outcome.get("resistance_peaks") or [], ensure_ascii=False),
        "event_types": "",
        "created_at": time.time(),
    }
    available_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(price_volume_score_daily)").fetchall()
    }
    columns = [column for column in values if column in available_columns]
    if not {"date", "code", "status", "quality"}.issubset(columns):
        raise RuntimeError("price_volume_score_daily schema is missing required outcome columns")
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO price_volume_score_daily({','.join(columns)}) VALUES({placeholders})",
        tuple(values[column] for column in columns),
    )


def compute_price_volume_score_for_code(
    code: str,
    *,
    required_days: int = 30,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    code = str(code).zfill(4)
    if not pv_merge_profiles or not pv_evaluate_profile:
        return unavailable_price_volume("module_unavailable", "invalid", "price_volume module unavailable", required_days=required_days)
    with _db_lock, closing(db()) as conn:
        analysis_date = resolve_full_market_analysis_date(conn, as_of_date)
        if not analysis_date:
            return unavailable_price_volume(
                "unavailable",
                "missing",
                "published full-market analysis date is unavailable",
                required_days=required_days,
            )
        hist = list(conn.execute(
            "SELECT date,close FROM history_price WHERE code=? AND date<=? ORDER BY date DESC LIMIT 260",
            (code, analysis_date),
        ).fetchall())
        if not hist:
            return unavailable_price_volume("kline_missing", "invalid", "missing K-line close data; cannot calculate price-volume profile", required_days=required_days)
        latest_date = str(hist[0]["date"])
        close = parse_num(hist[0]["close"])
        if close is None:
            return unavailable_price_volume("price_missing", "invalid", "missing close price", required_days=required_days)
        rows = list(conn.execute(
            """
            SELECT * FROM price_volume_profile_daily
            WHERE code=?
              AND date<=?
              AND quality IN ('high','high_mixed','medium_mixed')
              AND UPPER(source_name) LIKE '%FUGLE%'
            ORDER BY date DESC
            LIMIT 240
            """,
            (code, analysis_date),
        ).fetchall())
        if not rows:
            diag = price_volume_source_diagnostics(code)
            reason = "no usable price-volume profile rows"
            status_name = "source_permission_required"
            quality_name = "missing"
            if diag.get("profile_rows") and not diag.get("usable_rows"):
                latest = diag.get("latest") or {}
                latest_quality = str(latest.get("quality") or "")
                reason = f"profile rows exist but quality is unusable: {latest_quality} - {latest.get('quality_reason')}"
                if latest_quality:
                    status_name = latest_quality
                    quality_name = latest_quality
            elif (diag.get("status") or {}).get("message") and code in str((diag.get("status") or {}).get("message")):
                reason = str((diag.get("status") or {}).get("message"))
            out = unavailable_price_volume(status_name, quality_name, reason, required_days=required_days)
            out["source_hint"] = diag.get("source_hint")
            out["profile_rows"] = diag.get("profile_rows")
            out["usable_rows"] = diag.get("usable_rows")
            return out
        latest_profile_date = str(rows[0]["date"] or "")
        if latest_profile_date != latest_date:
            return unavailable_price_volume(
                "stale",
                "stale",
                f"latest price-volume profile is {latest_profile_date or 'unknown'}, "
                f"but latest K-line is {latest_date}",
                required_days=required_days,
            )
        selected_rows = rows[:required_days]
        selected_sources = {_source_key(r["source_name"]) for r in selected_rows}
        if len(selected_sources) != 1 or "" in selected_sources:
            return unavailable_price_volume(
                "source_mismatch",
                "invalid",
                "price-volume score input contains mixed or missing sources",
                required_days=required_days,
            )
        profiles = []
        source_mix: dict[str, int] = {}
        for r in selected_rows:
            try:
                profiles.append(json.loads(r["profile_json"] or "[]"))
                src = _source_key(r["source_name"])
                source_mix[src] = source_mix.get(src, 0) + 1
            except Exception:
                continue
        merged = pv_merge_profiles(list(reversed(profiles)))
        long_profiles = []
        for r in rows:
            try:
                long_profiles.append(json.loads(r["profile_json"] or "[]"))
            except Exception as exc:
                logging.warning("price-volume profile JSON parse failed for %s %s: %s", code, r["date"], safe_error(exc))
        long_profile = pv_merge_profiles(list(reversed(long_profiles))) if long_profiles else None
        closes = [parse_num(r["close"]) for r in reversed(hist[:10])]
        quality_base = "high"
        result = pv_evaluate_profile(
            profile=merged,
            close=float(close),
            closes=[float(x) for x in closes if x is not None],
            short_profile=merged,
            long_profile=long_profile,
            coverage_days=len(profiles),
            required_days=required_days,
            source_mix=source_mix,
            quality_base=quality_base,
        )
        if result.get("available"):
            result["status"] = PRICE_VOLUME_SCORE_STATUS
        _upsert_price_volume_score_outcome(
            conn,
            date=latest_date,
            code=code,
            close=float(close),
            source=rows[0],
            outcome=result,
        )
        cleanup_price_volume_history(conn, code, keep_rows=200)
        conn.commit()
        return result


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _source_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _is_fugle_source(value: Any) -> bool:
    return "fugle" in _source_key(value)


def _latest_history_context(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    analysis_date = resolve_full_market_analysis_date(conn)
    if not analysis_date:
        return None
    row = conn.execute(
        """
        SELECT date, close, source
        FROM history_price
        WHERE code=? AND date<=? AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT 1
        """,
        (code, analysis_date),
    ).fetchone()
    return dict(row) if row else None


def _read_true_distribution_for_date(
    conn: sqlite3.Connection,
    code: str,
    expected_date: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    exact = conn.execute(
        "SELECT 1 FROM price_volume_distribution WHERE stock_id=? AND trade_date=? LIMIT 1",
        (code, expected_date),
    ).fetchone()
    if not exact:
        latest = conn.execute(
            """
            SELECT MAX(trade_date) AS trade_date
            FROM price_volume_distribution
            WHERE stock_id=? AND trade_date<=?
            """,
            (code, expected_date),
        ).fetchone()
        distribution_date = str(latest["trade_date"] or "") if latest else ""
        if distribution_date:
            issue = unavailable_price_volume(
                "stale",
                "stale",
                f"true price-volume distribution is {distribution_date}, "
                f"but latest published K-line is {expected_date}",
            )
            issue["data_date"] = expected_date
            issue["distribution_date"] = distribution_date
            return None, issue
        return None, unavailable_price_volume(
            "unavailable",
            "missing",
            f"no true price-volume distribution for latest K-line {expected_date}",
        )
    distribution_date = expected_date

    columns = _table_columns(conn, "price_volume_distribution")
    source_expr = "source" if "source" in columns else "NULL AS source"
    source_quality_expr = "source_quality" if "source_quality" in columns else "NULL AS source_quality"
    data_quality_expr = "data_quality" if "data_quality" in columns else "NULL AS data_quality"
    rows = list(conn.execute(
        f"""
        SELECT price, volume_lots, volume_shares, total_volume_lots, snapshot_time,
               {source_expr}, {source_quality_expr}, {data_quality_expr}
        FROM price_volume_distribution
        WHERE stock_id=? AND trade_date=?
        ORDER BY price DESC
        """,
        (code, expected_date),
    ).fetchall())

    source_values = {_source_key(row["source"]) for row in rows}
    if len(source_values) != 1 or "" in source_values:
        issue = unavailable_price_volume(
            "source_mismatch",
            "invalid",
            "same-day true price-volume rows contain mixed or missing sources",
        )
        issue["data_date"] = expected_date
        return None, issue

    source_names = {str(row["source"] or "").strip() for row in rows}
    source_qualities = {
        _source_key(row["source_quality"])
        for row in rows
        if str(row["source_quality"] or "").strip()
    }
    data_qualities = {
        _source_key(row["data_quality"])
        for row in rows
        if str(row["data_quality"] or "").strip()
    }
    if len(source_qualities) > 1 or len(data_qualities) > 1:
        issue = unavailable_price_volume(
            "source_mismatch",
            "invalid",
            "same-day true price-volume rows contain mixed quality states",
        )
        issue["data_date"] = expected_date
        return None, issue
    known_invalid_qualities = {"volume mismatch", "invalid", "source mismatch"}
    invalid_quality = next(
        (
            value
            for value in source_qualities | data_qualities
            if value in known_invalid_qualities
        ),
        None,
    )
    if invalid_quality:
        issue = unavailable_price_volume(
            "volume_mismatch" if invalid_quality == "volume mismatch" else "invalid",
            "invalid",
            "same-day Fugle price-volume did not pass official volume reconciliation",
        )
        issue["data_date"] = expected_date
        issue["distribution_date"] = distribution_date
        return None, issue

    parsed: list[dict[str, Any]] = []
    total_lots = 0
    weighted = 0.0
    max_lots = 0
    snapshot_times = {
        str(row["snapshot_time"] or "").strip()
        for row in rows
        if str(row["snapshot_time"] or "").strip()
    }
    for row in rows:
        price = parse_num(row["price"])
        lots = parse_num(row["volume_lots"])
        if price is None or lots is None or lots <= 0:
            continue
        lot_i = int(round(float(lots)))
        total_lots += lot_i
        weighted += float(price) * lot_i
        max_lots = max(max_lots, lot_i)
        parsed.append({
            "price": fmt(price),
            "raw_price": float(price),
            "volume_lots": f"{lot_i:,}",
            "raw_volume_lots": lot_i,
        })
    if not parsed or total_lots <= 0:
        issue = unavailable_price_volume(
            "source_parse_error",
            "invalid",
            f"true price-volume rows for {expected_date} contain no usable price/volume values",
        )
        issue["data_date"] = expected_date
        return None, issue

    for item in parsed:
        item["bar_pct"] = round(float(item["raw_volume_lots"]) / float(max_lots) * 100, 2) if max_lots else 0
        item.pop("raw_price", None)
        item.pop("raw_volume_lots", None)
    dense = sorted(
        parsed,
        key=lambda item: parse_num(str(item.get("volume_lots", "")).replace(",", "")) or 0,
        reverse=True,
    )[:5]
    vwap = weighted / total_lots if total_lots else None
    source_name = next(iter(source_names))
    source_quality = next(iter(source_qualities), None)
    data_quality = next(iter(data_qualities), None)
    snapshot_quality = assess_post_close_snapshot(expected_date, snapshot_times)
    capture_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fugle_intraday_capture_runs'"
    ).fetchone()
    historical_complete_capture = False
    if capture_table and snapshot_quality.get("captured_at"):
        capture_run = conn.execute(
            """
            SELECT capture_complete,data_quality,latest_trade_time,
                   normalized_row_count,stored_row_count
            FROM fugle_intraday_capture_runs
            WHERE code=? AND trade_date=? AND endpoint='trades' AND source='FUGLE'
            LIMIT 1
            """,
            (code, expected_date),
        ).fetchone()
        historical_complete_capture = bool(
            capture_run
            and int(capture_run["capture_complete"] or 0) == 1
            and _source_key(capture_run["data_quality"]) == "session complete"
            and str(capture_run["latest_trade_time"] or "") >= "13:30:00"
            and int(capture_run["normalized_row_count"] or 0) > 0
            and int(capture_run["stored_row_count"] or 0) == int(capture_run["normalized_row_count"] or 0)
            and str(snapshot_quality.get("captured_at") or "")[:10] > expected_date
        )
    distribution_validated = bool(
        {value for value in (source_quality, data_quality) if value} == {"validated"}
        and (snapshot_quality.get("ready") or historical_complete_capture)
    )
    if distribution_validated:
        summary_text = f"已核對分價量 {expected_date}｜總量 {total_lots:,} 張｜成交重心 {fmt(vwap)}"
    else:
        snapshot_label = snapshot_quality.get("captured_at") or "時間未提供"
        summary_text = (
            f"盤中分價量快照 {expected_date} {snapshot_label}｜總量 {total_lots:,} 張｜"
            f"成交重心 {fmt(vwap)}｜尚未通過官方盤後量核對"
        )
    return {
        "is_true_price_volume": True,
        "distribution_validated": distribution_validated,
        "profile_rows": parsed,
        "trade_date": expected_date,
        "data_date": expected_date,
        "distribution_source": source_name,
        "distribution_source_quality": source_quality,
        "distribution_data_quality": data_quality,
        "snapshot_time": snapshot_quality.get("captured_at"),
        "session_complete": bool(snapshot_quality.get("ready") or historical_complete_capture),
        "snapshot_quality": snapshot_quality,
        "total_volume_lots": f"{total_lots:,}",
        "weighted_price": fmt(vwap),
        "dense_price_levels": [
            {"price": item.get("price"), "volume_lots": item.get("volume_lots")}
            for item in dense
        ],
        "summary_text": summary_text,
    }, None


def capture_fugle_price_volume_snapshot(
    code: str,
    payload: dict[str, Any],
    *,
    expected_date: str,
    verified_snapshot_time: str | None = None,
    allow_delayed_terminal_pagination: bool = False,
) -> dict[str, Any]:
    """Persist a real, explicitly dated Fugle intraday distribution as provisional.

    This phase deliberately does not claim EOD completeness or calculate a
    score.  Reconciliation happens only after an official same-day volume is
    available.
    """

    if not pv_normalize_profile_rows:
        return {
            "ok": False,
            "status": "module_unavailable",
            "quality": "invalid",
            "quality_reason": "price_volume module unavailable",
            "writes_db": False,
        }
    code = str(code).zfill(4)
    expected = normalize_date(expected_date)
    source_rows, payload_date = extract_fugle_price_volume_rows(payload)
    trade_date = normalize_date(payload_date)
    if not expected or not trade_date or trade_date != expected:
        return {
            "ok": False,
            "status": "source_delayed",
            "quality": "source_delayed",
            "expected_date": expected,
            "payload_date": trade_date,
            "quality_reason": "Fugle payload must contain the verified current-session trade date",
            "writes_db": False,
        }
    profile_shares, issues = pv_normalize_profile_rows(source_rows, volume_unit="lots")
    if not profile_shares:
        return {
            "ok": False,
            "status": "source_parse_error",
            "quality": "invalid",
            "quality_reason": "Fugle payload contains no usable price-volume rows",
            "issues": issues,
            "writes_db": False,
        }
    total_lots = sum(int(round(float(row["volume"]) / 1000.0)) for row in profile_shares)
    direction_by_price: dict[float, tuple[int | None, int | None]] = {}
    direction_complete = True
    for source_row in source_rows:
        price = parse_num(source_row.get("price"))
        bid = parse_num(source_row.get("volumeAtBid"))
        ask = parse_num(source_row.get("volumeAtAsk"))
        if price is None:
            continue
        bid_value = int(round(float(bid))) if bid is not None and bid >= 0 else None
        ask_value = int(round(float(ask))) if ask is not None and ask >= 0 else None
        if bid_value is None or ask_value is None:
            direction_complete = False
        direction_by_price[float(price)] = (bid_value, ask_value)
    direction_complete = bool(
        direction_complete
        and profile_shares
        and all(
            (pair := direction_by_price.get(float(row["price"]))) is not None
            and pair[0] is not None
            and pair[1] is not None
            and int(pair[0]) + int(pair[1]) <= int(round(float(row["volume"]) / 1000.0))
            for row in profile_shares
        )
    )
    fetched_at = time.time()
    snapshot_time = str(verified_snapshot_time or now_tpe().isoformat(timespec="seconds")).strip()
    if verified_snapshot_time:
        persisted_snapshot_quality = assess_post_close_snapshot(trade_date, {snapshot_time})
        delayed_terminal_pagination_ready = bool(
            allow_delayed_terminal_pagination
            and bool(persisted_snapshot_quality.get("reasons"))
            and set(persisted_snapshot_quality.get("reasons") or []).issubset(
                {"snapshot_date_mismatch", "snapshot_not_after_market_close"}
            )
            and "snapshot_date_mismatch"
            in set(persisted_snapshot_quality.get("reasons") or [])
            and str(persisted_snapshot_quality.get("captured_at") or "")[:10]
            > trade_date
        )
        if not (
            persisted_snapshot_quality.get("ready")
            or delayed_terminal_pagination_ready
        ):
            return {
                "ok": False,
                "status": "source_delayed",
                "quality": "source_delayed",
                "expected_date": expected,
                "snapshot_time": snapshot_time,
                "snapshot_quality": persisted_snapshot_quality,
                "quality_reason": "persisted capture time is not a verified same-day post-close snapshot",
                "writes_db": False,
            }
    with _db_lock, closing(db()) as conn:
        ensure_fugle_intraday_schema(conn)
        existing = conn.execute(
            """
            SELECT DISTINCT UPPER(COALESCE(source,'')) AS source,
                            UPPER(COALESCE(data_quality, source_quality, '')) AS quality
            FROM price_volume_distribution
            WHERE stock_id=? AND trade_date=?
            """,
            (code, trade_date),
        ).fetchall()
        if any(str(row["source"] or "") not in {"", "FUGLE"} for row in existing):
            return {
                "ok": False,
                "status": "source_mismatch",
                "quality": "invalid",
                "quality_reason": "same-day price-volume rows already belong to another source",
                "date": trade_date,
                "writes_db": False,
            }
        if any(str(row["quality"] or "") == "VALIDATED" for row in existing):
            return {
                "ok": True,
                "status": "preserved_validated",
                "quality": "validated",
                "date": trade_date,
                "price_level_count": len(profile_shares),
                "writes_db": False,
            }
        conn.execute(
            "DELETE FROM price_volume_distribution WHERE stock_id=? AND trade_date=?",
            (code, trade_date),
        )
        written = 0
        for row in profile_shares:
            lots = int(round(float(row["volume"]) / 1000.0))
            if lots <= 0:
                continue
            bid_lots, ask_lots = direction_by_price.get(float(row["price"]), (None, None))
            row_direction_ready = bool(
                bid_lots is not None
                and ask_lots is not None
                and bid_lots + ask_lots <= lots
            )
            written += int(upsert_fugle_price_volume(
                conn,
                {
                    "code": code,
                    "trade_date": trade_date,
                    "price": float(row["price"]),
                    "volume_lots": lots,
                    "total_volume_lots": total_lots,
                    "volume_at_bid": bid_lots if row_direction_ready else None,
                    "volume_at_ask": ask_lots if row_direction_ready else None,
                    "neutral_volume_lots": max(lots - int(bid_lots or 0) - int(ask_lots or 0), 0),
                    "data_quality": "INTRADAY_SNAPSHOT" if direction_complete else "PARTIAL",
                    "fetched_at": fetched_at,
                    "snapshot_time": snapshot_time,
                },
                ensure_schema=False,
            ))
        if written <= 0:
            conn.rollback()
            return {
                "ok": False,
                "status": "source_parse_error",
                "quality": "invalid",
                "quality_reason": "No normalized Fugle distribution rows were written",
                "writes_db": False,
            }
        conn.commit()
    return {
        "ok": True,
        "status": "captured",
        "quality": "intraday_snapshot" if direction_complete else "partial",
        "quality_reason": "Real Fugle intraday snapshot; awaiting official EOD volume reconciliation",
        "date": trade_date,
        "price_level_count": written,
        "total_volume_lots": total_lots,
        "snapshot_time": snapshot_time,
        "direction_available": direction_complete,
        "issues": issues,
        "writes_db": True,
    }


def _regular_session_trade_lots(
    conn: sqlite3.Connection,
    code: str,
    trade_date: str,
) -> float | None:
    """Return persisted Fugle regular-session lots for point-in-time reconciliation.

    Fugle's default price-volume endpoint is regular-board data, while the
    official TWSE/TPEx daily total can additionally contain odd-lot and
    after-hours trades.  Reconciliation compares the distribution with both
    this regular-session subtotal and the provider's complete captured subtotal;
    the closest exact scope is recorded on the validated profile.
    """

    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fugle_intraday_trades'"
    ).fetchone()
    if not table:
        return None
    row = conn.execute(
        """
        SELECT COUNT(*) AS row_count, SUM(COALESCE(size, 0)) AS volume_lots
        FROM fugle_intraday_trades
        WHERE code=? AND trade_date=? AND UPPER(COALESCE(source,''))='FUGLE'
          AND trade_time >= '09:00:00'
          AND trade_time <= '13:30:00.999999'
        """,
        (code, trade_date),
    ).fetchone()
    if not row or int(row["row_count"] or 0) <= 0:
        return None
    value = parse_num(row["volume_lots"])
    return float(value) if value is not None and float(value) > 0 else None


def reconcile_price_volume_profile_for_code(
    code: str,
    trade_date: str | None = None,
    *,
    required_days: int = 30,
) -> dict[str, Any]:
    """Validate a captured Fugle snapshot against official EOD volume, then score."""

    if not pv_canonical_hash:
        return unavailable_price_volume(
            "module_unavailable", "invalid", "price_volume module unavailable", required_days=required_days
        )
    code = str(code).zfill(4)
    requested_date = normalize_date(trade_date) if trade_date else None
    with _db_lock, closing(db()) as conn:
        target_analysis_date = resolve_full_market_analysis_date(conn, requested_date)
        if target_analysis_date:
            history = conn.execute(
                "SELECT date,close,volume,source,source_quality FROM history_price WHERE code=? AND date=? LIMIT 1",
                (code, target_analysis_date),
            ).fetchone()
        else:
            history = None
        if not history:
            return unavailable_price_volume(
                "source_delayed", "missing", "official same-day OHLCV is unavailable", required_days=required_days
            )
        target_date = str(history["date"])
        history_source = str(history["source"] or "").strip().upper()
        history_quality = str(history["source_quality"] or "").strip().upper() if "source_quality" in history.keys() else ""
        official_source = "TWSE" in history_source or "TPEX" in history_source
        official_quality = history_quality in {"OFFICIAL", "OK", "HIGH"}
        if not official_source or not official_quality:
            return {
                **unavailable_price_volume(
                    "source_delayed",
                    "missing",
                    "same-day official TWSE/TPEx EOD row is not available yet",
                    required_days=required_days,
                ),
                "ok": False,
                "status": "awaiting_official_eod",
                "date": target_date,
                "writes_db": False,
            }
        official_volume = parse_num(history["volume"])
        if official_volume is None or official_volume <= 0:
            return unavailable_price_volume(
                "source_delayed", "missing", "official same-day volume is unavailable", required_days=required_days
            )
        rows = list(conn.execute(
            """
            SELECT price,volume_lots,volume_shares,source,data_quality,source_quality,snapshot_time
            FROM price_volume_distribution
            WHERE stock_id=? AND trade_date=?
            ORDER BY price
            """,
            (code, target_date),
        ).fetchall())
        if not rows:
            return unavailable_price_volume(
                "unavailable", "missing", f"no Fugle distribution captured for {target_date}", required_days=required_days
            )
        snapshot_quality = assess_post_close_snapshot(
            target_date,
            {
                str(row["snapshot_time"] or "").strip()
                for row in rows
                if str(row["snapshot_time"] or "").strip()
            },
        )
        capture_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fugle_intraday_capture_runs'"
        ).fetchone()
        capture_run = None
        if capture_table:
            capture_run = conn.execute(
                """
                SELECT capture_complete,data_quality,latest_trade_time,
                       captured_volume_lots,normalized_row_count,stored_row_count
                FROM fugle_intraday_capture_runs
                WHERE code=? AND trade_date=? AND endpoint='trades' AND source='FUGLE'
                LIMIT 1
                """,
                (code, target_date),
            ).fetchone()
        capture_rows_complete = bool(
            capture_run is not None
            and int(capture_run["normalized_row_count"] or 0) > 0
            and int(capture_run["stored_row_count"] or 0) == int(capture_run["normalized_row_count"] or 0)
        )
        historical_snapshot_evidence = bool(
            set(snapshot_quality.get("reasons") or []).issubset(
                {"snapshot_date_mismatch", "snapshot_not_after_market_close"}
            )
            and bool(snapshot_quality.get("reasons"))
            and str(snapshot_quality.get("captured_at") or "")[:10] > target_date
        )
        capture_complete = bool(
            capture_rows_complete
            and int(capture_run["capture_complete"] or 0) == 1
            and _source_key(capture_run["data_quality"]) == "session complete"
        )
        post_close_pagination_candidate = bool(
            capture_rows_complete
            and int(capture_run["capture_complete"] or 0) == 0
            and _source_key(capture_run["data_quality"]) == "pagination complete session unverified"
            and (snapshot_quality.get("ready") or historical_snapshot_evidence)
        )
        historical_complete_capture = bool(
            capture_complete
            and historical_snapshot_evidence
        )
        if not snapshot_quality.get("ready") and not (
            historical_complete_capture or post_close_pagination_candidate
        ):
            return {
                **unavailable_price_volume(
                    "source_delayed",
                    "source_delayed",
                    "captured distribution is not a single post-close snapshot",
                    required_days=required_days,
                ),
                "ok": False,
                "status": "awaiting_post_close_snapshot",
                "date": target_date,
                "snapshot_quality": snapshot_quality,
                "writes_db": False,
            }
        if not (capture_complete or post_close_pagination_candidate):
            return {
                **unavailable_price_volume(
                    "source_delayed",
                    "source_delayed",
                    "full-day paginated trades have not reached a complete post-close evidence state",
                    required_days=required_days,
                ),
                "ok": False,
                "status": "awaiting_complete_trade_capture",
                "date": target_date,
                "writes_db": False,
            }
        sources = {_source_key(row["source"]) for row in rows}
        if sources != {"fugle"}:
            return unavailable_price_volume(
                "source_mismatch", "invalid", "captured distribution is not single-source Fugle", required_days=required_days
            )
        profile: list[dict[str, float]] = []
        for row in rows:
            price = parse_num(row["price"])
            shares = parse_num(row["volume_shares"])
            if shares is None:
                lots = parse_num(row["volume_lots"])
                shares = float(lots) * 1000.0 if lots is not None else None
            if price is not None and price > 0 and shares is not None and shares > 0:
                profile.append({"price": float(price), "volume": float(shares)})
        total_shares = sum(row["volume"] for row in profile)
        if not profile or total_shares <= 0:
            return unavailable_price_volume(
                "source_parse_error", "invalid", "captured distribution has no usable rows", required_days=required_days
            )
        all_session_trade_lots = parse_num(capture_run["captured_volume_lots"])
        regular_trade_lots = _regular_session_trade_lots(conn, code, target_date)
        distribution_lots = total_shares / 1000.0
        comparison_candidates = [
            ("regular_intraday", regular_trade_lots),
            ("fugle_captured_session", all_session_trade_lots),
        ]
        comparison_scope, comparison_trade_lots = min(
            (
                (scope, float(lots))
                for scope, lots in comparison_candidates
                if lots is not None and float(lots) > 0
            ),
            key=lambda item: abs(item[1] - distribution_lots),
            default=("unavailable", None),
        )
        trade_distribution_diff_pct = (
            abs(float(comparison_trade_lots) - distribution_lots) / float(comparison_trade_lots) * 100.0
            if comparison_trade_lots is not None and float(comparison_trade_lots) > 0
            else None
        )
        if (
            comparison_trade_lots is None
            or float(comparison_trade_lots) <= 0
            or trade_distribution_diff_pct is None
            or trade_distribution_diff_pct > 5.0
        ):
            return {
                **unavailable_price_volume(
                    "source_mismatch",
                    "invalid",
                    "price-volume distribution is not compatible with the complete paginated trade total",
                    required_days=required_days,
                ),
                "ok": False,
                "status": "trade_distribution_mismatch",
                "date": target_date,
                "captured_trade_lots": all_session_trade_lots,
                "regular_session_trade_lots": regular_trade_lots,
                "comparison_scope": comparison_scope,
                "distribution_lots": distribution_lots,
                "trade_distribution_diff_pct": trade_distribution_diff_pct,
                "writes_db": False,
            }
        volume_diff_pct = abs(total_shares - float(official_volume)) / float(official_volume) * 100.0
        official_upper_bound_tolerance_shares = 500.0
        regular_distribution_not_above_official = (
            total_shares <= float(official_volume) + official_upper_bound_tolerance_shares
        )
        profile_reconciliation = assess_persisted_price_volume_reconciliation(
            {
                "source_name": "Fugle intraday volumes",
                "quality": "high",
                "trade_scope": comparison_scope,
                "total_volume_shares": total_shares,
                "eod_volume_shares": float(official_volume),
            },
            official_volume_shares=float(official_volume),
            captured_volume_shares=total_shares,
            scope_capture_complete=bool(capture_complete or post_close_pagination_candidate),
        )
        quality = (
            "VALIDATED"
            if regular_distribution_not_above_official and profile_reconciliation.get("ready")
            else "SCOPED_VALIDATED"
            if regular_distribution_not_above_official and profile_reconciliation.get("scope_ready")
            else "VOLUME_MISMATCH"
        )
        reason = (
            f"Fugle distribution reconciled to persisted trades using {comparison_scope}; "
            f"official all-session volume gap {volume_diff_pct:.2f}% and coverage "
            f"{float(profile_reconciliation.get('official_coverage_ratio') or 0.0) * 100:.2f}%"
            if quality == "VALIDATED"
            else (
                f"Fugle regular-session price bins are complete for {comparison_scope}; "
                f"official all-session coverage is "
                f"{float(profile_reconciliation.get('official_coverage_ratio') or 0.0) * 100:.2f}% "
                "because exchange daily volume can also include odd-lot or after-hours trades; "
                "the scoped profile is stored but excluded from scoring"
            )
            if quality == "SCOPED_VALIDATED"
            else "Fugle distribution did not pass official all-session coverage; "
            f"difference {volume_diff_pct:.2f}%; "
            f"{profile_reconciliation.get('reason') or 'volume reconciliation failed'}"
        )
        conn.execute(
            """
            UPDATE price_volume_distribution
            SET data_quality=?, source_quality=?, updated_at=?
            WHERE stock_id=? AND trade_date=? AND UPPER(COALESCE(source,''))='FUGLE'
            """,
            (quality, quality, time.time(), code, target_date),
        )
        if quality in {"VALIDATED", "SCOPED_VALIDATED"} and post_close_pagination_candidate:
            conn.execute(
                """
                UPDATE fugle_intraday_capture_runs
                SET capture_complete=1,
                    data_quality='SESSION_COMPLETE',
                    reason='post-close terminal pagination reconciled to exact regular-session price bins; official total may include odd-lot or after-hours volume',
                    fetched_at=?
                WHERE code=? AND trade_date=? AND endpoint='trades' AND source='FUGLE'
                """,
                (time.time(), code, target_date),
            )
            capture_complete = True
        if quality == "VOLUME_MISMATCH":
            conn.execute(
                "DELETE FROM price_volume_profile_daily WHERE code=? AND date=? AND LOWER(COALESCE(quality,''))<>'high'",
                (code, target_date),
            )
            conn.commit()
            return {
                **unavailable_price_volume("volume_mismatch", "invalid", reason, required_days=required_days),
                "date": target_date,
                "official_volume_shares": float(official_volume),
                "captured_volume_shares": total_shares,
                "volume_diff_pct": volume_diff_pct,
                "profile_reconciliation": profile_reconciliation,
                "writes_db": True,
            }
        source_hash = pv_canonical_hash({
            "source": "Fugle intraday volumes",
            "date": target_date,
            "code": code,
            "profile": profile,
        })
        conn.execute(
            """
            INSERT OR REPLACE INTO price_volume_profile_daily(
                date,code,source_level,source_name,source_hash,trade_scope,volume_unit,
                total_volume_shares,eod_volume_shares,volume_diff_pct,price_level_count,
                min_price,max_price,profile_json,quality,quality_reason,fetched_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                target_date,
                code,
                int(PRICE_VOLUME_SOURCE_LEVELS.get("fugle_intraday_volumes", 3)),
                "Fugle intraday volumes",
                source_hash,
                comparison_scope,
                "shares",
                total_shares,
                float(official_volume),
                volume_diff_pct,
                len(profile),
                min(row["price"] for row in profile),
                max(row["price"] for row in profile),
                json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
                "high" if quality == "VALIDATED" else "scoped",
                reason,
                time.time(),
            ),
        )
        scoped_coverage_days: int | None = None
        if quality == "SCOPED_VALIDATED":
            scoped_coverage_days = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM price_volume_profile_daily
                    WHERE code=? AND date<=?
                      AND quality IN ('high','high_mixed','medium_mixed')
                      AND UPPER(source_name) LIKE '%FUGLE%'
                    """,
                    (code, target_date),
                ).fetchone()[0]
                or 0
            )
            _upsert_price_volume_score_outcome(
                conn,
                date=target_date,
                code=code,
                close=float(history["close"]),
                source={
                    "source_name": "Fugle intraday volumes",
                    "source_level": int(PRICE_VOLUME_SOURCE_LEVELS.get("fugle_intraday_volumes", 3)),
                    "source_hash": source_hash,
                },
                outcome={
                    "status": "excluded_scope_coverage",
                    "quality": "scoped",
                    "quality_reason": reason,
                    "coverage_days": scoped_coverage_days,
                    "required_days": required_days,
                },
            )
        cleanup_price_volume_history(conn, code, keep_rows=200)
        conn.commit()

    if quality == "SCOPED_VALIDATED":
        return {
            "ok": True,
            "status": "scoped_validated",
            "quality": "scoped",
            "quality_reason": reason,
            "date": target_date,
            "official_volume_shares": float(official_volume),
            "captured_volume_shares": total_shares,
            "volume_diff_pct": volume_diff_pct,
            "trade_scope": comparison_scope,
            "profile_reconciliation": profile_reconciliation,
            "coverage_days": scoped_coverage_days,
            "required_days": required_days,
            "score_available": False,
            "score_status": "excluded_scope_coverage",
            "score_quality_reason": "regular-session profile covers less than the required share of official all-session volume",
            "writes_db": True,
        }

    score = compute_price_volume_score_for_code(
        code,
        required_days=required_days,
        as_of_date=target_date,
    )
    return {
        "ok": True,
        "status": "validated",
        "quality": "high",
        "quality_reason": reason,
        "date": target_date,
        "official_volume_shares": float(official_volume),
        "captured_volume_shares": total_shares,
        "volume_diff_pct": volume_diff_pct,
        "trade_scope": comparison_scope,
        "profile_reconciliation": profile_reconciliation,
        "coverage_days": score.get("coverage_days"),
        "required_days": score.get("required_days", required_days),
        "score_available": bool(score.get("available")),
        "score_status": score.get("status"),
        "score_quality_reason": score.get("quality_reason"),
        "writes_db": True,
    }


def _score_issue(
    status: str,
    quality: str,
    reason: str,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required_days = int((row or {}).get("required_days") or 30)
    issue = unavailable_price_volume(status, quality, reason, required_days=required_days)
    issue["coverage_days"] = (row or {}).get("coverage_days")
    issue["grade"] = None
    issue["total_score"] = None
    return issue


def _read_compatible_persisted_score(
    conn: sqlite3.Connection,
    code: str,
    history: dict[str, Any],
    distribution: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    row = conn.execute(
        "SELECT * FROM price_volume_score_daily WHERE code=? AND date=? LIMIT 1",
        (code, str(history["date"])),
    ).fetchone()
    if not row:
        return None, _score_issue("unavailable", "missing", "no persisted price-volume score")
    score = dict(row)
    expected_date = str(history["date"])
    score_date = str(score.get("date") or "")
    if score_date != expected_date:
        return None, _score_issue(
            "stale",
            "stale",
            f"persisted price-volume score is {score_date or 'unknown'}, but latest K-line is {expected_date}",
            score,
        )

    status = _source_key(score.get("status"))
    if status != PRICE_VOLUME_SCORE_STATUS:
        explicit_non_ready = status in {
            "accumulating",
            "excluded scope coverage",
            "insufficient history",
        }
        return None, _score_issue(
            str(score.get("status") or "invalid_status") if explicit_non_ready else "invalid_status",
            str(score.get("quality") or "invalid"),
            (
                str(score.get("quality_reason") or "persisted price-volume outcome is not decision-ready")
                if explicit_non_ready
                else f"persisted price-volume score status must be {PRICE_VOLUME_SCORE_STATUS}, got {status or 'missing'}"
            ),
            score,
        )

    coverage_days = int(score.get("coverage_days") or 0)
    required_days = int(score.get("required_days") or 0)
    minimum_coverage = max(1, math.ceil(required_days * 0.8)) if required_days > 0 else 1
    if required_days <= 0 or coverage_days < minimum_coverage:
        return None, _score_issue(
            "accumulating",
            "accumulating",
            f"persisted price-volume coverage is {coverage_days}/{required_days}; "
            f"minimum is {minimum_coverage}",
            score,
        )

    score_quality = _source_key(score.get("quality"))
    if score_quality != PRICE_VOLUME_SCORE_QUALITY:
        return None, _score_issue(
            "source_mismatch",
            str(score.get("quality") or "invalid"),
            "only a single-source high-quality Fugle score may be merged with true price-volume rows",
            score,
        )

    distribution_source = distribution.get("distribution_source")
    distribution_qualities = {
        _source_key(distribution.get("distribution_source_quality")),
        _source_key(distribution.get("distribution_data_quality")),
    } - {""}
    if (
        not _is_fugle_source(score.get("source_name"))
        or not _is_fugle_source(distribution_source)
        or not distribution.get("distribution_validated")
        or not distribution_qualities
        or not distribution_qualities.issubset(PRICE_VOLUME_DISTRIBUTION_OK_QUALITIES)
    ):
        return None, _score_issue(
            "source_mismatch",
            "invalid",
            "persisted score and true price-volume distribution are not compatible Fugle sources",
            score,
        )

    profile = conn.execute(
        """
        SELECT date, source_name, source_hash, quality
        FROM price_volume_profile_daily
        WHERE code=? AND date=?
        LIMIT 1
        """,
        (code, expected_date),
    ).fetchone()
    if not profile:
        return None, _score_issue(
            "source_mismatch",
            "invalid",
            "same-day Fugle profile used by the persisted score is missing",
            score,
        )
    profile = dict(profile)
    if (
        not _is_fugle_source(profile.get("source_name"))
        or _source_key(profile.get("quality")) != PRICE_VOLUME_SCORE_QUALITY
        or not score.get("source_hash")
        or str(profile.get("source_hash") or "") != str(score.get("source_hash") or "")
    ):
        return None, _score_issue(
            "source_mismatch",
            "invalid",
            "persisted score does not match the same-day high-quality Fugle profile",
            score,
        )

    score_close = parse_num(score.get("close"))
    history_close = parse_num(history.get("close"))
    if score_close is None or history_close is None or not math.isclose(
        float(score_close),
        float(history_close),
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        return None, _score_issue(
            "price_mismatch",
            "invalid",
            "persisted price-volume score close does not match the latest K-line close",
            score,
        )

    try:
        score["support_peaks"] = json.loads(score.get("support_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        score["support_peaks"] = []
    try:
        score["resistance_peaks"] = json.loads(score.get("resistance_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        score["resistance_peaks"] = []
    score["available"] = True
    return score, None


def latest_true_price_volume_distribution(code: str) -> dict[str, Any] | None:
    code = str(code).zfill(4)
    try:
        with _db_lock, closing(db()) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            history = _latest_history_context(conn, code)
            if not history:
                return None
            distribution, _ = _read_true_distribution_for_date(conn, code, str(history["date"]))
            return distribution
    except Exception as exc:
        logging.warning("true price-volume distribution unavailable for %s: %s", code, safe_error(exc))
        return None


def latest_price_volume_summary(code: str) -> dict[str, Any]:
    """Read a date/source-consistent persisted price-volume summary without DB writes."""

    code = str(code).zfill(4)
    hidden_table = {
        "is_true_price_volume": False,
        "profile_rows": [],
        "summary_text": "",
        "support_text": "",
        "resistance_text": "",
        "cost_text": "",
        "grade": None,
        "total_score": None,
    }
    try:
        with _db_lock, closing(db()) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            history = _latest_history_context(conn, code)
            if not history:
                return {
                    **unavailable_price_volume(
                        "unavailable",
                        "missing",
                        "latest K-line is unavailable; price-volume data cannot be aligned",
                    ),
                    **hidden_table,
                }
            expected_date = str(history["date"])
            distribution, distribution_issue = _read_true_distribution_for_date(conn, code, expected_date)
            if not distribution:
                return {
                    **(distribution_issue or unavailable_price_volume("unavailable", "missing", "true price-volume unavailable")),
                    **hidden_table,
                    "data_date": expected_date,
                }
            score, score_issue = _read_compatible_persisted_score(conn, code, history, distribution)
    except Exception as exc:
        logging.warning("persisted price-volume summary unavailable for %s: %s", code, safe_error(exc))
        return {
            **unavailable_price_volume("unavailable", "invalid", safe_error(exc)),
            **hidden_table,
        }

    result = score or score_issue or unavailable_price_volume(
        "unavailable",
        "missing",
        "persisted price-volume score unavailable",
    )
    result.update(distribution)
    if not score:
        result["grade"] = None
        result["total_score"] = None
        result["support_text"] = ""
        result["resistance_text"] = ""
        result["cost_text"] = ""
        return result

    support = (result.get("support_peaks") or [{}])[0] if result.get("support_peaks") else None
    resistance = (result.get("resistance_peaks") or [{}])[0] if result.get("resistance_peaks") else None
    cost = result.get("weighted_cost")
    cost_state = "市場成本下方，偏獲利" if result.get("cost_state") == "market_profit" else "市場成本上方，反彈易遇解套賣壓"
    result["support_text"] = f"分價量支撐 {fmt(support.get('price'))}（{support.get('strength')}，{support.get('volume_share_pct')}%）" if support else ""
    result["resistance_text"] = f"分價量壓力 {fmt(resistance.get('price'))}（{resistance.get('strength')}，{resistance.get('volume_share_pct')}%）" if resistance else ""
    result["cost_text"] = f"分價量成交重心 {fmt(cost)}｜{cost_state}"
    return result

