from __future__ import annotations

"""Read-only SQLite integrity, key-duplication, and index-overlap audit."""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "review_src" / "data" / "taiwan50.db"


def _quoted(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [
        {
            "name": str(row[1]),
            "type": str(row[2] or ""),
            "not_null": bool(row[3]),
            "default": row[4],
            "primary_key_position": int(row[5]),
        }
        for row in conn.execute(f"PRAGMA table_info({_quoted(table)})")
    ]


def _indexes(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for row in conn.execute(f"PRAGMA index_list({_quoted(table)})"):
        name = str(row[1])
        xinfo = list(conn.execute(f"PRAGMA index_xinfo({_quoted(name)})"))
        columns = [
            {
                "name": str(value[2]),
                "desc": bool(value[3]),
            }
            for value in xinfo
            if int(value[5]) == 1 and value[2] is not None
        ]
        values.append(
            {
                "name": name,
                "unique": bool(row[2]),
                "origin": str(row[3]),
                "partial": bool(row[4]),
                "columns": columns,
            }
        )
    return values


def _redundant_indexes(table: str, indexes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index in indexes:
        if index["origin"] != "c" or index["partial"]:
            continue
        signature = [
            (value["name"], bool(value["desc"])) for value in index["columns"]
        ]
        for covering in indexes:
            if covering["name"] == index["name"] or covering["partial"]:
                continue
            covering_signature = [
                (value["name"], bool(value["desc"]))
                for value in covering["columns"]
            ]
            if signature and covering_signature[: len(signature)] == signature:
                candidates.append(
                    {
                        "table": table,
                        "index": index["name"],
                        "signature": signature,
                        "covered_by": covering["name"],
                        "covered_by_origin": covering["origin"],
                        "coverage_kind": (
                            "exact_duplicate"
                            if len(covering_signature) == len(signature)
                            else "left_prefix_candidate"
                        ),
                        "requires_query_plan_review": True,
                    }
                )
                break
    return candidates


def _duplicate_business_keys(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: list[dict[str, Any]],
    primary_key: list[str],
) -> dict[str, Any]:
    if primary_key:
        return {
            "status": "enforced_by_primary_key",
            "key": primary_key,
            "sample_groups": [],
        }
    names = {str(value["name"]) for value in columns}
    key: list[str] | None = None
    for candidate in (
        ["data_date", "symbol"],
        ["trade_date", "code"],
        ["date", "code"],
        ["market_date", "ticker"],
        ["request_id"],
        ["prediction_id"],
        ["event_revision_id"],
    ):
        if set(candidate).issubset(names):
            key = candidate
            break
    if not key:
        return {
            "status": "no_safe_inferred_business_key",
            "key": [],
            "sample_groups": [],
        }
    key_sql = ",".join(_quoted(value) for value in key)
    query = (
        f"SELECT {key_sql},COUNT(*) AS duplicate_count "
        f"FROM {_quoted(table)} GROUP BY {key_sql} "
        "HAVING COUNT(*)>1 LIMIT 101"
    )
    cursor = conn.execute(query)
    names_out = [str(value[0]) for value in cursor.description or ()]
    samples = [dict(zip(names_out, row, strict=True)) for row in cursor.fetchall()]
    return {
        "status": "duplicates_found" if samples else "no_duplicates_for_inferred_key",
        "key": key,
        "sample_groups": samples[:100],
        "sample_truncated": len(samples) > 100,
        "note": "inferred keys are audit candidates, not automatic deletion authority",
    }


def _institution_mirror_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if not {"institution_daily", "institution_activity_daily"} <= tables:
        return {"status": "not_applicable"}
    overlap = conn.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN legacy.foreign_net IS canonical.foreign_net THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.trust_net IS canonical.trust_net THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.dealer_net IS canonical.dealer_net THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.source IS canonical.source THEN 0 ELSE 1 END),
            SUM(CASE WHEN COALESCE(legacy.source_quality,'')=canonical.source_quality
                     AND legacy.fetched_at IS NOT NULL THEN 0 ELSE 1 END)
        FROM institution_daily AS legacy
        JOIN institution_activity_daily AS canonical
          ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
        """
    ).fetchone()
    legacy_only = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM institution_daily AS legacy
            WHERE NOT EXISTS(
                SELECT 1 FROM institution_activity_daily AS canonical
                WHERE canonical.trade_date=legacy.date AND canonical.code=legacy.code
            )
            """
        ).fetchone()[0]
    )
    canonical_only = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM institution_activity_daily AS canonical
            WHERE NOT EXISTS(
                SELECT 1 FROM institution_daily AS legacy
                WHERE legacy.date=canonical.trade_date AND legacy.code=canonical.code
            )
            """
        ).fetchone()[0]
    )
    return {
        "status": "ok" if all(int(value or 0) == 0 for value in overlap[1:]) else "mismatch",
        "overlap_rows": int(overlap[0] or 0),
        "foreign_net_mismatches": int(overlap[1] or 0),
        "trust_net_mismatches": int(overlap[2] or 0),
        "dealer_net_mismatches": int(overlap[3] or 0),
        "source_mismatches": int(overlap[4] or 0),
        "quality_or_fetched_at_mismatches": int(overlap[5] or 0),
        "legacy_only_rows": legacy_only,
        "canonical_only_rows": canonical_only,
        "legacy_table_retained": True,
        "reason": "active compatibility readers still query institution_daily",
    }


def _credit_mirror_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    required = {"credit_balance_daily", "margin_daily", "lending_daily"}
    if not required <= tables:
        return {
            "status": "not_applicable",
            "missing_tables": sorted(required - tables),
        }
    margin = conn.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN legacy.margin_delta IS canonical.margin_delta_lots THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.margin_balance IS canonical.margin_balance_lots THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.short_delta IS canonical.short_delta_lots THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.short_balance IS canonical.short_balance_lots THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.source IS canonical.margin_source THEN 0 ELSE 1 END),
            SUM(CASE WHEN COALESCE(legacy.source_quality,'')='official'
                          AND COALESCE(legacy.margin_unit,'')='lots'
                          AND COALESCE(legacy.short_unit,'')='lots'
                          AND legacy.fetched_at IS NOT NULL
                     THEN 0 ELSE 1 END)
        FROM margin_daily AS legacy
        JOIN credit_balance_daily AS canonical
          ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
        """
    ).fetchone()
    lending = conn.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN legacy.lending_delta IS canonical.sbl_delta_shares THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.lending_balance IS canonical.sbl_balance_shares THEN 0 ELSE 1 END),
            SUM(CASE WHEN legacy.source IS canonical.lending_source THEN 0 ELSE 1 END),
            SUM(CASE WHEN COALESCE(legacy.source_quality,'')='official'
                          AND COALESCE(legacy.lending_unit,'')='shares'
                          AND legacy.fetched_at IS NOT NULL
                     THEN 0 ELSE 1 END)
        FROM lending_daily AS legacy
        JOIN credit_balance_daily AS canonical
          ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
        """
    ).fetchone()
    margin_legacy_only = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM margin_daily AS legacy
            WHERE NOT EXISTS(
                SELECT 1 FROM credit_balance_daily AS canonical
                WHERE canonical.trade_date=legacy.date AND canonical.code=legacy.code
            )
            """
        ).fetchone()[0]
    )
    lending_legacy_only = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM lending_daily AS legacy
            WHERE NOT EXISTS(
                SELECT 1 FROM credit_balance_daily AS canonical
                WHERE canonical.trade_date=legacy.date AND canonical.code=legacy.code
            )
            """
        ).fetchone()[0]
    )
    canonical_margin_only = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM credit_balance_daily AS canonical
            WHERE NOT EXISTS(
                SELECT 1 FROM margin_daily AS legacy
                WHERE legacy.date=canonical.trade_date AND legacy.code=canonical.code
            )
            """
        ).fetchone()[0]
    )
    canonical_lending_only = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM credit_balance_daily AS canonical
            WHERE NOT EXISTS(
                SELECT 1 FROM lending_daily AS legacy
                WHERE legacy.date=canonical.trade_date AND legacy.code=canonical.code
            )
            """
        ).fetchone()[0]
    )
    mismatch_values = [*margin[1:], *lending[1:]]
    return {
        "status": (
            "ok" if all(int(value or 0) == 0 for value in mismatch_values)
            else "mismatch"
        ),
        "margin_overlap_rows": int(margin[0] or 0),
        "margin_delta_mismatches": int(margin[1] or 0),
        "margin_balance_mismatches": int(margin[2] or 0),
        "short_delta_mismatches": int(margin[3] or 0),
        "short_balance_mismatches": int(margin[4] or 0),
        "margin_source_mismatches": int(margin[5] or 0),
        "margin_quality_unit_or_fetched_at_mismatches": int(margin[6] or 0),
        "lending_overlap_rows": int(lending[0] or 0),
        "lending_delta_mismatches": int(lending[1] or 0),
        "lending_balance_mismatches": int(lending[2] or 0),
        "lending_source_mismatches": int(lending[3] or 0),
        "lending_quality_unit_or_fetched_at_mismatches": int(lending[4] or 0),
        "legacy_margin_only_rows": margin_legacy_only,
        "legacy_lending_only_rows": lending_legacy_only,
        "canonical_margin_only_rows": canonical_margin_only,
        "canonical_lending_only_rows": canonical_lending_only,
        "legacy_tables_retained": True,
        "reason": "active compatibility readers still query margin_daily and lending_daily",
    }


def audit_database(database: Path) -> dict[str, Any]:
    target = database.expanduser().resolve()
    uri = f"{target.as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        integrity = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        foreign_key_violations = [list(row) for row in conn.execute("PRAGMA foreign_key_check")]
        table_names = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        tables: list[dict[str, Any]] = []
        redundant: list[dict[str, Any]] = []
        duplicate_findings: list[dict[str, Any]] = []
        for table in table_names:
            columns = _columns(conn, table)
            primary_key = [
                str(value["name"])
                for value in sorted(
                    (value for value in columns if value["primary_key_position"]),
                    key=lambda value: int(value["primary_key_position"]),
                )
            ]
            indexes = _indexes(conn, table)
            row_count = int(
                conn.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0]
            )
            duplicate_audit = _duplicate_business_keys(
                conn,
                table=table,
                columns=columns,
                primary_key=primary_key,
            )
            table_redundant = _redundant_indexes(table, indexes)
            redundant.extend(table_redundant)
            if duplicate_audit["status"] == "duplicates_found":
                duplicate_findings.append(
                    {"table": table, **duplicate_audit}
                )
            tables.append(
                {
                    "table": table,
                    "row_count": row_count,
                    "primary_key": primary_key,
                    "columns": columns,
                    "indexes": indexes,
                    "duplicate_business_key_audit": duplicate_audit,
                    "redundant_index_candidates": table_redundant,
                }
            )
        return {
            "database": str(target),
            "size_bytes": target.stat().st_size,
            "quick_check": integrity,
            "foreign_key_violation_count": len(foreign_key_violations),
            "foreign_key_violations": foreign_key_violations[:100],
            "table_count": len(tables),
            "tables": tables,
            "duplicate_business_key_findings": duplicate_findings,
            "redundant_index_candidates": redundant,
            "institution_legacy_mirror": _institution_mirror_audit(conn),
            "credit_legacy_mirrors": _credit_mirror_audit(conn),
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit_database(args.database)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.report:
        target = args.report if args.report.is_absolute() else ROOT / args.report
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "database": report["database"],
                "size_bytes": report["size_bytes"],
                "quick_check": report["quick_check"],
                "foreign_key_violation_count": report["foreign_key_violation_count"],
                "table_count": report["table_count"],
                "duplicate_business_key_findings": report["duplicate_business_key_findings"],
                "redundant_index_candidates": report["redundant_index_candidates"],
                "institution_legacy_mirror": report["institution_legacy_mirror"],
                "credit_legacy_mirrors": report["credit_legacy_mirrors"],
                "report": str(args.report) if args.report else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["quick_check"] == ["ok"] and not report["foreign_key_violations"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
