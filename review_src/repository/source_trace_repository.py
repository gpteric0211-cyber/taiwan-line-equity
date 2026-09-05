from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from typing import Any

from core.db import db
from repository.full_market_batch_repository import resolve_full_market_analysis_date


def _source_counts(
    conn: sqlite3.Connection,
    table: str,
    code: str,
    limit: int,
    *,
    as_of_date: str | None,
) -> dict[str, int]:
    allowed = {"history_price", "institution_daily", "margin_daily", "foreign_shareholding"}
    if table not in allowed:
        return {}
    rows = conn.execute(
        f"""
        SELECT source, COUNT(*) AS c
        FROM (
            SELECT source
            FROM {table}
            WHERE code=? AND date<=?
            ORDER BY date DESC
            LIMIT ?
        )
        GROUP BY source
        """,
        (code, as_of_date, int(limit)),
    ).fetchall()
    return {str(r["source"] or "missing"): int(r["c"] or 0) for r in rows}


def _latest_source_row(
    conn: sqlite3.Connection,
    table: str,
    columns: str,
    code: str,
    as_of_date: str | None,
) -> sqlite3.Row | None:
    if not as_of_date:
        return None
    return conn.execute(
        f"SELECT {columns} FROM {table} WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, as_of_date),
    ).fetchone()


def source_trace_for_code(
    conn: sqlite3.Connection,
    code: str,
    *,
    as_of_date: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Hard provenance gate: numeric fields must trace to dated source rows."""
    code = str(code).zfill(4)
    issues: list[str] = []
    selected_date = resolve_full_market_analysis_date(conn, as_of_date)
    eod = _latest_source_row(conn, "eod_price", "date,source,updated_at", code, selected_date)
    hist = _latest_source_row(conn, "history_price", "date,source,volume_unit,updated_at", code, selected_date)
    val = _latest_source_row(conn, "valuation", "date,source,eps_source,updated_at", code, selected_date)
    inst = _latest_source_row(conn, "institution_daily", "date,source,updated_at", code, selected_date)
    margin = _latest_source_row(conn, "margin_daily", "date,source,updated_at", code, selected_date)
    price_row = hist if hist and (not eod or str(hist["date"] or "") >= str(eod["date"] or "")) else eod
    trace = {
        "price": dict(price_row) if price_row else None,
        "history": dict(hist) if hist else None,
        "valuation": dict(val) if val else None,
        "institution": dict(inst) if inst else None,
        "margin": dict(margin) if margin else None,
        "history_120_sources": _source_counts(conn, "history_price", code, 120, as_of_date=selected_date),
        "institution_20_sources": _source_counts(conn, "institution_daily", code, 20, as_of_date=selected_date),
        "margin_20_sources": _source_counts(conn, "margin_daily", code, 20, as_of_date=selected_date),
    }
    for label, row in {"price": price_row, "kline": hist, "valuation": val, "institution": inst, "margin": margin}.items():
        if not row:
            issues.append(f"{label} missing source row")
            continue
        if not row["date"]:
            issues.append(f"{label} missing source date")
        if not row["source"]:
            issues.append(f"{label} missing source name")
    if hist and str(hist["volume_unit"] or "").lower() != "shares":
        issues.append(f"kline volume_unit is not shares: {hist['volume_unit']}")
    for label, counts, minimum in [
        ("history last 120", trace["history_120_sources"], 120),
        ("institution last 20", trace["institution_20_sources"], 20),
        ("margin last 20", trace["margin_20_sources"], 20),
    ]:
        row_count = sum(int(v) for v in counts.values())
        if row_count < minimum:
            issues.append(f"{label} source rows insufficient: {row_count}/{minimum}")
        if counts.get("missing"):
            issues.append(f"{label} has {counts['missing']} rows without source")
    return trace, issues


def source_trace_for_code_v2(
    conn: sqlite3.Connection,
    code: str,
    *,
    as_of_date: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Readiness-friendly provenance check with per-table source dates."""
    code = str(code).zfill(4)
    issues: list[str] = []
    selected_date = resolve_full_market_analysis_date(conn, as_of_date)
    eod = _latest_source_row(conn, "eod_price", "date,source,updated_at", code, selected_date)
    hist = _latest_source_row(conn, "history_price", "date,source,volume_unit,updated_at", code, selected_date)
    val = _latest_source_row(conn, "valuation", "date,source,eps_source,updated_at", code, selected_date)
    inst = _latest_source_row(conn, "institution_daily", "date,source,updated_at", code, selected_date)
    margin = _latest_source_row(conn, "margin_daily", "date,source,updated_at", code, selected_date)
    foreign = _latest_source_row(conn, "foreign_shareholding", "date,source,updated_at", code, selected_date)
    price_row = hist if hist and (not eod or str(hist["date"] or "") >= str(eod["date"] or "")) else eod
    trace = {
        "price": dict(price_row) if price_row else None,
        "history": dict(hist) if hist else None,
        "valuation": dict(val) if val else None,
        "institution": dict(inst) if inst else None,
        "margin": dict(margin) if margin else None,
        "foreign_shareholding": dict(foreign) if foreign else None,
        "history_120_sources": _source_counts(conn, "history_price", code, 120, as_of_date=selected_date),
        "institution_20_sources": _source_counts(conn, "institution_daily", code, 20, as_of_date=selected_date),
        "margin_20_sources": _source_counts(conn, "margin_daily", code, 20, as_of_date=selected_date),
        "foreign_shareholding_120_sources": _source_counts(conn, "foreign_shareholding", code, 120, as_of_date=selected_date),
    }
    for label, row in {
        "price": price_row,
        "kline": hist,
        "valuation": val,
        "institution": inst,
        "margin": margin,
        "foreign_shareholding": foreign,
    }.items():
        if not row:
            issues.append(f"{label} missing source row")
            continue
        if not row["date"]:
            issues.append(f"{label} missing source date")
        if not row["source"]:
            issues.append(f"{label} missing source name")
    if hist and str(hist["volume_unit"] or "").lower() != "shares":
        issues.append(f"kline volume_unit is not shares: {hist['volume_unit']}")
    for label, counts, minimum in [
        ("history last 120", trace["history_120_sources"], 120),
        ("institution last 20", trace["institution_20_sources"], 20),
        ("margin last 5", trace["margin_20_sources"], 5),
        ("foreign shareholding last 20", trace["foreign_shareholding_120_sources"], 20),
    ]:
        row_count = sum(int(v) for v in counts.values())
        if row_count < minimum:
            issues.append(f"{label} source rows insufficient: {row_count}/{minimum}")
        if counts.get("missing"):
            issues.append(f"{label} has {counts['missing']} rows without source")
    return trace, issues


def latest_table_date(table: str, code: str) -> str | None:
    # Return latest data date for one code/table.
    allowed = {"history_price", "institution_daily", "margin_daily", "lending_daily", "foreign_shareholding"}
    if table not in allowed:
        return None
    with closing(db()) as conn:
        row = conn.execute(f"SELECT MAX(date) AS d FROM {table} WHERE code=?", (str(code).zfill(4),)).fetchone()
        return row["d"] if row and row["d"] else None


def local_dataset_count_since(table: str, code: str, start_date: str) -> int:
    # Count local rows in the requested backfill window.
    allowed = {"history_price", "institution_daily", "margin_daily", "lending_daily", "foreign_shareholding"}
    if table not in allowed:
        return 0
    with closing(db()) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE code=? AND date>=?",
            (str(code).zfill(4), start_date),
        ).fetchone()
        return int(row["c"] if row and row["c"] is not None else 0)


def required_local_rows(table: str, mode: str, update_days: int) -> int:
    # Minimum local row count required before skipping a FinMind dataset.
    if mode == "full":
        if table == "history_price":
            return min(120, max(60, int(update_days * 0.80)))
        return min(60, max(30, int(update_days * 0.40)))
    if table == "history_price":
        return max(30, min(45, int(update_days)))
    return max(20, min(35, int(update_days * 0.70)))


def should_skip_dataset(table: str, code: str, target_date: str, start_date: str, min_rows: int) -> bool:
    # Skip FinMind only when recency and local sufficiency are both met.
    latest = latest_table_date(table, code)
    if not (latest and latest >= target_date):
        return False
    return local_dataset_count_since(table, code, start_date) >= int(min_rows)


def infer_volume_unit(values: list[float]) -> dict[str, Any]:
    # Best-effort volume unit inference; audit only, no mutation.
    nums = sorted(float(v) for v in values if v is not None and float(v) > 0)
    if not nums:
        return {"unit": "unknown", "confidence": "none", "median": None, "count": 0}
    mid = nums[len(nums) // 2] if len(nums) % 2 else (nums[len(nums)//2 - 1] + nums[len(nums)//2]) / 2
    if mid >= 500_000:
        unit, conf = "shares", "medium"
    elif mid <= 50_000:
        unit, conf = "lots", "medium"
    else:
        unit, conf = "uncertain", "low"
    note = None
    if unit == "uncertain":
        note = "volume median is in an ambiguous range; cross-check high-liquidity stocks"
    return {"unit": unit, "confidence": conf, "median": mid, "count": len(nums), "min": min(nums), "max": max(nums), "note": note}


def audit_volume_units(conn: sqlite3.Connection, codes: list[str] | None = None, limit_per_code: int = 40) -> dict[str, Any]:
    # Audit volume units for history_price and eod_price without modifying DB.
    codes = [str(c).zfill(4) for c in (codes or ["2330", "2449", "2317", "2454"])]
    result: dict[str, Any] = {
        "tables": {},
        "recommendation": "audit_only_no_migration",
        "unit_consistent": None,
        "canonical_unit": "shares",
        "warning": None,
    }
    for table in ["history_price", "eod_price"]:
        samples: list[dict[str, Any]] = []
        values: list[float] = []
        turnovers: list[float] = []
        source_units: dict[str, int] = {}
        table_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        has_volume_unit = "volume_unit" in table_cols
        for code in codes:
            unit_select = ",volume_unit" if has_volume_unit else ""
            rows = conn.execute(
                f"SELECT code,date,close,volume{unit_select} FROM {table} WHERE code=? AND volume IS NOT NULL AND volume>0 AND close IS NOT NULL AND close>0 ORDER BY date DESC LIMIT ?",
                (code, int(limit_per_code)),
            ).fetchall()
            for r in rows[:5]:
                try:
                    turnover = float(r["close"]) * float(r["volume"])
                except Exception:
                    turnover = None
                unit = str(r["volume_unit"]).strip() if has_volume_unit and "volume_unit" in r.keys() and r["volume_unit"] else None
                samples.append({"code": r["code"], "date": r["date"], "close": r["close"], "volume": r["volume"], "volume_unit": unit, "turnover_est": turnover})
            for r in rows:
                try:
                    vol = float(r["volume"])
                    close_px = float(r["close"])
                    unit = str(r["volume_unit"]).strip().lower() if has_volume_unit and "volume_unit" in r.keys() and r["volume_unit"] else "unknown"
                    source_units[unit] = source_units.get(unit, 0) + 1
                    if vol > 0:
                        values.append(vol)
                    if vol > 0 and close_px > 0:
                        turnovers.append(close_px * vol)
                except Exception:
                    continue
        inf = infer_volume_unit(values)
        turnover_inf = infer_volume_unit(turnovers)  # reuse median/count fields; unit label not used for turnover
        result["tables"][table] = {
            "inference": inf,
            "declared_volume_units": source_units,
            "turnover_median_twd": turnover_inf.get("median"),
            "turnover_count": turnover_inf.get("count"),
            "samples": samples,
        }

    units = {t: result["tables"][t]["inference"]["unit"] for t in result["tables"]}
    declared_history_units = result.get("tables", {}).get("history_price", {}).get("declared_volume_units", {})
    declared_known = {k for k, v in declared_history_units.items() if k not in {"unknown", ""} and v}
    if declared_known and declared_known != {"shares"}:
        result["unit_consistent"] = False
        result["recommendation"] = "history_price_declared_units_not_canonical_shares; normalize before trusting volume indicators"
        result["warning"] = f"history_price has non-shares volume_unit values: {declared_history_units}"
    elif units.get("history_price") == units.get("eod_price") and units.get("history_price") in {"shares", "lots"}:
        result["unit_consistent"] = True
        result["recommendation"] = f"tables_consistent_{units['history_price']}; no migration unless formulas/UI require a different canonical unit"
        if units["history_price"] != "shares":
            result["warning"] = "DB volume units appear to be lots; normalize before formulas that assume shares"
    elif "uncertain" in units.values() or "unknown" in units.values():
        result["unit_consistent"] = None
        result["recommendation"] = "unit_uncertain; do not migrate automatically; compare samples with FinMind/TWSE raw data"
        result["warning"] = "volume unit is uncertain; cross-check representative raw data before trusting volume indicators"
    else:
        result["unit_consistent"] = False
        result["recommendation"] = "tables_may_be_mixed; backup before any targeted migration"
        result["warning"] = "history_price and eod_price volume units may differ; do not trust volume indicators until normalized"
    result["migration_note"] = (
        "If migration is needed, first CREATE TABLE ..._backup_before_volume_fix AS SELECT * FROM the affected table. "
        "Do not blindly multiply/divide the whole DB unless the audit is clear."
    )
    return result

