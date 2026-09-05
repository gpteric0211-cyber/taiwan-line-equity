from __future__ import annotations

import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

import app  # noqa: E402


class PublicationMarkerDashboardTest(unittest.TestCase):
    def test_dashboard_readiness_ignores_newer_partial_stock_day(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE full_market_batch_publications(
                    trade_date TEXT PRIMARY KEY
                );
                CREATE TABLE history_price(
                    date TEXT,code TEXT,open REAL,high REAL,low REAL,close REAL,
                    volume REAL,amount REAL,source TEXT,source_quality TEXT,
                    PRIMARY KEY(date,code)
                );
                CREATE TABLE eod_price(
                    date TEXT,code TEXT,close REAL,
                    PRIMARY KEY(date,code)
                );
                """
            )
            conn.execute(
                "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
                ("2026-08-26",),
            )
            conn.executemany(
                """
                INSERT INTO history_price(
                    date,code,open,high,low,close,volume,amount,source,source_quality
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    ("2026-08-26", "1101", 24.0, 24.5, 23.8, 24.3, 1_000_000, 24_300_000, "TWSE STOCK_DAY", "OFFICIAL"),
                    ("2026-08-27", "1101", 24.2, 24.4, 23.9, 24.1, 200_000, 4_820_000, "TWSE STOCK_DAY", "OFFICIAL"),
                ],
            )
            conn.executemany(
                "INSERT INTO eod_price(date,code,close) VALUES(?,?,?)",
                [
                    ("2026-08-26", "1101", 24.3),
                    ("2026-08-27", "1101", 24.1),
                ],
            )

            price, eod, history = app._latest_price_and_history_for_readiness(
                conn,
                "1101",
            )
            dashboard_date = app.latest_completed_tw50_close_date(conn, [])

        self.assertEqual(dashboard_date, "2026-08-26")
        self.assertEqual(price, 24.3)
        self.assertEqual(eod["date"], "2026-08-26")
        self.assertEqual(history[0]["date"], "2026-08-26")
        self.assertNotIn("2026-08-27", [row["date"] for row in history])


if __name__ == "__main__":
    unittest.main()
