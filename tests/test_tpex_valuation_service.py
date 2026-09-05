from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter import yahoo_history  # noqa: E402
from services import tpex_valuation_service  # noqa: E402


def _create_valuation_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE valuation(
            date TEXT, code TEXT, dividend_yield REAL, pe REAL, pb REAL,
            source TEXT, updated_at REAL, eps REAL, eps_source TEXT,
            PRIMARY KEY(date,code)
        )
        """
    )


class TpexValuationServiceTests(unittest.TestCase):
    def test_official_tpex_valuation_replaces_fallback_and_populates_both_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "valuation.db"
            with closing(sqlite3.connect(db_path)) as conn:
                _create_valuation_schema(conn)
                conn.execute(
                    "INSERT INTO valuation VALUES(?,?,?,?,?,?,?,?,?)",
                    ("2026-08-21", "3491", 42.0, 194.0, 35.0, "Yahoo Finance 3491.TWO", 1, 7.64, "Yahoo Finance 3491.TWO"),
                )
                conn.commit()

            def open_db() -> sqlite3.Connection:
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            row = {
                "data_date": "2026-08-21", "symbol": "3491", "name": "昇達科",
                "dividend_yield": 0.53, "pe_ratio": 101.75, "pb_ratio": 19.39,
                "source": "TPEX_PERATIO_ANALYSIS", "source_status": "ok",
                "updated_at": "2026-08-21 15:00:00", "timezone": "Asia/Taipei",
            }
            with (
                patch.object(tpex_valuation_service, "db", side_effect=open_db),
                patch.object(tpex_valuation_service, "recent_market_date_for_eod", return_value="2026-08-21"),
                patch.object(tpex_valuation_service, "set_status"),
            ):
                result = tpex_valuation_service.refresh_tpex_valuation_codes(
                    ["3491"], fetcher=lambda: [row],
                )
            self.assertTrue(result["ok"])
            with closing(sqlite3.connect(db_path)) as check:
                legacy = check.execute(
                    "SELECT dividend_yield,pe,pb,source,eps FROM valuation WHERE code='3491' AND date='2026-08-21'"
                ).fetchone()
                official = check.execute(
                    "SELECT dividend_yield,pe_ratio,pb_ratio,source FROM twse_daily_valuation WHERE symbol='3491'"
                ).fetchone()
            self.assertEqual(legacy, (0.53, 101.75, 19.39, "TPEX_PERATIO_ANALYSIS", 7.64))
            self.assertEqual(official, (0.53, 101.75, 19.39, "TPEX_PERATIO_ANALYSIS"))

    def test_delayed_tpex_valuation_is_written_but_never_marked_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "delayed.db"
            with closing(sqlite3.connect(db_path)) as conn:
                _create_valuation_schema(conn)
                conn.commit()

            def open_db() -> sqlite3.Connection:
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            row = {
                "data_date": "2026-08-20", "symbol": "3491", "name": "昇達科",
                "dividend_yield": 0.53, "pe_ratio": 101.75, "pb_ratio": 19.39,
                "source": "TPEX_PERATIO_ANALYSIS", "source_status": "ok",
            }
            with (
                patch.object(tpex_valuation_service, "db", side_effect=open_db),
                patch.object(tpex_valuation_service, "recent_market_date_for_eod", return_value="2026-08-21"),
                patch.object(tpex_valuation_service, "set_status"),
            ):
                result = tpex_valuation_service.refresh_tpex_valuation_codes(
                    ["3491"], fetcher=lambda: [row],
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "source_delayed")
            self.assertEqual(result["source_delayed_codes"], ["3491"])

    def test_full_market_batch_accepts_high_coverage_and_reports_unavailable_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "coverage.db"
            with closing(sqlite3.connect(db_path)) as conn:
                _create_valuation_schema(conn)
                conn.commit()

            def open_db() -> sqlite3.Connection:
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            codes = [str(value).zfill(4) for value in range(1, 101)]
            rows = [
                {
                    "data_date": "2026-08-21",
                    "symbol": code,
                    "name": code,
                    "dividend_yield": 0.0,
                    "pe_ratio": 10.0,
                    "pb_ratio": 1.0,
                    "source": "TPEX_PERATIO_ANALYSIS",
                    "source_status": "ok",
                }
                for code in codes[:96]
            ]
            with (
                patch.object(tpex_valuation_service, "db", side_effect=open_db),
                patch.object(tpex_valuation_service, "recent_market_date_for_eod", return_value="2026-08-21"),
                patch.object(tpex_valuation_service, "set_status"),
            ):
                result = tpex_valuation_service.refresh_tpex_valuation_codes(
                    codes,
                    fetcher=lambda: rows,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["coverage_pct"], 96.0)
            self.assertEqual(result["missing_codes"], codes[96:])

    def test_yahoo_valuation_never_overwrites_existing_official_exchange_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "preserve.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                _create_valuation_schema(conn)
                conn.execute(
                    "INSERT INTO valuation VALUES(?,?,?,?,?,?,?,?,?)",
                    ("2026-08-21", "3491", 0.53, 101.75, 19.39, "TPEX_PERATIO_ANALYSIS", 1, None, None),
                )
                conn.commit()

            def open_db() -> sqlite3.Connection:
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            with (
                patch.object(yahoo_history, "db", side_effect=open_db),
                patch.object(yahoo_history, "set_status"),
            ):
                result = yahoo_history.upsert_yfinance_tw_valuation("3491", market_type="otc")
            self.assertTrue(result["ok"])
            self.assertEqual(result["error"], "official_valuation_preserved")
            with closing(sqlite3.connect(db_path)) as check:
                saved = check.execute(
                    "SELECT dividend_yield,pe,pb,source FROM valuation WHERE code='3491'"
                ).fetchone()
            self.assertEqual(saved, (0.53, 101.75, 19.39, "TPEX_PERATIO_ANALYSIS"))


if __name__ == "__main__":
    unittest.main()
