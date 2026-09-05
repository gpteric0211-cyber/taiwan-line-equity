from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services import price_volume_service as service  # noqa: E402


TRADE_DATE = "2026-08-21"
CODE = "2454"


def fugle_payload(*, date: str = TRADE_DATE) -> dict:
    return {
        "date": date,
        "symbol": CODE,
        "data": [
            {"price": 99.0, "volume": 40, "volumeAtBid": 15, "volumeAtAsk": 20},
            {"price": 100.0, "volume": 60, "volumeAtBid": 25, "volumeAtAsk": 25},
        ],
    }


class PriceVolumeCaptureLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "price_volume_lifecycle.db"
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE history_price (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    volume_unit TEXT,
                    source TEXT,
                    source_quality TEXT,
                    market TEXT,
                    fetched_at REAL,
                    updated_at REAL,
                    PRIMARY KEY(date, code)
                );
                CREATE TABLE price_volume_distribution (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    price REAL NOT NULL,
                    volume_lots INTEGER NOT NULL DEFAULT 0,
                    volume_shares INTEGER,
                    total_volume_lots INTEGER,
                    snapshot_time TEXT,
                    created_at REAL,
                    updated_at REAL,
                    buy_volume_lots INTEGER NOT NULL DEFAULT 0,
                    sell_volume_lots INTEGER NOT NULL DEFAULT 0,
                    neutral_volume_lots INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    source_quality TEXT,
                    fetched_at REAL,
                    volume_at_bid INTEGER,
                    volume_at_ask INTEGER,
                    data_quality TEXT,
                    UNIQUE(stock_id, trade_date, price)
                );
                CREATE TABLE price_volume_profile_daily (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    source_level INTEGER,
                    source_name TEXT,
                    source_hash TEXT,
                    trade_scope TEXT,
                    volume_unit TEXT,
                    total_volume_shares REAL,
                    eod_volume_shares REAL,
                    volume_diff_pct REAL,
                    price_level_count INTEGER,
                    min_price REAL,
                    max_price REAL,
                    profile_json TEXT,
                    quality TEXT,
                    quality_reason TEXT,
                    fetched_at REAL,
                    PRIMARY KEY(date, code)
                );
                CREATE TABLE price_volume_score_daily (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    status TEXT,
                    quality TEXT,
                    PRIMARY KEY(date, code)
                );
                CREATE TABLE daily_inner_outer_volume (
                    stock_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    source TEXT,
                    PRIMARY KEY(stock_code, trade_date)
                );
                """
            )
            conn.commit()
        self.db_patcher = patch.object(service, "db", side_effect=self._connect)
        self.db_patcher.start()
        self.addCleanup(self.db_patcher.stop)
        self.now_patcher = patch.object(
            service,
            "now_tpe",
            return_value=datetime(2026, 8, 21, 13, 35, tzinfo=ZoneInfo("Asia/Taipei")),
        )
        self.now_patcher.start()
        self.addCleanup(self.now_patcher.stop)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _capture(self, payload: dict | None = None) -> dict:
        return service.capture_fugle_price_volume_snapshot(
            CODE,
            payload or fugle_payload(),
            expected_date=TRADE_DATE,
        )

    def _insert_history(
        self,
        *,
        date: str = TRADE_DATE,
        volume: float = 100_000,
        source: str = "TWSE STOCK_DAY",
        source_quality: str = "OFFICIAL",
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO history_price(
                    date,code,open,high,low,close,volume,volume_unit,
                    source,source_quality,fetched_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (date, CODE, 99.0, 101.0, 98.0, 100.0, volume, "shares", source, source_quality, 1.0, 1.0),
            )
            conn.commit()

    def _insert_high_profile(self, source_hash: str = "trusted-high-hash") -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO price_volume_profile_daily(
                    date,code,source_level,source_name,source_hash,trade_scope,volume_unit,
                    total_volume_shares,eod_volume_shares,volume_diff_pct,price_level_count,
                    min_price,max_price,profile_json,quality,quality_reason,fetched_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    TRADE_DATE,
                    CODE,
                    3,
                    "Fugle intraday volumes",
                    source_hash,
                    "regular_intraday",
                    "shares",
                    100_000,
                    100_000,
                    0.0,
                    2,
                    99.0,
                    100.0,
                    '[{"price":99.0,"volume":40000},{"price":100.0,"volume":60000}]',
                    "high",
                    "previously validated",
                    1.0,
                ),
            )
            conn.commit()

    def _insert_complete_trade_capture(self, *, captured_volume_lots: int = 100) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fugle_intraday_capture_runs(
                    code,trade_date,endpoint,source,snapshot_time,page_count,
                    provider_row_count,normalized_row_count,stored_row_count,
                    capture_complete,data_quality,reason,latest_trade_time,
                    latest_cumulative_volume,captured_volume_lots,fetched_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    CODE, TRADE_DATE, "trades", "FUGLE", f"{TRADE_DATE} 13:35:00",
                    2, 10, 10, 10, 1, "SESSION_COMPLETE", "", "13:30:00.000000",
                    captured_volume_lots, captured_volume_lots, 1.0,
                ),
            )
            conn.commit()

    def _insert_illiquid_post_close_capture(self, *, captured_volume_lots: int = 100) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fugle_intraday_capture_runs(
                    code,trade_date,endpoint,source,snapshot_time,page_count,
                    provider_row_count,normalized_row_count,stored_row_count,
                    capture_complete,data_quality,reason,latest_trade_time,
                    latest_cumulative_volume,captured_volume_lots,fetched_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    CODE, TRADE_DATE, "trades", "FUGLE", f"{TRADE_DATE} 13:35:00",
                    2, 10, 10, 10, 0, "PAGINATION_COMPLETE_SESSION_UNVERIFIED",
                    "latest trade preceded the closing auction", "12:45:00.000000",
                    captured_volume_lots, captured_volume_lots, 1.0,
                ),
            )
            conn.commit()

    def _insert_trade(self, *, trade_time: str, size: int, serial: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO fugle_intraday_trades(
                    code,trade_date,trade_time,price,size,volume,bid,ask,serial,
                    source,fetched_at,data_quality
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (CODE, TRADE_DATE, trade_time, 100.0, size, size, 99.5, 100.0,
                 serial, "FUGLE", 1.0, "SESSION_COMPLETE"),
            )
            conn.commit()

    def test_intraday_capture_writes_only_provisional_distribution(self) -> None:
        result = self._capture()

        self.assertTrue(result["ok"])
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT trade_date,price,volume_lots,volume_shares,volume_at_bid,volume_at_ask,
                       neutral_volume_lots,source,source_quality,data_quality
                FROM price_volume_distribution
                WHERE stock_id=?
                ORDER BY price
                """,
                (CODE,),
            ).fetchall()
            profile_count = conn.execute(
                "SELECT COUNT(*) FROM price_volume_profile_daily WHERE code=?",
                (CODE,),
            ).fetchone()[0]
            score_count = conn.execute(
                "SELECT COUNT(*) FROM price_volume_score_daily WHERE code=?",
                (CODE,),
            ).fetchone()[0]

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["trade_date"] for row in rows}, {TRADE_DATE})
        self.assertEqual([row["volume_lots"] for row in rows], [40, 60])
        self.assertEqual([row["volume_shares"] for row in rows], [40_000, 60_000])
        self.assertEqual([row["volume_at_bid"] for row in rows], [15, 25])
        self.assertEqual([row["volume_at_ask"] for row in rows], [20, 25])
        self.assertEqual([row["neutral_volume_lots"] for row in rows], [5, 10])
        self.assertEqual({row["source"] for row in rows}, {"FUGLE"})
        self.assertEqual({row["source_quality"] for row in rows}, {"INTRADAY_SNAPSHOT"})
        self.assertEqual({row["data_quality"] for row in rows}, {"INTRADAY_SNAPSHOT"})
        self.assertEqual(profile_count, 0)
        self.assertEqual(score_count, 0)

    def test_preclose_snapshot_cannot_be_reconciled(self) -> None:
        with patch.object(
            service,
            "now_tpe",
            return_value=datetime(2026, 8, 21, 13, 20, tzinfo=ZoneInfo("Asia/Taipei")),
        ):
            capture = self._capture()
        self.assertTrue(capture["ok"])
        self._insert_history()

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertFalse(result.get("ok", False))
        self.assertEqual(result["status"], "awaiting_post_close_snapshot")
        compute.assert_not_called()

    def test_missing_or_wrong_payload_date_writes_nothing(self) -> None:
        missing = fugle_payload()
        missing.pop("date")
        wrong = fugle_payload(date="2026-07-23")

        missing_result = self._capture(missing)
        wrong_result = self._capture(wrong)

        self.assertFalse(missing_result["ok"])
        self.assertFalse(wrong_result["ok"])
        with closing(self._connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_volume_distribution").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_volume_profile_daily").fetchone()[0], 0)

    def test_rebuild_rejects_unverified_persisted_snapshot_time(self) -> None:
        result = service.capture_fugle_price_volume_snapshot(
            CODE,
            fugle_payload(),
            expected_date=TRADE_DATE,
            verified_snapshot_time="2026-08-22 01:00:00+08:00",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "source_delayed")
        self.assertFalse(result["writes_db"])
        with closing(self._connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_volume_distribution").fetchone()[0], 0)

    def test_rebuild_accepts_delayed_snapshot_only_with_verified_terminal_permission(self) -> None:
        result = service.capture_fugle_price_volume_snapshot(
            CODE,
            fugle_payload(),
            expected_date=TRADE_DATE,
            verified_snapshot_time="2026-08-22 01:00:00+08:00",
            allow_delayed_terminal_pagination=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "captured")
        self.assertTrue(result["writes_db"])
        with closing(self._connect()) as conn:
            snapshot_times = {
                row[0]
                for row in conn.execute(
                    "SELECT snapshot_time FROM price_volume_distribution WHERE stock_id=?",
                    (CODE,),
                ).fetchall()
            }
        self.assertEqual(snapshot_times, {"2026-08-22 01:00:00+08:00"})

    def test_capture_and_failed_reconcile_do_not_overwrite_existing_high_profile(self) -> None:
        self._insert_high_profile()
        self._capture()
        self._insert_history(volume=120_000)

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertFalse(result.get("ok", False))
        compute.assert_not_called()
        with closing(self._connect()) as conn:
            profile = conn.execute(
                "SELECT source_hash,quality,quality_reason FROM price_volume_profile_daily WHERE code=? AND date=?",
                (CODE, TRADE_DATE),
            ).fetchone()
            qualities = {
                row[0]
                for row in conn.execute(
                    "SELECT data_quality FROM price_volume_distribution WHERE stock_id=? AND trade_date=?",
                    (CODE, TRADE_DATE),
                ).fetchall()
            }
        self.assertEqual(profile["source_hash"], "trusted-high-hash")
        self.assertEqual(profile["quality"], "high")
        self.assertEqual(profile["quality_reason"], "previously validated")
        self.assertNotEqual(qualities, {"VALIDATED"})

    def test_reconcile_requires_same_day_official_eod_before_validation_and_compute(self) -> None:
        self._capture()
        self._insert_complete_trade_capture()
        self._insert_history(
            source="FinMind",
            source_quality="FALLBACK",
        )

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            fallback_result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)
            self.assertFalse(fallback_result["ok"])
            compute.assert_not_called()

            self._insert_history(source="TWSE STOCK_DAY", source_quality="OFFICIAL")
            compute.return_value = {
                "available": False,
                "status": "accumulating",
                "quality": "accumulating",
                "quality_reason": "1/30 validated Fugle days",
            }
            validated_result = service.reconcile_price_volume_profile_for_code(
                CODE,
                TRADE_DATE,
                required_days=30,
            )

        self.assertTrue(validated_result["ok"])
        compute.assert_called_once_with(
            CODE,
            required_days=30,
            as_of_date=TRADE_DATE,
        )
        with closing(self._connect()) as conn:
            profile = conn.execute(
                """
                SELECT quality,source_name,total_volume_shares,eod_volume_shares,volume_diff_pct
                FROM price_volume_profile_daily
                WHERE code=? AND date=?
                """,
                (CODE, TRADE_DATE),
            ).fetchone()
            qualities = {
                (row["source_quality"], row["data_quality"])
                for row in conn.execute(
                    """
                    SELECT source_quality,data_quality
                    FROM price_volume_distribution
                    WHERE stock_id=? AND trade_date=?
                    """,
                    (CODE, TRADE_DATE),
                ).fetchall()
            }

        self.assertEqual(profile["quality"], "high")
        self.assertIn("Fugle", profile["source_name"])
        self.assertEqual(profile["total_volume_shares"], 100_000)
        self.assertEqual(profile["eod_volume_shares"], 100_000)
        self.assertAlmostEqual(profile["volume_diff_pct"], 0.0)
        self.assertEqual(qualities, {("VALIDATED", "VALIDATED")})

    def test_next_day_latest_completed_capture_can_reconcile_without_faking_snapshot_time(self) -> None:
        next_day = datetime(2026, 8, 22, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        with patch.object(service, "now_tpe", return_value=next_day):
            capture = self._capture()
        self.assertTrue(capture["ok"])
        self._insert_complete_trade_capture()
        self._insert_history(source="TWSE STOCK_DAY", source_quality="OFFICIAL")

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            compute.return_value = {
                "available": False,
                "status": "accumulating",
                "quality_reason": "1/30 validated Fugle days",
            }
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertTrue(result["ok"])
        with closing(self._connect()) as conn:
            snapshot = conn.execute(
                "SELECT snapshot_time,data_quality FROM price_volume_distribution WHERE stock_id=? LIMIT 1",
                (CODE,),
            ).fetchone()
            distribution, issue = service._read_true_distribution_for_date(conn, CODE, TRADE_DATE)
        self.assertTrue(str(snapshot["snapshot_time"]).startswith("2026-08-22"))
        self.assertEqual(snapshot["data_quality"], "VALIDATED")
        self.assertIsNone(issue)
        self.assertTrue(distribution["distribution_validated"])
        self.assertTrue(distribution["session_complete"])

    def test_documented_small_opening_trade_exclusion_does_not_block_reconcile(self) -> None:
        self._capture()
        self._insert_complete_trade_capture(captured_volume_lots=102)
        self._insert_history(source="TWSE STOCK_DAY", source_quality="OFFICIAL")

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            compute.return_value = {"available": False, "status": "accumulating"}
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertTrue(result["ok"])
        compute.assert_called_once()

    def test_after_hours_and_odd_lot_gap_does_not_invalidate_regular_distribution(self) -> None:
        self._capture()
        self._insert_trade(trade_time="09:00:00.000000", size=40, serial="regular-1")
        self._insert_trade(trade_time="13:30:00.000000", size=60, serial="regular-2")
        self._insert_trade(trade_time="14:30:00.000000", size=4, serial="after-hours")
        self._insert_complete_trade_capture(captured_volume_lots=104)
        self._insert_history(volume=104_000, source="TWSE STOCK_DAY", source_quality="OFFICIAL")

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            compute.return_value = {"available": False, "status": "accumulating"}
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["volume_diff_pct"], 100 / 26)
        compute.assert_called_once()
        with closing(self._connect()) as conn:
            profile = conn.execute(
                "SELECT trade_scope,total_volume_shares,eod_volume_shares,quality_reason "
                "FROM price_volume_profile_daily WHERE code=? AND date=?",
                (CODE, TRADE_DATE),
            ).fetchone()
        self.assertEqual(profile["trade_scope"], "regular_intraday")
        self.assertEqual(profile["total_volume_shares"], 100_000)
        self.assertEqual(profile["eod_volume_shares"], 104_000)
        self.assertIn("regular_intraday", profile["quality_reason"])

    def test_complete_scoped_distribution_is_stored_but_excluded_from_scoring(self) -> None:
        self._capture()
        self._insert_trade(trade_time="09:00:00.000000", size=40, serial="regular-1")
        self._insert_trade(trade_time="13:30:00.000000", size=60, serial="regular-2")
        self._insert_trade(trade_time="14:30:00.000000", size=50, serial="after-hours")
        self._insert_complete_trade_capture(captured_volume_lots=150)
        self._insert_history(volume=150_000, source="TWSE STOCK_DAY", source_quality="OFFICIAL")

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "scoped_validated")
        self.assertFalse(result["score_available"])
        self.assertIn(
            "profile_official_volume_coverage_below_threshold",
            result["profile_reconciliation"]["reasons"],
        )
        compute.assert_not_called()
        with closing(self._connect()) as conn:
            profile = conn.execute(
                "SELECT quality FROM price_volume_profile_daily WHERE code=? AND date=?",
                (CODE, TRADE_DATE),
            ).fetchone()
            score = conn.execute(
                "SELECT status,quality FROM price_volume_score_daily WHERE code=? AND date=?",
                (CODE, TRADE_DATE),
            ).fetchone()
            qualities = {
                row[0]
                for row in conn.execute(
                    "SELECT data_quality FROM price_volume_distribution WHERE stock_id=? AND trade_date=?",
                    (CODE, TRADE_DATE),
                ).fetchall()
            }
        self.assertEqual(profile["quality"], "scoped")
        self.assertEqual(score["status"], "excluded_scope_coverage")
        self.assertEqual(score["quality"], "scoped")
        self.assertEqual(qualities, {"SCOPED_VALIDATED"})

    def test_illiquid_stock_can_complete_only_after_official_volume_reconciliation(self) -> None:
        self._capture()
        self._insert_illiquid_post_close_capture()
        self._insert_history(source="TWSE STOCK_DAY", source_quality="OFFICIAL")

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            compute.return_value = {"available": False, "status": "accumulating"}
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertTrue(result["ok"])
        compute.assert_called_once()
        with closing(self._connect()) as conn:
            capture = conn.execute(
                """
                SELECT capture_complete,data_quality,reason
                FROM fugle_intraday_capture_runs
                WHERE code=? AND trade_date=? AND endpoint='trades'
                """,
                (CODE, TRADE_DATE),
            ).fetchone()
        self.assertEqual(capture["capture_complete"], 1)
        self.assertEqual(capture["data_quality"], "SESSION_COMPLETE")
        self.assertIn("official", capture["reason"])

    def test_next_day_illiquid_terminal_pagination_can_reconcile_as_scoped(self) -> None:
        next_day = datetime(2026, 8, 22, 1, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        with patch.object(service, "now_tpe", return_value=next_day):
            self._capture()
        self._insert_illiquid_post_close_capture(captured_volume_lots=100)
        self._insert_history(
            volume=150_000,
            source="TWSE STOCK_DAY",
            source_quality="OFFICIAL",
        )

        with patch.object(service, "compute_price_volume_score_for_code") as compute:
            result = service.reconcile_price_volume_profile_for_code(CODE, TRADE_DATE)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "scoped_validated")
        self.assertFalse(result["score_available"])
        compute.assert_not_called()
        with closing(self._connect()) as conn:
            capture = conn.execute(
                """
                SELECT capture_complete,data_quality,reason
                FROM fugle_intraday_capture_runs
                WHERE code=? AND trade_date=? AND endpoint='trades'
                """,
                (CODE, TRADE_DATE),
            ).fetchone()
        self.assertEqual(capture["capture_complete"], 1)
        self.assertEqual(capture["data_quality"], "SESSION_COMPLETE")
        self.assertIn("official", capture["reason"])


if __name__ == "__main__":
    unittest.main()
