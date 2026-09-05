from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from create_sqlite_snapshot import create_snapshot  # noqa: E402


def test_create_snapshot_copies_consistent_database(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "output" / "snapshot.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE history_price (date TEXT NOT NULL, close REAL NOT NULL)")
        conn.execute("INSERT INTO history_price VALUES ('2026-08-21', 100.0)")

    result = create_snapshot(source, destination)

    assert result["quick_check"] == "ok"
    assert result["history_latest"] == "2026-08-21"
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT close FROM history_price").fetchone()[0] == 100.0
