from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    ensure_single_track_v3_schema,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    acquire_scheduler_lease,
    create_news_retrieval_run,
    enqueue_scheduler_outbox,
    news_retrieval_run,
    record_scheduler_heartbeat,
    release_scheduler_lease,
    transition_news_retrieval_run,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _run() -> dict:
    return {
        "run_id": "run-20260902-evening-1800",
        "idempotency_key": "2026-09-02:evening_1800:v1",
        "slot_key": "evening_1800",
        "target_trade_date": "2026-09-02",
        "scheduled_for": "2026-09-01T18:00:00+08:00",
        "cutoff_at": "2026-09-01T18:00:00+08:00",
        "started_at": None,
        "completed_at": None,
        "status": "queued",
        "source_policy_version": "SourceAuthorityPolicyV1",
        "calendar_revision": "twse-calendar-2026-v1",
        "source_coverage": {},
        "source_failures": [],
        "late_reason": None,
        "created_at": "2026-09-01T17:59:00+08:00",
        "updated_at": "2026-09-01T17:59:00+08:00",
    }


def test_scheduler_schema_tranche_is_additive_and_versioned() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE legacy_market_table(id INTEGER PRIMARY KEY)")

    ensure_single_track_v3_schema(conn)

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "news_retrieval_run",
        "news_run_event",
        "single_track_v3_job_lease",
        "single_track_v3_worker_heartbeat",
        "single_track_v3_outbox",
    } <= tables
    assert conn.execute(
        "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()[0] == SINGLE_TRACK_V3_SCHEMA_VERSION
    assert "legacy_market_table" in tables


def test_news_retrieval_run_is_idempotent_and_transitions_by_cas() -> None:
    conn = _connection()
    created = create_news_retrieval_run(conn, _run())

    assert created["status"] == "queued"
    assert created["source_coverage"] == {}
    assert create_news_retrieval_run(conn, _run()) == created

    conflicting = _run()
    conflicting["source_policy_version"] = "different-policy"
    with pytest.raises(ValueError, match="different content"):
        create_news_retrieval_run(conn, conflicting)

    assert transition_news_retrieval_run(
        conn,
        created["run_id"],
        expected_status="queued",
        new_status="running",
        started_at="2026-09-01T18:00:02+08:00",
        updated_at="2026-09-01T18:00:02+08:00",
    ) is True
    assert transition_news_retrieval_run(
        conn,
        created["run_id"],
        expected_status="queued",
        new_status="running",
        started_at="2026-09-01T18:00:03+08:00",
        updated_at="2026-09-01T18:00:03+08:00",
    ) is False
    assert transition_news_retrieval_run(
        conn,
        created["run_id"],
        expected_status="running",
        new_status="partial",
        completed_at="2026-09-01T18:04:00+08:00",
        source_coverage={"official": "ok", "licensed_secondary": "timeout"},
        source_failures=[{"source": "licensed_secondary", "failure": "timeout"}],
        updated_at="2026-09-01T18:04:00+08:00",
    ) is True

    saved = news_retrieval_run(conn, created["run_id"])
    assert saved is not None
    assert saved["status"] == "partial"
    assert saved["source_coverage"]["official"] == "ok"
    assert saved["source_failures"] == [
        {"failure": "timeout", "source": "licensed_secondary"}
    ]
    with pytest.raises(ValueError, match="invalid scheduler run transition"):
        transition_news_retrieval_run(
            conn,
            created["run_id"],
            expected_status="partial",
            new_status="running",
            updated_at="2026-09-01T18:05:00+08:00",
        )


def test_live_lease_cannot_be_stolen_and_heartbeat_is_token_bound() -> None:
    conn = _connection()
    run = create_news_retrieval_run(conn, _run())

    assert acquire_scheduler_lease(
        conn,
        lease_key="single-track:2026-09-02:evening_1800",
        run_id=run["run_id"],
        owner_id="worker-a",
        lease_token="lease-token-a",
        acquired_at="2026-09-01T18:00:00+08:00",
        expires_at="2026-09-01T18:05:00+08:00",
    ) is True
    assert acquire_scheduler_lease(
        conn,
        lease_key="single-track:2026-09-02:evening_1800",
        run_id=run["run_id"],
        owner_id="worker-b",
        lease_token="lease-token-b",
        acquired_at="2026-09-01T18:04:00+08:00",
        expires_at="2026-09-01T18:09:00+08:00",
    ) is False
    assert record_scheduler_heartbeat(
        conn,
        worker_id="worker-a",
        lease_key="single-track:2026-09-02:evening_1800",
        lease_token="wrong-token",
        run_id=run["run_id"],
        worker_identity_digest="a" * 64,
        state="running",
        heartbeat_at="2026-09-01T18:01:00+08:00",
        expires_at="2026-09-01T18:06:00+08:00",
    ) is False
    assert record_scheduler_heartbeat(
        conn,
        worker_id="worker-a",
        lease_key="single-track:2026-09-02:evening_1800",
        lease_token="lease-token-a",
        run_id=run["run_id"],
        worker_identity_digest="a" * 64,
        state="running",
        heartbeat_at="2026-09-01T18:01:00+08:00",
        expires_at="2026-09-01T18:06:00+08:00",
        metadata={"slot": "evening_1800"},
    ) is True
    assert acquire_scheduler_lease(
        conn,
        lease_key="single-track:2026-09-02:evening_1800",
        run_id=run["run_id"],
        owner_id="worker-b",
        lease_token="lease-token-b",
        acquired_at="2026-09-01T18:07:00+08:00",
        expires_at="2026-09-01T18:12:00+08:00",
    ) is True
    assert release_scheduler_lease(
        conn,
        lease_key="single-track:2026-09-02:evening_1800",
        lease_token="lease-token-b",
        released_at="2026-09-01T18:08:00+08:00",
    ) is True


def test_outbox_dedupe_is_idempotent_and_conflicts_fail_closed() -> None:
    conn = _connection()
    run = create_news_retrieval_run(conn, _run())
    item = {
        "outbox_id": "outbox-run-1-partial",
        "run_id": run["run_id"],
        "event_type": "NEWS_RETRIEVAL_RUN_TERMINAL",
        "aggregate_key": run["run_id"],
        "dedupe_key": f"NEWS_RETRIEVAL_RUN_TERMINAL:{run['run_id']}",
        "payload": {"run_id": run["run_id"], "status": "partial"},
        "status": "pending",
        "available_at": "2026-09-01T18:04:00+08:00",
        "claimed_at": None,
        "delivered_at": None,
        "attempt_count": 0,
        "last_error_code": None,
        "created_at": "2026-09-01T18:04:00+08:00",
        "updated_at": "2026-09-01T18:04:00+08:00",
    }

    assert enqueue_scheduler_outbox(conn, item) == item["outbox_id"]
    assert enqueue_scheduler_outbox(conn, item) == item["outbox_id"]

    conflicting = dict(item)
    conflicting["payload"] = {"run_id": run["run_id"], "status": "success"}
    with pytest.raises(ValueError, match="different content"):
        enqueue_scheduler_outbox(conn, conflicting)
