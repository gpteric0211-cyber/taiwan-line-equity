from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import call, patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

import app as dashboard  # noqa: E402


TPE = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 8, 21, 13, 20, tzinfo=TPE)


def valid_session_evidence() -> dict:
    return {
        "ok": True,
        "status": "OK",
        "reason": "current_session_confirmed_by_twse_mis",
        "as_of": NOW.isoformat(timespec="seconds"),
        "accepted_evidence": [
            {
                "code": "0050",
                "source_date": "2026-08-21",
                "source_time": NOW.isoformat(timespec="seconds"),
                "age_seconds": 0.0,
            }
        ],
    }


def current_payload(code: str) -> dict:
    return {
        "date": "2026-08-21",
        "symbol": code,
        "data": [{"price": 100.0, "volume": 10}],
    }


class PriceVolumeAppBatchTest(unittest.TestCase):
    def test_post_returns_already_running_without_market_call_or_new_thread(self) -> None:
        with patch.object(dashboard, "_price_volume_update_running", True, create=True), patch.object(
            dashboard,
            "get_current_session_evidence",
            side_effect=AssertionError("already-running request must not call MIS again"),
        ), patch.object(
            dashboard.threading,
            "Thread",
            side_effect=AssertionError("already-running request must not start another thread"),
        ):
            result = dashboard.api_update_price_volume({"code": "2454"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "already_running")

    def test_background_orders_capture_before_reconcile_and_never_computes_directly(self) -> None:
        events: list[str] = []

        def capture(code, payload, *, expected_date):
            events.append(f"capture:{code}:{expected_date}")
            return {
                "ok": True,
                "status": "captured",
                "date": expected_date,
                "price_level_count": 1,
            }

        def reconcile(code, trade_date=None, required_days=30):
            events.append(f"reconcile:{code}:{trade_date}:{required_days}")
            return {
                "ok": False,
                "status": "awaiting_official_eod",
                "quality_reason": "same-day official EOD is not available yet",
            }

        with patch.object(dashboard, "_price_volume_update_running", True, create=True), patch.object(
            dashboard,
            "now_tpe",
            return_value=NOW,
        ), patch.object(
            dashboard,
            "fetch_fugle_price_volume_network",
            side_effect=lambda code: events.append(f"fetch:{code}") or current_payload(code),
        ), patch.object(
            dashboard,
            "capture_fugle_price_volume_snapshot",
            side_effect=capture,
            create=True,
        ), patch.object(
            dashboard,
            "reconcile_price_volume_profile_for_code",
            side_effect=reconcile,
            create=True,
        ), patch.object(
            dashboard,
            "compute_price_volume_score_for_code",
            side_effect=AssertionError("app must not bypass service reconciliation"),
            create=True,
        ), patch.object(dashboard, "set_status"), patch.object(dashboard, "prune_compute_caches"):
            dashboard.background_price_volume_update(["2454"], valid_session_evidence())
            self.assertFalse(dashboard._price_volume_update_running)

        self.assertEqual(
            events,
            [
                "fetch:2454",
                "capture:2454:2026-08-21",
                "reconcile:2454:2026-08-21:30",
            ],
        )

    def test_failed_capture_never_calls_reconcile(self) -> None:
        with patch.object(dashboard, "_price_volume_update_running", True, create=True), patch.object(
            dashboard,
            "now_tpe",
            return_value=NOW,
        ), patch.object(
            dashboard,
            "fetch_fugle_price_volume_network",
            return_value=current_payload("2454"),
        ), patch.object(
            dashboard,
            "capture_fugle_price_volume_snapshot",
            return_value={"ok": False, "status": "source_delayed", "quality_reason": "wrong payload date"},
            create=True,
        ), patch.object(
            dashboard,
            "reconcile_price_volume_profile_for_code",
            side_effect=AssertionError("failed capture must not be reconciled"),
            create=True,
        ), patch.object(dashboard, "set_status"), patch.object(dashboard, "prune_compute_caches"):
            dashboard.background_price_volume_update(["2454"], valid_session_evidence())
            self.assertFalse(dashboard._price_volume_update_running)

    def test_per_code_exception_continues_batch_and_finally_releases_lock(self) -> None:
        statuses = []

        def fetch(code: str):
            if code == "2454":
                raise RuntimeError("simulated Fugle failure")
            return current_payload(code)

        with patch.object(dashboard, "_price_volume_update_running", True, create=True), patch.object(
            dashboard,
            "now_tpe",
            return_value=NOW,
        ), patch.object(
            dashboard,
            "fetch_fugle_price_volume_network",
            side_effect=fetch,
        ) as fetch_mock, patch.object(
            dashboard,
            "capture_fugle_price_volume_snapshot",
            return_value={"ok": True, "status": "captured", "price_level_count": 1},
            create=True,
        ) as capture, patch.object(
            dashboard,
            "reconcile_price_volume_profile_for_code",
            return_value={"ok": False, "status": "awaiting_official_eod", "quality_reason": "pending close"},
            create=True,
        ), patch.object(
            dashboard,
            "set_status",
            side_effect=lambda key, status, message="": statuses.append((key, status, message)),
        ), patch.object(dashboard, "prune_compute_caches"):
            dashboard.background_price_volume_update(["2454", "2330"], valid_session_evidence())
            self.assertFalse(dashboard._price_volume_update_running)

        self.assertEqual(fetch_mock.call_args_list, [call("2454"), call("2330")])
        capture.assert_called_once()
        self.assertEqual(capture.call_args.args[0], "2330")
        self.assertTrue(statuses)
        self.assertNotEqual(statuses[-1][1], "loading")
        self.assertIn(statuses[-1][1], {"stale", "partial"})
        self.assertIn("失敗", statuses[-1][2])

    def test_eod_update_retries_reconcile_after_official_history_refresh(self) -> None:
        events: list[str] = []

        def reconcile(code, trade_date=None, required_days=30):
            events.append(f"reconcile:{code}:{trade_date}:{required_days}")
            return {"ok": True, "status": "validated"}

        with patch.object(dashboard, "_eod_update_running", False, create=True), patch.object(
            dashboard,
            "fetch_twse_eod_all",
            side_effect=lambda: events.append("official_eod") or ("2026-08-21", 2),
        ), patch.object(
            dashboard,
            "refresh_incomplete_taiwan50_history",
            side_effect=lambda rows: events.append("official_preload")
            or {"ready": True, "target_date": "2026-08-21", "checked": len(rows)},
        ), patch.object(
            dashboard,
            "read_components",
            return_value=[{"code": "2454"}],
        ), patch.object(
            dashboard,
            "get_watchlist_codes",
            return_value=["2330"],
        ), patch.object(
            dashboard,
            "fetch_twse_valuation_all",
        ), patch.object(
            dashboard,
            "update_corporate_actions",
        ), patch.object(
            dashboard,
            "reconcile_price_volume_profile_for_code",
            side_effect=reconcile,
            create=True,
        ), patch.object(
            dashboard,
            "compute_price_volume_score_for_code",
            side_effect=AssertionError("app must leave scoring inside service reconciliation"),
            create=True,
        ), patch.object(dashboard, "set_status"):
            dashboard.background_eod_update()
            self.assertFalse(dashboard._eod_update_running)

        self.assertEqual(events[:2], ["official_eod", "official_preload"])
        self.assertEqual(
            events[2:],
            [
                "reconcile:2330:2026-08-21:30",
                "reconcile:2454:2026-08-21:30",
            ],
        )

    def test_eod_update_does_not_reconcile_when_both_official_history_paths_fail(self) -> None:
        with patch.object(dashboard, "_eod_update_running", False, create=True), patch.object(
            dashboard,
            "fetch_twse_eod_all",
            side_effect=RuntimeError("simulated TWSE EOD failure"),
        ), patch.object(
            dashboard,
            "refresh_incomplete_taiwan50_history",
            side_effect=RuntimeError("simulated official preload failure"),
        ), patch.object(
            dashboard,
            "read_components",
            return_value=[{"code": "2454"}],
        ), patch.object(
            dashboard,
            "fetch_twse_valuation_all",
        ), patch.object(
            dashboard,
            "update_corporate_actions",
        ), patch.object(
            dashboard,
            "reconcile_price_volume_profile_for_code",
            side_effect=AssertionError("no verified official date means reconciliation must not run"),
            create=True,
        ), patch.object(dashboard, "set_status"):
            dashboard.background_eod_update()
            self.assertFalse(dashboard._eod_update_running)


if __name__ == "__main__":
    unittest.main()
