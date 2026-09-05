from __future__ import annotations

"""Read-only coverage audit for next-session analysis data families."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH  # noqa: E402


TABLE_SPECS: dict[str, tuple[str | None, str | None]] = {
    "history_price": ("date", "code"),
    "technical_indicator_vector_daily": ("trade_date", "stock_code"),
    "technical_indicator_component": ("trade_date", "stock_code"),
    "daily_technical_snapshot": ("trade_date", "code"),
    "institution_activity_daily": ("trade_date", "code"),
    "credit_balance_daily": ("trade_date", "code"),
    "estimated_chip_cost_daily": ("trade_date", "code"),
    "tdcc_holding_distribution": ("date", "code"),
    "tdcc_equity_summary": ("date", "code"),
    "valuation": ("date", "code"),
    "twse_daily_valuation": ("data_date", "symbol"),
    "global_market_daily_snapshot": ("market_date", "ticker"),
    "taifex_night_daily_snapshot": ("trade_date", "contract"),
    "research_news_item": ("published_at", None),
    "event_cluster": (None, None),
    "event_revision": ("available_at", None),
    "news_retrieval_run": ("scheduled_for", None),
    "premarket_intelligence_artifact": ("target_trade_date", None),
    "analysis_target_prediction": ("created_at", None),
    "analysis_target_outcome": ("prediction_trade_date", "stock_code"),
    "prediction_snapshot_manifest": ("created_at", None),
    "outcome_snapshot_manifest": ("created_at", None),
    "statistical_gate_manifest": ("sealed_at", None),
    "statistical_gate_holdout_member": (None, None),
    "statistical_evaluation_manifest": ("created_at", None),
}


def _quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quoted(table)})")}


def _coverage(
    conn: sqlite3.Connection,
    table: str,
    date_column: str | None,
    entity_column: str | None,
) -> dict[str, Any]:
    if not _table_exists(conn, table):
        return {"exists": False, "rows": 0}
    quoted_table = _quoted(table)
    result: dict[str, Any] = {
        "exists": True,
        "rows": int(conn.execute(f"SELECT COUNT(*) FROM {quoted_table}").fetchone()[0]),
    }
    if date_column:
        quoted_date = _quoted(date_column)
        minimum, maximum, dates = conn.execute(
            f"SELECT MIN({quoted_date}),MAX({quoted_date}),COUNT(DISTINCT {quoted_date}) "
            f"FROM {quoted_table}"
        ).fetchone()
        result.update(min_date=minimum, max_date=maximum, distinct_dates=int(dates or 0))
    if entity_column:
        quoted_entity = _quoted(entity_column)
        result["distinct_entities"] = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT {quoted_entity}) FROM {quoted_table}"
            ).fetchone()[0]
        )
    return result


def build_report(database: Path, *, full_integrity: bool = False) -> dict[str, Any]:
    resolved = database.expanduser().resolve()
    uri = f"{resolved.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=60) as conn:
        integrity_pragma = "integrity_check" if full_integrity else "quick_check"
        integrity = [str(row[0]) for row in conn.execute(f"PRAGMA {integrity_pragma}")]
        coverage = {
            table: _coverage(conn, table, date_column, entity_column)
            for table, (date_column, entity_column) in TABLE_SPECS.items()
        }
        credit_quality = {}
        if _table_exists(conn, "credit_balance_daily"):
            credit_columns = _columns(conn, "credit_balance_daily")
            credit_quality = {
                "official_rows": int(conn.execute(
                    "SELECT COUNT(*) FROM credit_balance_daily WHERE source_quality='official'"
                ).fetchone()[0]),
                "utilization_rows": (
                    int(conn.execute(
                        """SELECT COUNT(*) FROM credit_balance_daily
                           WHERE margin_utilization_pct IS NOT NULL
                              OR short_utilization_pct IS NOT NULL"""
                    ).fetchone()[0])
                    if {"margin_utilization_pct", "short_utilization_pct"} <= credit_columns
                    else 0
                ),
                "listed_rows": int(conn.execute(
                    "SELECT COUNT(*) FROM credit_balance_daily WHERE market='listed'"
                ).fetchone()[0]),
                "otc_rows": int(conn.execute(
                    "SELECT COUNT(*) FROM credit_balance_daily WHERE market='otc'"
                ).fetchone()[0]),
            }
    return {
        "database": str(resolved),
        "database_size_bytes": resolved.stat().st_size,
        "integrity_pragma": integrity_pragma,
        "integrity": integrity,
        "tables": coverage,
        "credit_quality": credit_quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full-integrity", action="store_true")
    args = parser.parse_args()
    report = build_report(args.database, full_integrity=bool(args.full_integrity))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["integrity"] == ["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
