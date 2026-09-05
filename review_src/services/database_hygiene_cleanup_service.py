from __future__ import annotations

"""Narrow, evidence-backed cleanup of exact duplicate SQLite indexes."""

import sqlite3
import time
from typing import Any

from core.institution_activity_schema import ensure_institution_activity_schema


REDUNDANT_EXACT_INDEXES = (
    "idx_branch_trade_code_date_broker_source",
    "idx_daily_inner_outer_volume_code_date",
    "idx_est_chip_cost_code_date_type",
    "idx_full_market_not_applicable_date",
    "idx_margin_amount_code_date_source",
    "idx_mis_quote_snapshot_code_ts",
    "idx_single_track_retrieval_attempt_run",
    "idx_single_track_source_snapshot_run",
    "idx_users_email",
)


def reconcile_official_institution_legacy_mirror(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Make the still-consumed legacy net-flow mirror match canonical official rows."""

    ensure_institution_activity_schema(conn)
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if not {"institution_daily", "institution_activity_daily"} <= tables:
        return {
            "ok": False,
            "status": "required_table_missing",
            "rows_reconciled": 0,
        }
    mismatch_where = """
        legacy.foreign_net IS NOT canonical.foreign_net
        OR legacy.trust_net IS NOT canonical.trust_net
        OR legacy.dealer_net IS NOT canonical.dealer_net
        OR legacy.source IS NOT canonical.source
        OR COALESCE(legacy.source_quality,'')<>canonical.source_quality
        OR legacy.fetched_at IS NULL
    """
    before = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM institution_daily AS legacy
            JOIN institution_activity_daily AS canonical
              ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
            WHERE {mismatch_where}
            """
        ).fetchone()[0]
    )
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
    fallback_epoch = time.time()
    cursor = conn.execute(
        f"""
        UPDATE institution_daily AS legacy
        SET foreign_net=canonical.foreign_net,
            trust_net=canonical.trust_net,
            dealer_net=canonical.dealer_net,
            source=canonical.source,
            source_quality=canonical.source_quality,
            fetched_at=COALESCE(
                CAST(strftime('%s',canonical.fetched_at) AS REAL),
                legacy.fetched_at,
                ?
            ),
            updated_at=COALESCE(
                CAST(strftime('%s',canonical.fetched_at) AS REAL),
                legacy.updated_at,
                ?
            )
        FROM institution_activity_daily AS canonical
        WHERE canonical.trade_date=legacy.date
          AND canonical.code=legacy.code
          AND ({mismatch_where})
        """,
        (fallback_epoch, fallback_epoch),
    )
    supplemental_cursor = conn.execute(
        """
        UPDATE institution_daily AS legacy
        SET source_quality='supplemental'
        WHERE NOT EXISTS(
            SELECT 1 FROM institution_activity_daily AS canonical
            WHERE canonical.trade_date=legacy.date AND canonical.code=legacy.code
        )
          AND LOWER(COALESCE(legacy.source,'')) LIKE 'finmind%'
          AND COALESCE(legacy.source_quality,'')=''
        """
    )
    after = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM institution_daily AS legacy
            JOIN institution_activity_daily AS canonical
              ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
            WHERE {mismatch_where}
            """
        ).fetchone()[0]
    )
    return {
        "ok": after == 0,
        "status": "reconciled" if after == 0 else "mismatch_remaining",
        "rows_requiring_reconciliation_before": before,
        "rows_reconciled": max(int(cursor.rowcount or 0), 0),
        "rows_requiring_reconciliation_after": after,
        "legacy_only_rows_preserved": legacy_only,
        "legacy_only_rows_labeled_supplemental": max(
            int(supplemental_cursor.rowcount or 0), 0
        ),
        "business_rows_deleted": 0,
        "note": (
            "legacy-only rows remain because active compatibility readers still consume "
            "institution_daily; they were not proven safe to delete"
        ),
    }


def reconcile_official_credit_legacy_mirrors(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Repair legacy margin/lending mirrors from canonical official balances."""

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    required = {"credit_balance_daily", "margin_daily", "lending_daily"}
    if not required <= tables:
        return {
            "ok": False,
            "status": "required_table_missing",
            "rows_reconciled": 0,
            "missing_tables": sorted(required - tables),
        }
    margin_where = """
        legacy.margin_delta IS NOT canonical.margin_delta_lots
        OR legacy.margin_balance IS NOT canonical.margin_balance_lots
        OR legacy.short_delta IS NOT canonical.short_delta_lots
        OR legacy.short_balance IS NOT canonical.short_balance_lots
        OR legacy.source IS NOT canonical.margin_source
        OR COALESCE(legacy.source_quality,'')<>'official'
        OR COALESCE(legacy.margin_unit,'')<>'lots'
        OR COALESCE(legacy.short_unit,'')<>'lots'
        OR legacy.fetched_at IS NULL
    """
    lending_where = """
        legacy.lending_delta IS NOT canonical.sbl_delta_shares
        OR legacy.lending_balance IS NOT canonical.sbl_balance_shares
        OR legacy.source IS NOT canonical.lending_source
        OR COALESCE(legacy.source_quality,'')<>'official'
        OR COALESCE(legacy.lending_unit,'')<>'shares'
        OR legacy.fetched_at IS NULL
    """
    margin_before = int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM margin_daily AS legacy
            JOIN credit_balance_daily AS canonical
              ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
            WHERE {margin_where}
            """
        ).fetchone()[0]
    )
    lending_before = int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM lending_daily AS legacy
            JOIN credit_balance_daily AS canonical
              ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
            WHERE {lending_where}
            """
        ).fetchone()[0]
    )
    fallback_epoch = time.time()
    margin_cursor = conn.execute(
        f"""
        UPDATE margin_daily AS legacy
        SET margin_delta=canonical.margin_delta_lots,
            margin_balance=canonical.margin_balance_lots,
            short_delta=canonical.short_delta_lots,
            short_balance=canonical.short_balance_lots,
            source=canonical.margin_source,
            source_quality='official',
            margin_unit='lots',
            short_unit='lots',
            fetched_at=COALESCE(
                CAST(strftime('%s',canonical.first_seen_at) AS REAL),
                legacy.fetched_at,
                ?
            ),
            updated_at=COALESCE(
                CAST(strftime('%s',canonical.first_seen_at) AS REAL),
                legacy.updated_at,
                ?
            )
        FROM credit_balance_daily AS canonical
        WHERE canonical.trade_date=legacy.date
          AND canonical.code=legacy.code
          AND ({margin_where})
        """,
        (fallback_epoch, fallback_epoch),
    )
    lending_cursor = conn.execute(
        f"""
        UPDATE lending_daily AS legacy
        SET lending_delta=canonical.sbl_delta_shares,
            lending_balance=canonical.sbl_balance_shares,
            source=canonical.lending_source,
            source_quality='official',
            lending_unit='shares',
            fetched_at=COALESCE(
                CAST(strftime('%s',canonical.first_seen_at) AS REAL),
                legacy.fetched_at,
                ?
            ),
            updated_at=COALESCE(
                CAST(strftime('%s',canonical.first_seen_at) AS REAL),
                legacy.updated_at,
                ?
            )
        FROM credit_balance_daily AS canonical
        WHERE canonical.trade_date=legacy.date
          AND canonical.code=legacy.code
          AND ({lending_where})
        """,
        (fallback_epoch, fallback_epoch),
    )
    margin_after = int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM margin_daily AS legacy
            JOIN credit_balance_daily AS canonical
              ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
            WHERE {margin_where}
            """
        ).fetchone()[0]
    )
    lending_after = int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM lending_daily AS legacy
            JOIN credit_balance_daily AS canonical
              ON canonical.trade_date=legacy.date AND canonical.code=legacy.code
            WHERE {lending_where}
            """
        ).fetchone()[0]
    )
    legacy_margin_only = int(
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
    legacy_lending_only = int(
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
    return {
        "ok": margin_after == 0 and lending_after == 0,
        "status": (
            "reconciled" if margin_after == 0 and lending_after == 0
            else "mismatch_remaining"
        ),
        "margin_rows_requiring_reconciliation_before": margin_before,
        "margin_rows_reconciled": max(int(margin_cursor.rowcount or 0), 0),
        "margin_rows_requiring_reconciliation_after": margin_after,
        "lending_rows_requiring_reconciliation_before": lending_before,
        "lending_rows_reconciled": max(int(lending_cursor.rowcount or 0), 0),
        "lending_rows_requiring_reconciliation_after": lending_after,
        "legacy_margin_only_rows_preserved": legacy_margin_only,
        "legacy_lending_only_rows_preserved": legacy_lending_only,
        "business_rows_deleted": 0,
        "note": "legacy-only secondary rows remain until compatibility readers migrate",
    }


def _quoted(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _index_signature(conn: sqlite3.Connection, index: str) -> list[tuple[str, bool]]:
    return [
        (str(row[2]), bool(row[3]))
        for row in conn.execute(f"PRAGMA index_xinfo({_quoted(index)})")
        if int(row[5]) == 1 and row[2] is not None
    ]


def remove_exact_duplicate_indexes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Drop only indexes with an extant PK/UNIQUE index of identical signature."""

    page_count_before = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_before = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    removed: list[dict[str, Any]] = []
    already_absent: list[str] = []
    failures: list[dict[str, Any]] = []
    for index_name in REDUNDANT_EXACT_INDEXES:
        source = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
            (index_name,),
        ).fetchone()
        if source is None:
            already_absent.append(index_name)
            continue
        table = str(source[0])
        signature = _index_signature(conn, index_name)
        covering: str | None = None
        for row in conn.execute(f"PRAGMA index_list({_quoted(table)})"):
            candidate = str(row[1])
            if candidate == index_name or str(row[3]) not in {"pk", "u"}:
                continue
            if _index_signature(conn, candidate) == signature:
                covering = candidate
                break
        if not signature or covering is None:
            failures.append(
                {
                    "index": index_name,
                    "table": table,
                    "reason": "identical_primary_or_unique_cover_not_found",
                    "signature": signature,
                }
            )
            continue
        row_count = int(
            conn.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0]
        )
        conn.execute(f"DROP INDEX {_quoted(index_name)}")
        removed.append(
            {
                "index": index_name,
                "table": table,
                "row_count_unchanged": row_count,
                "covered_by": covering,
                "signature": signature,
                "business_rows_deleted": 0,
            }
        )
    page_count_after = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_after = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "ok": not failures,
        "removed": removed,
        "removed_count": len(removed),
        "already_absent": already_absent,
        "failures": failures,
        "business_rows_deleted": 0,
        "page_count_before": page_count_before,
        "page_count_after": page_count_after,
        "freelist_count_before": freelist_before,
        "freelist_count_after": freelist_after,
        "vacuum_performed": False,
    }
