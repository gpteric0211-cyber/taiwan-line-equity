from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from salvage_fugle_scoped_rows import salvage_scoped_fugle_rows  # noqa: E402


def _create_database(path: Path, *, with_rows: bool) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE fugle_intraday_trades(
                code TEXT, trade_date TEXT, trade_time TEXT, size INTEGER,
                source TEXT, serial TEXT, raw_json TEXT,
                PRIMARY KEY(code,trade_date,serial,source)
            );
            CREATE TABLE fugle_intraday_capture_runs(
                code TEXT, trade_date TEXT, endpoint TEXT, source TEXT,
                normalized_row_count INTEGER, stored_row_count INTEGER,
                capture_complete INTEGER, data_quality TEXT,
                captured_volume_lots INTEGER, latest_trade_time TEXT,
                PRIMARY KEY(code,trade_date,endpoint,source)
            );
            CREATE TABLE price_volume_distribution(
                stock_id TEXT, trade_date TEXT, data_quality TEXT,
                source_quality TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO price_volume_distribution VALUES('2330','2026-09-01','VALIDATED','VALIDATED')"
        )
        if with_rows:
            conn.execute(
                "INSERT INTO fugle_intraday_trades VALUES(?,?,?,?,?,?,?)",
                (
                    "2330",
                    "2026-09-01",
                    "13:30:00.000000",
                    5,
                    "FUGLE",
                    "1",
                    json.dumps({"price": 100, "size": 5}),
                ),
            )
            conn.execute(
                "INSERT INTO fugle_intraday_capture_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "2330",
                    "2026-09-01",
                    "trades",
                    "FUGLE",
                    1,
                    1,
                    1,
                    "SESSION_COMPLETE",
                    5,
                    "13:30:00.000000",
                ),
            )


def test_salvage_scoped_rows_copies_only_validated_code(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _create_database(source, with_rows=True)
    _create_database(destination, with_rows=False)

    result = salvage_scoped_fugle_rows(
        source, destination, "2026-09-01", ["2330"]
    )

    assert result["ok"] is True
    assert result["copied_trade_rows"] == 1
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fugle_intraday_trades").fetchone()[0] == 1
        assert conn.execute("SELECT data_quality FROM fugle_intraday_capture_runs").fetchone()[0] == "SESSION_COMPLETE"
