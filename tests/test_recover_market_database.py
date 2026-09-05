from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from recover_market_database import recover_database  # noqa: E402


def _create_source(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE history_price (
                stock_id TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL NOT NULL,
                PRIMARY KEY(stock_id, date)
            );
            CREATE TABLE price_volume_distribution (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                price REAL NOT NULL,
                volume_lots INTEGER NOT NULL,
                UNIQUE(stock_id, trade_date, price)
            );
            CREATE INDEX idx_price_volume_distribution_date
                ON price_volume_distribution(trade_date);
            CREATE TABLE fugle_intraday_trades (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                serial TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY(code, trade_date, serial, source)
            );
            CREATE INDEX idx_fugle_intraday_trades_code_date
                ON fugle_intraday_trades(code, trade_date);
            CREATE TABLE fugle_intraday_capture_runs (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY(code, trade_date, endpoint, source)
            );
            CREATE TABLE trusted_reference (
                code TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE technical_indicator_component (
                trade_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                value REAL,
                PRIMARY KEY(trade_date, stock_code)
            );
            CREATE TABLE technical_indicator_state (
                stock_code TEXT PRIMARY KEY,
                state_value REAL
            );
            INSERT INTO history_price VALUES ('6669', '2026-08-27', 6960.0);
            INSERT INTO history_price VALUES ('6669', '2026-08-28', 7200.0);
            INSERT INTO price_volume_distribution(stock_id, trade_date, price, volume_lots)
                VALUES ('6669', '2026-08-27', 6960.0, 10);
            INSERT INTO price_volume_distribution(stock_id, trade_date, price, volume_lots)
                VALUES ('6669', '2026-08-28', 7200.0, 20);
            INSERT INTO fugle_intraday_trades VALUES
                ('6669', '2026-08-28', '1', 'FUGLE');
            INSERT INTO fugle_intraday_capture_runs VALUES
                ('6669', '2026-08-28', 'trades', 'FUGLE');
            INSERT INTO trusted_reference VALUES ('6669', 'preserve-me');
            INSERT INTO technical_indicator_component VALUES ('2026-08-28', '6669', 1.0);
            INSERT INTO technical_indicator_state VALUES ('6669', 1.0);
            """
        )


def test_recover_database_copies_trusted_and_resets_supplemental_claims(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "recovered.db"
    _create_source(source)

    result = recover_database(source, destination, price_volume_history_days=2)

    assert result["source_preserved"] is True
    assert result["verification"]["quick_check"] == "ok"
    assert result["verification"]["integrity_check"] == "ok"
    assert result["verification"]["journal_mode"] == "wal"
    assert result["salvage"]["price_volume_distribution"]["rows"] == 2
    with sqlite3.connect(destination) as conn:
        assert conn.execute(
            "SELECT value FROM trusted_reference WHERE code='6669'"
        ).fetchone()[0] == "preserve-me"
        assert conn.execute(
            "SELECT COUNT(*) FROM price_volume_distribution"
        ).fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM fugle_intraday_trades").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fugle_intraday_capture_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM technical_indicator_component").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM technical_indicator_state").fetchone()[0] == 0


def test_recover_database_refuses_existing_or_same_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _create_source(source)
    existing = tmp_path / "existing.db"
    existing.touch()

    with pytest.raises(ValueError, match="must differ"):
        recover_database(source, source)
    with pytest.raises(FileExistsError, match="already exists"):
        recover_database(source, existing)
