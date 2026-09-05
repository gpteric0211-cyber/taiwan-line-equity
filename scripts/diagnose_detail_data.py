from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from datetime import date


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = REPO_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.components import read_components  # noqa: E402
from core.config import DB_PATH  # noqa: E402
from core.valuation_normalizer import build_valuation_quality_payload  # noqa: E402
from repository.market_profile_repository import resolve_market_profile  # noqa: E402
from repository.watchlist_repository import get_watchlist_codes  # noqa: E402
from services.industry_profile_service import latest_industry_valuation_payload, latest_theme_profile_payload  # noqa: E402
from us_relations import US_RELATION_MAP, related_us_assets_for_code  # noqa: E402


CORE_TABLES = [
    "history_price",
    "eod_price",
    "valuation",
    "institution_daily",
    "margin_daily",
    "foreign_shareholding",
    "corporate_actions",
    "daily_chip_momentum",
]

MANUAL_CLASSIFICATION_SOURCE = "MANUAL_CURATED_EXCEL"
LOCAL_READY_THRESHOLD = 120
US_RELATION_MAPPING_LOCATION = "review_src/us_relations.py:US_RELATION_MAP"


def clean_display_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "undefined", "nan"}:
        return ""
    return text


def display_name_for_code(conn: sqlite3.Connection, code: str) -> tuple[str, str]:
    row = conn.execute("SELECT name FROM watchlist WHERE code=? LIMIT 1", (code,)).fetchone()
    if row and clean_display_name(row["name"]):
        return clean_display_name(row["name"]), "watchlist"
    row = conn.execute("SELECT name FROM eod_price WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    if row and clean_display_name(row["name"]):
        return clean_display_name(row["name"]), "eod_price"
    if table_exists(conn, "stock_industry_profile"):
        row = conn.execute("SELECT name FROM stock_industry_profile WHERE code=? LIMIT 1", (code,)).fetchone()
        if row and clean_display_name(row["name"]):
            return clean_display_name(row["name"]), "stock_industry_profile"
    for item in read_components():
        if str(item.get("code", "")).zfill(4)[:4] == code and clean_display_name(item.get("name")):
            return clean_display_name(item.get("name")), "core.components"
    return code, "code_fallback"


def resolve_detail_source_for_diagnose(
    code: str,
    *,
    requested_source: str | None,
    is_watchlist: bool,
    is_tw50: bool,
    local_history_rows: int,
) -> dict[str, Any]:
    requested = str(requested_source or "").strip().lower()
    local_ready = int(local_history_rows or 0) >= LOCAL_READY_THRESHOLD
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
    return {
        "requested_source": requested or None,
        "resolved_detail_source": resolved,
        "local_detail_ready": local_ready,
        "local_history_rows": int(local_history_rows or 0),
        "local_ready_threshold": LOCAL_READY_THRESHOLD,
        "tw50_membership_source": "core.components.read_components" if is_tw50 else None,
    }


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def tables_like(conn: sqlite3.Connection, patterns: list[str]) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    found: list[str] = []
    for row in rows:
        name = str(row[0])
        low = name.lower()
        if any(pattern in low for pattern in patterns):
            found.append(name)
    return found


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def detect_code_column(cols: set[str]) -> str | None:
    for col in ("code", "stock_id", "symbol"):
        if col in cols:
            return col
    return None


def detect_date_column(cols: set[str]) -> str | None:
    for col in ("date", "trade_date", "data_date", "analysis_date", "calc_date"):
        if col in cols:
            return col
    return None


def code_table_summary(conn: sqlite3.Connection, table: str, code: str) -> dict[str, Any]:
    out: dict[str, Any] = {"table": table, "exists": table_exists(conn, table)}
    if not out["exists"]:
        return out
    cols = table_columns(conn, table)
    code_col = detect_code_column(cols)
    date_col = detect_date_column(cols)
    out["code_column"] = code_col
    out["date_column"] = date_col
    if not code_col:
        out["row_count"] = None
        out["latest_date"] = None
        out["reason"] = "no code-like column"
        return out
    row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {code_col}=?", (code,)).fetchone()
    out["row_count"] = int(row[0] or 0)
    if date_col:
        latest = conn.execute(
            f"SELECT {date_col} FROM {table} WHERE {code_col}=? AND {date_col} IS NOT NULL "
            f"ORDER BY {date_col} DESC LIMIT 1",
            (code,),
        ).fetchone()
        out["latest_date"] = latest[0] if latest else None
    else:
        out["latest_date"] = None
    return out


def market_type_for_code(conn: sqlite3.Connection, code: str) -> str | None:
    return str((resolve_market_profile(code) or {}).get("market_type") or "unknown")


def iso_lag_days(latest: Any, expected: str | None) -> int | None:
    if not latest or not expected:
        return None
    try:
        latest_date = date.fromisoformat(str(latest)[:10])
        expected_date = date.fromisoformat(str(expected)[:10])
    except Exception:
        return None
    return (expected_date - latest_date).days


def total_rows(conn: sqlite3.Connection, table: str) -> int | None:
    if not table_exists(conn, table):
        return None
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0] or 0)


def latest_raw_valuation_quality(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    candidates: list[tuple[str, sqlite3.Row]] = []
    if table_exists(conn, "twse_daily_valuation"):
        row = conn.execute(
            "SELECT *, 'twse_daily_valuation' AS table_name FROM twse_daily_valuation WHERE symbol=? ORDER BY data_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            candidates.append(("twse_daily_valuation", row))
    if table_exists(conn, "valuation"):
        row = conn.execute(
            "SELECT *, 'valuation' AS table_name FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            candidates.append(("valuation", row))
    if not candidates:
        return {
            "valuation_nonblocking": True,
            "available": False,
            "reason": "valuation_missing",
            "quality": build_valuation_quality_payload(None),
        }
    table_name, row = candidates[0]
    data = dict(row)
    if table_name == "valuation":
        data = {
            **data,
            "pe_ratio": data.get("pe"),
            "pb_ratio": data.get("pb"),
            "data_date": data.get("date"),
            "table_name": "valuation",
        }
    else:
        data["table_name"] = "twse_daily_valuation"
    quality = build_valuation_quality_payload(data)
    return {
        "valuation_nonblocking": True,
        "available": True,
        "table_name": table_name,
        "source_type": quality.get("source_type"),
        "source_market": quality.get("source_market"),
        "source_name": quality.get("source_name"),
        "source_id": quality.get("source_id"),
        "dividend_yield_source": quality.get("dividend_yield_source"),
        "dividend_yield_display": quality.get("dividend_yield_normalized")
        if quality.get("dividend_yield_status") == "normal"
        else None,
        "can_derive_dividend_yield": bool(quality.get("can_derive_dividend_yield")),
        "derived_dividend_yield": quality.get("derived_dividend_yield"),
        "derived_dividend_yield_status": quality.get("derived_dividend_yield_status"),
        "derived_dividend_yield_reason": quality.get("derived_dividend_yield_reason"),
        "quality": quality,
        "suspicious_flags": quality.get("suspicious_flags") or [],
        "parse_warnings": quality.get("parse_warnings") or [],
    }


def latest_market_date(conn: sqlite3.Connection) -> str | None:
    candidates: list[str] = []
    for table, col in (("history_price", "date"), ("eod_price", "date")):
        if table_exists(conn, table):
            row = conn.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY {col} DESC LIMIT 1").fetchone()
            if row and row[0]:
                candidates.append(str(row[0]))
    return max(candidates) if candidates else None


def readiness_for_code(summaries: dict[str, dict[str, Any]], expected_date: str | None = None) -> dict[str, Any]:
    missing: list[str] = []
    hist_count = int((summaries.get("history_price") or {}).get("row_count") or 0)
    eod_count = int((summaries.get("eod_price") or {}).get("row_count") or 0)
    valuation_count = int((summaries.get("valuation") or {}).get("row_count") or 0)
    inst_count = int((summaries.get("institution_daily") or {}).get("row_count") or 0)
    margin_count = int((summaries.get("margin_daily") or {}).get("row_count") or 0)
    foreign_count = int((summaries.get("foreign_shareholding") or {}).get("row_count") or 0)
    tdcc_count = sum(
        int(summary.get("row_count") or 0)
        for name, summary in summaries.items()
        if any(key in name.lower() for key in ("tdcc", "equity", "concentration"))
    )
    if hist_count < 120:
        missing.append("missing_history")
    if hist_count == 0 and eod_count == 0:
        missing.append("missing_price")
    if valuation_count == 0:
        missing.append("missing_valuation")
    if inst_count < 20:
        missing.append("missing_institution")
    if margin_count < 20:
        missing.append("missing_margin")
    if foreign_count < 20:
        missing.append("missing_foreign_shareholding")
    if tdcc_count == 0:
        missing.append("missing_tdcc")
    price_available = bool(hist_count > 0 or eod_count > 0)
    technical_ready = bool(hist_count >= 60 and price_available)
    blocking_missing: list[str] = []
    if hist_count < 60:
        blocking_missing.append("missing_history")
    if not price_available:
        blocking_missing.append("missing_price")
    nonblocking_missing = [item for item in missing if item not in blocking_missing]
    table_ready = {
        "history_price": hist_count >= 120,
        "eod_price": eod_count > 0,
        "valuation": valuation_count > 0,
        "institution_daily": inst_count >= 20,
        "margin_daily": margin_count >= 20,
        "foreign_shareholding": foreign_count >= 20,
        "tdcc_equity": tdcc_count > 0,
        "daily_chip_momentum": int((summaries.get("daily_chip_momentum") or {}).get("row_count") or 0) > 0,
    }
    table_lag_days: dict[str, int | None] = {}
    for table, summary in summaries.items():
        table_lag_days[table] = iso_lag_days(summary.get("latest_date"), expected_date)
    latest_price_status = "ok" if price_available else "missing"
    unsupported_reason = None
    if hist_count == 0 and eod_count == 0 and valuation_count == 0:
        unsupported_reason = "no_local_source_rows"
    return {
        "ready": technical_ready,
        "complete_ready": not missing,
        "missing": missing,
        "blocking_missing": blocking_missing,
        "nonblocking_missing": nonblocking_missing,
        "bootstrap_needed": bool(missing),
        "price_available": price_available,
        "latest_price_source_status": latest_price_status,
        "unsupported_reason": unsupported_reason,
        "table_ready": table_ready,
        "table_lag_days": table_lag_days,
        "hist_count": hist_count,
        "eod_count": eod_count,
        "valuation_count": valuation_count,
        "institution_count": inst_count,
        "margin_count": margin_count,
        "foreign_shareholding_count": foreign_count,
        "tdcc_equity_related_count": tdcc_count,
    }


def manual_classification_rows(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    if not table_exists(conn, "stock_theme_profile"):
        return []
    rows = conn.execute(
        """
        SELECT code, tag_type, tag_name, source, source_key, quality, updated_at
        FROM stock_theme_profile
        WHERE code=? AND source=?
        ORDER BY
          CASE tag_type
            WHEN 'primary_revenue' THEN 1
            WHEN 'secondary_revenue_1' THEN 2
            WHEN 'secondary_revenue_2' THEN 3
            WHEN 'secondary_revenue_3' THEN 4
            WHEN 'subindustry' THEN 5
            WHEN 'theme' THEN 6
            WHEN 'sector_group' THEN 7
            WHEN 'official_category' THEN 8
            ELSE 9
          END,
          tag_name
        """,
        (code, MANUAL_CLASSIFICATION_SOURCE),
    ).fetchall()
    return [dict(row) for row in rows]


def classification_diagnostics(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    manual_rows = manual_classification_rows(conn, code)
    theme_payload = latest_theme_profile_payload(code)
    valuation_payload = latest_industry_valuation_payload(code)
    source_keys = {str(row.get("source_key") or "") for row in manual_rows}
    if any(key.startswith("主要分類") for key in source_keys) and any(key.startswith("次分類") for key in source_keys):
        mapping_used = "user_authoritative_hybrid"
    elif any(key.startswith("主要分類") for key in source_keys):
        mapping_used = "mapping_a"
    elif any(key in {"細分類", "細分類2", "細分類3"} for key in source_keys):
        mapping_used = "legacy_or_unknown"
    else:
        mapping_used = "not_found"
    primary = theme_payload.get("primary_peer_group") or {}
    display_tags = theme_payload.get("classification_display_tags_deduped") or theme_payload.get("display_tags") or []
    return {
        "theme_profile_available": bool(theme_payload.get("available")),
        "peer_group_type": primary.get("type") or valuation_payload.get("peer_group_type"),
        "peer_group_name": primary.get("name") or valuation_payload.get("peer_group_name"),
        "peer_count": primary.get("peer_count") or valuation_payload.get("peer_count"),
        "fallback_reason": valuation_payload.get("fallback_reason"),
        "peer_refinement_reason": valuation_payload.get("peer_refinement_reason") or primary.get("peer_refinement_reason"),
        "peer_intersection_logic": valuation_payload.get("peer_intersection_logic") or primary.get("peer_intersection_logic"),
        "broad_primary_group": valuation_payload.get("broad_primary_group") or primary.get("broad_primary_group"),
        "manual_classification_source": MANUAL_CLASSIFICATION_SOURCE if manual_rows else "not_found",
        "manual_classification_tags": [
            {
                "tag_type": row.get("tag_type"),
                "tag_name": row.get("tag_name"),
                "source_key": row.get("source_key"),
                "quality": row.get("quality"),
            }
            for row in manual_rows
        ],
        "classification_display_tags_deduped": display_tags,
        "fine_categories_display": theme_payload.get("fine_categories_display") or [],
        "official_category_display": theme_payload.get("official_category_display"),
        "peer_basis_display": theme_payload.get("peer_basis_display"),
        "classification_mapping_used": mapping_used,
        "user_classification_is_authoritative": bool(manual_rows and mapping_used == "user_authoritative_hybrid"),
    }


def diagnose(codes: list[str], *, mode: str | None = None) -> dict[str, Any]:
    db_path = Path(DB_PATH)
    result: dict[str, Any] = {
        "db_path": str(db_path.resolve()),
        "db_exists": db_path.exists(),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "latest_market_date": None,
        "table_totals": {},
        "tdcc_equity_tables": [],
        "mode": mode,
        "codes": {},
        "portable_db_empty_suspected": False,
    }
    if not db_path.exists():
        result["portable_db_empty_suspected"] = True
        return result
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        watchlist_codes = set(get_watchlist_codes())
        tw50_codes = {str(item.get("code", "")).zfill(4)[:4] for item in read_components()}
        tdcc_tables = tables_like(conn, ["tdcc", "equity", "concentration"])
        result["tdcc_equity_tables"] = tdcc_tables
        result["latest_market_date"] = latest_market_date(conn)
        for table in [*CORE_TABLES, *tdcc_tables]:
            if table not in result["table_totals"]:
                result["table_totals"][table] = total_rows(conn, table)
        for code in codes:
            normalized = str(code).strip().zfill(4)[:4]
            summaries: dict[str, dict[str, Any]] = {}
            for table in [*CORE_TABLES, *tdcc_tables]:
                summaries[table] = code_table_summary(conn, table, normalized)
                summaries[table]["lag_days"] = iso_lag_days(summaries[table].get("latest_date"), result["latest_market_date"])
            readiness = readiness_for_code(summaries, result["latest_market_date"])
            market_profile = resolve_market_profile(normalized)
            is_watchlist = normalized in watchlist_codes
            is_tw50 = normalized in tw50_codes
            display_name, display_name_source = display_name_for_code(conn, normalized)
            source_context = resolve_detail_source_for_diagnose(
                normalized,
                requested_source=mode,
                is_watchlist=is_watchlist,
                is_tw50=is_tw50,
                local_history_rows=int(readiness.get("hist_count") or 0),
            )
            valuation_payload = latest_industry_valuation_payload(normalized)
            valuation_quality = latest_raw_valuation_quality(conn, normalized)
            theme_payload = latest_theme_profile_payload(normalized)
            rel = related_us_assets_for_code(normalized)
            rel_assets = rel.get("assets") or []
            related_etf_count = len([x for x in rel_assets if "ETF" in str(x.get("type") or "").upper() or "ETF" in str(x.get("name") or "").upper()])
            classification = classification_diagnostics(conn, normalized)
            valuation_blocking = bool(
                not source_context.get("local_detail_ready")
                and not readiness.get("ready")
                and int(readiness.get("valuation_count") or 0) == 0
            )
            if readiness.get("unsupported_reason") == "no_local_source_rows" and market_profile.get("market_type") in {"otc", "unknown"}:
                readiness["unsupported_reason"] = None
                readiness["source_fallback"] = market_profile.get("yahoo_symbols") or []
            result["codes"][normalized] = {
                "display_name_source": display_name_source,
                "display_name": display_name,
                "is_watchlist": is_watchlist,
                "is_tw50": is_tw50,
                **source_context,
                "market_type": market_profile.get("market_type"),
                "market_profile": market_profile,
                "yahoo_symbol": market_profile.get("yahoo_symbol"),
                "yahoo_symbols": market_profile.get("yahoo_symbols"),
                "ready": readiness.get("ready"),
                "missing": readiness.get("missing"),
                "table_ready": readiness.get("table_ready"),
                "row_count": {
                    table: summary.get("row_count")
                    for table, summary in summaries.items()
                },
                "latest_date": {
                    table: summary.get("latest_date")
                    for table, summary in summaries.items()
                },
                "lag_days": readiness.get("table_lag_days"),
                "price_available": readiness.get("price_available"),
                "latest_price_source_status": readiness.get("latest_price_source_status"),
                "bootstrap_needed": readiness.get("bootstrap_needed"),
                "unsupported_reason": readiness.get("unsupported_reason"),
                "tables": summaries,
                "readiness": readiness,
                "technical_ready": bool(readiness.get("table_ready", {}).get("history_price") or source_context.get("local_detail_ready")),
                "valuation_ready": bool(readiness.get("table_ready", {}).get("valuation")),
                "valuation_source_type": valuation_quality.get("source_type"),
                "valuation_source_market": valuation_quality.get("source_market"),
                "valuation_source_name": valuation_quality.get("source_name"),
                "valuation_source_id": valuation_quality.get("source_id"),
                "dividend_yield_display": valuation_quality.get("dividend_yield_display"),
                "chip_ready": bool(
                    readiness.get("table_ready", {}).get("institution_daily")
                    and readiness.get("table_ready", {}).get("margin_daily")
                ),
                "tdcc_ready": bool(readiness.get("table_ready", {}).get("tdcc_equity")),
                "theme_profile": {
                    "available": bool(theme_payload.get("available")),
                    "display_tags": theme_payload.get("display_tags") or [],
                    "fine_categories_display": theme_payload.get("fine_categories_display") or [],
                    "official_category_display": theme_payload.get("official_category_display"),
                    "peer_basis_display": theme_payload.get("peer_basis_display"),
                },
                "manual_classification_tags": classification.get("manual_classification_tags"),
                "classification_display_tags_deduped": classification.get("classification_display_tags_deduped"),
                "peer_group_type": valuation_payload.get("peer_group_type") or classification.get("peer_group_type"),
                "peer_group_name": valuation_payload.get("peer_group_name") or classification.get("peer_group_name"),
                "peer_count": valuation_payload.get("peer_count") or classification.get("peer_count"),
                "peer_refinement_reason": valuation_payload.get("peer_refinement_reason") or classification.get("peer_refinement_reason"),
                "related_us_mapping_location": US_RELATION_MAPPING_LOCATION,
                "related_us_mapping_key": normalized if normalized in US_RELATION_MAP else (
                    valuation_payload.get("peer_group_name")
                    or theme_payload.get("peer_basis_display")
                    or next(iter(theme_payload.get("display_tags") or []), None)
                ),
                "related_us_count": len(rel_assets),
                "related_etf_count": related_etf_count,
                "valuation_blocking": valuation_blocking,
                "valuation_quality": valuation_quality,
                "missing_reason_user_facing": None if readiness.get("ready") or source_context.get("local_detail_ready") else readiness.get("unsupported_reason") or "local_detail_not_ready",
                "classification": classification,
            }
    core_total = sum(int(v or 0) for k, v in result["table_totals"].items() if k in {"history_price", "eod_price", "valuation"})
    result["portable_db_empty_suspected"] = bool(result["db_exists"] and core_total == 0)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose local DB completeness for stock detail pages.")
    parser.add_argument("--codes", default="1101,2317,2382,2330", help="Comma-separated stock codes.")
    parser.add_argument("--mode", choices=["watchlist", "tw50"], default="", help="Diagnose current watchlist or Taiwan50 component scope.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "watchlist":
        codes = get_watchlist_codes()
    elif args.mode == "tw50":
        codes = [str(item.get("code") or "").zfill(4)[:4] for item in read_components()]
    else:
        codes = [part.strip() for part in str(args.codes).split(",") if part.strip()]
    result = diagnose(codes, mode=args.mode or None)
    if args.mode and not codes:
        result["warning"] = f"{args.mode} scope is empty or unavailable"
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
