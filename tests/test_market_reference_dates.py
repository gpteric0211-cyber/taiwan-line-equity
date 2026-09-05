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

from repository.history_repository import recent_market_reference_dates


class MarketReferenceDateTests(unittest.TestCase):
    def test_default_reference_dates_stop_at_publication_marker(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE history_price(date TEXT, code TEXT, close REAL)")
            conn.execute(
                "CREATE TABLE full_market_batch_publications(trade_date TEXT PRIMARY KEY)"
            )
            conn.executemany(
                "INSERT INTO history_price(date,code,close) VALUES(?,?,?)",
                [
                    ("2026-08-25", "1101", 20.0),
                    ("2026-08-26", "1101", 21.0),
                    ("2026-08-27", "1101", 99.0),
                ],
            )
            conn.execute(
                "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
                ("2026-08-26",),
            )

            dates = recent_market_reference_dates(conn, required_days=30)

        self.assertEqual(dates, ["2026-08-26", "2026-08-25"])

    def test_partial_completed_date_remains_visible_to_gap_detection(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE history_price(date TEXT, code TEXT, close REAL)")
            codes = ["1101", "1216", "1301", "1303", "1326"]
            conn.executemany(
                "INSERT INTO history_price(date,code,close) VALUES(?,?,?)",
                [("2026-08-19", code, 10.0) for code in codes]
                + [("2026-08-20", code, 10.0) for code in codes[:1]],
            )

            dates = recent_market_reference_dates(
                conn,
                required_days=30,
                latest_completed_date="2026-08-20",
            )

        self.assertEqual(dates, ["2026-08-20", "2026-08-19"])

    def test_expected_latest_completed_date_is_included_when_all_rows_are_missing(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE history_price(date TEXT, code TEXT, close REAL)")
            conn.execute(
                "INSERT INTO history_price(date,code,close) VALUES(?,?,?)",
                ("2026-08-19", "2454", 3845.0),
            )

            dates = recent_market_reference_dates(
                conn,
                required_days=30,
                latest_completed_date="2026-08-20",
            )

        self.assertEqual(dates, ["2026-08-20", "2026-08-19"])


if __name__ == "__main__":
    unittest.main()
