from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.twse import fetch_twse_stock_month_rows  # noqa: E402
from core.db import assert_db_integrity, db  # noqa: E402
from core.market_foundation_schema import upsert_daily_ohlcv_rows  # noqa: E402


def parse_codes(raw: str) -> list[str]:
    return sorted({
        part.strip().zfill(4)
        for part in str(raw or "").replace(";", ",").split(",")
        if part.strip().isdigit()
    })


def find_candidates(codes: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with db() as conn:
        for code in codes:
            first_official = conn.execute(
                """
                SELECT MIN(date) AS first_date
                FROM history_price
                WHERE code=?
                  AND (UPPER(COALESCE(source,'')) LIKE '%TWSE%'
                       OR UPPER(COALESCE(source,'')) LIKE '%TPEX%')
                  AND UPPER(COALESCE(source_quality,'')) IN ('OFFICIAL','OK','HIGH')
                """,
                (code,),
            ).fetchone()
            first_date = str(first_official["first_date"] or "") if first_official else ""
            if not first_date:
                continue
            rows = conn.execute(
                """
                SELECT *
                FROM history_price
                WHERE code=? AND date<?
                  AND UPPER(COALESCE(source,'')) NOT LIKE '%TWSE%'
                  AND UPPER(COALESCE(source,'')) NOT LIKE '%TPEX%'
                ORDER BY date
                """,
                (code, first_date),
            ).fetchall()
            out.extend({**dict(row), "first_official_date": first_date} for row in rows)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify weak-source rows before the first official TWSE/TPEx row; "
            "optionally quarantine dates absent from a successful official monthly response."
        )
    )
    parser.add_argument("--codes", required=True, help="Comma-separated stock codes; no broad default is allowed.")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report", default="logs/ohlcv_validation/prelisting_review.json")
    args = parser.parse_args()

    assert_db_integrity()
    codes = parse_codes(args.codes)
    if not codes:
        raise SystemExit("No valid stock codes supplied")
    candidates = find_candidates(codes)
    months = sorted({(str(row["code"]), f"{str(row['date'])[:7]}-01") for row in candidates})
    official_dates: dict[tuple[str, str], set[str]] = {}
    fetches: list[dict[str, Any]] = []
    official_rows: list[dict[str, Any]] = []
    for code, month in months:
        result = fetch_twse_stock_month_rows(code, month)
        rows = list(result.get("rows") or [])
        fetches.append({
            "code": code,
            "month": month[:7],
            "ok": bool(result.get("ok")),
            "row_count": len(rows),
            "error": result.get("error"),
        })
        if result.get("ok"):
            official_dates[(code, month[:7])] = {str(row.get("date")) for row in rows}
            official_rows.extend(rows)
        time.sleep(0.25)

    verified_absent = [
        row
        for row in candidates
        if (str(row["code"]), str(row["date"])[:7]) in official_dates
        and str(row["date"]) not in official_dates[(str(row["code"]), str(row["date"])[:7])]
    ]
    unresolved = [
        row
        for row in candidates
        if (str(row["code"]), str(row["date"])[:7]) not in official_dates
    ]
    official_written = 0
    quarantined = 0
    if args.write:
        fetched_at = time.time()
        for row in official_rows:
            row["updated_at"] = fetched_at
            row["fetched_at"] = fetched_at
        with db() as conn:
            if official_rows:
                official_written = upsert_daily_ohlcv_rows(conn, official_rows)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS invalid_history_price_quarantine(
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    source TEXT,
                    reason TEXT NOT NULL,
                    row_json TEXT NOT NULL,
                    quarantined_at REAL NOT NULL,
                    PRIMARY KEY(code,date)
                )
                """
            )
            quarantined_at = time.time()
            for row in verified_absent:
                reason = (
                    "weak-source row predates first official listed-market row and is absent "
                    "from a successful official monthly response"
                )
                conn.execute(
                    """
                    INSERT INTO invalid_history_price_quarantine(
                        code,date,source,reason,row_json,quarantined_at
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(code,date) DO UPDATE SET
                        source=excluded.source,reason=excluded.reason,
                        row_json=excluded.row_json,quarantined_at=excluded.quarantined_at
                    """,
                    (
                        row["code"],
                        row["date"],
                        row.get("source"),
                        reason,
                        json.dumps(row, ensure_ascii=False, default=str),
                        quarantined_at,
                    ),
                )
                conn.execute(
                    "DELETE FROM history_price WHERE code=? AND date=? AND source=?",
                    (row["code"], row["date"], row.get("source")),
                )
                quarantined += 1
            conn.commit()
        assert_db_integrity()

    report = {
        "ok": not unresolved,
        "dry_run": not args.write,
        "writes_db": bool(args.write and (official_written or quarantined)),
        "codes": codes,
        "candidate_count": len(candidates),
        "verified_absent_count": len(verified_absent),
        "unresolved_count": len(unresolved),
        "official_rows_written": official_written,
        "quarantined_and_removed": quarantined,
        "fetches": fetches,
        "verified_absent": verified_absent,
        "unresolved": unresolved,
    }
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "dry_run": report["dry_run"],
        "candidate_count": report["candidate_count"],
        "verified_absent_count": report["verified_absent_count"],
        "unresolved_count": report["unresolved_count"],
        "official_rows_written": report["official_rows_written"],
        "quarantined_and_removed": report["quarantined_and_removed"],
    }, ensure_ascii=False, indent=2))
    print(f"report={report_path}")
    return 0 if report["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
