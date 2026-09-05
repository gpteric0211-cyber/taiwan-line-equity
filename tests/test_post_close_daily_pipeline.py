from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from scripts import run_post_close_daily_pipeline as pipeline  # noqa: E402


def create_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stock_master(
                code TEXT PRIMARY KEY,is_active INTEGER,security_type TEXT
            );
            CREATE TABLE stock_no_trade_dates(code TEXT,trade_date TEXT);
            CREATE TABLE fugle_intraday_capture_runs(
                code TEXT,trade_date TEXT,endpoint TEXT,data_quality TEXT,
                capture_complete INTEGER,provider_row_count INTEGER,
                normalized_row_count INTEGER,stored_row_count INTEGER
            );
            CREATE TABLE price_volume_distribution(
                stock_id TEXT,trade_date TEXT,data_quality TEXT
            );
            INSERT INTO stock_master VALUES
                ('1101',1,'stock'),('1102',1,'stock'),('1103',1,'stock');
            INSERT INTO stock_no_trade_dates VALUES ('1103','2026-08-26');
            INSERT INTO fugle_intraday_capture_runs VALUES
                ('1101','2026-08-26','trades','SESSION_COMPLETE',1,10,10,10),
                ('1102','2026-08-26','trades','SOURCE_DELAYED',0,0,0,0);
            INSERT INTO price_volume_distribution VALUES
                ('1101','2026-08-26','VALIDATED'),
                ('1102','2026-08-26','OK');
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_capture_progress_excludes_official_no_trade_stocks(tmp_path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    create_database(database)
    monkeypatch.setattr(pipeline, "DB_PATH", database)

    progress = pipeline.capture_progress("2026-08-26")

    assert progress == {
        "active_stocks": 3,
        "no_trade_stocks": 1,
        "required_capture_stocks": 2,
        "attempted_stocks": 2,
        "distribution_stocks": 2,
        "validated_distribution_stocks": 1,
        "scoped_validated_distribution_stocks": 0,
    }


def test_source_delayed_capture_remains_retryable(tmp_path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    create_database(database)
    monkeypatch.setattr(pipeline, "DB_PATH", database)

    assert pipeline.capture_needed_codes("2026-08-26") == ["1102"]


def test_unvalidated_distribution_without_any_trade_capture_is_retryable(tmp_path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    create_database(database)
    conn = sqlite3.connect(database)
    try:
        conn.execute("INSERT INTO stock_master VALUES ('1104',1,'stock')")
        conn.execute("INSERT INTO price_volume_distribution VALUES ('1104','2026-08-26','OK')")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pipeline, "DB_PATH", database)

    assert pipeline.capture_needed_codes("2026-08-26") == ["1102", "1104"]


def test_scoped_validated_distribution_is_not_recaptured(tmp_path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    create_database(database)
    conn = sqlite3.connect(database)
    try:
        conn.execute("INSERT INTO stock_master VALUES ('1104',1,'stock')")
        conn.execute("INSERT INTO price_volume_distribution VALUES ('1104','2026-08-26','SCOPED_VALIDATED')")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pipeline, "DB_PATH", database)

    assert pipeline.capture_needed_codes("2026-08-26") == ["1102"]


def test_complete_paginated_capture_waiting_for_official_is_not_recaptured(tmp_path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    create_database(database)
    conn = sqlite3.connect(database)
    try:
        conn.execute("INSERT INTO stock_master VALUES ('1104',1,'stock')")
        conn.execute("INSERT INTO price_volume_distribution VALUES ('1104','2026-08-26','OK')")
        conn.execute(
            """
            INSERT INTO fugle_intraday_capture_runs VALUES(
                '1104','2026-08-26','trades',
                'PAGINATION_COMPLETE_SESSION_UNVERIFIED',0,8,8,8
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(pipeline, "DB_PATH", database)

    assert pipeline.capture_needed_codes("2026-08-26") == ["1102"]


def test_capture_stage_never_calls_official_reconciliation_or_parity(monkeypatch) -> None:
    progress = {
        "active_stocks": 1,
        "no_trade_stocks": 0,
        "required_capture_stocks": 1,
        "attempted_stocks": 1,
        "distribution_stocks": 1,
        "validated_distribution_stocks": 1,
        "scoped_validated_distribution_stocks": 0,
    }
    reports: list[dict] = []
    commands: list[list[str]] = []
    remaining_calls = iter([["1101"], []])
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-26")
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 26, 18, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(
        pipeline,
        "sync_official_stock_master",
        lambda **_kwargs: {"ok": True, "status": "ok"},
    )
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(
        pipeline,
        "reconcile_full_market_price_volume_after_official_update",
        lambda _result: (_ for _ in ()).throw(AssertionError("capture must not reconcile or promote data")),
    )
    monkeypatch.setattr(
        pipeline,
        "run_surface_parity_audit",
        lambda _date: (_ for _ in ()).throw(AssertionError("capture must not run parity audit")),
    )
    monkeypatch.setattr(pipeline, "capture_needed_codes", lambda _date: next(remaining_calls))
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda payload: reports.append(payload))

    assert pipeline.run_capture_pipeline() == 0
    assert len(commands) == 1
    assert "run_fugle_all_from_xlsx_progress.py" in commands[0][1]
    assert not any("update_all_market_database.py" in item[1] for item in commands)
    assert reports[-1]["official_update_deferred"] is True
    assert reports[-1]["remaining_capture_count"] == 0
    assert reports[-1]["status"] == "capture_complete_awaiting_official_verification"
    assert reports[-1]["price_volume_is_decision_ready"] is False


def test_t_plus_one_capture_is_skipped_without_calling_fugle(monkeypatch) -> None:
    reports: list[dict] = []
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 27, 6, 45, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(
        pipeline,
        "_capture_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("historical capture must not call Fugle")
        ),
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda value: reports.append(value))

    assert pipeline.run_capture_pipeline("2026-08-26") == 0
    assert reports[-1]["status"] == "historical_capture_not_attempted"
    assert reports[-1]["fugle_called"] is False


def test_stock_master_refresh_failure_does_not_suppress_same_day_capture(monkeypatch) -> None:
    progress = {
        "active_stocks": 1,
        "no_trade_stocks": 0,
        "required_capture_stocks": 1,
        "attempted_stocks": 1,
        "distribution_stocks": 1,
        "validated_distribution_stocks": 0,
        "scoped_validated_distribution_stocks": 0,
    }
    reports: list[dict] = []
    commands: list[list[str]] = []
    remaining_calls = iter([["1101"], []])
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 26, 15, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(
        pipeline,
        "sync_official_stock_master",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("metadata delayed")),
    )
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(pipeline, "capture_needed_codes", lambda _date: next(remaining_calls))
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda value: reports.append(value))

    assert pipeline.run_capture_pipeline("2026-08-26") == 4
    assert len(commands) == 1
    assert "run_fugle_all_from_xlsx_progress.py" in commands[0][1]
    assert reports[-1]["stock_master"]["universe_status"] == ("provisional_from_last_official_sync")


def test_finalize_stage_never_calls_fugle_and_uses_verified_official_date(monkeypatch) -> None:
    progress = {
        "active_stocks": 1,
        "no_trade_stocks": 0,
        "required_capture_stocks": 1,
        "attempted_stocks": 1,
        "distribution_stocks": 1,
        "validated_distribution_stocks": 1,
        "scoped_validated_distribution_stocks": 0,
    }
    reports: list[dict] = []
    commands: list[list[str]] = []
    reconciliations: list[dict] = []
    outcome_archives: list[dict] = []
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-26")
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 26, 18, 5, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(pipeline, "capture_needed_codes", lambda _date: [])
    monkeypatch.setattr(
        pipeline,
        "_load_official_report",
        lambda: {
            "official_exact_date_ohlcv": {
                "ok": True,
                "storage_allowed": True,
                "trade_date": "2026-08-26",
            }
        },
    )
    monkeypatch.setattr(
        pipeline,
        "reconcile_full_market_price_volume_after_official_update",
        lambda result: (
            reconciliations.append(result)
            or {
                "ok": True,
                "status": "ok",
                "required_trading_stock_count": 1,
                "decision_ready_count": 1,
            }
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "archive_shadow_outcomes_after_official_update",
        lambda **kwargs: (
            outcome_archives.append(kwargs)
            or {
                "status": "complete",
                "enabled": True,
                "writes_attempted": True,
                "blocking_stable_pipeline": False,
                "outcome_rows_written": 3,
            }
        ),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if "audit_canonical_surface_parity.py" in command[1]:
            return SimpleNamespace(returncode=0, stdout='{"status":"passed"}\n')
        if "run_fugle_all_from_xlsx_progress.py" in command[1]:
            raise AssertionError("finalize must never call Fugle")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    monkeypatch.setattr(pipeline, "write_progress_report", lambda payload: reports.append(payload))

    assert pipeline.run_finalize_pipeline(enable_shadow_outcome_archive=True) == 0
    official_commands = [command for command in commands if "update_all_market_database.py" in command[1]]
    assert len(official_commands) == 1
    assert reconciliations == [{"verified_trade_date": "2026-08-26", "official_sources": []}]
    assert reports[-1]["official_update_exit_code"] == 0
    assert reports[-1]["fugle_called_during_finalize"] is False
    assert outcome_archives == [
        {
            "enabled": True,
            "official_update_exit_code": 0,
            "verified_official_trade_date": "2026-08-26",
        }
    ]
    assert reports[-1]["shadow_outcome_archive"]["outcome_rows_written"] == 3


def test_shadow_outcome_archive_is_default_off_and_opt_in_transactional(tmp_path, monkeypatch) -> None:
    database = tmp_path / "shadow-outcomes.db"
    sqlite3.connect(database).close()
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "DB_PATH", database)
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 27, 18, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )

    def materialize(conn, *, recorded_at):
        calls.append(recorded_at)
        conn.execute("CREATE TABLE archived(value TEXT)")
        conn.execute("INSERT INTO archived VALUES ('sealed')")
        return {
            "ok": True,
            "prediction_samples_seen": 1,
            "outcome_rows_ready": 3,
            "outcome_rows_written": 3,
            "results": [],
        }

    monkeypatch.setattr(pipeline, "materialize_available_target_outcomes", materialize)

    disabled = pipeline.archive_shadow_outcomes_after_official_update(
        enabled=False,
        official_update_exit_code=0,
        verified_official_trade_date="2026-08-27",
    )
    assert disabled["status"] == "disabled_pending_rollout_authorization"
    assert disabled["writes_attempted"] is False
    assert calls == []

    enabled = pipeline.archive_shadow_outcomes_after_official_update(
        enabled=True,
        official_update_exit_code=0,
        verified_official_trade_date="2026-08-27",
    )
    assert enabled["status"] == "complete"
    assert enabled["outcome_rows_written"] == 3
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT value FROM archived").fetchone()[0] == "sealed"


def test_official_success_does_not_hide_supplemental_capture_gap(monkeypatch) -> None:
    progress = {
        "active_stocks": 1,
        "no_trade_stocks": 0,
        "required_capture_stocks": 1,
        "attempted_stocks": 1,
        "distribution_stocks": 1,
        "validated_distribution_stocks": 0,
        "scoped_validated_distribution_stocks": 0,
    }
    reports: list[dict] = []
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-26")
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 26, 18, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(pipeline, "capture_needed_codes", lambda _date: ["1101"])
    monkeypatch.setattr(
        pipeline,
        "_load_official_report",
        lambda: {
            "official_exact_date_ohlcv": {
                "ok": True,
                "storage_allowed": True,
                "trade_date": "2026-08-26",
            }
        },
    )
    monkeypatch.setattr(
        pipeline,
        "reconcile_full_market_price_volume_after_official_update",
        lambda _result: {"ok": False, "status": "partial"},
    )
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        pipeline,
        "run_surface_parity_audit",
        lambda _date: (_ for _ in ()).throw(AssertionError("partial capture must not be audited")),
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda payload: reports.append(payload))

    assert pipeline.run_finalize_pipeline() == 4
    assert reports[-1]["status"] == "official_complete_supplemental_pending"
    assert pipeline.run_finalize_pipeline(publish_official_core=True) == 0
    assert reports[-1]["status"] == "official_complete_supplemental_pending"
    assert reports[-1]["publication_scope"] == "official_core"
    assert reports[-1]["official_core_ready"] is True
    assert reports[-1]["full_analysis_ready"] is False
    assert reports[-1]["official_update_exit_code"] == 0
    assert reports[-1]["price_volume_is_decision_ready"] is False
    assert reports[-1]["surface_parity_audit"]["status"] == "skipped"
    assert "price-volume capture" in reports[-1]["surface_parity_audit"]["reason"]
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=5))
    assert pipeline.run_finalize_pipeline(publish_official_core=True) == 5
    assert reports[-1]["official_core_ready"] is False
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(pipeline, "_load_official_report", lambda: {})
    assert pipeline.run_finalize_pipeline(publish_official_core=True) == 4


def test_complete_scoped_capture_stops_retry_but_keeps_scoring_pending(monkeypatch) -> None:
    progress = {
        "active_stocks": 1,
        "no_trade_stocks": 0,
        "required_capture_stocks": 1,
        "attempted_stocks": 1,
        "distribution_stocks": 1,
        "validated_distribution_stocks": 0,
        "scoped_validated_distribution_stocks": 1,
    }
    reports: list[dict] = []
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-26")
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 26, 18, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(pipeline, "capture_needed_codes", lambda _date: [])
    monkeypatch.setattr(
        pipeline,
        "_load_official_report",
        lambda: {
            "official_exact_date_ohlcv": {
                "ok": True,
                "storage_allowed": True,
                "trade_date": "2026-08-26",
            }
        },
    )
    monkeypatch.setattr(
        pipeline,
        "reconcile_full_market_price_volume_after_official_update",
        lambda _result: {
            "ok": True,
            "status": "ok",
            "required_trading_stock_count": 1,
            "decision_ready_count": 0,
        },
    )
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda payload: reports.append(payload))

    assert pipeline.run_finalize_pipeline() == 0
    assert reports[-1]["status"] == "data_collection_complete_analysis_history_pending"
    assert reports[-1]["price_volume_capture_ready"] is True
    assert reports[-1]["price_volume_is_decision_ready"] is False
    assert reports[-1]["surface_parity_audit"]["exit_code"] == 0


def test_surface_parity_mismatch_is_nonretryable_safety_failure(monkeypatch) -> None:
    progress = {
        "active_stocks": 1,
        "no_trade_stocks": 0,
        "required_capture_stocks": 1,
        "attempted_stocks": 1,
        "distribution_stocks": 1,
        "validated_distribution_stocks": 1,
        "scoped_validated_distribution_stocks": 0,
    }
    reports: list[dict] = []
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-26")
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 26, 18, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(pipeline, "capture_needed_codes", lambda _date: [])
    monkeypatch.setattr(
        pipeline,
        "_load_official_report",
        lambda: {
            "official_exact_date_ohlcv": {
                "ok": True,
                "storage_allowed": True,
                "trade_date": "2026-08-26",
            }
        },
    )
    monkeypatch.setattr(
        pipeline,
        "reconcile_full_market_price_volume_after_official_update",
        lambda _result: {
            "ok": True,
            "status": "ok",
            "required_trading_stock_count": 1,
            "decision_ready_count": 1,
        },
    )
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        pipeline,
        "run_surface_parity_audit",
        lambda _date: {
            "status": "failed",
            "exit_code": 3,
            "retryable": False,
            "mismatch_count": 1,
        },
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda payload: reports.append(payload))

    assert pipeline.run_finalize_pipeline() == 3
    assert pipeline.run_finalize_pipeline(publish_official_core=True) == 3
    assert reports[-1]["status"] == "surface_parity_failed"
    assert reports[-1]["surface_parity_audit"]["retryable"] is False


def test_retry_loop_retries_only_retryable_exit_codes(monkeypatch) -> None:
    calls: list[str] = []
    results = iter([4, 5, 0])
    current = datetime(2026, 8, 27, 15, 5, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-27")
    monkeypatch.setattr(pipeline, "assert_db_integrity", lambda _path, **_kwargs: None)
    monkeypatch.setattr(pipeline, "now_tpe", lambda: current)
    monkeypatch.setattr(
        pipeline,
        "run_finalize_pipeline",
        lambda trade_date: calls.append(trade_date) or next(results),
    )
    monkeypatch.setattr(pipeline.time, "sleep", lambda _seconds: None)

    code = pipeline.run_with_retries(
        stage="finalize",
        max_retries=18,
        retry_delay_seconds=1800,
        window_end="23:59",
    )

    assert code == 0
    assert calls == ["2026-08-27", "2026-08-27", "2026-08-27"]


def test_retry_loop_does_not_retry_nonretryable_failure(monkeypatch) -> None:
    calls: list[str] = []
    current = datetime(2026, 8, 27, 15, 5, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-27")
    monkeypatch.setattr(pipeline, "assert_db_integrity", lambda _path, **_kwargs: None)
    monkeypatch.setattr(pipeline, "now_tpe", lambda: current)
    monkeypatch.setattr(
        pipeline,
        "run_finalize_pipeline",
        lambda trade_date: calls.append(trade_date) or 2,
    )
    monkeypatch.setattr(
        pipeline.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )

    code = pipeline.run_with_retries(
        stage="finalize",
        max_retries=18,
        retry_delay_seconds=1800,
        window_end="23:59",
    )

    assert code == 2
    assert calls == ["2026-08-27"]


def test_retry_loop_stops_when_next_retry_would_pass_window_end(monkeypatch) -> None:
    calls: list[str] = []
    current = datetime(2026, 8, 27, 23, 45, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(pipeline, "target_trade_date", lambda: "2026-08-27")
    monkeypatch.setattr(pipeline, "assert_db_integrity", lambda _path, **_kwargs: None)
    monkeypatch.setattr(pipeline, "now_tpe", lambda: current)
    monkeypatch.setattr(
        pipeline,
        "run_finalize_pipeline",
        lambda trade_date: calls.append(trade_date) or 4,
    )
    monkeypatch.setattr(
        pipeline.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )

    code = pipeline.run_with_retries(
        stage="finalize",
        max_retries=18,
        retry_delay_seconds=1800,
        window_end="23:59",
    )

    assert code == 4
    assert calls == ["2026-08-27"]


def test_capture_propagates_source_delayed_and_forwards_hard_deadline(monkeypatch) -> None:
    progress = {
        "active_stocks": 1,
        "no_trade_stocks": 0,
        "required_capture_stocks": 1,
        "attempted_stocks": 1,
        "distribution_stocks": 0,
        "validated_distribution_stocks": 0,
        "scoped_validated_distribution_stocks": 0,
    }
    commands: list[list[str]] = []
    remaining_calls = iter([["1101"], ["1101"]])
    monkeypatch.setattr(
        pipeline,
        "now_tpe",
        lambda: datetime(2026, 8, 28, 15, 5, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(
        pipeline,
        "sync_official_stock_master",
        lambda **_kwargs: {"ok": True, "status": "ok"},
    )
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(
        pipeline,
        "capture_needed_codes",
        lambda _date: next(remaining_calls),
    )
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=5),
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda _payload: None)

    assert pipeline.run_capture_pipeline("2026-08-28", window_end="17:50") == 5
    assert len(commands) == 1
    assert commands[0][commands[0].index("--window-end") + 1] == "17:50"
    assert commands[0][commands[0].index("--batch-size") + 1] == "1"


def test_retry_deadline_is_frozen_across_midnight(monkeypatch) -> None:
    calls: list[str] = []
    times = iter(
        [
            datetime(2026, 8, 28, 23, 58, tzinfo=ZoneInfo("Asia/Taipei")),
            datetime(2026, 8, 28, 23, 58, tzinfo=ZoneInfo("Asia/Taipei")),
            datetime(2026, 8, 29, 0, 1, tzinfo=ZoneInfo("Asia/Taipei")),
        ]
    )
    monkeypatch.setattr(pipeline, "now_tpe", lambda: next(times))
    monkeypatch.setattr(pipeline, "assert_db_integrity", lambda _path, **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "run_finalize_pipeline",
        lambda trade_date: calls.append(trade_date) or 4,
    )
    monkeypatch.setattr(
        pipeline.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )

    assert (
        pipeline.run_with_retries(
            stage="finalize",
            max_retries=18,
            retry_delay_seconds=30,
            window_end="23:59",
            trade_date="2026-08-28",
        )
        == 4
    )
    assert calls == ["2026-08-28"]


def test_integrity_preflight_fails_closed_before_scheduled_stage(monkeypatch) -> None:
    reports: list[dict] = []
    monkeypatch.setattr(
        pipeline,
        "assert_db_integrity",
        lambda _path: (_ for _ in ()).throw(RuntimeError("database disk image is malformed")),
    )
    monkeypatch.setattr(
        pipeline,
        "run_capture_pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("corrupt database must never reach a writer")
        ),
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda value: reports.append(value))

    code = pipeline.run_with_retries(
        stage="capture",
        max_retries=5,
        retry_delay_seconds=1,
        window_end="17:50",
        trade_date="2026-09-01",
    )

    assert code == pipeline.DATABASE_INTEGRITY_FAILURE_EXIT_CODE
    assert reports[-1]["status"] == "database_integrity_failed"
    assert reports[-1]["integrity_phase"] == "preflight"
    assert reports[-1]["retryable"] is False


def test_integrity_postflight_fails_closed_after_scheduled_stage(monkeypatch) -> None:
    reports: list[dict] = []
    checks = iter([None, RuntimeError("database disk image is malformed")])

    def integrity_check(_path, **_kwargs) -> None:
        outcome = next(checks)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(pipeline, "assert_db_integrity", integrity_check)
    monkeypatch.setattr(pipeline, "run_capture_pipeline", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(pipeline, "write_progress_report", lambda value: reports.append(value))

    code = pipeline.run_with_retries(
        stage="capture",
        max_retries=5,
        retry_delay_seconds=1,
        window_end="17:50",
        trade_date="2026-09-01",
    )

    assert code == pipeline.DATABASE_INTEGRITY_FAILURE_EXIT_CODE
    assert reports[-1]["status"] == "database_integrity_failed"
    assert reports[-1]["integrity_phase"] == "postflight"
    assert reports[-1]["stage_exit_code"] == 0
    assert reports[-1]["retryable"] is False


def test_official_unobserved_codes_do_not_block_validated_trading_universe(
    monkeypatch,
) -> None:
    progress = {
        "active_stocks": 2,
        "no_trade_stocks": 0,
        "required_capture_stocks": 2,
        "attempted_stocks": 1,
        "distribution_stocks": 1,
        "validated_distribution_stocks": 1,
        "scoped_validated_distribution_stocks": 0,
    }
    reports: list[dict] = []
    monkeypatch.setattr(pipeline, "capture_progress", lambda _date: dict(progress))
    monkeypatch.setattr(pipeline, "capture_needed_codes", lambda _date: ["1102"])
    monkeypatch.setattr(
        pipeline,
        "recover_missing_price_volume_from_persisted_trades",
        lambda _date, **_kwargs: {"ok": True, "recovered_count": 0},
    )
    monkeypatch.setattr(
        pipeline,
        "_load_official_report",
        lambda: {
            "official_exact_date_ohlcv": {
                "ok": True,
                "storage_allowed": True,
                "trade_date": "2026-08-28",
            }
        },
    )
    monkeypatch.setattr(
        pipeline,
        "reconcile_full_market_price_volume_after_official_update",
        lambda _result: {
            "ok": True,
            "status": "ok",
            "required_trading_stock_count": 1,
            "capture_validated_count": 1,
            "decision_ready_count": 1,
            "missing_capture_count": 0,
            "retryable_count": 0,
            "rejected_count": 0,
            "official_unobserved_codes": ["1102"],
        },
    )
    monkeypatch.setattr(
        pipeline.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        pipeline,
        "run_surface_parity_audit",
        lambda _date: {"status": "passed", "exit_code": 0, "retryable": False},
    )
    monkeypatch.setattr(pipeline, "write_progress_report", lambda value: reports.append(value))

    assert pipeline.run_finalize_pipeline("2026-08-28") == 0
    assert reports[-1]["price_volume_capture_ready"] is True
    assert reports[-1]["remaining_capture_count"] == 1
    assert reports[-1]["official_unobserved_codes"] == ["1102"]


def test_pipeline_uses_canonical_configured_database_path() -> None:
    from core.config import DB_PATH as canonical_db_path

    assert pipeline.DB_PATH == canonical_db_path


def test_lock_wait_cannot_cross_fixed_window_deadline(monkeypatch) -> None:
    times = iter(
        [
            datetime(2026, 8, 28, 23, 58, tzinfo=ZoneInfo("Asia/Taipei")),
            datetime(2026, 8, 28, 23, 58, tzinfo=ZoneInfo("Asia/Taipei")),
            datetime(2026, 8, 28, 23, 58, tzinfo=ZoneInfo("Asia/Taipei")),
            datetime(2026, 8, 29, 0, 1, tzinfo=ZoneInfo("Asia/Taipei")),
        ]
    )
    lock_attempts: list[bool] = []

    @contextmanager
    def busy_lock():
        lock_attempts.append(True)
        yield False

    monkeypatch.setattr(pipeline, "now_tpe", lambda: next(times))
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(pipeline.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pipeline, "exclusive_process_lock", busy_lock)

    assert (
        pipeline.main(
            [
                "--stage",
                "finalize",
                "--date",
                "2026-08-28",
                "--window-end",
                "23:59",
                "--lock-wait-seconds",
                "3600",
            ]
        )
        == 4
    )
    assert len(lock_attempts) == 1
