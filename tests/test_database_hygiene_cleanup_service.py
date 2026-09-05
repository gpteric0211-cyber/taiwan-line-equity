from __future__ import annotations

import sqlite3

from repository.institution_activity_repository import upsert_official_institution_rows
from services.database_hygiene_cleanup_service import (
    reconcile_official_credit_legacy_mirrors,
    reconcile_official_institution_legacy_mirror,
    remove_exact_duplicate_indexes,
)


def test_cleanup_removes_only_exact_indexes_covered_by_primary_or_unique_keys() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE daily_inner_outer_volume(
            stock_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            value REAL,
            PRIMARY KEY(stock_code,trade_date)
        );
        CREATE INDEX idx_daily_inner_outer_volume_code_date
            ON daily_inner_outer_volume(stock_code,trade_date);
        CREATE TABLE users(email TEXT NOT NULL UNIQUE, name TEXT);
        CREATE INDEX idx_users_email ON users(email);
        CREATE TABLE estimated_chip_cost_daily(
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            cost_type TEXT NOT NULL,
            UNIQUE(code,trade_date,cost_type)
        );
        CREATE UNIQUE INDEX idx_est_chip_cost_code_date_type
            ON estimated_chip_cost_daily(code,trade_date,cost_type);
        """
    )
    conn.execute(
        "INSERT INTO daily_inner_outer_volume VALUES('2454','2026-09-01',1.0)"
    )

    result = remove_exact_duplicate_indexes(conn)

    assert result["ok"] is True
    assert {row["index"] for row in result["removed"]} == {
        "idx_daily_inner_outer_volume_code_date",
        "idx_est_chip_cost_code_date_type",
        "idx_users_email",
    }
    assert result["business_rows_deleted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM daily_inner_outer_volume").fetchone()[0] == 1


def test_cleanup_refuses_index_without_identical_unique_cover() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE mis_quote_snapshot(
            code TEXT NOT NULL,
            snapshot_ts TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            PRIMARY KEY(code,snapshot_ts,sequence)
        );
        CREATE INDEX idx_mis_quote_snapshot_code_ts
            ON mis_quote_snapshot(code,snapshot_ts);
        """
    )

    result = remove_exact_duplicate_indexes(conn)

    assert result["ok"] is False
    assert result["removed_count"] == 0
    assert result["failures"][0]["index"] == "idx_mis_quote_snapshot_code_ts"
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_mis_quote_snapshot_code_ts'"
    ).fetchone()


def _legacy_institution_table(
    conn: sqlite3.Connection,
    *,
    include_metadata: bool = True,
) -> None:
    metadata = ", source_quality TEXT, fetched_at REAL" if include_metadata else ""
    conn.execute(
        f"""
        CREATE TABLE institution_daily(
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            foreign_net REAL,
            trust_net REAL,
            dealer_net REAL,
            source TEXT,
            updated_at REAL
            {metadata},
            PRIMARY KEY(date,code)
        )
        """
    )


def test_official_institution_write_keeps_legacy_mirror_quality_metadata() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _legacy_institution_table(conn, include_metadata=False)

    upsert_official_institution_rows(
        conn,
        [
            {
                "date": "2026-07-31",
                "code": "3491",
                "market": "otc",
                "foreign_buy": 10,
                "foreign_sell": 20,
                "foreign_net": -10,
                "trust_buy": 30,
                "trust_sell": 5,
                "trust_net": 25,
                "dealer_buy": 4,
                "dealer_sell": 1,
                "dealer_net": 3,
                "source": "TPEX_INSTITUTION_DAILY_TRADE",
            }
        ],
    )

    row = conn.execute(
        "SELECT trust_net,source,source_quality,fetched_at FROM institution_daily"
    ).fetchone()
    assert tuple(row[:3]) == (25.0, "TPEX_INSTITUTION_DAILY_TRADE", "official")
    assert float(row[3]) > 0


def test_reconcile_official_institution_mirror_repairs_values_and_preserves_legacy_only() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_institution_table(conn)
    conn.executescript(
        """
        CREATE TABLE institution_activity_daily(
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            foreign_net INTEGER,
            trust_net INTEGER,
            dealer_net INTEGER,
            source TEXT NOT NULL,
            source_quality TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(trade_date,code)
        );
        INSERT INTO institution_daily
        VALUES('2026-07-31','3491',-10,1,3,'FinMind',1,NULL,NULL);
        INSERT INTO institution_daily
        VALUES('2025-11-06','7769',5,0,0,'FinMind',1,NULL,NULL);
        INSERT INTO institution_activity_daily
        VALUES('2026-07-31','3491',-10,25,3,'TPEX_OFFICIAL','official',
               '2026-09-02T12:00:00+08:00');
        """
    )

    result = reconcile_official_institution_legacy_mirror(conn)

    assert result["ok"] is True
    assert result["rows_reconciled"] == 1
    assert result["legacy_only_rows_preserved"] == 1
    assert result["legacy_only_rows_labeled_supplemental"] == 1
    repaired = conn.execute(
        "SELECT trust_net,source,source_quality FROM institution_daily WHERE code='3491'"
    ).fetchone()
    assert tuple(repaired) == (25.0, "TPEX_OFFICIAL", "official")
    assert conn.execute(
        "SELECT source_quality FROM institution_daily WHERE code='7769'"
    ).fetchone()[0] == "supplemental"
    assert conn.execute("SELECT COUNT(*) FROM institution_daily").fetchone()[0] == 2


def test_reconcile_official_credit_mirrors_repairs_overlap_and_preserves_legacy_only() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE credit_balance_daily(
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            margin_delta_lots INTEGER,
            margin_balance_lots INTEGER,
            short_delta_lots INTEGER,
            short_balance_lots INTEGER,
            sbl_delta_shares INTEGER,
            sbl_balance_shares INTEGER,
            margin_source TEXT,
            lending_source TEXT,
            first_seen_at TEXT,
            PRIMARY KEY(trade_date,code)
        );
        CREATE TABLE margin_daily(
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            margin_delta REAL,
            margin_balance REAL,
            short_delta REAL,
            short_balance REAL,
            source TEXT,
            updated_at REAL,
            source_quality TEXT,
            fetched_at REAL,
            margin_unit TEXT,
            short_unit TEXT,
            PRIMARY KEY(date,code)
        );
        CREATE TABLE lending_daily(
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            lending_delta REAL,
            lending_balance REAL,
            source TEXT,
            updated_at REAL,
            source_quality TEXT,
            fetched_at REAL,
            lending_unit TEXT,
            PRIMARY KEY(date,code)
        );
        INSERT INTO credit_balance_daily VALUES(
            '2026-08-28','2317',12,9012,-3,105,2500,120000,
            'TWSE_MARGIN_OFFICIAL','TWSE_SBL_OFFICIAL','2026-09-01T09:00:00+00:00'
        );
        INSERT INTO margin_daily VALUES(
            '2026-08-28','2317',777,9012,9,105,'FinMind',1,
            'secondary',1,NULL,NULL
        );
        INSERT INTO lending_daily VALUES(
            '2026-08-28','2317',9,120000,'FinMind',1,
            'secondary',1,NULL
        );
        INSERT INTO margin_daily VALUES(
            '2025-11-06','7769',5,500,0,0,'FinMind',1,
            'secondary',1,NULL,NULL
        );
        INSERT INTO lending_daily VALUES(
            '2025-11-06','7769',50,5000,'FinMind',1,
            'secondary',1,NULL
        );
        """
    )

    result = reconcile_official_credit_legacy_mirrors(conn)

    assert result["ok"] is True
    assert result["margin_rows_reconciled"] == 1
    assert result["lending_rows_reconciled"] == 1
    assert result["legacy_margin_only_rows_preserved"] == 1
    assert result["legacy_lending_only_rows_preserved"] == 1
    assert result["business_rows_deleted"] == 0
    margin = conn.execute(
        """
        SELECT margin_delta,short_delta,source,source_quality,margin_unit,short_unit
        FROM margin_daily WHERE date='2026-08-28' AND code='2317'
        """
    ).fetchone()
    assert tuple(margin) == (
        12.0,
        -3.0,
        "TWSE_MARGIN_OFFICIAL",
        "official",
        "lots",
        "lots",
    )
    lending = conn.execute(
        """
        SELECT lending_delta,source,source_quality,lending_unit
        FROM lending_daily WHERE date='2026-08-28' AND code='2317'
        """
    ).fetchone()
    assert tuple(lending) == (
        2500.0,
        "TWSE_SBL_OFFICIAL",
        "official",
        "shares",
    )
    assert conn.execute("SELECT COUNT(*) FROM margin_daily").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM lending_daily").fetchone()[0] == 2
