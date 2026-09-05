from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.data_quality import assess_daily_ohlcv  # noqa: E402
from core.market_foundation_schema import upsert_daily_ohlcv  # noqa: E402


def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE history_price(
            date TEXT, code TEXT, open REAL, high REAL, low REAL, close REAL,
            volume REAL, amount REAL, volume_unit TEXT, source TEXT,
            updated_at REAL, PRIMARY KEY(date, code)
        );
        CREATE TABLE institution_daily(
            date TEXT, code TEXT, foreign_net REAL, trust_net REAL,
            dealer_net REAL, source TEXT, updated_at REAL,
            PRIMARY KEY(date, code)
        );
        CREATE TABLE margin_daily(
            date TEXT, code TEXT, margin_delta REAL, margin_balance REAL,
            short_delta REAL, short_balance REAL, source TEXT, updated_at REAL,
            PRIMARY KEY(date, code)
        );
        """
    )
    return conn


def bar(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "date": "2026-08-21",
        "code": "2454",
        "open": 3725.0,
        "high": 3810.0,
        "low": 3695.0,
        "close": 3790.0,
        "volume": 1_000_000,
        "amount": 3_790_000_000,
        "volume_unit": "shares",
        "source": "TWSE STOCK_DAY",
        "updated_at": 1.0,
    }
    row.update(overrides)
    return row


class OhlcvQualityAndPriorityTests(unittest.TestCase):
    def test_rejects_impossible_or_zero_volume_daily_bars(self) -> None:
        self.assertFalse(assess_daily_ohlcv(bar(open=3820.0))["ready"])
        self.assertFalse(assess_daily_ohlcv(bar(volume=0))["ready"])
        self.assertFalse(assess_daily_ohlcv(bar(close=0))["ready"])

    def test_rejects_known_typhoon_closure_date(self) -> None:
        conn = memory_db()
        try:
            self.assertFalse(upsert_daily_ohlcv(conn, bar(date="2026-07-10")))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM history_price").fetchone()[0], 0)
        finally:
            conn.close()

    def test_yahoo_cannot_overwrite_official_row(self) -> None:
        conn = memory_db()
        try:
            self.assertTrue(upsert_daily_ohlcv(conn, bar()))
            self.assertFalse(
                upsert_daily_ohlcv(
                    conn,
                    bar(
                        close=3870.0,
                        high=3870.0,
                        source="Yahoo Finance chart 2454.TW",
                        source_quality="FALLBACK",
                        updated_at=2.0,
                    ),
                )
            )
            saved = conn.execute(
                "SELECT close,high,source FROM history_price WHERE date=? AND code=?",
                ("2026-08-21", "2454"),
            ).fetchone()
            self.assertEqual((saved["close"], saved["high"], saved["source"]), (3790.0, 3810.0, "TWSE STOCK_DAY"))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
