from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
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

from adapter.mis import get_current_session_evidence, parse_mis_quote  # noqa: E402
from adapter.twse_calendar import (  # noqa: E402
    parse_twse_holiday_schedule,
    refresh_twse_holiday_cache,
)
from core.config import DEFAULT_TAIWAN_MARKET_HOLIDAYS  # noqa: E402
from core.market_calendar_cache import load_twse_calendar_cache  # noqa: E402
from core.market_session import is_taiwan_trading_day  # noqa: E402
from scripts import run_fugle_watchlist_update as watchlist_runner  # noqa: E402
from scripts import update_fugle_intraday_supplemental as fugle_update  # noqa: E402


TPE = ZoneInfo("Asia/Taipei")


def mis_row(at: datetime, *, code: str = "0050", trade_time: str = "13:19:30") -> dict[str, str]:
    return {
        "c": code,
        "ch": f"tse_{code}.tw",
        "d": at.strftime("%Y%m%d"),
        "tlong": str(int(at.timestamp() * 1000)),
        "t": trade_time,
        "z": "50.00",
        "y": "49.50",
        "o": "49.80",
        "h": "50.20",
        "l": "49.70",
        "v": "12000",
    }


class TaiwanCalendarTests(unittest.TestCase):
    def test_complete_2026_weekday_closures_include_official_and_typhoon_dates(self) -> None:
        expected = {
            "2026-01-01",
            "2026-02-12",
            "2026-02-13",
            "2026-02-16",
            "2026-02-17",
            "2026-02-18",
            "2026-02-19",
            "2026-02-20",
            "2026-02-27",
            "2026-04-03",
            "2026-04-06",
            "2026-05-01",
            "2026-06-19",
            "2026-07-10",
            "2026-09-25",
            "2026-09-28",
            "2026-10-09",
            "2026-10-26",
            "2026-12-25",
        }
        self.assertEqual(DEFAULT_TAIWAN_MARKET_HOLIDAYS, expected)
        for value in expected:
            self.assertFalse(is_taiwan_trading_day(datetime.fromisoformat(value).date()), value)
        self.assertTrue(is_taiwan_trading_day(datetime.fromisoformat("2026-08-21").date()))

    def test_official_adapter_caches_closures_and_keeps_open_reference_days_open(self) -> None:
        payload = [
            {"Name": "中華民國開國紀念日", "Date": "1150101", "Description": "依規定放假1日。"},
            {"Name": "國曆新年開始交易日", "Date": "1150102", "Description": "國曆新年開始交易。"},
            {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212", "Description": ""},
        ]

        def requester(*_args, **_kwargs):
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "calendar.json"
            result = refresh_twse_holiday_cache(required_year=2026, cache_path=cache_path, requester=requester)
            self.assertTrue(result["ok"])
            snapshot = load_twse_calendar_cache(cache_path)
            self.assertIsNotNone(snapshot)
            self.assertIn("2026-01-01", snapshot["closure_dates"])
            self.assertIn("2026-02-12", snapshot["closure_dates"])
            self.assertIn("2026-07-10", snapshot["closure_dates"])
            self.assertIn("2026-01-02", snapshot["open_reference_dates"])

            def failed_requester(*_args, **_kwargs):
                raise RuntimeError("source unavailable")

            fallback = refresh_twse_holiday_cache(
                required_year=2026,
                cache_path=cache_path,
                requester=failed_requester,
            )
            self.assertEqual(fallback["status"], "SOURCE_DELAYED")
            self.assertTrue(fallback["fallback_available"])
            self.assertFalse(fallback["cache_written"])

    def test_historical_calendar_report_merges_with_current_cache(self) -> None:
        historical_payload = {
            "stat": "ok",
            "fields": ["日期", "名稱", "說明"],
            "data": [
                ["2025-12-25", "行憲紀念日", "依規定放假1日。"],
                ["2025-01-02", "國曆新年開始交易日", "國曆新年開始交易。"],
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "calendar.json"
            current_payload = [
                {"Name": "中華民國開國紀念日", "Date": "1150101", "Description": "依規定放假1日。"},
            ]
            refresh_twse_holiday_cache(
                required_year=2026,
                cache_path=cache_path,
                requester=lambda *_args, **_kwargs: current_payload,
            )
            calls: list[dict] = []

            def requester(*_args, **kwargs):
                calls.append(kwargs)
                return historical_payload

            result = refresh_twse_holiday_cache(
                required_year=2025,
                cache_path=cache_path,
                requester=requester,
            )
            snapshot = load_twse_calendar_cache(cache_path)

        self.assertTrue(result["ok"])
        self.assertEqual(snapshot["years"], [2025, 2026])
        self.assertIn("2025-12-25", snapshot["closure_dates"])
        self.assertIn("2026-01-01", snapshot["closure_dates"])
        self.assertEqual(calls[0]["params"], {"response": "json", "date": "2025"})

    def test_historical_schedule_includes_official_extraordinary_closures(self) -> None:
        historical_payload = {
            "stat": "OK",
            "fields": ["日期", "名稱", "說明"],
            "data": [["113年10月10日", "國慶日", "依規定放假1日。"]],
        }

        snapshot = parse_twse_holiday_schedule(
            historical_payload,
            source="TWSE holidaySchedule historical JSON",
            source_url="https://www.twse.com.tw/holidaySchedule/holidaySchedule",
        )

        self.assertIn("2024-10-02", snapshot["closure_dates"])
        self.assertIn("2024-10-03", snapshot["closure_dates"])
        self.assertIn("2024-10-31", snapshot["closure_dates"])
        extraordinary = {
            entry["date"]: entry
            for entry in snapshot["entries"]
            if entry["date"] in {"2024-10-02", "2024-10-03", "2024-10-31"}
        }
        self.assertEqual(set(extraordinary), {"2024-10-02", "2024-10-03", "2024-10-31"})
        self.assertTrue(all(entry["source"] == "TWSE extraordinary closure bulletin" for entry in extraordinary.values()))


class MisSourceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 21, 13, 20, 0, tzinfo=TPE)

    def test_quote_accepts_explicit_current_d_and_tlong(self) -> None:
        quote = parse_mis_quote(mis_row(self.now), as_of=self.now)
        self.assertIsNotNone(quote)
        self.assertEqual(quote["quote_date"], "2026-08-21")
        self.assertEqual(quote["source_date_fields"], ["d", "tlong"])

    def test_quote_rejects_missing_noncurrent_or_conflicting_source_dates(self) -> None:
        missing = mis_row(self.now)
        missing.pop("d")
        missing.pop("tlong")
        self.assertIsNone(parse_mis_quote(missing, as_of=self.now))

        old = mis_row(datetime(2026, 8, 20, 13, 20, tzinfo=TPE))
        self.assertIsNone(parse_mis_quote(old, as_of=self.now))

        conflict = mis_row(self.now)
        conflict["tlong"] = str(int(datetime(2026, 8, 20, 13, 20, tzinfo=TPE).timestamp() * 1000))
        self.assertIsNone(parse_mis_quote(conflict, as_of=self.now))

    def test_current_session_gate_is_read_only_and_requires_fresh_trade(self) -> None:
        calls = []

        def requester(*_args, **_kwargs):
            calls.append(1)
            return {"msgArray": [mis_row(self.now)]}

        evidence = get_current_session_evidence(["0050"], as_of=self.now, requester=requester)
        self.assertTrue(evidence["ok"])
        self.assertFalse(evidence["writes_db"])
        self.assertFalse(evidence["updates_cache"])
        self.assertEqual(len(calls), 1)

        def stale_requester(*_args, **_kwargs):
            return {"msgArray": [mis_row(self.now, trade_time="09:00:00")]}

        stale = get_current_session_evidence(["0050"], as_of=self.now, requester=stale_requester)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason"], "no_current_session_mis_evidence")

    def test_known_typhoon_closure_skips_without_mis_request(self) -> None:
        holiday = datetime(2026, 7, 10, 13, 20, tzinfo=TPE)

        def requester(*_args, **_kwargs):
            raise AssertionError("MIS must not be called on a known closed day")

        evidence = get_current_session_evidence(["0050"], as_of=holiday, requester=requester)
        self.assertEqual(evidence["status"], "MARKET_CLOSED")
        self.assertTrue(evidence["clean_skip"])
        self.assertEqual(evidence["request_count"], 0)


class FuglePreflightAndRunnerTests(unittest.TestCase):
    def test_latest_completed_capture_allows_verified_friday_on_weekend(self) -> None:
        weekend = datetime(2026, 8, 22, 10, 0, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=weekend), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            return_value={"ok": True, "status": "OK", "cache_written": True},
        ), patch.object(
            fugle_update,
            "get_current_session_evidence",
            side_effect=AssertionError("latest-completed capture must not require live MIS evidence"),
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date=None,
                evidence_codes=["0050"],
                evidence_max_age_seconds=300,
                write_calendar_cache=True,
                capture_phase="latest_completed",
            )
        self.assertTrue(result["fugle_api_allowed"])
        self.assertEqual(result["expected_date"], "2026-08-21")
        self.assertEqual(result["status"], "OK")

    def test_latest_completed_capture_rejects_requested_date_mismatch(self) -> None:
        weekend = datetime(2026, 8, 22, 10, 0, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=weekend), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            return_value={"ok": True, "status": "OK", "cache_written": True},
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date="2026-08-20",
                evidence_codes=["0050"],
                evidence_max_age_seconds=300,
                write_calendar_cache=True,
                capture_phase="latest_completed",
            )
        self.assertFalse(result["fugle_api_allowed"])
        self.assertEqual(result["status"], "SOURCE_DELAYED")
        self.assertIn("2026-08-21", result["reason"])

    def test_latest_completed_capture_waits_for_current_session_close(self) -> None:
        intraday = datetime(2026, 8, 21, 10, 0, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=intraday), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            return_value={"ok": True, "status": "OK", "cache_written": True},
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date=None,
                evidence_codes=["0050"],
                evidence_max_age_seconds=300,
                write_calendar_cache=True,
                capture_phase="latest_completed",
            )
        self.assertTrue(result["clean_skip"])
        self.assertFalse(result["fugle_api_allowed"])
        self.assertEqual(result["status"], "SKIPPED_BEFORE_MARKET_CLOSE")

    def test_latest_completed_capture_allows_previous_session_before_market_open(self) -> None:
        before_open = datetime(2026, 9, 2, 1, 0, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=before_open), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            return_value={"ok": True, "status": "OK", "cache_written": True},
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date="2026-09-01",
                evidence_codes=["0050"],
                evidence_max_age_seconds=300,
                write_calendar_cache=True,
                capture_phase="latest_completed",
            )

        self.assertTrue(result["fugle_api_allowed"])
        self.assertEqual(result["expected_date"], "2026-09-01")
        self.assertEqual(result["status"], "OK")

    def test_scheduled_catch_up_before_capture_window_is_clean_skip(self) -> None:
        early = datetime(2026, 8, 24, 9, 0, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=early), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            side_effect=AssertionError("calendar refresh should not run outside the capture window"),
        ), patch.object(
            fugle_update,
            "get_current_session_evidence",
            side_effect=AssertionError("MIS should not run outside the capture window"),
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date=None,
                evidence_codes=["0050"],
                evidence_max_age_seconds=900,
                write_calendar_cache=True,
                window_start="13:15",
                window_end="13:30",
            )
        self.assertTrue(result["clean_skip"])
        self.assertEqual(result["status"], "SKIPPED_OUTSIDE_CAPTURE_WINDOW")

    def test_normal_planned_day_without_mis_evidence_fails_closed(self) -> None:
        normal_day = datetime(2026, 8, 21, 13, 20, tzinfo=TPE)
        missing_evidence = {
            "ok": False,
            "status": "SOURCE_DELAYED",
            "clean_skip": False,
            "reason": "no_current_session_mis_evidence",
        }
        with patch.object(fugle_update, "now_tpe", return_value=normal_day), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            return_value={"ok": True, "status": "OK", "cache_written": True},
        ), patch.object(
            fugle_update,
            "get_current_session_evidence",
            return_value=missing_evidence,
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date=None,
                evidence_codes=["0050"],
                evidence_max_age_seconds=900,
                write_calendar_cache=True,
            )
        self.assertFalse(result["fugle_api_allowed"])
        self.assertFalse(result["clean_skip"])
        self.assertEqual(result["status"], "SOURCE_DELAYED")

    def test_fugle_run_blocks_before_any_fugle_api_when_mis_evidence_is_missing(self) -> None:
        args = argparse.Namespace(
            backfill_side_inferred=False,
            dry_run=False,
            write=True,
            codes="2317",
            date=None,
            evidence_codes="0050",
            evidence_max_age_seconds=900,
        )
        blocked = {
            "ok": False,
            "status": "SOURCE_DELAYED",
            "reason": "no_current_session_mis_evidence",
            "today": "2026-08-21",
            "clean_skip": False,
            "fugle_api_allowed": False,
        }
        with patch.object(fugle_update, "fugle_market_preflight", return_value=blocked), patch.object(
            fugle_update,
            "fetch_endpoint",
            side_effect=AssertionError("Fugle API must not be called"),
        ), patch.object(
            fugle_update,
            "load_api_key",
            side_effect=AssertionError("Fugle credentials must not be loaded before the market gate passes"),
        ):
            exit_code, result = fugle_update.run(args)
        self.assertEqual(exit_code, fugle_update.SOURCE_DELAYED_RETRYABLE)
        self.assertFalse(result["writes_db"])

    def test_typhoon_preflight_is_clean_skip_before_calendar_refresh_or_mis(self) -> None:
        holiday = datetime(2026, 7, 10, 13, 20, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=holiday), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            side_effect=AssertionError("annual refresh is unnecessary on a known closure"),
        ), patch.object(
            fugle_update,
            "get_current_session_evidence",
            side_effect=AssertionError("MIS must not be called on a known closure"),
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date=None,
                evidence_codes=["0050"],
                evidence_max_age_seconds=900,
                write_calendar_cache=True,
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["clean_skip"])
        self.assertFalse(result["fugle_api_allowed"])

    def test_watchlist_runner_builds_gated_watchlist_command(self) -> None:
        args = argparse.Namespace(
            output=ROOT / "docs" / "FUGLE_WATCHLIST_UPDATE_REPORT.md",
            evidence_codes="0050,2330",
            evidence_max_age_seconds=900,
            dry_run=False,
            include_quote=False,
            window_start="13:15",
            window_end="13:30",
            capture_phase="post_close",
        )
        command = watchlist_runner.build_command(args, ["2317", "2454"])
        self.assertEqual(command[0], sys.executable)
        self.assertIn("2317,2454", command)
        self.assertIn("--write", command)
        self.assertIn("--evidence-codes", command)
        self.assertIn("13:15", command)
        self.assertIn("13:30", command)
        self.assertIn("--capture-phase", command)
        self.assertIn("post_close", command)

    def test_post_close_capture_uses_calendar_without_live_mis(self) -> None:
        after_close = datetime(2026, 8, 21, 13, 35, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=after_close), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            return_value={"ok": True, "status": "OK", "cache_written": True},
        ), patch.object(
            fugle_update,
            "get_current_session_evidence",
            side_effect=AssertionError("post-close capture must not require stale live MIS evidence"),
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date=None,
                evidence_codes=["0050"],
                evidence_max_age_seconds=300,
                write_calendar_cache=True,
                window_start="13:31",
                window_end="14:10",
                capture_phase="post_close",
            )
        self.assertTrue(result["fugle_api_allowed"])
        self.assertEqual(result["status"], "OK")

    def test_post_close_capture_before_1330_is_clean_skip(self) -> None:
        before_close = datetime(2026, 8, 21, 13, 30, tzinfo=TPE)
        with patch.object(fugle_update, "now_tpe", return_value=before_close), patch.object(
            fugle_update,
            "refresh_twse_holiday_cache",
            return_value={"ok": True, "status": "OK", "cache_written": True},
        ):
            result = fugle_update.fugle_market_preflight(
                requested_date=None,
                evidence_codes=["0050"],
                evidence_max_age_seconds=300,
                write_calendar_cache=True,
                window_start="13:30",
                window_end="14:10",
                capture_phase="post_close",
            )
        self.assertTrue(result["clean_skip"])
        self.assertEqual(result["status"], "SKIPPED_BEFORE_MARKET_CLOSE")

    def test_windows_task_script_defaults_to_weekday_post_close_watchlist(self) -> None:
        script = (ROOT / "scripts" / "create_windows_fugle_watchlist_task.ps1").read_text(encoding="utf-8")
        self.assertIn('[string]$Time = "13:35"', script)
        self.assertIn("--capture-phase post_close", script)
        self.assertIn("Monday,Tuesday,Wednesday,Thursday,Friday", script)
        self.assertIn("run_fugle_watchlist_update.bat", script)

    def test_post_close_tasks_split_capture_from_official_finalization(self) -> None:
        capture_registration = (
            ROOT / "scripts" / "create_windows_full_market_database_task.ps1"
        ).read_text(encoding="utf-8")
        official_registration = (
            ROOT / "scripts" / "create_windows_official_reconciliation_task.ps1"
        ).read_text(encoding="utf-8")
        installer = (
            ROOT / "scripts" / "install_windows_post_close_tasks.ps1"
        ).read_text(encoding="utf-8")
        runner = (
            ROOT / "scripts" / "run_full_market_daily_database_update.bat"
        ).read_text(encoding="utf-8")
        isolated_runner = (
            ROOT / "scripts" / "run_isolated_post_close_pipeline.py"
        ).read_text(encoding="utf-8")
        pipeline = (
            ROOT / "scripts" / "run_post_close_daily_pipeline.py"
        ).read_text(encoding="utf-8")
        retry = (
            ROOT / "scripts" / "retry_post_close_analysis_update.py"
        ).read_text(encoding="utf-8")

        self.assertIn('[string]$Time = "15:00"', capture_registration)
        self.assertIn('[string]$WindowEnd = "17:50"', capture_registration)
        self.assertIn("--stage capture", capture_registration)
        self.assertIn("--lock-wait-seconds 10200", capture_registration)
        self.assertIn("$Now -le $WindowEndToday", capture_registration)
        self.assertNotIn("$CatchUpTrigger", capture_registration)
        self.assertIn('[string]$Time = "18:10"', official_registration)
        self.assertIn('[string]$FinalFallbackTime = "23:40"', official_registration)
        self.assertIn('[string]$CatchUpTime = "06:45"', official_registration)
        self.assertIn("--stage finalize", official_registration)
        self.assertIn("--lock-wait-seconds 3600", official_registration)
        self.assertIn("Monday,Tuesday,Wednesday,Thursday,Friday", capture_registration)
        self.assertIn("Monday,Tuesday,Wednesday,Thursday,Friday", official_registration)
        self.assertIn(
            "-DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            official_registration,
        )
        self.assertIn("including Friday data on Saturday", official_registration)
        self.assertIn("-and -not $PreviousTaskWasRunning", official_registration)
        self.assertIn("-MultipleInstances IgnoreNew", capture_registration)
        self.assertIn("-MultipleInstances IgnoreNew", official_registration)
        self.assertIn("-StartWhenAvailable", capture_registration)
        self.assertIn("-StartWhenAvailable", official_registration)
        self.assertIn("Start-ScheduledTask -TaskName $TaskName", capture_registration)
        self.assertIn("Start-ScheduledTask -TaskName $TaskName", official_registration)
        self.assertIn("Disable-ScheduledTask", installer)
        self.assertNotIn("-RestartCount", capture_registration + official_registration)
        self.assertIn("run_isolated_post_close_pipeline.py", runner)
        self.assertIn("run_post_close_daily_pipeline.py", isolated_runner)
        self.assertIn('parser.add_argument("--max-retries", type=int, default=18)', pipeline)
        self.assertIn('parser.add_argument("--window-end", default="23:59")', pipeline)
        self.assertIn('choices=("capture", "finalize")', pipeline)
        self.assertIn("RETRYABLE_CODES = {4, 5}", pipeline)
        self.assertIn('"--max-retries"', pipeline)
        self.assertIn('"0"', pipeline)
        self.assertIn("run_post_close_daily_pipeline", retry)
        self.assertIn('"finalize"', retry)
        self.assertIn("from core.config import DB_PATH", pipeline)
        self.assertIn('"--batch-size"', pipeline)
        self.assertIn("%*", runner)
        self.assertIn("logs\\post_close_scheduler", runner)
        self.assertIn('set "RUN_EXIT_CODE=%ERRORLEVEL%"', runner)
        self.assertIn("exit /b %RUN_EXIT_CODE%", runner)
        self.assertIn('>> "%LOG_FILE%" 2>&1', runner)
        self.assertNotIn(
            "C:\\Users\\",
            capture_registration + official_registration + installer + runner + pipeline + retry,
        )


if __name__ == "__main__":
    unittest.main()
