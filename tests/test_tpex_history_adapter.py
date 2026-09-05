from __future__ import annotations

import sqlite3
import sys
import unittest
import tempfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.tpex_history import (  # noqa: E402
    TPEX_TRADING_STOCK_URL,
    fetch_tpex_stock_month_rows,
)
from core.market_foundation_schema import upsert_daily_ohlcv  # noqa: E402
from core.data_quality import derive_official_monthly_no_bar_evidence  # noqa: E402
from repository.history_repository import history_date_coverage  # noqa: E402
from repository.stock_no_trade_repository import (  # noqa: E402
    ensure_stock_no_trade_schema,
    upsert_verified_no_trade_dates,
)
from services import tpex_history_service  # noqa: E402
from services import data_repair_service  # noqa: E402


def _payload() -> dict:
    return {
        "stat": "ok",
        "tables": [{
            "fields": ["日 期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"],
            "data": [
                ["115/07/01", "1,584", "2,049,904", "1,360.00", "1,360.00", "1,260.00", "1,260.00", "-75.00", "10,378"],
                ["115/07/15", "0", "0", "--", "--", "--", "--", "0.00", "0"],
            ],
        }],
    }


def _create_history_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE history_price(
            date TEXT, code TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL, amount REAL, volume_unit TEXT,
            source TEXT, updated_at REAL, source_quality TEXT,
            fetched_at REAL, market TEXT, PRIMARY KEY(date,code)
        )
        """
    )


class TpexHistoryAdapterTests(unittest.TestCase):
    def test_complete_official_month_can_classify_absent_market_dates(self) -> None:
        result = derive_official_monthly_no_bar_evidence(
            code="3491",
            market="otc",
            month="2026-07",
            official_rows=[{"date": "2026-07-01", "code": "3491"}],
            explicit_no_bar_rows=[{"trade_date": "2026-07-03", "code": "3491"}],
            market_reference_dates=["2026-07-01", "2026-07-02", "2026-07-03"],
            first_seen_date="2009-01-01",
            source="TPEX TRADING_STOCK",
            source_url=TPEX_TRADING_STOCK_URL,
        )

        self.assertEqual([row["trade_date"] for row in result], ["2026-07-02"])
        self.assertEqual(result[0]["evidence"]["reported_volume"], 0)

    def test_parses_official_bar_and_keeps_zero_price_row_as_no_trade_evidence(self) -> None:
        calls: list[dict] = []

        def fake_post(url: str, **kwargs):
            calls.append({"url": url, **kwargs})
            return _payload()

        result = fetch_tpex_stock_month_rows(
            "3491",
            "2026-07-21",
            http_post=fake_post,
            retries=1,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["verified_no_trade_count"], 1)
        self.assertEqual(result["rows"][0]["date"], "2026-07-01")
        self.assertEqual(result["rows"][0]["volume"], 1_584_000)
        self.assertEqual(result["rows"][0]["amount"], 2_049_904_000)
        self.assertEqual(result["rows"][0]["source"], "TPEX TRADING_STOCK")
        no_trade = result["verified_no_trade_dates"][0]
        self.assertEqual(no_trade["trade_date"], "2026-07-15")
        self.assertEqual(no_trade["source_url"], TPEX_TRADING_STOCK_URL)
        self.assertEqual(calls[0]["data"]["date"], "2026/07/01")

    def test_fails_closed_on_field_schema_mismatch(self) -> None:
        result = fetch_tpex_stock_month_rows(
            "3491",
            "2026-07-01",
            http_post=lambda *args, **kwargs: {"stat": "ok", "tables": [{"fields": ["日期"], "data": []}]},
            retries=1,
        )
        self.assertFalse(result["ok"])
        self.assertIn("schema mismatch", result["error"])

    def test_empty_complete_official_month_is_not_a_source_failure(self) -> None:
        result = fetch_tpex_stock_month_rows(
            "8291",
            "2026-01-01",
            http_post=lambda *args, **kwargs: {
                "stat": "ok",
                "tables": [{
                    "fields": ["日 期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤"],
                    "data": [],
                }],
            },
            retries=1,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["empty_official_report"])
        self.assertEqual(result["rows"], [])
        self.assertIsNone(result["error"])

    def test_zero_regular_lots_with_residual_activity_is_not_a_missing_row(self) -> None:
        payload = {
            "stat": "ok",
            "tables": [{
                "fields": ["日 期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"],
                "data": [["115/06/24", "0", "1", "--", "--", "--", "--", "0.00", "1"]],
            }],
        }
        result = fetch_tpex_stock_month_rows(
            "3226",
            "2026-06-01",
            http_post=lambda *args, **kwargs: payload,
            retries=1,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 0)
        self.assertEqual(result["verified_no_trade_count"], 1)
        evidence = result["verified_no_trade_dates"][0]
        self.assertEqual(
            evidence["reason"],
            "official_no_regular_lot_ohlcv_with_residual_activity",
        )
        self.assertEqual(evidence["evidence"]["reported_regular_lot_volume"], 0)
        self.assertEqual(evidence["evidence"]["reported_transaction_count"], 1)
        self.assertFalse(result["invalid_rows"])

    def test_reported_volume_without_ohlc_is_preserved_as_non_kline_activity(self) -> None:
        payload = {
            "stat": "ok",
            "tables": [{
                "fields": ["日 期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"],
                "data": [["115/06/26", "1", "21", "--", "--", "--", "--", "0.00", "4"]],
            }],
        }
        result = fetch_tpex_stock_month_rows(
            "3226",
            "2026-06-01",
            http_post=lambda *args, **kwargs: payload,
            retries=1,
        )

        self.assertTrue(result["ok"])
        evidence = result["verified_no_trade_dates"][0]
        self.assertEqual(evidence["reason"], "official_no_ohlcv_with_residual_activity")
        self.assertEqual(evidence["evidence"]["reported_regular_lot_volume"], 1)
        self.assertEqual(evidence["evidence"]["reported_amount_thousands"], 21)
        self.assertEqual(evidence["evidence"]["reported_transaction_count"], 4)
        self.assertFalse(result["invalid_rows"])

    def test_rejects_missing_volume_invalid_date_amount_and_duplicate_rows(self) -> None:
        fields = ["日 期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤"]
        cases = {
            "missing_volume_is_not_no_trade": [["115/07/15", "--", "0", "--", "--", "--", "--"]],
            "impossible_date": [["115/07/32", "1,000", "1,000,000", "10", "11", "9", "10"]],
            "nonpositive_amount": [["115/07/01", "1,000", "0", "10", "11", "9", "10"]],
            "amount_price_inconsistent": [["115/07/01", "1,000", "99,000,000", "10", "11", "9", "10"]],
            "duplicate_date": [
                ["115/07/01", "1,000", "10,000", "10", "11", "9", "10"],
                ["115/07/01", "1,100", "11,000", "10", "11", "9", "10"],
            ],
        }
        for label, rows in cases.items():
            with self.subTest(label=label):
                result = fetch_tpex_stock_month_rows(
                    "3491",
                    "2026-07-01",
                    http_post=lambda *args, _rows=rows, **kwargs: {
                        "stat": "ok",
                        "tables": [{"fields": fields, "data": _rows}],
                    },
                    retries=1,
                )
                self.assertFalse(result["ok"])
                self.assertTrue(result["invalid_rows"])
                if label == "missing_volume_is_not_no_trade":
                    self.assertEqual(result["verified_no_trade_count"], 0)

        blank = fetch_tpex_stock_month_rows(
            "",
            "2026-07-01",
            http_post=lambda *args, **kwargs: _payload(),
            retries=1,
        )
        self.assertFalse(blank["ok"])
        self.assertEqual(blank["error"], "invalid code")

    def test_verified_stock_no_trade_date_extends_strict_coverage_window(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE history_price(
                    date TEXT, code TEXT, open REAL, high REAL, low REAL,
                    close REAL, volume REAL, amount REAL, volume_unit TEXT,
                    source TEXT, updated_at REAL, source_quality TEXT,
                    fetched_at REAL, market TEXT, PRIMARY KEY(date,code)
                )
                """
            )
            ensure_stock_no_trade_schema(conn)
            for trade_date in ("2026-08-21", "2026-08-20", "2026-08-19", "2026-08-18"):
                conn.execute(
                    "INSERT INTO history_price(date,code,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?)",
                    (trade_date, "2330", 10, 11, 9, 10, 1000, "TWSE STOCK_DAY"),
                )
            for trade_date in ("2026-08-21", "2026-08-19", "2026-08-18"):
                conn.execute(
                    "INSERT INTO history_price(date,code,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?)",
                    (trade_date, "3491", 10, 11, 9, 10, 1000, "TPEX TRADING_STOCK"),
                )
            missing = history_date_coverage(
                conn,
                "3491",
                required_days=3,
                reference_dates=["2026-08-21", "2026-08-20", "2026-08-19"],
            )
            self.assertFalse(missing["ready"])

            written = upsert_verified_no_trade_dates(conn, [{
                "trade_date": "2026-08-20",
                "code": "3491",
                "market": "otc",
                "reason": "official_zero_volume_no_price_row",
                "source": "TPEX TRADING_STOCK",
                "source_url": TPEX_TRADING_STOCK_URL,
                "source_quality": "official",
                "evidence": {"row": ["115/08/20", "0", "0", "--"]},
            }])
            self.assertEqual(written, 1)
            ready = history_date_coverage(
                conn,
                "3491",
                required_days=3,
                reference_dates=["2026-08-21", "2026-08-20", "2026-08-19"],
            )
            self.assertTrue(ready["ready"])
            self.assertEqual(ready["verified_no_trade_dates"], ["2026-08-20"])
            self.assertEqual(ready["reason"], "ok_with_verified_no_trade_dates")
            self.assertEqual(ready["earliest_reference_date"], "2026-08-18")
        finally:
            conn.close()

    def test_sparse_stock_coverage_expands_past_many_verified_no_trade_dates(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            _create_history_schema(conn)
            ensure_stock_no_trade_schema(conn)
            market_dates = [
                (date(2026, 8, 26) - timedelta(days=offset)).isoformat()
                for offset in range(23)
            ]
            for trade_date in market_dates:
                conn.execute(
                    "INSERT INTO history_price(date,code,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?)",
                    (trade_date, "2330", 10, 11, 9, 10, 1000, "TWSE MI_INDEX"),
                )
            for trade_date in market_dates[-3:]:
                conn.execute(
                    "INSERT INTO history_price(date,code,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?)",
                    (trade_date, "3491", 10, 11, 9, 10, 1000, "TPEX TRADING_STOCK"),
                )
            written = upsert_verified_no_trade_dates(conn, [
                {
                    "trade_date": trade_date,
                    "code": "3491",
                    "market": "otc",
                    "reason": "official_zero_volume_no_price_row",
                    "source": "TPEX TRADING_STOCK",
                    "source_url": TPEX_TRADING_STOCK_URL,
                    "source_quality": "official",
                    "evidence": {"reported_volume": 0},
                }
                for trade_date in market_dates[:20]
            ])
            self.assertEqual(written, 20)

            result = history_date_coverage(
                conn,
                "3491",
                required_days=3,
                reference_dates=market_dates[:3],
            )

            self.assertTrue(result["ready"])
            self.assertEqual(result["observed_days"], 3)
            self.assertEqual(result["coverage_ratio"], 1.0)
            self.assertGreaterEqual(len(result["verified_no_trade_dates"]), 20)
        finally:
            conn.close()

    def test_official_no_trade_is_mutually_exclusive_with_daily_bars(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            _create_history_schema(conn)
            conn.execute(
                """
                INSERT INTO history_price(
                    date,code,open,high,low,close,volume,amount,volume_unit,
                    source,updated_at,source_quality,fetched_at,market
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-07-15", "3491", 10, 11, 9, 10, 1000, 10000,
                    "shares", "Yahoo Finance", 1, "FALLBACK", 1, "otc",
                ),
            )
            evidence = {
                "trade_date": "2026-07-15",
                "code": "3491",
                "market": "otc",
                "reason": "official_zero_volume_no_price_row",
                "source": "TPEX TRADING_STOCK",
                "source_url": TPEX_TRADING_STOCK_URL,
                "source_quality": "official",
                "evidence": {"row": ["115/07/15", "0", "0", "--"]},
            }
            self.assertEqual(upsert_verified_no_trade_dates(conn, [evidence]), 1)
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM history_price WHERE code='3491' AND date='2026-07-15'"
            ).fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM invalid_history_price_quarantine WHERE code='3491' AND date='2026-07-15'"
            ).fetchone())

            fallback = {
                "date": "2026-07-15", "code": "3491", "open": 10,
                "high": 11, "low": 9, "close": 10, "volume": 1000,
                "amount": 10000, "volume_unit": "shares",
                "source": "FinMind", "source_quality": "FALLBACK",
                "updated_at": 2, "fetched_at": 2, "market": "otc",
            }
            self.assertFalse(upsert_daily_ohlcv(conn, fallback))

            # A later correction from the exact same official source may
            # replace stale no-trade evidence, but a fallback never may.
            conn.execute(
                "UPDATE stock_no_trade_dates SET verified_at=1 WHERE code='3491' AND trade_date='2026-07-15'"
            )
            corrected = {
                **fallback,
                "source": "TPEX TRADING_STOCK",
                "source_quality": "official",
                "updated_at": 3,
                "fetched_at": 3,
            }
            self.assertTrue(upsert_daily_ohlcv(conn, corrected))
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM stock_no_trade_dates WHERE code='3491' AND trade_date='2026-07-15'"
            ).fetchone())
            self.assertEqual(conn.execute(
                "SELECT source FROM history_price WHERE code='3491' AND date='2026-07-15'"
            ).fetchone()[0], "TPEX TRADING_STOCK")
        finally:
            conn.close()

    def test_invalid_official_zero_price_row_does_not_block_no_bar_evidence(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            _create_history_schema(conn)
            conn.execute(
                """
                INSERT INTO history_price(
                    date,code,open,high,low,close,volume,amount,volume_unit,
                    source,updated_at,source_quality,fetched_at,market
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-06-26", "3491", 0, 0, 0, 0, 1069, 14864,
                    "shares", "TPEX TRADING_STOCK", 1, "official", 1, "otc",
                ),
            )
            evidence = {
                "trade_date": "2026-06-26",
                "code": "3491",
                "market": "otc",
                "reason": "official_complete_monthly_report_no_daily_bar",
                "source": "TPEX TRADING_STOCK",
                "source_url": TPEX_TRADING_STOCK_URL,
                "source_quality": "official",
                "evidence": {"reported_volume": 0},
            }

            self.assertEqual(upsert_verified_no_trade_dates(conn, [evidence]), 1)
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM history_price WHERE code='3491' AND date='2026-06-26'"
            ).fetchone())
            row = conn.execute(
                "SELECT reason,evidence_json FROM stock_no_trade_dates WHERE code='3491'"
            ).fetchone()
            self.assertEqual(row["reason"], "official_no_ohlcv_with_residual_activity")
            self.assertIn('"reported_volume_shares":1069.0', row["evidence_json"])
        finally:
            conn.close()

    def test_coverage_fails_closed_if_history_and_no_trade_evidence_intersect(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            _create_history_schema(conn)
            ensure_stock_no_trade_schema(conn)
            for code in ("2330", "3491"):
                for trade_date in ("2026-07-15", "2026-07-14"):
                    conn.execute(
                        "INSERT INTO history_price(date,code,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?)",
                        (trade_date, code, 10, 11, 9, 10, 1000, "TPEX TRADING_STOCK" if code == "3491" else "TWSE STOCK_DAY"),
                    )
            conn.execute(
                """
                INSERT INTO stock_no_trade_dates(
                    trade_date,code,market,reason,source,source_url,
                    source_quality,evidence_json,verified_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "2026-07-15", "3491", "otc", "legacy_conflict",
                    "TPEX TRADING_STOCK", TPEX_TRADING_STOCK_URL,
                    "official", "{}", 1,
                ),
            )
            result = history_date_coverage(
                conn,
                "3491",
                required_days=1,
                reference_dates=["2026-07-15"],
            )
            self.assertFalse(result["ready"])
            self.assertEqual(result["reason"], "history_no_trade_conflict")
            self.assertEqual(result["no_trade_history_conflicts"], ["2026-07-15"])
        finally:
            conn.close()

    def test_explicit_service_refresh_persists_official_rows_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tpex.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE history_price(
                        date TEXT, code TEXT, open REAL, high REAL, low REAL,
                        close REAL, volume REAL, amount REAL, volume_unit TEXT,
                        source TEXT, updated_at REAL, source_quality TEXT,
                        fetched_at REAL, market TEXT, PRIMARY KEY(date,code)
                    )
                    """
                )
                conn.commit()

            def open_db() -> sqlite3.Connection:
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            def fake_fetcher(code: str, month: str) -> dict:
                return {
                    "ok": True,
                    "rows": [{
                        "date": "2026-08-21",
                        "code": code,
                        "open": 1285.0,
                        "high": 1345.0,
                        "low": 1250.0,
                        "close": 1280.0,
                        "volume": 1_546_000,
                        "amount": 1_997_000_000,
                        "volume_unit": "shares",
                        "source": "TPEX TRADING_STOCK",
                        "source_quality": "official",
                        "market": "otc",
                    }],
                    "verified_no_trade_dates": [{
                        "trade_date": "2026-08-20",
                        "code": code,
                        "market": "otc",
                        "reason": "official_zero_volume_no_price_row",
                        "source": "TPEX TRADING_STOCK",
                        "source_url": TPEX_TRADING_STOCK_URL,
                        "source_quality": "official",
                        "evidence": {"month": month},
                    }],
                }

            statuses: list[tuple] = []
            with (
                patch.object(tpex_history_service, "db", side_effect=open_db),
                patch.object(tpex_history_service, "set_status", side_effect=lambda *args: statuses.append(args)),
                patch.object(tpex_history_service, "recent_market_date_for_eod", return_value="2026-08-21"),
            ):
                result = tpex_history_service.refresh_tpex_history_codes(
                    ["3491"],
                    months=1,
                    fetcher=fake_fetcher,
                    sleep_seconds=0,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["official_rows_written"], 1)
            self.assertEqual(result["official_no_trade_rows_written"], 1)
            self.assertEqual(statuses[-1][1], "fresh")
            with closing(sqlite3.connect(db_path)) as check:
                saved = check.execute(
                    "SELECT close,volume,source FROM history_price WHERE code='3491'"
                ).fetchone()
                exception = check.execute(
                    "SELECT reason,source FROM stock_no_trade_dates WHERE code='3491'"
                ).fetchone()
            self.assertEqual(saved, (1280.0, 1_546_000.0, "TPEX TRADING_STOCK"))
            self.assertEqual(exception, ("official_zero_volume_no_price_row", "TPEX TRADING_STOCK"))

    def test_service_does_not_mark_delayed_official_month_as_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "delayed.db"
            with closing(sqlite3.connect(db_path)) as conn:
                _create_history_schema(conn)
                conn.commit()

            def open_db() -> sqlite3.Connection:
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            def fake_fetcher(code: str, month: str) -> dict:
                return {
                    "ok": True,
                    "rows": [{
                        "date": "2026-08-01", "code": code, "open": 10,
                        "high": 11, "low": 9, "close": 10, "volume": 1000,
                        "amount": 10000, "volume_unit": "shares",
                        "source": "TPEX TRADING_STOCK",
                        "source_quality": "official", "market": "otc",
                    }],
                    "verified_no_trade_dates": [],
                }

            with (
                patch.object(tpex_history_service, "db", side_effect=open_db),
                patch.object(tpex_history_service, "set_status"),
                patch.object(tpex_history_service, "recent_market_date_for_eod", return_value="2026-08-21"),
            ):
                result = tpex_history_service.refresh_tpex_history_codes(
                    ["3491"], months=1, fetcher=fake_fetcher, sleep_seconds=0,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["source_delayed_codes"], ["3491"])
            self.assertEqual(result["latest_official_dates"]["3491"], "2026-08-01")

    def test_unknown_market_is_probed_officially_and_never_guessed_into_yahoo(self) -> None:
        twse_calls: list[list[dict]] = []
        tpex_calls: list[list[str]] = []
        ready_gaps = {
            "history_ready": True,
            "price_ready": True,
            "valuation_ready": True,
        }
        with (
            patch.object(data_repair_service, "resolve_market_profile", return_value={"market_type": "unknown"}),
            patch.object(data_repair_service, "fetch_twse_eod_all"),
            patch.object(data_repair_service, "refresh_twse_stock_day_codes", side_effect=lambda items, **kwargs: twse_calls.append(items)),
            patch.object(data_repair_service, "refresh_tpex_history_codes", side_effect=lambda codes, **kwargs: tpex_calls.append(codes) or {"ok": False}),
            patch.object(data_repair_service, "refresh_tpex_valuation_codes", return_value={"ok": False}),
            patch.object(data_repair_service, "fetch_twse_valuation_all"),
            patch.object(data_repair_service, "read_components", return_value=[]),
            patch.object(data_repair_service, "list_watchlist_code_name_items", return_value=[]),
            patch.object(data_repair_service, "local_data_gaps", return_value=ready_gaps),
            patch.object(data_repair_service, "upsert_yfinance_tw_history") as yahoo_history,
            patch.object(data_repair_service, "upsert_yfinance_tw_valuation") as yahoo_valuation,
            patch.object(data_repair_service, "_update_finmind_codes"),
            patch.object(data_repair_service, "_update_corporate_actions"),
            patch.object(data_repair_service, "_prune_compute_caches"),
            patch.object(data_repair_service, "_warm_row_cache"),
            patch.object(data_repair_service, "_set_status"),
        ):
            data_repair_service.ensure_complete_data_for_codes(["3491"], days=120)
        self.assertEqual([item["code"] for item in twse_calls[0]], ["3491"])
        self.assertEqual(tpex_calls[0], ["3491"])
        yahoo_history.assert_not_called()
        yahoo_valuation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
