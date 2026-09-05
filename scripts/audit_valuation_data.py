from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = REPO_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.valuation_normalizer import build_valuation_quality_payload  # noqa: E402


SAMPLE_CODES = ["3491", "2330", "2454", "2317", "5425", "8261", "2481", "6435"]
MANUAL_CLASSIFICATION_SOURCE = "MANUAL_CURATED_EXCEL"


def resolve_db_path() -> Path:
    for key in ("DB_PATH", "TAIWAN50_DB_PATH"):
        raw = os.getenv(key)
        if raw:
            path = Path(raw.strip().strip('"').strip("'"))
            return path if path.is_absolute() else (REPO_ROOT / path)
    for path in (
        REVIEW_SRC / "data" / "taiwan50.db",
        REVIEW_SRC / "taiwan50.db",
    ):
        if path.exists():
            return path
    raise SystemExit("db_path_unknown")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def safe_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def code_name_map(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}
    for table, code_col, name_col in (
        ("stock_industry_profile", "code", "name"),
        ("watchlist", "code", "name"),
        ("eod_price", "code", "name"),
        ("twse_daily_valuation", "symbol", "name"),
    ):
        if not table_exists(conn, table):
            continue
        for row in safe_rows(conn, f"SELECT {code_col} AS code, {name_col} AS name FROM {table} WHERE {code_col} IS NOT NULL"):
            code = str(row["code"] or "").strip().zfill(4)[:4]
            name = str(row["name"] or "").strip()
            if code and name and name.lower() not in {"none", "null", "nan"}:
                out.setdefault(code, name)
    return out


def market_map(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}
    if table_exists(conn, "stock_industry_profile"):
        for row in safe_rows(conn, "SELECT code, market FROM stock_industry_profile WHERE code IS NOT NULL"):
            code = str(row["code"] or "").strip().zfill(4)[:4]
            market = str(row["market"] or "").strip()
            if code and market:
                out[code] = market
    return out


def collect_universe(conn: sqlite3.Connection, scope: str, explicit_codes: list[str]) -> tuple[list[str], dict[str, Any]]:
    codes: set[str] = {str(code).strip().zfill(4)[:4] for code in explicit_codes if str(code).strip()}
    sources: Counter[str] = Counter()
    if codes:
        sources["explicit_codes"] = len(codes)
    if not codes:
        if table_exists(conn, "stock_theme_profile"):
            rows = safe_rows(
                conn,
                "SELECT DISTINCT code FROM stock_theme_profile WHERE source=? AND code IS NOT NULL",
                (MANUAL_CLASSIFICATION_SOURCE,),
            )
            for row in rows:
                code = str(row["code"] or "").strip().zfill(4)[:4]
                if code.isdigit():
                    codes.add(code)
            sources["stock_theme_profile"] = len(rows)
        if table_exists(conn, "stock_industry_profile"):
            rows = safe_rows(conn, "SELECT DISTINCT code FROM stock_industry_profile WHERE code IS NOT NULL")
            for row in rows:
                code = str(row["code"] or "").strip().zfill(4)[:4]
                if code.isdigit():
                    codes.add(code)
            sources["stock_industry_profile"] = len(rows)
        if table_exists(conn, "watchlist"):
            rows = safe_rows(conn, "SELECT DISTINCT code FROM watchlist WHERE code IS NOT NULL")
            for row in rows:
                code = str(row["code"] or "").strip().zfill(4)[:4]
                if code.isdigit():
                    codes.add(code)
            sources["watchlist"] = len(rows)
        if table_exists(conn, "valuation"):
            rows = safe_rows(conn, "SELECT DISTINCT code FROM valuation WHERE code IS NOT NULL")
            for row in rows:
                code = str(row["code"] or "").strip().zfill(4)[:4]
                if code.isdigit():
                    codes.add(code)
            sources["valuation"] = len(rows)
        if table_exists(conn, "twse_daily_valuation"):
            rows = safe_rows(conn, "SELECT DISTINCT symbol FROM twse_daily_valuation WHERE symbol IS NOT NULL")
            for row in rows:
                code = str(row["symbol"] or "").strip().zfill(4)[:4]
                if code.isdigit():
                    codes.add(code)
            sources["twse_daily_valuation"] = len(rows)
        component_path = REVIEW_SRC / "data" / "taiwan50_components.csv"
        if component_path.exists():
            with component_path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                n = 0
                for row in reader:
                    code = str(row.get("code") or row.get("代號") or "").strip().zfill(4)[:4]
                    if code.isdigit():
                        codes.add(code)
                        n += 1
                sources["tw50_components_file"] = n
    markets = market_map(conn)
    if scope == "listed":
        codes = {c for c in codes if markets.get(c) in {"上市", "listed", "TSE"}}
    elif scope == "otc":
        codes = {c for c in codes if markets.get(c) in {"上櫃", "otc", "TPEX"}}
    return sorted(codes), {"universe_sources": dict(sources)}


def latest_valuation_row(conn: sqlite3.Connection, code: str) -> tuple[dict[str, Any] | None, str | None]:
    if table_exists(conn, "twse_daily_valuation"):
        row = conn.execute(
            "SELECT *, 'twse_daily_valuation' AS table_name FROM twse_daily_valuation WHERE symbol=? ORDER BY data_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            data = dict(row)
            data["table_name"] = "twse_daily_valuation"
            return data, "twse_daily_valuation"
    if table_exists(conn, "valuation"):
        row = conn.execute(
            "SELECT *, 'valuation' AS table_name FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            data = dict(row)
            data.update(
                {
                    "data_date": data.get("date"),
                    "pe_ratio": data.get("pe"),
                    "pb_ratio": data.get("pb"),
                    "table_name": "valuation",
                }
            )
            return data, "valuation"
    return None, None


def latest_price_date(conn: sqlite3.Connection, code: str) -> str | None:
    dates: list[str] = []
    for table in ("history_price", "eod_price"):
        if table_exists(conn, table):
            row = conn.execute(
                f"SELECT date FROM {table} WHERE code=? AND date IS NOT NULL ORDER BY date DESC LIMIT 1",
                (code,),
            ).fetchone()
            if row and row["date"]:
                dates.append(str(row["date"]))
    return max(dates) if dates else None


def row_excerpt(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    keys = [
        "data_date",
        "date",
        "source",
        "pe_ratio",
        "pe",
        "pb_ratio",
        "pb",
        "dividend_yield",
        "cash_dividend_per_share",
        "official_reference_price",
        "close_price",
    ]
    excerpt = {k: data.get(k) for k in keys if k in data}
    return json.dumps(excerpt, ensure_ascii=False, sort_keys=True)


def audit_code(conn: sqlite3.Connection, code: str, names: dict[str, str], markets: dict[str, str]) -> dict[str, Any]:
    row, table = latest_valuation_row(conn, code)
    quality = build_valuation_quality_payload(row)
    price_date = latest_price_date(conn, code)
    date_mismatch = bool(quality.get("valuation_date") and price_date and str(quality["valuation_date"])[:10] != str(price_date)[:10])
    source = str((row or {}).get("source") or "")
    market = markets.get(code) or "unknown"
    source_mismatch = bool(market in {"上櫃", "otc"} and "TWSE" in source.upper())
    suspicious = bool(
        quality.get("suspicious_flags")
        or quality.get("dividend_yield_suspicious")
        or quality.get("pe_extreme_outlier")
        or quality.get("pb_extreme_outlier")
        or source_mismatch
    )
    if not row:
        status = "unavailable"
    elif suspicious:
        status = "suspicious"
    else:
        status = "normal"
    return {
        "code": code,
        "name": names.get(code) or "",
        "market_type": market,
        "valuation_table": table or "",
        "source_id": quality.get("source_id") or source,
        "source_type": quality.get("source_type") or "",
        "source_market": quality.get("source_market") or "",
        "source_name": quality.get("source_name") or source,
        "raw_source": source,
        "valuation_date": quality.get("valuation_date") or "",
        "valuation_close_price": quality.get("valuation_close_price"),
        "current_price_date": price_date or "",
        "pe_raw": quality.get("pe_raw"),
        "pb_raw": quality.get("pb_raw"),
        "dividend_yield_raw": quality.get("dividend_yield_raw"),
        "pe_normalized": quality.get("pe_normalized"),
        "pb_normalized": quality.get("pb_normalized"),
        "dividend_yield_normalized": quality.get("dividend_yield_normalized"),
        "pe_status": quality.get("pe_status"),
        "pb_status": quality.get("pb_status"),
        "dividend_yield_status": quality.get("dividend_yield_status"),
        "dividend_yield_source": quality.get("dividend_yield_source") or "",
        "dividend_yield_unavailable_reason": quality.get("dividend_yield_unavailable_reason") or "",
        "can_derive_dividend_yield": bool(quality.get("can_derive_dividend_yield")),
        "derived_dividend_yield": quality.get("derived_dividend_yield"),
        "derived_dividend_yield_status": quality.get("derived_dividend_yield_status") or "",
        "derived_dividend_yield_reason": quality.get("derived_dividend_yield_reason") or "",
        "derived_cash_dividend_per_share": quality.get("derived_cash_dividend_per_share"),
        "derived_reference_price": quality.get("derived_reference_price"),
        "suspicious_flags": "|".join(quality.get("suspicious_flags") or []),
        "parse_warnings": "|".join(quality.get("parse_warnings") or []),
        "unavailable_reason": quality.get("unavailable_reason") or ("valuation_missing" if not row else ""),
        "source_unit_unknown": bool(quality.get("source_unit_unknown")),
        "dividend_yield_suspicious": bool(quality.get("dividend_yield_suspicious")),
        "pe_loss_or_non_meaningful": bool(quality.get("pe_loss_or_non_meaningful")),
        "pe_high_but_not_outlier": bool(quality.get("pe_high_but_not_outlier")),
        "pe_extreme_outlier": bool(quality.get("pe_extreme_outlier")),
        "pb_net_worth_non_positive": bool(quality.get("pb_net_worth_non_positive")),
        "pb_extreme_outlier": bool(quality.get("pb_extreme_outlier")),
        "field_shift_suspicious": bool(quality.get("field_shift_suspicious")),
        "pe_pb_possible_swap": bool(quality.get("pe_pb_possible_swap")),
        "source_mismatch": source_mismatch,
        "date_mismatch": date_mismatch,
        "status": status,
        "warnings": "|".join([x for x in [
            "source_mismatch" if source_mismatch else "",
            "date_mismatch" if date_mismatch else "",
        ] if x]),
        "raw_row_excerpt": row_excerpt(row),
    }


def summarize(rows: list[dict[str, Any]], meta: dict[str, Any], db_path: Path, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(row["status"] for row in rows)
    dy_status_counts = Counter(str(row.get("dividend_yield_status") or "") for row in rows)
    dy_reason_counts = Counter(
        str(row.get("dividend_yield_unavailable_reason") or "normal")
        for row in rows
        if str(row.get("dividend_yield_status") or "") != "normal"
    )
    dy_unavailable_count = sum(
        1
        for row in rows
        if str(row.get("dividend_yield_status") or "") in {"unavailable", "parse_warning", "source_unit_unknown", "dividend_yield_suspicious"}
    )
    derived_status_counts = Counter(str(row.get("derived_dividend_yield_status") or "") for row in rows)
    derived_reason_counts = Counter(
        str(row.get("derived_dividend_yield_reason") or "normal")
        for row in rows
        if str(row.get("derived_dividend_yield_status") or "") != "normal"
    )
    dy_source_counts = Counter(str(row.get("dividend_yield_source") or "none") for row in rows)
    derived_by_market = Counter(
        str(row.get("source_market") or "unknown")
        for row in rows
        if row.get("dividend_yield_source") == "derived_from_official_cash_dividend"
    )
    no_selected_valuation_count = sum(1 for row in rows if not row.get("valuation_table"))
    reason_count_total = sum(dy_reason_counts.values())
    unclassified_count = max(0, dy_unavailable_count - reason_count_total)
    summary = {
        "db_path": str(db_path),
        "db_readonly": True,
        "db_unchanged": before == after,
        "db_before": before,
        "db_after": after,
        "total_codes": len(rows),
        "checked_codes": len(rows),
        "skipped_codes": 0,
        "normal_count": counts.get("normal", 0),
        "unavailable_count": counts.get("unavailable", 0),
        "suspicious_count": counts.get("suspicious", 0),
        "dividend_yield_normal_count": dy_status_counts.get("normal", 0),
        "dividend_yield_unavailable_count": dy_unavailable_count,
        "no_selected_valuation_count": no_selected_valuation_count,
        "dividend_yield_suspicious_count": sum(1 for r in rows if r["dividend_yield_suspicious"]),
        "dividend_yield_unavailable_reason_counts": dict(sorted(dy_reason_counts.items())),
        "dividend_yield_unavailable_reason_count_total": reason_count_total,
        "dividend_yield_unclassified_count": unclassified_count,
        "dividend_yield_zero_count": sum(1 for r in rows if r["dividend_yield_normalized"] == 0),
        "dividend_yield_source_counts": dict(sorted(dy_source_counts.items())),
        "derived_dividend_yield_normal_count": derived_status_counts.get("normal", 0),
        "derived_dividend_yield_suspicious_count": derived_status_counts.get("dividend_yield_suspicious", 0),
        "derived_dividend_yield_reason_counts": dict(sorted(derived_reason_counts.items())),
        "derived_dividend_yield_by_market": dict(sorted(derived_by_market.items())),
        "derived_dividend_yield_used_count": dy_source_counts.get("derived_from_official_cash_dividend", 0),
        "official_zero_dividend_count": sum(1 for r in rows if r.get("derived_dividend_yield_reason") == "official_zero_dividend"),
        "missing_official_dividend_or_price_count": sum(
            1 for r in rows if r.get("derived_dividend_yield_reason") == "missing_official_dividend_or_price"
        ),
        "source_unit_unknown_count": sum(1 for r in rows if r["source_unit_unknown"]),
        "parse_warning_count": sum(1 for r in rows if r["parse_warnings"]),
        "fullwidth_digit_parse_warning_count": sum(1 for r in rows if "fullwidth_digit_normalized" in str(r["parse_warnings"])),
        "pe_loss_or_non_meaningful_count": sum(1 for r in rows if r["pe_loss_or_non_meaningful"]),
        "pe_high_but_not_outlier_count": sum(1 for r in rows if r["pe_high_but_not_outlier"]),
        "pe_extreme_outlier_count": sum(1 for r in rows if r["pe_extreme_outlier"]),
        "pb_net_worth_non_positive_count": sum(1 for r in rows if r["pb_net_worth_non_positive"]),
        "pb_extreme_outlier_count": sum(1 for r in rows if r["pb_extreme_outlier"]),
        "source_mismatch_count": sum(1 for r in rows if r["source_mismatch"]),
        "date_mismatch_count": sum(1 for r in rows if r["date_mismatch"]),
        "field_shift_suspicious_count": sum(1 for r in rows if r["field_shift_suspicious"]),
        "pe_pb_possible_swap_count": sum(1 for r in rows if r["pe_pb_possible_swap"]),
        "pe_pb_possible_swap_examples": [r["code"] for r in rows if r["pe_pb_possible_swap"]][:10],
        "top_suspicious_examples": [
            {
                "code": r["code"],
                "dividend_yield_raw": r["dividend_yield_raw"],
                "dividend_yield_unavailable_reason": r.get("dividend_yield_unavailable_reason"),
                "flags": r["suspicious_flags"],
            }
            for r in rows
            if r["status"] == "suspicious"
        ][:10],
        **meta,
    }
    return summary


def write_reports(output_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else ["code", "status"]
    for filename, data_rows in (
        ("valuation_audit_full.csv", rows),
        ("valuation_audit_suspicious.csv", [r for r in rows if r["status"] == "suspicious"]),
        ("valuation_audit_skipped.csv", [r for r in rows if r["status"] == "unavailable"]),
    ):
        with (output_dir / filename).open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data_rows)
    with (output_dir / "valuation_audit_suspicious.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            if row["status"] == "suspicious":
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    sample_lines = []
    by_code = {r["code"]: r for r in rows}
    for code in SAMPLE_CODES:
        row = by_code.get(code)
        if row:
            sample_lines.append(
                f"| {code} | {row['valuation_table']} | {row['pe_normalized']} | {row['pb_normalized']} | {row['dividend_yield_normalized']} | {row['dividend_yield_status']} | {row['suspicious_flags']} |"
            )
    md = [
        "# Valuation Audit Summary",
        "",
        "This report is generated by `scripts/audit_valuation_data.py` in SQLite read-only mode. CSV and JSONL detail outputs are intentionally ignored by Git.",
        "",
        "## Summary",
        "",
        f"- DB read-only: {summary['db_readonly']}",
        f"- DB unchanged after audit: {summary['db_unchanged']}",
        f"- Total codes: {summary['total_codes']}",
        f"- Normal: {summary['normal_count']}",
        f"- Unavailable: {summary['unavailable_count']}",
        f"- Suspicious: {summary['suspicious_count']}",
        f"- Dividend yield normal: {summary['dividend_yield_normal_count']}",
        f"- Dividend yield unavailable/not displayable: {summary['dividend_yield_unavailable_count']}",
        f"- No selected valuation: {summary['no_selected_valuation_count']}",
        f"- Dividend yield suspicious: {summary['dividend_yield_suspicious_count']}",
        f"- Dividend yield unavailable reason counts: {json.dumps(summary['dividend_yield_unavailable_reason_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- Dividend yield reason count total: {summary['dividend_yield_unavailable_reason_count_total']}",
        f"- Dividend yield unclassified count: {summary['dividend_yield_unclassified_count']}",
        f"- Dividend yield source counts: {json.dumps(summary['dividend_yield_source_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- Derived dividend yield used: {summary['derived_dividend_yield_used_count']}",
        f"- Derived dividend yield normal: {summary['derived_dividend_yield_normal_count']}",
        f"- Derived dividend yield suspicious: {summary['derived_dividend_yield_suspicious_count']}",
        f"- Derived dividend yield reason counts: {json.dumps(summary['derived_dividend_yield_reason_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- Derived dividend yield by market: {json.dumps(summary['derived_dividend_yield_by_market'], ensure_ascii=False, sort_keys=True)}",
        f"- Official zero dividend count: {summary['official_zero_dividend_count']}",
        f"- Missing official dividend or price count: {summary['missing_official_dividend_or_price_count']}",
        f"- Source unit unknown: {summary['source_unit_unknown_count']}",
        f"- PE high but not outlier: {summary['pe_high_but_not_outlier_count']}",
        f"- PE extreme outlier: {summary['pe_extreme_outlier_count']}",
        f"- PB extreme outlier: {summary['pb_extreme_outlier_count']}",
        f"- Field shift suspicious: {summary['field_shift_suspicious_count']}",
        "",
        "## Top Suspicious Examples",
        "",
        "```json",
        json.dumps(summary["top_suspicious_examples"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Sample Codes",
        "",
        "| Code | Table | PE | PB | Yield | Yield Status | Flags |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *sample_lines,
        "",
        "## No-Write Check",
        "",
        f"- Before: size={summary['db_before']['size']} sha256={summary['db_before']['sha256']}",
        f"- After: size={summary['db_after']['size']} sha256={summary['db_after']['sha256']}",
    ]
    (output_dir / "valuation_audit_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only audit for valuation PE/PB/dividend yield data.")
    parser.add_argument("--scope", choices=["all", "listed", "otc"], default="all")
    parser.add_argument("--codes", default="", help="Comma-separated code list. Overrides scope universe when provided.")
    parser.add_argument("--output-dir", default="docs/audit/valuation")
    parser.add_argument("--dry-run", action="store_true", help="Alias for report-only behavior; DB writes are never supported.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_db_path()
    if not db_path.exists():
        raise SystemExit("db_path_unknown")
    before = {"size": db_path.stat().st_size, "sha256": sha256_file(db_path)}
    explicit_codes = [part.strip() for part in str(args.codes or "").split(",") if part.strip()]
    with connect_readonly(db_path) as conn:
        names = code_name_map(conn)
        markets = market_map(conn)
        codes, meta = collect_universe(conn, args.scope, explicit_codes)
        if not codes:
            summary = summarize([], {**meta, "skipped_reason": "universe_empty"}, db_path, before, before)
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        rows = [audit_code(conn, code, names, markets) for code in codes]
    after = {"size": db_path.stat().st_size, "sha256": sha256_file(db_path)}
    summary = summarize(rows, meta, db_path, before, after)
    write_reports(REPO_ROOT / args.output_dir, rows, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
