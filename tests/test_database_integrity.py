from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core import db as db_module  # noqa: E402
from core.db import RUNTIME_SCHEMA_VERSION, assert_db_integrity, runtime_schema_is_current  # noqa: E402


class DatabaseIntegrityTests(unittest.TestCase):
    def test_accepts_valid_sqlite_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "valid.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO sample(value) VALUES('ok')")
            conn.commit()
            conn.close()
            assert_db_integrity(path)

    def test_rejects_non_sqlite_or_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.db"
            path.write_bytes(b"not a sqlite database")
            with self.assertRaises(RuntimeError):
                assert_db_integrity(path)

    def test_runtime_schema_marker_is_checked_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marked.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE app_schema_state(singleton_id INTEGER PRIMARY KEY,schema_version TEXT,applied_at TEXT)"
            )
            conn.execute(
                "INSERT INTO app_schema_state VALUES(1,?,CURRENT_TIMESTAMP)",
                (RUNTIME_SCHEMA_VERSION,),
            )
            conn.commit()
            conn.close()

            self.assertTrue(runtime_schema_is_current(path))

    def test_init_db_skips_startup_writes_when_schema_marker_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marked.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE app_schema_state(singleton_id INTEGER PRIMARY KEY,schema_version TEXT,applied_at TEXT)"
            )
            conn.execute(
                "INSERT INTO app_schema_state VALUES(1,?,CURRENT_TIMESTAMP)",
                (RUNTIME_SCHEMA_VERSION,),
            )
            conn.commit()
            conn.close()

            original_path = db_module.DB_PATH
            try:
                db_module.DB_PATH = path
                db_module.init_db()
            finally:
                db_module.DB_PATH = original_path

            conn = sqlite3.connect(path)
            try:
                watchlist = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist'"
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNone(watchlist)


if __name__ == "__main__":
    unittest.main()
