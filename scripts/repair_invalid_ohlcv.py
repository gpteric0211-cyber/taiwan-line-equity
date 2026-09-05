from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.twse import fetch_twse_stock_month_rows  # noqa: E402
from core.components import read_components  # noqa: E402
from core.data_quality import assess_daily_ohlcv  # noqa: E402
from core.db import assert_db_integrity, db  # noqa: E402
from core.market_foundation_schema import upsert_daily_ohlcv_rows  # noqa: E402
from core.market_session import is_taiwan_trading_day  # noqa: E402


def parse_codes(raw: str | None) -> list[str]:
    if not raw:
        return sorted({str(item["code"]).zfill(4) for item in read_components()})
    return sorted({part.strip().zfill(4) for part in raw.replace(";", ",").split(",") if part.strip().isdigit()})


def row_problem(row: dict[str, Any]) -> str | None:
    quality = assess_daily_ohlcv(row)
    reasons = list(quality.get("reasons") or [])
    normalized_date = quality.get("date")
    if normalized_date:
        try:
            if not is_taiwan_trading_day(date.fromisoformat(str(normalized_date))):
                reasons.append("not_an_official_trading_day")
        except ValueError:
            reasons.append("invalid_or_missing_date")
    return ",".join(dict.fromkeys(reasons)) if reasons else None


def audit_rows(codes: list[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in codes)
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM history_price WHERE code IN ({placeholders}) ORDER BY code,date",
            codes,
        ).fetchall()
    invalid: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        problem = row_problem(row)
        if problem:
            invalid.append({
                "code": str(row.get("code") or "").zfill(4),
                "date": row.get("date"),
                "source": row.get("source"),
                "reason": problem,
                "row": row,
            })
    return invalid


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and repair invalid Taiwan-stock daily OHLCV rows from explicit-date TWSE monthly data.")
    parser.add_argument("--codes", help="Comma-separated codes; defaults to current Taiwan 50 components.")
    parser.add_argument("--write", action="store_true", help="Write official replacement rows. Default is read-only audit.")
    parser.add_argument(
        "--remove-unrepairable",
        action="store_true",
        help="Quarantine and remove rows that remain invalid after attempted official repair.",
    )
    parser.add_argument("--report", default="logs/ohlcv_validation/latest.json")
    args = parser.parse_args()

    assert_db_integrity()
    codes = parse_codes(args.codes)
    before = audit_rows(codes)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in before:
        grouped[(str(item["code"]), f"{str(item['date'])[:7]}-01")].append(item)

    fetches: list[dict[str, Any]] = []
    rows_written = 0
    if args.write:
        official_rows: list[dict[str, Any]] = []
        for code, month in sorted(grouped):
            result = fetch_twse_stock_month_rows(code, month)
            fetches.append({
                "code": code,
                "month": month[:7],
                "ok": bool(result.get("ok")),
                "row_count": int(result.get("row_count") or 0),
                "error": result.get("error"),
            })
            official_rows.extend(result.get("rows") or [])
            time.sleep(0.25)
        fetched_at = time.time()
        for row in official_rows:
            row["updated_at"] = fetched_at
            row["fetched_at"] = fetched_at
        if official_rows:
            with db() as conn:
                rows_written = upsert_daily_ohlcv_rows(conn, official_rows)
                conn.commit()

    after = audit_rows(codes)
    quarantined = 0
    if args.write and args.remove_unrepairable and after:
        removable = list(after)
        if removable:
            with db() as conn:
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
                for item in removable:
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
                            item["code"],
                            item["date"],
                            item.get("source"),
                            item["reason"],
                            json.dumps(item["row"], ensure_ascii=False, default=str),
                            quarantined_at,
                        ),
                    )
                    conn.execute(
                        "DELETE FROM history_price WHERE code=? AND date=?",
                        (item["code"], item["date"]),
                    )
                    quarantined += 1
                conn.commit()
            after = audit_rows(codes)
    report = {
        "ok": not after,
        "dry_run": not args.write,
        "writes_db": bool(args.write and (rows_written or quarantined)),
        "code_count": len(codes),
        "invalid_before_count": len(before),
        "invalid_after_count": len(after),
        "official_rows_written": rows_written,
        "quarantined_and_removed": quarantined,
        "fetches": fetches,
        "invalid_before": before,
        "invalid_after": after,
    }
    report_path = ROOT / args.report if not Path(args.report).is_absolute() else Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "ok", "dry_run", "writes_db", "code_count", "invalid_before_count",
        "invalid_after_count", "official_rows_written",
        "quarantined_and_removed",
    )}, ensure_ascii=False, indent=2))
    print(f"report={report_path}")
    return 0 if report["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
