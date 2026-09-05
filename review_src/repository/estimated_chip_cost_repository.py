from __future__ import annotations

import math
import sqlite3
from typing import Any, Iterable

from analysis.estimated_chip_cost import DailyCostInput
from core.cost_source_registry import (
    CANONICAL_COST_FORMULA_VERSION,
    CANONICAL_COST_TYPES,
)
from repository.full_market_batch_repository import resolve_full_market_analysis_date


ESTIMATED_CHIP_COST_COLUMNS = (
    "code",
    "trade_date",
    "cost_type",
    "cost_label",
    "estimated_cost",
    "cost_status",
    "confidence",
    "data_source_confidence",
    "data_source_status",
    "source_license",
    "source_detail",
    "source_tables",
    "calculation_method",
    "formula_version",
    "price_basis",
    "price_basis_value",
    "price_to_cost_deviation_pct",
    "accumulation_status",
    "position_shares",
    "total_cost_amount",
    "cumulative_net_shares",
    "estimate_start_date",
    "estimate_end_date",
    "sample_days",
    "display_reason",
    "debug_reason",
    "missing_required_fields",
)


def ensure_estimated_chip_cost_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS estimated_chip_cost_daily (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            code                        TEXT NOT NULL,
            trade_date                  TEXT NOT NULL,
            cost_type                   TEXT NOT NULL,
            cost_label                  TEXT,
            estimated_cost              REAL,
            cost_status                 TEXT NOT NULL,
            confidence                  TEXT NOT NULL DEFAULT 'unavailable',
            data_source_confidence      TEXT NOT NULL DEFAULT 'unavailable',
            data_source_status          TEXT,
            source_license              TEXT,
            source_detail               TEXT,
            source_tables               TEXT,
            calculation_method          TEXT,
            formula_version             TEXT,
            price_basis                 TEXT,
            price_basis_value           REAL,
            price_to_cost_deviation_pct REAL,
            accumulation_status         TEXT,
            position_shares             REAL,
            total_cost_amount           REAL,
            cumulative_net_shares       REAL,
            estimate_start_date         TEXT,
            estimate_end_date           TEXT,
            sample_days                 INTEGER,
            display_reason              TEXT,
            debug_reason                TEXT,
            missing_required_fields     TEXT,
            created_at                  TEXT DEFAULT (datetime('now')),
            updated_at                  TEXT DEFAULT (datetime('now')),
            UNIQUE(code, trade_date, cost_type),
            CHECK (cost_type IN (
                'foreign_estimated',
                'trust_estimated',
                'margin_incremental_estimated',
                'margin_reliable_cost',
                'main_force_reference_zone',
                'main_force_branch_cost'
            )),
            CHECK (cost_status IN (
                'ok',
                'estimated',
                'proxy_only',
                'unavailable',
                'invalid',
                'insufficient_data',
                'missing_required_source'
            )),
            CHECK (confidence IN ('high', 'medium', 'low', 'unavailable')),
            CHECK (data_source_confidence IN ('high', 'medium', 'low', 'unavailable')),
            CHECK (
                accumulation_status IS NULL OR
                accumulation_status IN ('building', 'markup', 'distribution', 'neutral', 'unavailable')
            )
        );
        CREATE INDEX IF NOT EXISTS idx_est_chip_cost_code_type_date
            ON estimated_chip_cost_daily (code, cost_type, trade_date);
        CREATE INDEX IF NOT EXISTS idx_est_chip_cost_date_type
            ON estimated_chip_cost_daily (trade_date, cost_type);

        CREATE TABLE IF NOT EXISTS margin_financing_amount_daily (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            code                       TEXT NOT NULL,
            trade_date                 TEXT NOT NULL,
            financing_buy_amount       REAL,
            financing_loan_amount      REAL,
            financing_balance_amount   REAL,
            financing_amount_unit      TEXT,
            margin_balance             REAL,
            margin_balance_unit        TEXT,
            source_name                TEXT NOT NULL,
            source_type                TEXT,
            source_license             TEXT,
            raw_hash                   TEXT,
            imported_at                TEXT,
            created_at                 TEXT DEFAULT (datetime('now')),
            updated_at                 TEXT DEFAULT (datetime('now')),
            UNIQUE(code, trade_date, source_name)
        );
        CREATE INDEX IF NOT EXISTS idx_margin_amount_code_date
            ON margin_financing_amount_daily (code, trade_date);

        CREATE TABLE IF NOT EXISTS broker_branch_trade_daily (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            code                       TEXT NOT NULL,
            trade_date                 TEXT NOT NULL,
            broker_id                  TEXT NOT NULL,
            broker_name                TEXT,
            branch_id                  TEXT NOT NULL,
            branch_name                TEXT,
            buy_shares                 REAL,
            sell_shares                REAL,
            net_shares                 REAL,
            buy_amount                 REAL,
            sell_amount                REAL,
            net_amount                 REAL,
            source_name                TEXT NOT NULL,
            source_type                TEXT,
            source_license             TEXT,
            raw_hash                   TEXT,
            imported_at                TEXT,
            created_at                 TEXT DEFAULT (datetime('now')),
            updated_at                 TEXT DEFAULT (datetime('now')),
            UNIQUE(code, trade_date, broker_id, branch_id, source_name)
        );
        CREATE INDEX IF NOT EXISTS idx_branch_trade_code_date
            ON broker_branch_trade_daily (code, trade_date);
        CREATE INDEX IF NOT EXISTS idx_branch_trade_broker_date
            ON broker_branch_trade_daily (broker_id, branch_id, trade_date);
        """
    )


def _clean_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _clean_value(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def list_history_codes(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT code FROM history_price ORDER BY code").fetchall()
    return [normalize_code(r[0]) for r in rows if normalize_code(r[0])]


def load_cost_input_rows(
    conn: sqlite3.Connection,
    codes: Iterable[str],
    days: int,
    as_of_date: str | None = None,
) -> list[DailyCostInput]:
    analysis_date = resolve_full_market_analysis_date(conn, as_of_date)
    if not analysis_date:
        return []
    if not table_exists(conn, "institution_activity_daily"):
        raise RuntimeError(
            "canonical cost source table institution_activity_daily is missing"
        )
    all_rows: list[DailyCostInput] = []
    for code in sorted({normalize_code(c) for c in codes if normalize_code(c)}):
        rows = conn.execute(
            """
            SELECT
                h.code,
                h.date AS trade_date,
                h.close,
                h.volume,
                h.amount,
                i.foreign_net,
                i.trust_net,
                i.source AS institution_source,
                i.source_quality AS institution_source_quality
            FROM (
                SELECT *
                FROM history_price
                WHERE code=? AND date<=?
                ORDER BY date DESC
                LIMIT ?
            ) h
            LEFT JOIN institution_activity_daily i
                ON i.code = h.code AND i.trade_date = h.date
            ORDER BY h.code ASC, h.date ASC
            """,
            (code, analysis_date, int(days)),
        ).fetchall()
        for row in rows:
            all_rows.append(
                DailyCostInput(
                    code=normalize_code(row["code"]),
                    trade_date=str(row["trade_date"]),
                    close=_clean_number(row["close"]),
                    volume=_clean_number(row["volume"]),
                    amount=_clean_number(row["amount"]),
                    foreign_net=_clean_number(row["foreign_net"]),
                    trust_net=_clean_number(row["trust_net"]),
                    institution_source=row["institution_source"],
                    institution_source_quality=row["institution_source_quality"],
                )
            )
    return sorted(all_rows, key=lambda r: (r.code, r.trade_date))


def read_canonical_estimated_cost_rows(
    conn: sqlite3.Connection,
    *,
    code: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    """Read one exact-date, exact-formula snapshot; never fall back to MAX(date)."""

    if not table_exists(conn, "estimated_chip_cost_daily"):
        return []
    placeholders = ",".join("?" for _ in CANONICAL_COST_TYPES)
    rows = conn.execute(
        f"""
        SELECT {', '.join(ESTIMATED_CHIP_COST_COLUMNS)}
        FROM estimated_chip_cost_daily
        WHERE code=?
          AND trade_date=?
          AND formula_version=?
          AND cost_type IN ({placeholders})
        ORDER BY cost_type
        """,
        (
            normalize_code(code),
            str(trade_date),
            CANONICAL_COST_FORMULA_VERSION,
            *CANONICAL_COST_TYPES,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_estimated_chip_cost_rows(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    row_list = list(rows)
    if not row_list:
        return 0
    placeholders = ", ".join("?" for _ in ESTIMATED_CHIP_COST_COLUMNS)
    columns_sql = ", ".join(ESTIMATED_CHIP_COST_COLUMNS)
    update_sql = ", ".join(
        f"{column}=excluded.{column}"
        for column in ESTIMATED_CHIP_COST_COLUMNS
        if column not in {"code", "trade_date", "cost_type"}
    )
    sql = f"""
        INSERT INTO estimated_chip_cost_daily ({columns_sql})
        VALUES ({placeholders})
        ON CONFLICT(code, trade_date, cost_type)
        DO UPDATE SET
            {update_sql},
            updated_at = datetime('now')
    """
    values = [
        tuple(_clean_value(row.get(column)) for column in ESTIMATED_CHIP_COST_COLUMNS)
        for row in row_list
    ]
    conn.executemany(sql, values)
    return len(row_list)


def prune_estimated_chip_cost(conn: sqlite3.Connection, codes: Iterable[str], keep_days: int = 720) -> int:
    deleted = 0
    cost_types = [
        row["cost_type"]
        for row in conn.execute("SELECT DISTINCT cost_type FROM estimated_chip_cost_daily").fetchall()
    ]
    for code in sorted({normalize_code(c) for c in codes if normalize_code(c)}):
        for cost_type in cost_types:
            cutoff_row = conn.execute(
                """
                SELECT trade_date
                FROM estimated_chip_cost_daily
                WHERE code=? AND cost_type=?
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT 1 OFFSET ?
                """,
                (code, cost_type, max(0, keep_days - 1)),
            ).fetchone()
            if not cutoff_row:
                continue
            cur = conn.execute(
                """
                DELETE FROM estimated_chip_cost_daily
                WHERE code=? AND cost_type=? AND trade_date < ?
                """,
                (code, cost_type, cutoff_row["trade_date"]),
            )
            deleted += cur.rowcount if cur.rowcount is not None else 0
    return deleted


def count_rows_by_status(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row.get('cost_type')}:{row.get('cost_status')}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def max_trade_dates_per_code_type(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "estimated_chip_cost_daily"):
        return 0
    row = conn.execute(
        """
        SELECT MAX(c) AS max_count
        FROM (
            SELECT code, cost_type, COUNT(DISTINCT trade_date) AS c
            FROM estimated_chip_cost_daily
            GROUP BY code, cost_type
        )
        """
    ).fetchone()
    return int(row["max_count"] or 0) if row else 0
