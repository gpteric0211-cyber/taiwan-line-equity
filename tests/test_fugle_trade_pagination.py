from __future__ import annotations

import argparse
import sqlite3
import sys
import unittest
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from scripts import update_fugle_intraday_supplemental as fugle_update  # noqa: E402
from core.fugle_intraday_schema import ensure_fugle_intraday_schema, upsert_fugle_capture_run  # noqa: E402


CODE = "2454"
TRADE_DATE = "2026-08-21"
TPE = ZoneInfo("Asia/Taipei")


def trade(serial: int, *, micros: int | None = None) -> dict:
    return {
        "serial": serial,
        "time": micros or 1_787_000_000_000_000 + serial,
        "price": 100.0 + serial,
        "size": 1,
        "volume": serial,
        "bid": 99.0,
        "ask": 100.0,
    }


def page_payload(rows: list[dict], *, date: str = TRADE_DATE, symbol: str = CODE) -> dict:
    return {
        "date": date,
        "type": "EQUITY",
        "exchange": "TWSE",
        "market": "TSE",
        "symbol": symbol,
        "data": rows,
    }


def successful_page(payload: dict) -> dict:
    return {
        "ok": True,
        "http_status": 200,
        "json_ok": True,
        "payload": payload,
        "error_type": "",
        "error": "",
        "reason": "",
    }


class FugleTradePaginationTests(unittest.TestCase):
    def _collect(self, fake_fetch, *, page_limit: int = 3, max_pages: int = 5) -> dict:
        with patch.object(fugle_update, "fetch_endpoint", side_effect=fake_fetch):
            return fugle_update.fetch_trades_paginated(
                api_base="https://example.invalid",
                code=CODE,
                key_variants=["test-key"],
                timeout=1.0,
                page_limit=page_limit,
                max_pages=max_pages,
            )

    def test_provider_request_is_blocked_after_fixed_capture_deadline(self) -> None:
        deadline = datetime(2026, 8, 28, 17, 50, tzinfo=TPE)
        with patch.object(
            fugle_update,
            "now_tpe",
            return_value=datetime(2026, 8, 28, 17, 50, 1, tzinfo=TPE),
        ), patch.object(
            fugle_update.requests,
            "get",
            side_effect=AssertionError("provider must not be called after deadline"),
        ):
            result = fugle_update.fetch_endpoint(
                api_base="https://example.invalid",
                endpoint="volumes",
                code=CODE,
                key_variants=["test-key"],
                timeout=1.0,
                deadline=deadline,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "CAPTURE_WINDOW_EXPIRED")

    def test_pages_with_offset_limit_sort_asc_until_short_terminal_page_and_deduplicates_serial(self) -> None:
        calls: list[dict] = []
        pages = {
            0: page_payload([trade(1), trade(2), trade(3)]),
            3: page_payload([trade(3), trade(4)]),
        }

        def fake_fetch(**kwargs):
            self.assertEqual(kwargs["endpoint"], "trades")
            params = dict(kwargs.get("params") or {})
            calls.append(params)
            return successful_page(pages[int(params["offset"])])

        result = self._collect(fake_fetch)

        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["response_date"], TRADE_DATE)
        self.assertEqual(result["response_symbol"], CODE)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual([str(row["serial"]) for row in result["rows"]], ["1", "2", "3", "4"])
        self.assertEqual(
            calls,
            [
                {"offset": 0, "limit": 3, "sort": "asc"},
                {"offset": 3, "limit": 3, "sort": "asc"},
            ],
        )

    def test_rejects_a_page_from_a_different_date_or_symbol(self) -> None:
        for mismatch, second_page in (
            ("date", page_payload([trade(4)], date="2026-08-20")),
            ("symbol", page_payload([trade(4)], symbol="2330")),
        ):
            with self.subTest(mismatch=mismatch):
                pages = {
                    0: page_payload([trade(1), trade(2), trade(3)]),
                    3: second_page,
                }

                def fake_fetch(**kwargs):
                    offset = int((kwargs.get("params") or {})["offset"])
                    return successful_page(pages[offset])

                result = self._collect(fake_fetch)

                self.assertFalse(result["ok"])
                self.assertFalse(result["complete"])
                self.assertTrue(str(result.get("error_type") or "").strip())
                self.assertIn(mismatch, str(result.get("reason") or "").lower())

    def test_any_page_error_is_incomplete_and_preserves_error_type_and_reason(self) -> None:
        def fake_fetch(**kwargs):
            offset = int((kwargs.get("params") or {})["offset"])
            if offset == 0:
                return successful_page(page_payload([trade(1), trade(2), trade(3)]))
            return {
                "ok": False,
                "http_status": None,
                "json_ok": False,
                "payload": None,
                "error_type": "TIMEOUT",
                "error": "second page timed out",
                "reason": "second page timed out",
            }

        result = self._collect(fake_fetch)

        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_type"], "TIMEOUT")
        self.assertIn("timed out", result["reason"])

    def test_full_pages_at_max_pages_are_not_complete(self) -> None:
        calls: list[int] = []

        def fake_fetch(**kwargs):
            offset = int((kwargs.get("params") or {})["offset"])
            calls.append(offset)
            return successful_page(
                page_payload([trade(offset + 1), trade(offset + 2), trade(offset + 3)])
            )

        result = self._collect(fake_fetch, page_limit=3, max_pages=2)

        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_type"], "MAX_PAGES_EXCEEDED")
        self.assertTrue(str(result.get("reason") or "").strip())
        self.assertEqual(calls, [0, 3])


class FugleRequestThrottleTests(unittest.TestCase):
    def test_rolling_limiter_counts_paginated_requests_in_one_shared_budget(self) -> None:
        limiter = fugle_update.RollingRequestLimiter(2, window_seconds=1.0)

        with patch.object(
            fugle_update.time,
            "monotonic",
            side_effect=[0.0, 0.1, 0.2, 1.01],
        ), patch.object(fugle_update.time, "sleep") as sleep:
            limiter.wait()
            limiter.wait()
            limiter.wait()

        sleep.assert_called_once()
        self.assertAlmostEqual(float(sleep.call_args.args[0]), 0.8, places=6)
        self.assertEqual(limiter.summary()["request_count"], 3)
        self.assertEqual(limiter.summary()["throttle_wait_count"], 1)

    def test_fetch_endpoint_retries_429_using_retry_after(self) -> None:
        class Response:
            def __init__(self, status_code: int, payload: dict, *, retry_after: str = ""):
                self.status_code = status_code
                self._payload = payload
                self.headers = {"Retry-After": retry_after} if retry_after else {}
                self.text = "rate limited" if status_code == 429 else "ok"

            def json(self):
                return self._payload

        responses = [
            Response(429, {"error": "rate limit exceeded"}, retry_after="7"),
            Response(200, page_payload([trade(1)])),
        ]
        with patch.object(fugle_update, "enable_system_truststore", return_value=False), patch.object(
            fugle_update,
            "ca_bundle_candidates",
            return_value=[("test", True)],
        ), patch.object(fugle_update.requests, "get", side_effect=responses) as get, patch.object(
            fugle_update.time,
            "sleep",
        ) as sleep:
            result = fugle_update.fetch_endpoint(
                api_base="https://example.invalid",
                endpoint="trades",
                code=CODE,
                key_variants=["test-key"],
                timeout=1.0,
                max_retries=1,
                retry_wait_seconds=60.0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(7.0)


class FugleTradeTimeNormalizationTests(unittest.TestCase):
    def test_microsecond_epoch_is_normalized_to_taipei_time_with_six_digits(self) -> None:
        moment = datetime(2026, 8, 21, 13, 30, 0, 123456, tzinfo=TPE)
        micros = int(moment.timestamp() * 1_000_000)

        self.assertEqual(
            fugle_update.normalize_fugle_trade_time(micros),
            "13:30:00.123456",
        )
        self.assertEqual(
            fugle_update.normalize_fugle_trade_time(str(micros), trade_date=TRADE_DATE),
            "13:30:00.123456",
        )

    def test_explicit_trade_date_mismatch_or_invalid_timestamp_returns_empty_string(self) -> None:
        moment = datetime(2026, 8, 21, 13, 30, 0, 123456, tzinfo=TPE)
        micros = int(moment.timestamp() * 1_000_000)

        self.assertEqual(
            fugle_update.normalize_fugle_trade_time(micros, trade_date="2026-08-20"),
            "",
        )
        self.assertEqual(fugle_update.normalize_fugle_trade_time("not-a-timestamp"), "")


class FugleRunSuccessGateTests(unittest.TestCase):
    def test_illiquid_terminal_capture_waits_for_eod_reconciliation_without_exit_4(self) -> None:
        after_close = datetime(2026, 8, 21, 13, 35, tzinfo=TPE)
        last_trade = datetime(2026, 8, 21, 13, 20, tzinfo=TPE)
        trade_payload = page_payload(
            [trade(1, micros=int(last_trade.timestamp() * 1_000_000))]
        )
        volume_payload = {
            "date": TRADE_DATE,
            "symbol": CODE,
            "data": [
                {
                    "price": 101.0,
                    "volume": 1,
                    "volumeAtBid": 0,
                    "volumeAtAsk": 1,
                }
            ],
        }
        paginated = {
            "ok": True,
            "complete": True,
            "http_status": 200,
            "json_ok": True,
            "response_date": TRADE_DATE,
            "response_symbol": CODE,
            "rows": trade_payload["data"],
            "payload": trade_payload,
            "page_count": 1,
            "error_type": "",
            "reason": "",
        }
        args = argparse.Namespace(
            backfill_side_inferred=False,
            dry_run=True,
            write=False,
            codes=CODE,
            date=None,
            evidence_codes="0050,2330",
            evidence_max_age_seconds=300,
            window_start="13:31",
            window_end="14:10",
            capture_phase="post_close",
            api_base="https://example.invalid",
            include_quote=False,
            timeout=1.0,
            save_json_dir=None,
            allow_noncurrent_date=False,
            sleep_seconds=0.0,
            skip_prune=True,
            prune_retain_days=300,
        )
        allowed = {
            "ok": True,
            "status": "OK",
            "fugle_api_allowed": True,
            "today": TRADE_DATE,
        }

        def fake_endpoint(**kwargs):
            if kwargs["endpoint"] == "volumes":
                return successful_page(volume_payload)
            raise AssertionError(f"unexpected endpoint: {kwargs['endpoint']}")

        with patch.object(fugle_update, "fugle_market_preflight", return_value=allowed), patch.object(
            fugle_update,
            "load_api_key",
            return_value=("test-key", ["test-key"], "test"),
        ), patch.object(fugle_update, "now_tpe", return_value=after_close), patch.object(
            fugle_update,
            "fetch_trades_paginated",
            return_value=paginated,
        ), patch.object(fugle_update, "fetch_endpoint", side_effect=fake_endpoint), patch.object(
            fugle_update,
            "db",
            side_effect=lambda: nullcontext(object()),
        ), patch.object(fugle_update, "previous_close_for_trade_date", return_value=None):
            exit_code, result = fugle_update.run(args)

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["partial"])
        self.assertEqual(result["status"], "awaiting_official_volume_reconciliation")
        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["capture_success_count"], 1)
        self.assertEqual(result["reconciliation_pending_codes"], [CODE])
        self.assertEqual(
            result["stock_results"][CODE]["capture_outcome"],
            "awaiting_official_volume_reconciliation",
        )

    def test_raw_nonempty_trades_that_normalize_to_zero_rows_do_not_count_as_success(self) -> None:
        after_close = datetime(2026, 8, 21, 13, 35, tzinfo=TPE)
        malformed_trade_payload = page_payload([{"serial": 1}])
        volume_payload = {
            "date": TRADE_DATE,
            "symbol": CODE,
            "data": [
                {
                    "price": 100.0,
                    "volume": 10,
                    "volumeAtBid": 4,
                    "volumeAtAsk": 5,
                }
            ],
        }
        paginated = {
            "ok": True,
            "complete": True,
            "http_status": 200,
            "json_ok": True,
            "response_date": TRADE_DATE,
            "response_symbol": CODE,
            "rows": malformed_trade_payload["data"],
            "payload": malformed_trade_payload,
            "page_count": 1,
            "error_type": "",
            "reason": "",
        }

        def fake_endpoint(**kwargs):
            if kwargs["endpoint"] == "trades":
                return successful_page(malformed_trade_payload)
            if kwargs["endpoint"] == "volumes":
                return successful_page(volume_payload)
            raise AssertionError(f"unexpected endpoint: {kwargs['endpoint']}")

        args = argparse.Namespace(
            backfill_side_inferred=False,
            dry_run=True,
            write=False,
            codes=CODE,
            date=None,
            evidence_codes="0050,2330",
            evidence_max_age_seconds=300,
            window_start="13:31",
            window_end="14:10",
            capture_phase="post_close",
            api_base="https://example.invalid",
            include_quote=False,
            timeout=1.0,
            save_json_dir=None,
            allow_noncurrent_date=False,
            sleep_seconds=0.0,
            skip_prune=True,
            prune_retain_days=300,
        )
        allowed = {
            "ok": True,
            "status": "OK",
            "fugle_api_allowed": True,
            "today": TRADE_DATE,
        }

        with patch.object(fugle_update, "fugle_market_preflight", return_value=allowed), patch.object(
            fugle_update,
            "load_api_key",
            return_value=("test-key", ["test-key"], "test"),
        ), patch.object(fugle_update, "now_tpe", return_value=after_close), patch.object(
            fugle_update,
            "fetch_trades_paginated",
            return_value=paginated,
            create=True,
        ), patch.object(fugle_update, "fetch_endpoint", side_effect=fake_endpoint), patch.object(
            fugle_update,
            "db",
            side_effect=lambda: nullcontext(object()),
        ), patch.object(fugle_update, "previous_close_for_trade_date", return_value=None):
            exit_code, result = fugle_update.run(args)

        self.assertEqual(exit_code, fugle_update.PARTIAL_RETRYABLE)
        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["stock_results"][CODE]["trades"]["normalized_rows"], 0)


class FugleCaptureQualityMonotonicityTests(unittest.TestCase):
    def test_empty_retry_cannot_downgrade_complete_persisted_capture(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE price_volume_distribution(
                stock_id TEXT,trade_date TEXT,price REAL,volume_lots INTEGER
            );
            CREATE TABLE daily_inner_outer_volume(
                stock_code TEXT,trade_date TEXT,source TEXT
            );
            """
        )
        ensure_fugle_intraday_schema(conn)
        complete = {
            "code": CODE, "trade_date": TRADE_DATE, "endpoint": "trades",
            "snapshot_time": f"{TRADE_DATE}T13:35:00+08:00", "page_count": 2,
            "provider_row_count": 10, "normalized_row_count": 10,
            "stored_row_count": 10, "capture_complete": True,
            "data_quality": "SESSION_COMPLETE", "captured_volume_lots": 100,
        }
        unavailable = {
            "code": CODE, "trade_date": TRADE_DATE, "endpoint": "trades",
            "snapshot_time": f"{TRADE_DATE}T22:00:00+08:00", "page_count": 0,
            "provider_row_count": 0, "normalized_row_count": 0,
            "stored_row_count": 0, "capture_complete": False,
            "data_quality": "UNAVAILABLE", "reason": "provider returned no rows",
        }

        self.assertTrue(upsert_fugle_capture_run(conn, complete))
        self.assertTrue(upsert_fugle_capture_run(conn, unavailable))
        row = conn.execute(
            "SELECT capture_complete,data_quality,stored_row_count FROM fugle_intraday_capture_runs"
        ).fetchone()

        self.assertEqual(row, (1, "SESSION_COMPLETE", 10))


if __name__ == "__main__":
    unittest.main()
