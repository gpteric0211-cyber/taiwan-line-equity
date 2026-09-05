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

from adapter.taiwan_full_market_history import (  # noqa: E402
    fetch_tpex_full_market_date,
    fetch_twse_full_market_date,
)
from adapter.tpex import normalize_tpex_stock_row  # noqa: E402
from core.market_analytics_schema import ensure_market_analytics_schema  # noqa: E402
from core.full_market_batch_schema import ensure_full_market_batch_schema  # noqa: E402
from repository.full_market_batch_repository import (  # noqa: E402
    evaluate_full_market_batch,
    latest_published_full_market_date,
    record_full_market_batch,
)
from repository.stock_no_trade_repository import (  # noqa: E402
    ensure_stock_no_trade_schema,
    upsert_verified_no_trade_dates,
)
from services.full_market_history_service import (  # noqa: E402
    _derived_no_trade_rows,
    refresh_full_market_history_date,
)


class FullMarketHistoryAdapterTest(unittest.TestCase):
    def test_tpex_company_master_keeps_official_listing_date(self) -> None:
        row = normalize_tpex_stock_row({
            "SecuritiesCompanyCode": "1240",
            "CompanyName": "茂生農經股份有限公司",
            "DateOfListing": "20180808",
        })
        self.assertEqual(row["listing_date"], "2018-08-08")

    def test_twse_parser_keeps_stock_and_records_official_no_trade(self) -> None:
        payload = {
            "stat": "OK",
            "date": "20260821",
            "tables": [{
                "fields": ["證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
                "data": [
                    ["2330", "台積電", "1,000", "1,230,000", "1,220", "1,240", "1,215", "1,230"],
                    ["2454", "聯發科", "0", "0", "--", "--", "--", "--"],
                    ["0050", "ETF", "100", "20,000", "200", "201", "199", "200"],
                ],
            }],
        }
        with patch("adapter.taiwan_full_market_history.request_json", return_value=payload):
            result = fetch_twse_full_market_date("2026-08-21")

        self.assertTrue(result["ok"])
        # The exchange adapter is security-type neutral; the service later
        # intersects these rows with stock_master to exclude ETFs/warrants.
        self.assertEqual([row["code"] for row in result["rows"]], ["2330", "0050"])
        self.assertEqual(result["rows"][0]["volume"], 1000.0)
        self.assertEqual([row["code"] for row in result["verified_no_trade_dates"]], ["2454"])

    def test_tpex_parser_rejects_a_response_for_the_wrong_date(self) -> None:
        with patch(
            "adapter.taiwan_full_market_history.request_json",
            return_value={"stat": "ok", "date": "2026/08/20", "tables": []},
        ):
            result = fetch_tpex_full_market_date("2026-08-21")

        self.assertFalse(result["ok"])
        self.assertIn("date/status mismatch", result["error"])


class FullMarketHistoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "full-market.db"
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE history_price(
                    date TEXT,code TEXT,open REAL,high REAL,low REAL,close REAL,
                    volume REAL,amount REAL,volume_unit TEXT,source TEXT,
                    updated_at REAL,source_quality TEXT,fetched_at REAL,market TEXT,
                    PRIMARY KEY(date,code)
                );
                """
            )
            ensure_market_analytics_schema(conn)
            ensure_stock_no_trade_schema(conn)
            ensure_full_market_batch_schema(conn)
            conn.executemany(
                """
                INSERT INTO stock_master(
                    code,name,market,exchange,security_type,is_active,source,
                    source_status,first_seen_date,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    ("2330", "台積電", "listed", "TWSE", "stock", 1, "TEST", "ok", "1994-09-05", "now"),
                    ("3491", "昇達科", "otc", "TPEX", "stock", 1, "TEST", "ok", "2005-12-09", "now"),
                ],
            )
            conn.commit()

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            return conn

        patcher = patch("services.full_market_history_service.db", side_effect=connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _result(market: str, code: str, source: str) -> dict:
        return {
            "ok": True,
            "market": market,
            "source": source,
            "data_date": "2026-08-21",
            "rows": [{
                "date": "2026-08-21",
                "code": code,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000.0,
                "amount": 101000.0,
                "volume_unit": "shares",
                "source": source,
                "source_quality": "official",
                "market": market,
            }],
            "verified_no_trade_dates": [],
            "error": None,
        }

    def test_exact_date_refresh_writes_both_markets_with_full_coverage(self) -> None:
        result = refresh_full_market_history_date(
            "2026-08-21",
            twse_fetcher=lambda _: self._result("listed", "2330", "TWSE MI_INDEX"),
            tpex_fetcher=lambda _: self._result("otc", "3491", "TPEX DAILY_QUOTES"),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_written"], 2)
        self.assertEqual(result["batch_status"], "complete")
        self.assertEqual(result["batch"]["unclassified_count"], 0)
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute(
                "SELECT code,source FROM history_price ORDER BY code"
            ).fetchall()
            publication = conn.execute(
                "SELECT trade_date FROM full_market_batch_publications"
            ).fetchone()
        self.assertEqual(rows, [("2330", "TWSE MI_INDEX"), ("3491", "TPEX DAILY_QUOTES")])
        self.assertEqual(publication[0], "2026-08-21")

    def test_exact_date_refresh_is_atomic_when_one_market_is_not_qualified(self) -> None:
        delayed_tpex = self._result("otc", "3491", "TPEX DAILY_QUOTES")
        delayed_tpex["data_date"] = "2026-08-20"

        result = refresh_full_market_history_date(
            "2026-08-21",
            twse_fetcher=lambda _: self._result("listed", "2330", "TWSE MI_INDEX"),
            tpex_fetcher=lambda _: delayed_tpex,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["rows_written"], 0)
        self.assertEqual(result["no_trade_rows_written"], 0)
        with closing(sqlite3.connect(self.path)) as conn:
            row_count = conn.execute("SELECT COUNT(*) FROM history_price").fetchone()[0]
        self.assertEqual(row_count, 0)

    def test_rows_roll_back_when_batch_marker_fails(self) -> None:
        with patch(
            "services.full_market_history_service.record_full_market_batch",
            side_effect=RuntimeError("marker write failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "marker write failed"):
                refresh_full_market_history_date(
                    "2026-08-21",
                    twse_fetcher=lambda _: self._result("listed", "2330", "TWSE MI_INDEX"),
                    tpex_fetcher=lambda _: self._result("otc", "3491", "TPEX DAILY_QUOTES"),
                )

        with closing(sqlite3.connect(self.path)) as conn:
            history_count = conn.execute("SELECT COUNT(*) FROM history_price").fetchone()[0]
            run_count = conn.execute("SELECT COUNT(*) FROM full_market_batch_runs").fetchone()[0]
        self.assertEqual(history_count, 0)
        self.assertEqual(run_count, 0)

    def test_95_percent_is_stored_as_partial_but_not_published(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            extra = [
                (
                    str(1100 + index),
                    f"測試{index}",
                    "listed",
                    "TWSE",
                    "stock",
                    1,
                    "TEST",
                    "ok",
                    "2000-01-01",
                    "now",
                )
                for index in range(1, 21)
            ]
            conn.executemany(
                """
                INSERT INTO stock_master(
                    code,name,market,exchange,security_type,is_active,source,
                    source_status,first_seen_date,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                extra,
            )
            conn.commit()

        listed_rows = [self._result("listed", "2330", "TWSE MI_INDEX")["rows"][0]]
        listed_rows.extend(
            self._result("listed", str(1100 + index), "TWSE MI_INDEX")["rows"][0]
            for index in range(1, 20)
        )
        listed = {
            **self._result("listed", "2330", "TWSE MI_INDEX"),
            "rows": listed_rows,
        }
        result = refresh_full_market_history_date(
            "2026-08-21",
            twse_fetcher=lambda _: listed,
            tpex_fetcher=lambda _: self._result("otc", "3491", "TPEX DAILY_QUOTES"),
        )

        self.assertTrue(result["storage_allowed"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["rows_written"], 21)
        self.assertEqual(result["batch_status"], "partial")
        self.assertEqual(result["batch"]["unclassified_count"], 1)
        with closing(sqlite3.connect(self.path)) as conn:
            publications = conn.execute(
                "SELECT COUNT(*) FROM full_market_batch_publications"
            ).fetchone()[0]
        self.assertEqual(publications, 0)

    def test_unclassified_progresses_to_zero_before_publication(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                INSERT INTO stock_master(
                    code,name,market,exchange,security_type,is_active,source,
                    source_status,first_seen_date,last_seen_date,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "9999", "已失效測試股", "listed", "TWSE", "stock", 0,
                    "TEST", "not_in_latest_official_list", "2000-01-01", None, "now",
                ),
            )
            conn.execute(
                """
                INSERT INTO history_price(
                    date,code,open,high,low,close,volume,amount,volume_unit,
                    source,updated_at,source_quality,fetched_at,market
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-08-21", "2330", 100, 102, 99, 101, 1000, 101000,
                    "shares", "TWSE MI_INDEX", 1, "official", 1, "listed",
                ),
            )
            first = evaluate_full_market_batch(conn, "2026-08-21")
            record_full_market_batch(
                conn,
                first,
                storage_status="backfill_verified",
            )
            conn.commit()
            self.assertEqual(first["unclassified_count"], 1)
            self.assertEqual(first["expected_active_asof"], 2)
            self.assertEqual(first["batch_status"], "partial")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM full_market_batch_publications").fetchone()[0],
                0,
            )

            upsert_verified_no_trade_dates(
                conn,
                [{
                    "trade_date": "2026-08-21",
                    "code": "3491",
                    "market": "otc",
                    "reason": "official_daily_report_no_trade",
                    "source": "TPEX DAILY_QUOTES",
                    "source_url": "https://www.tpex.org.tw/example",
                    "source_quality": "official",
                    "evidence": {"reported_volume": 0},
                }],
            )
            second = evaluate_full_market_batch(conn, "2026-08-21")
            record_full_market_batch(
                conn,
                second,
                storage_status="backfill_verified",
            )
            conn.commit()

            self.assertEqual(second["unclassified_count"], 0)
            self.assertEqual(second["batch_status"], "complete")
            run_history = conn.execute(
                """
                SELECT batch_status,unclassified_count
                FROM full_market_batch_runs
                ORDER BY finalized_at, rowid
                """
            ).fetchall()
            publication = conn.execute(
                "SELECT trade_date FROM full_market_batch_publications"
            ).fetchone()
            third = evaluate_full_market_batch(conn, "2026-08-22")
            record_full_market_batch(
                conn,
                third,
                storage_status="backfill_verified",
            )
            conn.commit()
            latest_published = latest_published_full_market_date(conn)
        self.assertEqual(
            [(row[0], row[1]) for row in run_history],
            [("partial", 1), ("complete", 0)],
        )
        self.assertEqual(publication[0], "2026-08-21")
        self.assertEqual(third["batch_status"], "partial")
        self.assertEqual(latest_published, "2026-08-21")

    def test_overlapping_classifications_cannot_be_complete(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                INSERT INTO history_price(
                    date,code,open,high,low,close,volume,amount,volume_unit,
                    source,updated_at,source_quality,fetched_at,market
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-08-21", "2330", 100, 102, 99, 101, 1000, 101000,
                    "shares", "TWSE MI_INDEX", 1, "official", 1, "listed",
                ),
            )
            conn.execute(
                """
                INSERT INTO stock_no_trade_dates(
                    trade_date,code,market,reason,source,source_url,
                    source_quality,evidence_json,verified_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-08-21", "2330", "listed", "conflict",
                    "TWSE MI_INDEX", "https://www.twse.com.tw/example",
                    "official", '{"reported_volume":0}', 1,
                ),
            )
            conn.execute(
                """
                INSERT INTO stock_no_trade_dates(
                    trade_date,code,market,reason,source,source_url,
                    source_quality,evidence_json,verified_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-08-21", "3491", "otc", "no_trade",
                    "TPEX DAILY_QUOTES", "https://www.tpex.org.tw/example",
                    "official", '{"reported_volume":0}', 1,
                ),
            )
            evaluation = evaluate_full_market_batch(conn, "2026-08-21")

        self.assertEqual(evaluation["unclassified_count"], 0)
        self.assertEqual(evaluation["overlap_count"], 1)
        self.assertEqual(evaluation["batch_status"], "partial")

    def test_official_named_source_with_fallback_quality_is_not_classified(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                INSERT INTO history_price(
                    date,code,open,high,low,close,volume,amount,volume_unit,
                    source,updated_at,source_quality,fetched_at,market
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-08-21", "2330", 100, 102, 99, 101, 1000, 101000,
                    "shares", "TWSE MI_INDEX", 1, "fallback", 1, "listed",
                ),
            )
            evaluation = evaluate_full_market_batch(conn, "2026-08-21")

        self.assertEqual(evaluation["classified_ohlcv_count"], 0)
        self.assertEqual(evaluation["unclassified_count"], 2)
        self.assertEqual(evaluation["batch_status"], "partial")

    def test_official_report_absence_plus_empty_licensed_feed_is_zero_trade_evidence(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE fugle_intraday_capture_runs(
                    code TEXT,trade_date TEXT,endpoint TEXT,data_quality TEXT,
                    capture_complete INTEGER,provider_row_count INTEGER,
                    normalized_row_count INTEGER,stored_row_count INTEGER,reason TEXT
                );
                INSERT INTO fugle_intraday_capture_runs VALUES(
                    '8105','2026-08-26','trades','UNAVAILABLE',0,0,0,0,
                    'http_status=200; EMPTY_TRADES; request failed'
                );
                """
            )
            rows = _derived_no_trade_rows(
                conn,
                market="listed",
                trade_date="2026-08-26",
                missing_codes={"8105"},
                official_source="TWSE MI_INDEX",
                raw_coverage=0.9972,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "8105")
        self.assertEqual(rows[0]["evidence"]["reported_volume"], 0)
        self.assertEqual(rows[0]["evidence"]["corroboration"], "licensed_trade_feed_empty")

    def test_generic_capture_failure_is_not_treated_as_zero_trade(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE fugle_intraday_capture_runs(
                    code TEXT,trade_date TEXT,endpoint TEXT,data_quality TEXT,
                    capture_complete INTEGER,provider_row_count INTEGER,
                    normalized_row_count INTEGER,stored_row_count INTEGER,reason TEXT
                );
                INSERT INTO fugle_intraday_capture_runs VALUES(
                    '8105','2026-08-26','trades','UNAVAILABLE',0,0,0,0,
                    'provider retry policy exhausted'
                );
                """
            )
            rows = _derived_no_trade_rows(
                conn,
                market="listed",
                trade_date="2026-08-26",
                missing_codes={"8105"},
                official_source="TWSE MI_INDEX",
                raw_coverage=0.9972,
            )

        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
