from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.market_analytics_schema import ensure_market_analytics_schema  # noqa: E402
from scoring import _wilder_rsi, calculate_indicators, wilder_rsi_value  # noqa: E402
from services.market_analytics_service import rebuild_daily_technical_snapshots  # noqa: E402
from services.stock_master_service import sync_official_stock_master  # noqa: E402


def trading_dates(start: date, count: int) -> list[str]:
    values: list[str] = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current += timedelta(days=1)
    return values


class DailyTechnicalSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "analytics.db"
        self.rows: list[dict] = []
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE history_price(
                    date TEXT,code TEXT,open REAL,high REAL,low REAL,close REAL,
                    volume REAL,volume_unit TEXT,source TEXT,source_quality TEXT,
                    market TEXT,PRIMARY KEY(date,code)
                );
                CREATE TABLE stock_industry_profile(code TEXT PRIMARY KEY);
                CREATE TABLE full_market_batch_publications(
                    trade_date TEXT PRIMARY KEY
                );
                """
            )
            ensure_market_analytics_schema(conn)
            conn.execute(
                """
                INSERT INTO stock_master(
                    code,name,market,exchange,security_type,is_active,source,
                    source_status,updated_at
                ) VALUES('2454','聯發科','listed','TWSE','stock',1,'TEST','ok','2026-01-01')
                """
            )
            for index, trade_date in enumerate(trading_dates(date(2025, 12, 1), 130)):
                close = 100.0 + index * 0.2 + ((index % 5) - 2) * 0.1
                row = {
                    "date": trade_date,
                    "code": "2454",
                    "open": close - 0.3,
                    "high": close + 0.8,
                    "low": close - 0.9,
                    "close": close,
                    "volume": 1_000_000 + index * 1_000,
                    "volume_unit": "shares",
                    "source": "TWSE STOCK_DAY",
                    "source_quality": "official",
                    "market": "listed",
                }
                self.rows.append(row)
                conn.execute(
                    "INSERT INTO history_price VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(row.values()),
                )
            conn.execute(
                "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
                (self.rows[-1]["date"],),
            )
            conn.commit()

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            return conn

        patcher = patch("services.market_analytics_service.db", side_effect=connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_latest_snapshot_reuses_shared_indicator_formula(self) -> None:
        result = rebuild_daily_technical_snapshots(codes=["2454"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_written"], 1)
        self.assertEqual(result["write_commit_interval_codes"], 50)
        self.assertEqual(result["write_batch_count"], 1)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            stored = conn.execute(
                "SELECT * FROM daily_technical_snapshot WHERE code='2454'"
            ).fetchone()
        expected = calculate_indicators(pd.DataFrame(self.rows)).iloc[-1]
        self.assertEqual(stored["trade_date"], self.rows[-1]["date"])
        self.assertEqual(stored["data_quality"], "ok")
        self.assertEqual(stored["decision_ready"], 1)
        self.assertAlmostEqual(stored["rsi14"], float(expected["rsi14"]), places=12)
        self.assertAlmostEqual(stored["macd_dif"], float(expected["dif"]), places=12)
        self.assertAlmostEqual(stored["macd_signal"], float(expected["macd_signal"]), places=12)

    @patch("services.market_analytics_service.history_date_coverage")
    def test_latest_snapshot_accepts_calendar_gaps_when_official_no_trade_dates_are_verified(
        self,
        coverage_mock,
    ) -> None:
        coverage_mock.return_value = {
            "ready": True,
            "reason": "ok_with_verified_no_trade_dates",
            "verified_no_trade_dates": ["2026-02-11"],
        }

        result = rebuild_daily_technical_snapshots(codes=["2454"])

        self.assertEqual(result["snapshot_rows_ready"], 1)
        with closing(sqlite3.connect(self.path)) as conn:
            stored = conn.execute(
                "SELECT data_quality,decision_ready,quality_reason FROM daily_technical_snapshot WHERE code='2454'"
            ).fetchone()
        self.assertEqual(stored[0], "ok")
        self.assertEqual(stored[1], 1)
        self.assertIn("official no-trade dates verified", stored[2])

    def test_exact_missing_date_is_reported_without_fake_snapshot(self) -> None:
        result = rebuild_daily_technical_snapshots(
            codes=["2454"],
            trade_date="2026-12-31",
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed_code_count"], 0)
        self.assertEqual(result["missing_history_codes"], ["2454"])

    def test_scalar_rsi_matches_canonical_series_for_every_prefix(self) -> None:
        closes = [float(row["close"]) for row in self.rows]
        for period in (5, 10, 14):
            for end in range(period + 1, len(closes) + 1):
                expected = _wilder_rsi(
                    pd.Series(closes[max(0, end - 120) : end], dtype="float64"),
                    period,
                ).iloc[-1]
                actual = wilder_rsi_value(closes[:end], period)
                self.assertIsNotNone(actual)
                self.assertAlmostEqual(float(actual), float(expected), places=12)


class OfficialStockMasterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "master.db"
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE history_price(date TEXT,code TEXT,PRIMARY KEY(date,code));
                INSERT INTO history_price VALUES('2026-08-21','2330');
                INSERT INTO history_price VALUES('2026-08-21','3491');
                """
            )
            conn.commit()

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            return conn

        patcher = patch("services.stock_master_service.db", side_effect=connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_syncs_both_official_company_universes(self) -> None:
        result = sync_official_stock_master(
            twse_fetcher=lambda: {
                "ok": True,
                "source": "TWSE_COMPANY_OPENAPI",
                "rows": 1,
                "items": [
                    {
                        "code": "2330",
                        "name": "台積電",
                        "market": "listed",
                        "exchange": "TWSE",
                        "security_type": "stock",
                        "listing_date": "1994-09-05",
                        "source": "TWSE_COMPANY_OPENAPI",
                    }
                ],
            },
            tpex_fetcher=lambda: {
                "ok": True,
                "source": "TPEX OpenAPI",
                "rows": 1,
                "items": [{"code": "3491", "name": "昇達科"}],
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["universe_versions_written"], 2)
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute(
                "SELECT code,market,exchange,last_seen_date FROM stock_master ORDER BY code"
            ).fetchall()
            universe_versions = conn.execute(
                """
                SELECT COUNT(*) FROM data_observation_version
                WHERE dataset_key='stock_universe_membership'
                """
            ).fetchone()[0]
        self.assertEqual(rows, [("2330", "listed", "TWSE", "2026-08-21"), ("3491", "otc", "TPEX", "2026-08-21")])
        self.assertEqual(universe_versions, 2)


if __name__ == "__main__":
    unittest.main()
