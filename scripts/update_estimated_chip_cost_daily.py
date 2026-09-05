from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.estimated_chip_cost import calculate_institution_estimated_cost
from core.cost_source_registry import all_cost_types
from repository.estimated_chip_cost_repository import (
    count_rows_by_status,
    ensure_estimated_chip_cost_schema,
    list_history_codes,
    load_cost_input_rows,
    max_trade_dates_per_code_type,
    prune_estimated_chip_cost,
    table_exists,
    upsert_estimated_chip_cost_rows,
)


SAMPLE_CODES = ("3491", "2317", "6757", "2330", "2454")


def parse_codes(raw: str | None, conn: sqlite3.Connection) -> list[str]:
    if raw:
        return [part.strip().zfill(4) for part in raw.split(",") if part.strip()]
    return list_history_codes(conn)


def open_connection(db_path: Path, write: bool) -> sqlite3.Connection:
    if write:
        conn = sqlite3.connect(str(db_path))
    else:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = count_rows_by_status(rows)
    by_type = Counter(row["cost_type"] for row in rows)
    valid_by_type = Counter(
        row["cost_type"]
        for row in rows
        if row.get("cost_status") in {"ok", "estimated", "proxy_only"}
        and row.get("estimated_cost") is not None
    )
    unit_unknown_count = sum(
        1
        for row in rows
        if row.get("cost_type") == "margin_incremental_estimated"
        and row.get("debug_reason") == "unit_unknown"
    )
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["code"] in SAMPLE_CODES and len(samples[row["code"]]) < 6:
            samples[row["code"]].append(
                {
                    "date": row["trade_date"],
                    "type": row["cost_type"],
                    "status": row["cost_status"],
                    "cost": row.get("estimated_cost"),
                    "confidence": row.get("confidence"),
                    "debug": row.get("debug_reason"),
                }
            )
    return {
        "total_rows": len(rows),
        "by_type": dict(sorted(by_type.items())),
        "valid_by_type": dict(sorted(valid_by_type.items())),
        "by_status": by_status,
        "unit_unknown_count": unit_unknown_count,
        "margin_incremental_estimated_valid_count": valid_by_type.get(
            "margin_incremental_estimated", 0
        ),
        "sample_rows": dict(samples),
    }


def write_markdown_report(
    output_path: Path,
    *,
    db_path: Path,
    codes: list[str],
    days: int,
    writes_db: bool,
    rows: list[dict[str, Any]],
    write_count: int,
    prune_deleted: int,
    max_retained_dates: int | None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize_rows(rows)
    lines = [
        "# Estimated Chip Cost Update Report",
        "",
        f"- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- DB: `{db_path}`",
        f"- Codes: {', '.join(codes)}",
        f"- Requested days: {days}",
        f"- writes_db: {str(writes_db).lower()}",
        f"- generated_rows: {summary['total_rows']}",
        f"- write_count: {write_count}",
        f"- prune_deleted: {prune_deleted}",
        f"- max_retained_trade_dates_per_code_type: {max_retained_dates if max_retained_dates is not None else '--'}",
        "",
        "## Cost Type Counts",
        "",
        "| cost_type | generated | valid_cost_rows |",
        "| --- | ---: | ---: |",
    ]
    by_type = summary["by_type"]
    valid_by_type = summary["valid_by_type"]
    for cost_type in all_cost_types():
        lines.append(f"| {cost_type} | {by_type.get(cost_type, 0)} | {valid_by_type.get(cost_type, 0)} |")

    lines.extend(["", "## Status Counts", "", "| key | count |", "| --- | ---: |"])
    for key, count in summary["by_status"].items():
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Canonical Scope",
            "",
            "- Only foreign/trust official incremental-position estimates are persisted by this command.",
            "- Dashboard, detail, Bot and LINE read the same exact-date canonical rows.",
            "",
            "## Safety Confirmations",
            "",
            "- Moving-average inventory method is used; FIFO is not claimed.",
            "- Margin and broker-branch cost estimates are not produced by this canonical path.",
            "- Price-volume / POC is a separate market reference and is never relabeled as main-force cost.",
            "- No volume residual method is used.",
            "- No external API, Goodinfo scraping, captcha bypass, or Cloudflare bypass is used.",
            "",
            "## Sample Rows",
            "",
        ]
    )
    for code, items in summary["sample_rows"].items():
        lines.append(f"### {code}")
        lines.append("")
        lines.append("| date | type | status | cost | confidence | debug |")
        lines.append("| --- | --- | --- | ---: | --- | --- |")
        for item in items:
            lines.append(
                f"| {item['date']} | {item['type']} | {item['status']} | {item['cost'] if item['cost'] is not None else '--'} | {item['confidence']} | {item['debug']} |"
            )
        lines.append("")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Update estimated chip cost rows from local free public data.")
    parser.add_argument("--db", default=str(ROOT / "review_src" / "data" / "taiwan50.db"))
    parser.add_argument("--codes", default=None, help="Comma-separated stock codes. Defaults to all history_price codes.")
    parser.add_argument("--days", type=int, default=240)
    parser.add_argument("--dry-run", action="store_true", help="Do not write DB. This is the default unless --write is set.")
    parser.add_argument("--write", action="store_true", help="Write rows with idempotent UPSERT and prune latest 720 dates.")
    parser.add_argument("--output", default=str(ROOT / "docs" / "ESTIMATED_CHIP_COST_UPDATE_REPORT.md"))
    parser.add_argument("--poc-windows", default="60,120", help="Reserved for existing price-volume windows; no custom POC calculation is introduced.")
    args = parser.parse_args()

    db_path = Path(args.db)
    output_path = Path(args.output)
    writes_db = bool(args.write)
    if args.dry_run and args.write:
        print("ERROR: choose only one of --dry-run or --write")
        return 1
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return 1

    with open_connection(db_path, writes_db) as conn:
        if writes_db:
            ensure_estimated_chip_cost_schema(conn)
        codes = parse_codes(args.codes, conn)
        inputs = load_cost_input_rows(conn, codes, args.days)
        rows = [
            *calculate_institution_estimated_cost(inputs, "foreign_estimated"),
            *calculate_institution_estimated_cost(inputs, "trust_estimated"),
        ]
        write_count = 0
        prune_deleted = 0
        max_retained_dates = None
        if writes_db:
            write_count = upsert_estimated_chip_cost_rows(conn, rows)
            prune_deleted = prune_estimated_chip_cost(conn, codes, keep_days=720)
            conn.execute("PRAGMA optimize;")
            conn.commit()
            max_retained_dates = max_trade_dates_per_code_type(conn)
        else:
            max_retained_dates = (
                max_trade_dates_per_code_type(conn)
                if table_exists(conn, "estimated_chip_cost_daily")
                else None
            )

    write_markdown_report(
        output_path,
        db_path=db_path,
        codes=codes,
        days=args.days,
        writes_db=writes_db,
        rows=rows,
        write_count=write_count,
        prune_deleted=prune_deleted,
        max_retained_dates=max_retained_dates,
    )
    summary = summarize_rows(rows)
    print(
        {
            "ok": True,
            "writes_db": writes_db,
            "codes": len(codes),
            "generated_rows": summary["total_rows"],
            "write_count": write_count,
            "unit_unknown_count": summary["unit_unknown_count"],
            "margin_incremental_estimated_valid_count": summary[
                "margin_incremental_estimated_valid_count"
            ],
            "output": str(output_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
