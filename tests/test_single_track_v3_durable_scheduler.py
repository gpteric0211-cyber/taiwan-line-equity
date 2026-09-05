from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    ensure_single_track_v3_schema,
    rollback_single_track_v3_schema,
)
from repository.single_track_v3_calendar_repository import (  # noqa: E402
    calendar_revision,
    latest_calendar_revision_at,
    next_open_session_after,
    seal_calendar_revision,
)
from task.single_track_v3_scheduler import (  # noqa: E402
    build_due_run_declarations,
    run_scheduler_tick,
    scheduler_preflight,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connection(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path) if path is not None else ":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _calendar_payload() -> tuple[dict, list[dict]]:
    created_at = "2026-08-01T09:05:00+08:00"
    revision = {
        "calendar_revision": "twse-official-session-2026-r1",
        "source_id": "twse_official_calendar",
        "source_url": "https://example.invalid/twse-calendar-evidence",
        "source_digest": _sha("official-calendar-source"),
        "session_policy_version": "TWSESessionPolicyV1",
        "revision_published_at": "2026-08-01T09:00:00+08:00",
        "revision_available_at": "2026-08-01T09:01:00+08:00",
        "timezone": "Asia/Taipei",
        "sealed_at": created_at,
        "created_at": created_at,
    }
    session_specs = (
        ("2026-09-04", "scheduled", "09:00", "13:30", None),
        ("2026-09-05", "cancelled", None, None, "officially_cancelled"),
        ("2026-09-07", "scheduled", "09:00", "13:30", None),
        ("2026-09-08", "delayed", "10:00", "13:30", None),
        ("2026-09-09", "special_session", "11:00", "12:00", None),
        ("2026-09-10", "early_close", "09:00", "11:30", None),
    )
    sessions = []
    for trade_date, state, opened, closed, cancellation_reason in session_specs:
        sessions.append(
            {
                "trade_date": trade_date,
                "session_state": state,
                "scheduled_open_at": (
                    f"{trade_date}T{opened}:00+08:00" if opened else None
                ),
                "scheduled_close_at": (
                    f"{trade_date}T{closed}:00+08:00" if closed else None
                ),
                "cancellation_reason": cancellation_reason,
                "source_evidence_digest": _sha(f"session:{trade_date}:{state}"),
                "created_at": created_at,
            }
        )
    return revision, sessions


def _sealed_calendar(conn: sqlite3.Connection) -> dict:
    ensure_single_track_v3_schema(conn)
    revision, sessions = _calendar_payload()
    return seal_calendar_revision(conn, revision, sessions)


def _slot(declarations: list[dict], name: str, scheduled_for: str) -> dict:
    return next(
        item
        for item in declarations
        if item["slot_key"] == name and item["scheduled_for"] == scheduled_for
    )


def test_calendar_revision_is_immutable_point_in_time_and_session_aware() -> None:
    conn = _connection()
    calendar = _sealed_calendar(conn)

    assert calendar["calendar_revision"] == "twse-official-session-2026-r1"
    assert {item["session_state"] for item in calendar["sessions"]} == {
        "scheduled",
        "cancelled",
        "delayed",
        "special_session",
        "early_close",
    }
    assert latest_calendar_revision_at(
        conn, visible_at="2026-08-01T09:00:30+08:00"
    ) is None
    visible = latest_calendar_revision_at(
        conn, visible_at="2026-09-06T12:00:00+08:00"
    )
    assert visible is not None
    assert visible["revision_digest"] == calendar["revision_digest"]
    next_session = next_open_session_after(
        conn,
        calendar_revision_value=calendar["calendar_revision"],
        cutoff_at="2026-09-06T12:15:00+08:00",
    )
    assert next_session is not None
    assert next_session["trade_date"] == "2026-09-07"

    revision, sessions = _calendar_payload()
    assert seal_calendar_revision(conn, revision, sessions) == calendar
    sessions[2]["scheduled_open_at"] = "2026-09-07T09:30:00+08:00"
    with pytest.raises(ValueError, match="conflicts"):
        seal_calendar_revision(conn, revision, sessions)


def test_fixed_slots_have_exact_tpe_schedule_deadlines_and_late_catch_up() -> None:
    conn = _connection()
    calendar = _sealed_calendar(conn)

    cases = (
        (
            "2026-09-06T18:00:00+08:00",
            "evening_1800",
            "2026-09-06T18:00:00+08:00",
            "2026-09-06T18:15:00+08:00",
        ),
        (
            "2026-09-06T21:00:00+08:00",
            "evening_2100",
            "2026-09-06T21:00:00+08:00",
            "2026-09-06T21:15:00+08:00",
        ),
        (
            "2026-09-07T06:00:00+08:00",
            "preopen_0600",
            "2026-09-07T06:00:00+08:00",
            "2026-09-07T06:15:00+08:00",
        ),
        (
            "2026-09-07T06:45:00+08:00",
            "preopen_final_scan",
            "2026-09-07T06:45:00+08:00",
            "2026-09-07T07:00:00+08:00",
        ),
    )
    for observed_at, slot_name, scheduled_for, cutoff_at in cases:
        declarations = build_due_run_declarations(
            calendar,
            observed_at=observed_at,
            source_policy_version="SourceAuthorityPolicyV1",
        )
        declaration = _slot(declarations, slot_name, scheduled_for)
        assert declaration["status"] == "queued"
        assert declaration["cutoff_at"] == cutoff_at
        assert declaration["late_reason"] is None
        assert declaration["target_trade_date"] == "2026-09-07"

    late = build_due_run_declarations(
        calendar,
        observed_at="2026-09-07T07:30:00+08:00",
        source_policy_version="SourceAuthorityPolicyV1",
    )
    final_scan = _slot(
        late, "preopen_final_scan", "2026-09-07T06:45:00+08:00"
    )
    assert final_scan["status"] == "queued"
    assert final_scan["cutoff_at"] == "2026-09-07T07:45:00+08:00"
    assert final_scan["late_reason"] == "boot_wake_catch_up_point_in_time_only"
    assert final_scan["_catch_up"] is True


def test_sentinel_runs_on_weekend_and_never_backfills_missed_cutoffs() -> None:
    conn = _connection()
    calendar = _sealed_calendar(conn)
    declarations = build_due_run_declarations(
        calendar,
        observed_at="2026-09-06T12:07:00+08:00",
        source_policy_version="SourceAuthorityPolicyV1",
    )
    sentinel = [
        item
        for item in declarations
        if item["slot_key"] == "high_signal_sentinel_15m"
    ]

    assert len(sentinel) == 97
    assert sentinel[-1]["scheduled_for"] == "2026-09-06T12:00:00+08:00"
    assert sentinel[-1]["status"] == "queued"
    assert sentinel[-1]["cutoff_at"] == "2026-09-06T12:15:00+08:00"
    assert sentinel[-1]["target_trade_date"] == "2026-09-07"
    assert all(item["status"] == "skipped" for item in sentinel[:-1])
    assert all(
        item["cutoff_at"] == "2026-09-06T12:07:00+08:00"
        for item in sentinel[:-1]
    )
    assert all(
        item["late_reason"]
        == "sentinel_interval_missed_no_historical_cutoff_backfill"
        for item in sentinel[:-1]
    )


def test_tick_survives_reopen_and_is_idempotent_without_duplicate_runs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "scheduler-restart.sqlite3"
    conn = _connection(database_path)
    _sealed_calendar(conn)
    first = run_scheduler_tick(
        conn,
        observed_at="2026-09-06T12:07:00+08:00",
        source_policy_version="SourceAuthorityPolicyV1",
    )
    conn.commit()
    run_count = conn.execute("SELECT COUNT(*) FROM news_retrieval_run").fetchone()[0]
    assert first["replayed"] is False
    assert run_count == 101
    assert len(first["materialized_run_ids"]) == 101
    assert len(first["skipped_run_ids"]) == 100
    conn.close()

    reopened = _connection(database_path)
    replay = run_scheduler_tick(
        reopened,
        observed_at="2026-09-06T12:07:00+08:00",
        source_policy_version="SourceAuthorityPolicyV1",
    )
    assert replay["replayed"] is True
    assert reopened.execute("SELECT COUNT(*) FROM news_retrieval_run").fetchone()[0] == run_count

    advanced = run_scheduler_tick(
        reopened,
        observed_at="2026-09-06T12:16:00+08:00",
        source_policy_version="SourceAuthorityPolicyV1",
    )
    assert advanced["replayed"] is False
    assert len(advanced["materialized_run_ids"]) == 1
    assert reopened.execute("SELECT COUNT(*) FROM news_retrieval_run").fetchone()[0] == run_count + 1


def test_preflight_is_read_only_and_reports_worker_schema_ready() -> None:
    conn = _connection()
    _sealed_calendar(conn)
    before = conn.total_changes

    report = scheduler_preflight(
        conn,
        observed_at="2026-09-06T12:07:00+08:00",
    )

    assert conn.total_changes == before
    assert report["schema_ready"] is True
    assert report["calendar_ready"] is True
    assert report["retrieval_worker_ready"] is True
    assert report["install_ready"] is True
    assert "durable_retrieval_worker_schema_not_initialized" not in report["reason_codes"]
    assert report["production_writes"] == 0


def test_stage89_schema_and_rollback_include_scheduler_and_retrieval_tables() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE legacy_market_table(id INTEGER PRIMARY KEY)")
    ensure_single_track_v3_schema(conn)
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert SINGLE_TRACK_V3_SCHEMA_VERSION == "single-track-v3-v11-stage1.1"
    assert {
        "single_track_v3_calendar_revision",
        "single_track_v3_calendar_session",
        "single_track_v3_scheduler_tick",
        "single_track_v3_retrieval_source_attempt",
        "single_track_v3_research_run_item",
        "single_track_v3_retrieval_worker_receipt",
    } <= tables

    rollback_single_track_v3_schema(conn, allow_destructive=True)
    remaining = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "legacy_market_table" in remaining
    assert "single_track_v3_calendar_revision" not in remaining
    assert "single_track_v3_calendar_session" not in remaining
    assert "single_track_v3_scheduler_tick" not in remaining
    assert "single_track_v3_retrieval_source_attempt" not in remaining
    assert "single_track_v3_research_run_item" not in remaining
    assert "single_track_v3_retrieval_worker_receipt" not in remaining


def test_scheduler_task_has_no_adapter_service_or_model_import_boundary() -> None:
    source_path = REVIEW_SRC / "task" / "single_track_v3_scheduler.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    assert not any(name.startswith("adapter") for name in imported_modules)
    assert not any(name.startswith("services") for name in imported_modules)
    assert not any("qwen" in name.casefold() for name in imported_modules)


def test_portable_cli_offline_probe_runs_outside_project_cwd(tmp_path: Path) -> None:
    runner = REVIEW_SRC.parent / "scripts" / "run_single_track_v3_scheduler.py"
    completed = subprocess.run(
        [sys.executable, str(runner), "--temporary-restart-probe"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["passed"] is True
    assert payload["synthetic_offline_probe"] is True
    assert payload["production_database_used"] is False
    assert payload["production_scheduler_installed"] is False
    assert payload["replay_detected"] is True
    assert payload["zero_gpu_model_calls"] == 0


def test_cli_write_mode_requires_explicit_allow_write(tmp_path: Path) -> None:
    runner = REVIEW_SRC.parent / "scripts" / "run_single_track_v3_scheduler.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--run",
            "--database",
            str(tmp_path / "does-not-exist.sqlite3"),
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0
    assert "--run requires explicit --allow-write" in completed.stderr


def test_windows_installer_is_explicit_and_fail_closed_by_source_contract() -> None:
    installer = (
        REVIEW_SRC.parent / "scripts" / "install_single_track_v3_scheduler.ps1"
    ).read_text(encoding="utf-8")
    what_if_guard = installer.index("if (-not $Install)")
    install_call = installer.index("Register-ScheduledTask")
    assert what_if_guard < install_call
    assert "Preflight.install_ready" in installer
    assert "Taipei Standard Time" in installer
    assert "StartWhenAvailable" in installer
    assert "WakeToRun" in installer
    assert "-RepetitionInterval (New-TimeSpan -Minutes 15)" in installer
    assert "-RepetitionDuration ([TimeSpan]::MaxValue)" in installer
    assert "ReplaceExisting" in installer
