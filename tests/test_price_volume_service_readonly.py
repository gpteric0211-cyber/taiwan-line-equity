from __future__ import annotations

import re
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

from services import price_volume_service as service  # noqa: E402


MUTATING_SQL = re.compile(
    r"^\s*(?:INSERT|REPLACE|UPDATE|DELETE|CREATE|ALTER|DROP|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)


class PersistedPriceVolumeSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "price_volume.db"
        self.traced_sql: list[str] = []
        with closing(self._fixture_connection()) as conn:
            conn.executescript(
                """
                CREATE TABLE history_price (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    close REAL,
                    source TEXT,
                    PRIMARY KEY(date, code)
                );
                CREATE TABLE full_market_batch_publications (
                    trade_date TEXT PRIMARY KEY
                );
                CREATE TABLE price_volume_distribution (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    price REAL NOT NULL,
                    volume_lots INTEGER,
                    volume_shares INTEGER,
                    total_volume_lots INTEGER,
                    snapshot_time TEXT,
                    source TEXT,
                    source_quality TEXT,
                    data_quality TEXT,
                    UNIQUE(stock_id, trade_date, price)
                );
                CREATE TABLE price_volume_profile_daily (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    source_name TEXT,
                    source_hash TEXT,
                    quality TEXT,
                    PRIMARY KEY(date, code)
                );
                CREATE TABLE price_volume_score_daily (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    close REAL,
                    source_name TEXT,
                    source_hash TEXT,
                    coverage_days INTEGER,
                    required_days INTEGER,
                    quality TEXT,
                    quality_reason TEXT,
                    status TEXT,
                    weighted_cost REAL,
                    cost_state TEXT,
                    total_score INTEGER,
                    grade TEXT,
                    support_json TEXT,
                    resistance_json TEXT,
                    PRIMARY KEY(date, code)
                );
                """
            )
            conn.execute(
                "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
                ("2026-08-21",),
            )
            conn.commit()

        self.db_patcher = patch.object(service, "db", side_effect=self._service_connection)
        self.db_patcher.start()
        self.addCleanup(self.db_patcher.stop)

    def _fixture_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _service_connection(self) -> sqlite3.Connection:
        conn = self._fixture_connection()
        conn.set_trace_callback(self.traced_sql.append)
        return conn

    def _seed_history(self, date: str = "2026-08-21", close: float = 100.0) -> None:
        with closing(self._fixture_connection()) as conn:
            conn.execute(
                "INSERT INTO history_price(date,code,close,source) VALUES(?,?,?,?)",
                (date, "2454", close, "TWSE STOCK_DAY"),
            )
            conn.commit()

    def _seed_distribution(
        self,
        date: str = "2026-08-21",
        *,
        sources: tuple[str, str] = ("FUGLE", "FUGLE"),
    ) -> None:
        with closing(self._fixture_connection()) as conn:
            conn.executemany(
                """
                INSERT INTO price_volume_distribution(
                    stock_id,trade_date,price,volume_lots,volume_shares,total_volume_lots,
                    snapshot_time,source,source_quality,data_quality
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    ("2454", date, 99.0, 10, 10000, 30, f"{date} 13:35:00", sources[0], "VALIDATED", "VALIDATED"),
                    ("2454", date, 100.0, 20, 20000, 30, f"{date} 13:35:00", sources[1], "VALIDATED", "VALIDATED"),
                ],
            )
            conn.commit()

    def _seed_score(
        self,
        date: str = "2026-08-21",
        *,
        source_name: str = "Fugle intraday volumes",
        quality: str = "high",
        status: str = "ok",
        coverage_days: int = 24,
        required_days: int = 30,
        close: float = 100.0,
        source_hash: str = "same-day-fugle-hash",
    ) -> None:
        with closing(self._fixture_connection()) as conn:
            conn.execute(
                """
                INSERT INTO price_volume_profile_daily(date,code,source_name,source_hash,quality)
                VALUES(?,?,?,?,?)
                """,
                (date, "2454", source_name, source_hash, quality),
            )
            conn.execute(
                """
                INSERT INTO price_volume_score_daily(
                    date,code,close,source_name,source_hash,coverage_days,required_days,
                    quality,quality_reason,status,weighted_cost,cost_state,total_score,grade,
                    support_json,resistance_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    date,
                    "2454",
                    close,
                    source_name,
                    source_hash,
                    coverage_days,
                    required_days,
                    quality,
                    "fixture",
                    status,
                    99.5,
                    "market_profit",
                    80,
                    "A",
                    '[{"price":99.0,"strength":"strong","volume_share_pct":33.3}]',
                    '[{"price":101.0,"strength":"medium","volume_share_pct":20.0}]',
                ),
            )
            conn.commit()

    def _assert_summary_sql_is_read_only(self) -> None:
        mutations = [statement for statement in self.traced_sql if MUTATING_SQL.match(statement)]
        self.assertEqual(mutations, [], f"summary executed mutating SQL: {mutations}")

    def test_get_summary_reads_persisted_rows_without_computing_or_writing(self) -> None:
        self._seed_history()
        self._seed_distribution()
        self._seed_score()

        with patch.object(
            service,
            "compute_price_volume_score_for_code",
            side_effect=AssertionError("GET summary must not compute or persist a score"),
        ):
            result = service.latest_price_volume_summary("2454")

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["is_true_price_volume"])
        self.assertEqual(result["trade_date"], "2026-08-21")
        self.assertEqual(result["distribution_source"], "FUGLE")
        self.assertEqual(result["grade"], "A")
        self.assertEqual(result["total_score"], 80)
        self._assert_summary_sql_is_read_only()

    def test_summary_ignores_newer_unpublished_partial_rows(self) -> None:
        self._seed_history()
        self._seed_distribution()
        self._seed_score()
        self._seed_history("2026-08-22", 105.0)
        self._seed_distribution("2026-08-22")
        self._seed_score("2026-08-22", close=105.0)

        result = service.latest_price_volume_summary("2454")

        self.assertTrue(result["available"])
        self.assertEqual(result["trade_date"], "2026-08-21")
        self.assertEqual(result["data_date"], "2026-08-21")
        self._assert_summary_sql_is_read_only()

    def test_old_true_distribution_is_not_mixed_with_new_reconstructed_score(self) -> None:
        self._seed_history("2026-08-21")
        self._seed_distribution("2026-07-23")
        self._seed_score(
            "2026-08-21",
            source_name="OHLCV reconstructed volume profile",
            quality="medium_ohlcv_reconstructed",
            status="ok",
            source_hash="reconstructed-hash",
        )

        result = service.latest_price_volume_summary("2454")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["data_date"], "2026-08-21")
        self.assertEqual(result["distribution_date"], "2026-07-23")
        self.assertFalse(result["is_true_price_volume"])
        self.assertEqual(result["profile_rows"], [])
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["total_score"])
        self._assert_summary_sql_is_read_only()

    def test_legacy_ok_distribution_is_not_treated_as_validated(self) -> None:
        self._seed_history()
        self._seed_distribution()
        self._seed_score()
        with closing(self._fixture_connection()) as conn:
            conn.execute(
                "UPDATE price_volume_distribution SET source_quality='OK',data_quality='OK' WHERE stock_id='2454'"
            )
            conn.commit()

        result = service.latest_price_volume_summary("2454")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "source_mismatch")
        self.assertFalse(result.get("distribution_validated", False))
        self._assert_summary_sql_is_read_only()

    def test_same_day_distribution_with_mixed_sources_is_hidden(self) -> None:
        self._seed_history()
        self._seed_distribution(sources=("FUGLE", "YAHOO_TIME_SALES"))
        self._seed_score()

        result = service.latest_price_volume_summary("2454")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "source_mismatch")
        self.assertFalse(result["is_true_price_volume"])
        self.assertEqual(result["profile_rows"], [])
        self.assertIsNone(result["total_score"])
        self._assert_summary_sql_is_read_only()

    def test_same_day_reconstructed_score_does_not_impersonate_true_distribution(self) -> None:
        self._seed_history()
        self._seed_distribution()
        self._seed_score(
            source_name="OHLCV reconstructed volume profile",
            quality="medium_ohlcv_reconstructed",
            status="ok",
            source_hash="reconstructed-hash",
        )

        result = service.latest_price_volume_summary("2454")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "source_mismatch")
        self.assertTrue(result["is_true_price_volume"])
        self.assertGreater(len(result["profile_rows"]), 0)
        self.assertEqual(result["distribution_source"], "FUGLE")
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["total_score"])
        self._assert_summary_sql_is_read_only()

    def test_score_below_eighty_percent_coverage_is_not_exposed(self) -> None:
        self._seed_history()
        self._seed_distribution()
        self._seed_score(coverage_days=23, required_days=30)

        result = service.latest_price_volume_summary("2454")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "accumulating")
        self.assertTrue(result["is_true_price_volume"])
        self.assertGreater(len(result["profile_rows"]), 0)
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["total_score"])
        self._assert_summary_sql_is_read_only()

    def test_score_status_must_be_ok_even_at_required_coverage(self) -> None:
        self._seed_history()
        self._seed_distribution()
        self._seed_score(status="ready", coverage_days=24, required_days=30)

        result = service.latest_price_volume_summary("2454")

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "invalid_status")
        self.assertTrue(result["is_true_price_volume"])
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["total_score"])
        self._assert_summary_sql_is_read_only()


if __name__ == "__main__":
    unittest.main()
