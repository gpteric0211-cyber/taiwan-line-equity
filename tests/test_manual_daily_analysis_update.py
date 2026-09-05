from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import scripts.run_isolated_manual_daily_analysis_update as isolated
import scripts.run_manual_daily_analysis_update as manual
import scripts.verify_daily_analysis_update as verify


def test_daily_chip_updates_every_active_stock(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(manual, "db", lambda: conn)
    monkeypatch.setattr(
        manual,
        "active_stock_codes",
        lambda _conn: ["1101", "2330", "6488"],
    )
    captured: dict[str, object] = {}

    def fake_refresh(codes, date, mode, dry_run):
        captured.update(codes=codes, date=date, mode=mode, dry_run=dry_run)
        return {
            "ok": True,
            "total": len(codes),
            "write_count": len(codes),
            "failed_count": 0,
        }

    monkeypatch.setattr(manual, "refresh_daily_chip_momentum_for_codes", fake_refresh)
    result = manual._daily_chip("2026-09-03")

    assert result["ok"] is True
    assert result["scope"] == "all_active_listed_and_otc_stocks"
    assert captured["codes"] == ["1101", "2330", "6488"]
    assert captured["date"] == "2026-09-03"
    assert captured["mode"] == "tw50"
    assert captured["dry_run"] is False


def test_manual_update_fails_closed_after_first_failed_step(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        manual,
        "_official_catch_up",
        lambda _trade_date: {"ok": False, "exit_code": 4},
    )
    called = {"capture": False}

    def forbidden_capture(_trade_date: str):
        called["capture"] = True
        return {"ok": True}

    monkeypatch.setattr(manual, "_capture", forbidden_capture)
    report_path = tmp_path / "manual.json"
    result = manual.run_manual_update(
        trade_date="2026-09-03",
        report_path=report_path,
    )

    assert result["ok"] is False
    assert result["status"] == "failed_closed"
    assert result["completed_steps"] == 1
    assert called["capture"] is False
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "failed_closed"


def test_source_incomplete_candidate_can_pass_safe_publication_gate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for name in (
        "_official_catch_up",
        "_replay_cached_capture",
        "_capture",
        "_official_finalize",
        "_daily_chip",
        "_taiwan50_close_batch",
        "_external_events",
    ):
        monkeypatch.setattr(manual, name, lambda _trade_date: {"ok": True})
    monkeypatch.setattr(manual, "_tdcc_weekly", lambda: {"ok": True})
    monkeypatch.setattr(
        manual,
        "build_daily_analysis_report",
        lambda *_args, **_kwargs: {
            "safe_to_publish": True,
            "daily_update_complete": False,
        },
    )

    result = manual.run_manual_update(
        trade_date="2026-09-03",
        report_path=tmp_path / "manual.json",
    )

    assert result["ok"] is True
    assert result["safe_to_publish"] is True
    assert result["daily_update_complete"] is False
    assert result["status"] == "source_incomplete_safe_to_publish"


def test_retryable_capture_continues_to_official_verification(monkeypatch) -> None:
    monkeypatch.setattr(manual, "run_capture_pipeline", lambda *_args, **_kwargs: 4)
    monkeypatch.setattr(
        manual,
        "capture_progress",
        lambda _trade_date: {
            "attempted_stocks": 1970,
            "required_capture_stocks": 1978,
        },
    )

    result = manual._capture("2026-09-03")

    assert result["ok"] is True
    assert result["capture_stage_ready"] is False
    assert result["deferred_to_official_verification"] is True


def test_plan_only_bypasses_database_snapshot(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_subprocess(command, cwd):
        calls["command"] = command
        calls["cwd"] = cwd
        return Completed()

    monkeypatch.setattr(isolated.subprocess, "run", fake_subprocess)
    monkeypatch.setattr(
        isolated,
        "run_isolated_update",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not snapshot")),
    )

    assert isolated.main(["--plan-only"]) == 0
    assert "--plan-only" in calls["command"]


def test_batch_publication_complete_requires_matching_point_in_time_universe() -> None:
    complete = {
        "expected_active_asof": 1978,
        "classified_ohlcv_count": 1941,
        "official_no_trade_count": 37,
        "not_applicable_count": 0,
        "expected_universe_hash": "same",
        "classified_universe_hash": "same",
    }

    assert verify._publication_complete(complete) is True
    assert verify._publication_complete({**complete, "official_no_trade_count": 36}) is False
    assert verify._publication_complete({**complete, "classified_universe_hash": "other"}) is False


def test_equal_counts_cannot_hide_wrong_stock_codes() -> None:
    result = verify._coverage({"2330", "6488"}, {"2330", "1101"})
    assert result["complete"] is False
    assert result["missing_codes"] == ["6488"]
    assert verify._coverage(set(), set())["complete"] is False


def test_partial_supplemental_source_is_not_complete() -> None:
    assert not verify._source_ready({"ok": True, "status": "partial"})
    assert not verify._source_ready({"ok": False, "status": "ok"})
    assert verify._source_ready({"ok": True, "status": "ok"})


def test_partial_last_day_is_retried_even_when_newer_rows_exist(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE history_price(date TEXT, source_quality TEXT, source TEXT)")
    conn.executemany("INSERT INTO history_price VALUES (?, 'official', 'TWSE')",
                     [("2026-09-01",), ("2026-09-02",), ("2026-09-03",)])
    conn.execute("CREATE TABLE full_market_batch_publications(trade_date TEXT)")
    conn.execute("INSERT INTO full_market_batch_publications VALUES ('2026-09-01')")
    monkeypatch.setattr(manual, "db", lambda: conn)
    monkeypatch.setattr(manual, "_batch_publication", lambda *_: {"valid": True})
    monkeypatch.setattr(manual, "_publication_complete", lambda p: bool(p.get("valid")))
    monkeypatch.setattr(manual, "is_taiwan_trading_day", lambda d: d.weekday() < 5)
    assert manual._missing_official_dates("2026-09-04") == ["2026-09-02", "2026-09-03"]


def test_published_verification_uses_same_explicit_date_and_database(monkeypatch, tmp_path) -> None:
    active = tmp_path / "market.db"
    monkeypatch.setenv("TAIWAN50_DB_PATH", str(active))
    monkeypatch.setattr(isolated, "run_isolated_update", lambda *a, **k: 0)
    calls = []
    monkeypatch.setattr(verify, "main", lambda args: calls.append(args) or 4)
    assert isolated.main(["--date", "2026-09-03"]) == 4
    assert calls == [["--database", str(active), "--date", "2026-09-03"]]


def test_failed_candidate_does_not_verify_old_live_database(monkeypatch) -> None:
    monkeypatch.setattr(isolated, "run_isolated_update", lambda *a, **k: 10)
    monkeypatch.setattr(verify, "main", lambda args: (_ for _ in ()).throw(AssertionError()))
    assert isolated.main(["--date", "2026-09-03"]) == 10
