from __future__ import annotations

import argparse
import csv
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


TDR_INDUSTRY_CODE = "91"
SAMPLE_LIMIT = 30
MOJIBAKE_MARKERS = (
    "鍙", "鐧", "鑲", "鍏", "鎶", "妫", "鏂", "闆", "櫃", "櫉",
    "", "", "", "", "绉", "鏈", "闄",
)

TWSE_PARSER_COLUMN_MAPPING = {
    "Code": "symbol",
    "Name": "name",
    "Date": "data_date",
    "ClosingPrice": "close_price",
    "CashDividend": "cash_dividend_per_share",
    "DividendPerShare": "cash_dividend_per_share",
    "DividendYield": "dividend_yield",
    "DividendYear": "dividend_year",
    "PEratio": "pe_ratio",
    "PBratio": "pb_ratio",
    "FiscalYearQuarter": "financial_year_quarter",
}

TPEX_PARSER_COLUMN_MAPPING = {
    "Date": "data_date",
    "SecuritiesCompanyCode": "symbol",
    "CompanyName": "name",
    "DividendPerShare": "cash_dividend_per_share",
    "PriceEarningRatio": "pe_ratio",
    "YieldRatio": "dividend_yield",
    "PriceBookRatio": "pb_ratio",
}


def resolve_db_path() -> Path:
    for key in ("DB_PATH", "TAIWAN50_DB_PATH"):
        raw = os.getenv(key)
        if raw:
            p = Path(raw.strip().strip('"').strip("'"))
            return p if p.is_absolute() else (REPO_ROOT / p)
    for p in (REVIEW_SRC / "data" / "taiwan50.db", REVIEW_SRC / "taiwan50.db"):
        if p.exists():
            return p
    raise SystemExit("db_path_unknown")


def connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def safe_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def norm_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def clean_report_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return fallback
    return text


def collect_universe(conn: sqlite3.Connection) -> list[str]:
    codes: set[str] = set()
    sources = [
        ("stock_theme_profile", "code"),
        ("stock_industry_profile", "code"),
        ("watchlist", "code"),
        ("twse_daily_valuation", "symbol"),
        ("valuation", "code"),
    ]
    for table, col in sources:
        if not table_exists(conn, table):
            continue
        for row in safe_rows(conn, f"SELECT DISTINCT {col} AS code FROM {table} WHERE {col} IS NOT NULL"):
            code = norm_code(row["code"])
            if code.isdigit():
                codes.add(code)
    component_path = REVIEW_SRC / "data" / "taiwan50_components.csv"
    if component_path.exists():
        with component_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                code = norm_code(row.get("code") or row.get("代號"))
                if code.isdigit():
                    codes.add(code)
    return sorted(codes)


def profile_for_code(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    if table_exists(conn, "stock_industry_profile"):
        row = conn.execute("SELECT * FROM stock_industry_profile WHERE code=? LIMIT 1", (code,)).fetchone()
        if row:
            return dict(row)
    return {}


def name_for_code(conn: sqlite3.Connection, code: str, profile: dict[str, Any]) -> str:
    for table, col, name_col in (
        ("twse_daily_valuation", "symbol", "name"),
        ("valuation", "code", "code"),
        ("watchlist", "code", "name"),
        ("eod_price", "code", "name"),
    ):
        if not table_exists(conn, table):
            continue
        row = conn.execute(f"SELECT {name_col} AS name FROM {table} WHERE {col}=? LIMIT 1", (code,)).fetchone()
        if row and row["name"]:
            return clean_report_text(row["name"])
    return clean_report_text(profile.get("name"))


def selected_valuation_row(conn: sqlite3.Connection, code: str) -> tuple[dict[str, Any] | None, str | None]:
    if table_exists(conn, "twse_daily_valuation"):
        row = conn.execute(
            "SELECT *, 'twse_daily_valuation' AS table_name FROM twse_daily_valuation WHERE symbol=? ORDER BY data_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            return dict(row), "twse_daily_valuation"
    if table_exists(conn, "valuation"):
        row = conn.execute(
            "SELECT *, 'valuation' AS table_name FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            data = dict(row)
            data.update({"data_date": data.get("date"), "pe_ratio": data.get("pe"), "pb_ratio": data.get("pb")})
            return data, "valuation"
    return None, None


def latest_legacy_row(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    if not table_exists(conn, "valuation"):
        return None
    row = conn.execute("SELECT * FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    return dict(row) if row else None


def parser_mapping_for_source(source_type: str | None) -> dict[str, str]:
    if source_type == "official_tpex":
        return TPEX_PARSER_COLUMN_MAPPING
    if source_type == "official_twse":
        return TWSE_PARSER_COLUMN_MAPPING
    return {}


def classify_no_valuation_reason(profile: dict[str, Any], code: str) -> str:
    market = str(profile.get("market") or "").strip()
    industry_code = str(profile.get("industry_code") or "").strip()
    industry = str(profile.get("industry") or "").strip()
    if industry_code == TDR_INDUSTRY_CODE or "存託" in industry or code.startswith("91"):
        return "unsupported_security_type"
    if not market:
        return "missing_market_type"
    if market in {"上市", "上櫃", "listed", "otc", "TSE", "TPEX"}:
        return "official_absent"
    return "cannot_verify"


def reason_for_unavailable(row: dict[str, Any] | None, quality: dict[str, Any]) -> str:
    if not row:
        return "source_row_missing"
    return str(quality.get("dividend_yield_unavailable_reason") or "cannot_verify")


def investigate_code(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    row, table = selected_valuation_row(conn, code)
    quality = build_valuation_quality_payload(row)
    profile = profile_for_code(conn, code)
    name = name_for_code(conn, code, profile)
    source_type = str(quality.get("source_type") or "")
    source_name = str(quality.get("source_name") or "")
    market_type = clean_report_text(profile.get("market") or quality.get("source_market") or "unknown", fallback="unknown")
    dy_status = str(quality.get("dividend_yield_status") or "")
    dy_reason = reason_for_unavailable(row, quality) if dy_status != "normal" else ""
    no_row_reason = classify_no_valuation_reason(profile, code) if not row else ""
    official_row = row if table == "twse_daily_valuation" else None
    legacy_row = latest_legacy_row(conn, code)
    return {
        "code": code,
        "name": clean_report_text(name),
        "market_type": market_type,
        "industry_code": profile.get("industry_code"),
        "industry": clean_report_text(profile.get("industry")),
        "selected_table": table or "",
        "selected_source_type": source_type,
        "selected_source_name": source_name,
        "valuation_date": quality.get("valuation_date") or "",
        "pe_raw": quality.get("pe_raw"),
        "pb_raw": quality.get("pb_raw"),
        "dividend_yield_raw": quality.get("dividend_yield_raw"),
        "pe_status": quality.get("pe_status"),
        "pb_status": quality.get("pb_status"),
        "dividend_yield_status": dy_status,
        "dividend_yield_source": quality.get("dividend_yield_source") or "",
        "dividend_yield_unavailable_reason": dy_reason,
        "can_derive_dividend_yield": bool(quality.get("can_derive_dividend_yield")),
        "derived_dividend_yield": quality.get("derived_dividend_yield"),
        "derived_dividend_yield_status": quality.get("derived_dividend_yield_status") or "",
        "derived_dividend_yield_reason": quality.get("derived_dividend_yield_reason") or "",
        "derived_cash_dividend_per_share": quality.get("derived_cash_dividend_per_share"),
        "derived_reference_price": quality.get("derived_reference_price"),
        "suspicious_flags": "|".join(quality.get("suspicious_flags") or []),
        "parse_warnings": "|".join(quality.get("parse_warnings") or []),
        "official_twse_found": bool(table == "twse_daily_valuation" and source_type == "official_twse"),
        "official_tpex_found": bool(table == "twse_daily_valuation" and source_type == "official_tpex"),
        "found_in_profile": bool(profile),
        "found_in_theme_profile": bool(
            table_exists(conn, "stock_theme_profile")
            and conn.execute("SELECT 1 FROM stock_theme_profile WHERE code=? LIMIT 1", (code,)).fetchone()
        ),
        "found_in_valuation_table": bool(row),
        "no_selected_valuation_reason": no_row_reason,
        "official_raw_row": json.dumps(official_row or {}, ensure_ascii=False, sort_keys=True),
        "legacy_raw_row": json.dumps(legacy_row or {}, ensure_ascii=False, sort_keys=True),
        "parser_column_mapping": json.dumps(parser_mapping_for_source(source_type), ensure_ascii=False, sort_keys=True),
    }


def write_outputs(output_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suspicious = [
        r for r in records
        if r["dividend_yield_status"] == "dividend_yield_suspicious" or "dividend_yield_unit_or_field_shift_suspicious" in r["suspicious_flags"]
    ]
    unavailable = [
        r for r in records
        if r["selected_table"] and r["dividend_yield_status"] in {"unavailable", "parse_warning", "source_unit_unknown", "dividend_yield_suspicious"}
    ]
    no_selected = [r for r in records if not r["selected_table"]]
    details = [
        *(dict(r, category="suspicious_yield") for r in suspicious),
        *(dict(r, category="dividend_yield_unavailable") for r in unavailable),
        *(dict(r, category="no_selected_valuation") for r in no_selected),
    ]
    fieldnames = sorted({key for row in details for key in row}) if details else ["category", "code"]
    with (output_dir / "valuation_gap_details.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(details)
    with (output_dir / "valuation_gap_details.jsonl").open("w", encoding="utf-8") as fh:
        for row in details:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    reason_counts = Counter(r["dividend_yield_unavailable_reason"] or "normal" for r in unavailable)
    derived_reason_counts = Counter(
        r["derived_dividend_yield_reason"] or "normal"
        for r in records
        if r.get("derived_dividend_yield_status") != "normal"
    )
    dy_source_counts = Counter(r.get("dividend_yield_source") or "none" for r in records)
    no_selected_reasons = Counter(r["no_selected_valuation_reason"] or "cannot_verify" for r in no_selected)
    reason_total = sum(reason_counts.values())
    summary = {
        "total_records": len(records),
        "suspicious_yield_count": len(suspicious),
        "dividend_yield_unavailable_count": len(unavailable),
        "dividend_yield_unavailable_reason_counts": dict(sorted(reason_counts.items())),
        "dividend_yield_reason_count_total": reason_total,
        "dividend_yield_unclassified_count": max(0, len(unavailable) - reason_total),
        "dividend_yield_source_counts": dict(sorted(dy_source_counts.items())),
        "derived_dividend_yield_used_count": dy_source_counts.get("derived_from_official_cash_dividend", 0),
        "derived_dividend_yield_reason_counts": dict(sorted(derived_reason_counts.items())),
        "no_selected_valuation_count": len(no_selected),
        "no_selected_valuation_reason_counts": dict(sorted(no_selected_reasons.items())),
        "suspicious_yield_sample": suspicious[:SAMPLE_LIMIT],
        "unavailable_sample": unavailable[:SAMPLE_LIMIT],
        "no_selected_sample": no_selected[:SAMPLE_LIMIT],
    }
    md = [
        "# Valuation Gap Summary",
        "",
        "Generated by `scripts/investigate_valuation_gaps.py` in SQLite read-only mode.",
        "CSV and JSONL detail outputs are ignored by Git.",
        "",
        "## Counts",
        "",
        f"- Total records checked: {summary['total_records']}",
        f"- Suspicious yield: {summary['suspicious_yield_count']}",
        f"- Dividend yield unavailable/not displayable: {summary['dividend_yield_unavailable_count']}",
        f"- Dividend yield unavailable reason counts: {json.dumps(summary['dividend_yield_unavailable_reason_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- Reason count total: {summary['dividend_yield_reason_count_total']}",
        f"- Unclassified count: {summary['dividend_yield_unclassified_count']}",
        f"- Dividend yield source counts: {json.dumps(summary['dividend_yield_source_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- Derived dividend yield used: {summary['derived_dividend_yield_used_count']}",
        f"- Derived dividend yield reason counts: {json.dumps(summary['derived_dividend_yield_reason_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- No selected valuation: {summary['no_selected_valuation_count']}",
        f"- No selected valuation reason counts: {json.dumps(summary['no_selected_valuation_reason_counts'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## Suspicious Yield Sample",
        "",
        "| Code | Name | Source | Date | Raw yield | Reason | Flags |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in suspicious[:SAMPLE_LIMIT]:
        md.append(
            f"| {r['code']} | {r['name']} | {r['selected_source_type']} | {r['valuation_date']} | {r['dividend_yield_raw']} | {r['dividend_yield_unavailable_reason']} | {r['suspicious_flags']} |"
        )
    md.extend([
        "",
        "## Dividend Yield Unavailable Sample",
        "",
        "| Code | Name | Source | Date | Raw yield | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for r in unavailable[:SAMPLE_LIMIT]:
        md.append(
            f"| {r['code']} | {r['name']} | {r['selected_source_type']} | {r['valuation_date']} | {r['dividend_yield_raw']} | {r['dividend_yield_unavailable_reason']} |"
        )
    md.extend([
        "",
        "## No Selected Valuation Sample",
        "",
        "| Code | Name | Market | Industry | Reason |",
        "| --- | --- | --- | --- | --- |",
    ])
    for r in no_selected[:SAMPLE_LIMIT]:
        md.append(
            f"| {r['code']} | {r['name']} | {r['market_type']} | {r['industry']} | {r['no_selected_valuation_reason']} |"
        )
    (output_dir / "valuation_gap_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Investigate remaining valuation dividend-yield gaps.")
    parser.add_argument("--output-dir", default="docs/audit/valuation")
    parser.add_argument("--codes", default="", help="Optional comma-separated code list.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = resolve_db_path()
    with connect_readonly(db_path) as conn:
        codes = [c.strip() for c in str(args.codes or "").split(",") if c.strip()]
        if not codes:
            codes = collect_universe(conn)
        records = [investigate_code(conn, norm_code(code)) for code in codes]
    summary = write_outputs(REPO_ROOT / args.output_dir, records)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
