from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    ensure_single_track_v3_schema,
)
from repository.single_track_v3_research_repository import (  # noqa: E402
    record_research_news_item,
    research_news_item,
)
from repository.single_track_v3_retrieval_repository import (  # noqa: E402
    retrieval_source_attempts,
    retrieval_worker_receipt,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    acquire_scheduler_lease,
    create_news_retrieval_run,
    news_retrieval_run,
    transition_news_retrieval_run,
)
from task.single_track_v3_retrieval_worker import (  # noqa: E402
    RETRIEVAL_WORKER_CONTRACT_VERSION,
    RetrievalSourceSpec,
    retrieval_worker_schema_ready,
    run_retrieval_worker,
)


TPE = ZoneInfo("Asia/Taipei")


class IncrementingClock:
    def __init__(self, value: str) -> None:
        self.current = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def _connection(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or ":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _run(run_id: str = "retrieval-run-1", *, cutoff: str = "2026-09-01T18:15:00+08:00") -> dict:
    return {
        "run_id": run_id,
        "idempotency_key": f"idempotency:{run_id}",
        "slot_key": "evening_1800",
        "target_trade_date": "2026-09-02",
        "scheduled_for": "2026-09-01T18:00:00+08:00",
        "cutoff_at": cutoff,
        "started_at": None,
        "completed_at": None,
        "status": "queued",
        "source_policy_version": "SourceAuthorityPolicyV1",
        "calendar_revision": "offline-calendar-r1",
        "source_coverage": {},
        "source_failures": [],
        "late_reason": None,
        "created_at": "2026-09-01T17:59:00+08:00",
        "updated_at": "2026-09-01T17:59:00+08:00",
    }


def _event() -> dict:
    return {
        "event_key": hashlib.sha256(b"gdelt-event-1").hexdigest(),
        "event_date": "2026-09-01",
        "title": "聯發科發布受控測試事件",
        "publisher": "example.com",
        "url": "https://example.com/news/2454-event",
        "publisher_published_at": None,
        "index_seen_at": "2026-09-01T17:55:00+08:00",
        "retrieved_at": "2026-09-01T17:59:30+08:00",
        "verification_state": "unverified",
        "untrusted_text": True,
        "rights": {
            "source_id": "GDELT_DOC_INDEX",
            "allow_fetch": True,
            "allow_model": True,
            "allow_display": True,
            "allow_store_excerpt": False,
            "metadata_retention_days": 30,
            "raw_body_retention_seconds": 0,
            "attribution_required": True,
            "last_terms_review": "2026-08-29",
            "policy_note": "metadata-only test fixture",
            "policy_version": "news-research-policy-v1",
        },
        "citation_required": True,
        "authority_tier": "news_radar",
        "quality": "unverified",
        "can_override_main_status": False,
    }


def _success_result() -> dict:
    return {
        "ok": True,
        "status": "ok",
        "events": [_event()],
        "attempts": 1,
        "timeout_class": None,
        "adapter_version": "controlled-news-metadata-test-v1",
        "source_policy_version": "news-research-policy-v1",
        "source_id": "GDELT_DOC_INDEX",
        "source_attempts": [
            {
                "source_id": "GDELT_DOC_INDEX",
                "status": "ok",
                "timeout_class": None,
            }
        ],
        "raw_article_bodies_fetched": 0,
        "raw_body_retention_seconds": 0,
        "canonical_table_writes": 0,
    }


def _failure_result(source_id: str, status: str, timeout_class: str | None) -> dict:
    return {
        "ok": False,
        "status": status,
        "events": [],
        "timeout_class": timeout_class,
        "adapter_version": "failure-fixture-v1",
        "source_policy_version": "news-research-policy-v1",
        "source_id": source_id,
        "raw_article_bodies_fetched": 0,
        "raw_body_retention_seconds": 0,
        "canonical_table_writes": 0,
    }


def _initialize(conn: sqlite3.Connection, run: dict | None = None) -> None:
    ensure_single_track_v3_schema(conn)
    if run is not None:
        create_news_retrieval_run(conn, run, ensure_schema=False)
    conn.commit()


def test_retrieval_schema_is_additive_noncanonical_and_versioned() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE legacy_market_table(id INTEGER PRIMARY KEY)")
    _initialize(conn)

    assert retrieval_worker_schema_ready(conn) is True
    assert SINGLE_TRACK_V3_SCHEMA_VERSION == "single-track-v3-v11-stage1.1"
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "single_track_v3_retrieval_source_attempt",
        "single_track_v3_research_run_item",
        "single_track_v3_retrieval_worker_receipt",
    } <= tables
    assert "legacy_market_table" in tables


def test_stage89_retrieval_tables_upgrade_before_scope_index_creation() -> None:
    conn = _connection()
    conn.executescript(
        """
        CREATE TABLE single_track_v3_retrieval_source_attempt (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            query_digest TEXT NOT NULL,
            source_id TEXT NOT NULL,
            attempt_ordinal INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            source_status TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            adapter_version TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            result_digest TEXT NOT NULL,
            raw_article_bodies_fetched INTEGER NOT NULL DEFAULT 0,
            raw_body_retention_seconds INTEGER NOT NULL DEFAULT 0,
            canonical_table_writes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE single_track_v3_retrieval_worker_receipt (
            receipt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            terminal_status TEXT NOT NULL,
            source_attempt_ids_json TEXT NOT NULL DEFAULT '[]',
            news_item_ids_json TEXT NOT NULL DEFAULT '[]',
            pruned_item_count INTEGER NOT NULL DEFAULT 0,
            terminal_outbox_id TEXT NOT NULL UNIQUE,
            worker_contract_version TEXT NOT NULL,
            result_digest TEXT NOT NULL,
            zero_model_calls INTEGER NOT NULL DEFAULT 0,
            canonical_table_writes INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )

    ensure_single_track_v3_schema(conn)

    attempt_columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(single_track_v3_retrieval_source_attempt)"
        )
    }
    receipt_columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(single_track_v3_retrieval_worker_receipt)"
        )
    }
    indexes = {
        row[1] for row in conn.execute(
            "PRAGMA index_list(single_track_v3_retrieval_source_attempt)"
        )
    }
    assert "scope_key" in attempt_columns
    assert {
        "entity_refs_json",
        "query_digests_json",
        "query_plan_version",
        "source_plan_version",
    } <= receipt_columns
    assert "idx_single_track_retrieval_attempt_scope" in indexes
    assert conn.execute(
        "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()[0] == "single-track-v3-v11-stage1.1"


def test_worker_seals_terminal_result_and_restart_replays_without_adapter_call(
    tmp_path: Path,
) -> None:
    database = tmp_path / "retrieval-worker.sqlite3"
    conn = _connection(database)
    _initialize(conn, _run())
    adapter_calls = 0

    def fetcher(_: str) -> dict:
        nonlocal adapter_calls
        adapter_calls += 1
        return _success_result()

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454 聯發科 重大訊息"],
        entity_refs=["2454"],
        source_fetchers={"CONTROLLED_NEWS_METADATA": fetcher},
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    assert result["status"] == "success"
    assert result["replayed"] is False
    assert result["adapter_calls"] == 1
    assert result["zero_model_calls"] == 0
    assert adapter_calls == 1
    receipt = retrieval_worker_receipt(conn, "retrieval-run-1")
    assert receipt is not None
    assert receipt["worker_contract_version"] == RETRIEVAL_WORKER_CONTRACT_VERSION
    assert len(receipt["source_attempt_ids"]) == 1
    assert len(receipt["news_item_ids"]) == 1
    assert receipt["entity_refs"] == ["2454"]
    assert len(receipt["query_digests"]) == 1
    assert receipt["query_plan_version"] == "AdHocRetrievalQueryPlanV1"
    assert receipt["source_plan_version"] == "AdHocRetrievalSourcePlanV1"
    news_item_id = receipt["news_item_ids"][0]
    item = research_news_item(conn, news_item_id)
    assert item is not None
    assert item["source_class"] == "news_radar"
    assert item["verification_state"] == "unverified"
    assert item["raw_body_retained"] is False
    assert item["event_cluster_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM canonical_analysis_artifact").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM single_track_v3_outbox").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM single_track_v3_research_run_item").fetchone()[0] == 1
    conn.close()

    reopened = _connection(database)

    def forbidden_fetcher(_: str) -> dict:
        raise AssertionError("adapter must not run after a durable terminal receipt")

    replay = run_retrieval_worker(
        reopened,
        run_id="retrieval-run-1",
        queries=["2454 聯發科 重大訊息"],
        entity_refs=["2454"],
        source_fetchers={"CONTROLLED_NEWS_METADATA": forbidden_fetcher},
        clock=IncrementingClock("2026-09-01T18:01:00+08:00"),
    )
    assert replay["status"] == "success"
    assert replay["replayed"] is True
    assert replay["adapter_calls"] == 0
    assert reopened.execute("SELECT COUNT(*) FROM single_track_v3_outbox").fetchone()[0] == 1
    reopened.close()


def test_replay_rejects_changed_query_entity_or_plan_identity_without_adapter_call(
    tmp_path: Path,
) -> None:
    database = tmp_path / "retrieval-replay-identity.sqlite3"
    conn = _connection(database)
    _initialize(conn, _run())
    run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454 official event"],
        entity_refs=["2454"],
        source_fetchers={"CONTROLLED_NEWS_METADATA": lambda _: _success_result()},
        query_plan_version="QueryPlanV1",
        source_plan_version="SourcePlanV1",
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    conn.close()
    reopened = _connection(database)
    calls = 0

    def forbidden(_: str) -> dict:
        nonlocal calls
        calls += 1
        return _success_result()

    with pytest.raises(ValueError, match="replay identity conflicts"):
        run_retrieval_worker(
            reopened,
            run_id="retrieval-run-1",
            queries=["2454 changed query"],
            entity_refs=["2454"],
            source_fetchers={"CONTROLLED_NEWS_METADATA": forbidden},
            query_plan_version="QueryPlanV1",
            source_plan_version="SourcePlanV1",
            clock=IncrementingClock("2026-09-01T18:01:00+08:00"),
        )
    assert calls == 0
    reopened.close()


def test_live_lease_conflict_performs_zero_adapter_calls() -> None:
    conn = _connection()
    _initialize(conn, _run())
    assert acquire_scheduler_lease(
        conn,
        lease_key="single-track-v3:retrieval:retrieval-run-1",
        run_id="retrieval-run-1",
        owner_id="other-worker",
        lease_token="other-live-token",
        acquired_at="2026-09-01T18:00:00+08:00",
        expires_at="2026-09-01T18:10:00+08:00",
        ensure_schema=False,
    ) is True
    conn.commit()
    calls = 0

    def fetcher(_: str) -> dict:
        nonlocal calls
        calls += 1
        return _success_result()

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454"],
        source_fetchers={"CONTROLLED_NEWS_METADATA": fetcher},
        clock=IncrementingClock("2026-09-01T18:01:00+08:00"),
    )
    assert result["status"] == "lease_not_acquired"
    assert result["adapter_calls"] == 0
    assert calls == 0
    assert news_retrieval_run(conn, "retrieval-run-1")["status"] == "queued"
    assert retrieval_source_attempts(conn, "retrieval-run-1") == []


def test_expired_running_lease_is_resumed_without_rewriting_original_start() -> None:
    conn = _connection()
    _initialize(conn, _run())
    assert acquire_scheduler_lease(
        conn,
        lease_key="single-track-v3:retrieval:retrieval-run-1",
        run_id="retrieval-run-1",
        owner_id="crashed-worker",
        lease_token="expired-token",
        acquired_at="2026-09-01T18:00:00+08:00",
        expires_at="2026-09-01T18:01:00+08:00",
        ensure_schema=False,
    ) is True
    assert transition_news_retrieval_run(
        conn,
        "retrieval-run-1",
        expected_status="queued",
        new_status="running",
        started_at="2026-09-01T18:00:00+08:00",
        updated_at="2026-09-01T18:00:00+08:00",
        ensure_schema=False,
    ) is True
    conn.commit()

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454"],
        source_fetchers={"CONTROLLED_NEWS_METADATA": lambda _: _success_result()},
        clock=IncrementingClock("2026-09-01T18:02:00+08:00"),
    )
    assert result["status"] == "success"
    assert result["adapter_calls"] == 1
    saved = news_retrieval_run(conn, "retrieval-run-1")
    assert saved["started_at"] == "2026-09-01T18:00:00+08:00"
    assert saved["status"] == "success"


def test_no_results_is_a_complete_success_not_a_fake_neutral_event() -> None:
    conn = _connection()
    _initialize(conn, _run())
    no_results = {
        **_failure_result("GDELT_DOC_INDEX", "no_results", None),
        "ok": True,
    }

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454"],
        source_fetchers={"CONTROLLED_NEWS_METADATA": lambda _: no_results},
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    assert result["status"] == "success"
    assert retrieval_source_attempts(conn, "retrieval-run-1")[0]["outcome"] == "no_results"
    assert conn.execute("SELECT COUNT(*) FROM research_news_item").fetchone()[0] == 0


def test_source_specs_bind_scope_and_once_per_run_does_not_repeat_per_query() -> None:
    conn = _connection()
    _initialize(conn, _run())
    calls = {"radar": 0, "official": 0}

    def radar(_: str) -> dict:
        calls["radar"] += 1
        return _success_result()

    def official(query: str) -> dict:
        calls["official"] += 1
        assert query == ""
        return {
            **_failure_result("MOPS_DAILY", "no_results", None),
            "ok": True,
        }

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454 event", "2454 policy"],
        entity_refs=["2454"],
        source_specs=[
            RetrievalSourceSpec(
                source_id="CONTROLLED_NEWS_METADATA",
                scope_key="company_industry_news",
                fetcher=radar,
                query_mode="per_query",
            ),
            RetrievalSourceSpec(
                source_id="MOPS_DAILY",
                scope_key="official_company",
                fetcher=official,
                query_mode="once_per_run",
            ),
        ],
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    assert result["status"] == "success"
    assert result["adapter_calls"] == 3
    assert calls == {"radar": 2, "official": 1}
    attempts = retrieval_source_attempts(conn, "retrieval-run-1")
    assert [item["scope_key"] for item in attempts].count("company_industry_news") == 2
    assert [item["scope_key"] for item in attempts].count("official_company") == 1
    coverage = news_retrieval_run(conn, "retrieval-run-1")["source_coverage"]
    assert coverage["by_scope"]["company_industry_news"]["successful"] == 2
    assert coverage["by_scope"]["official_company"]["no_results"] == 1


def test_timeout_rate_limit_and_offline_are_durable_and_prune_still_runs() -> None:
    conn = _connection()
    ensure_single_track_v3_schema(conn)
    create_news_retrieval_run(conn, _run("old-run"), ensure_schema=False)
    create_news_retrieval_run(conn, _run(), ensure_schema=False)
    record_research_news_item(
        conn,
        {
            "news_item_id": "old-expired-news",
            "source_id": "OLD_TEST_SOURCE",
            "source_class": "news_radar",
            "publisher": "example.com",
            "source_url": "https://example.com/expired",
            "publisher_published_at": None,
            "publisher_time_verified": False,
            "index_seen_at": "2026-08-01T09:00:00+08:00",
            "first_retrieved_at": "2026-08-01T09:01:00+08:00",
            "last_retrieved_at": "2026-08-01T09:01:00+08:00",
            "available_at": "2026-08-01T09:00:00+08:00",
            "effective_tw_trade_date": "2026-08-03",
            "verification_state": "unverified",
            "title": "expired hot metadata",
            "key_points": [],
            "short_excerpt": "",
            "content_hash": "a" * 64,
            "event_fingerprint": "b" * 64,
            "dedup_cluster": "expired-cluster",
            "entity_refs": ["2454"],
            "event_cluster_id": None,
            "event_revision_id": None,
            "source_rights": {
                "allow_fetch": True,
                "allow_model": True,
                "allow_display": True,
                "allow_store_excerpt": False,
                "content_retention_days": 1,
                "attribution": "example.com",
            },
            "source_policy_version": "test-policy-v1",
            "retention_class": "hot_news",
            "content_expires_at": "2026-08-02T09:01:00+08:00",
            "content_pruned_at": None,
            "untrusted_text": True,
            "raw_body_retained": False,
            "first_run_id": "old-run",
            "last_run_id": "old-run",
            "created_at": "2026-08-01T09:01:00+08:00",
            "updated_at": "2026-08-01T09:01:00+08:00",
        },
        ensure_schema=False,
    )
    conn.commit()
    fetchers = {
        "OFFLINE": lambda _: _failure_result("OFFLINE", "offline", "source_offline"),
        "RATE": lambda _: _failure_result("RATE", "rate_limited", None),
        "TIMEOUT": lambda _: _failure_result("TIMEOUT", "timeout", "source_timeout"),
    }

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454"],
        source_fetchers=fetchers,
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    assert result["status"] == "failed"
    attempts = retrieval_source_attempts(conn, "retrieval-run-1")
    assert {item["failure_class"] for item in attempts} == {
        "offline",
        "rate_limited",
        "timeout",
    }
    run = news_retrieval_run(conn, "retrieval-run-1")
    assert {item["failure_class"] for item in run["source_failures"]} == {
        "offline",
        "rate_limited",
        "timeout",
    }
    expired = research_news_item(conn, "old-expired-news")
    assert expired["content_pruned_at"] is not None
    assert expired["title"] == ""
    assert expired["verification_state"] == "expired"
    assert retrieval_worker_receipt(conn, "retrieval-run-1")["pruned_item_count"] == 1


def test_nonzero_raw_retention_or_canonical_writes_fail_closed() -> None:
    conn = _connection()
    _initialize(conn, _run())
    unsafe = _success_result()
    unsafe["raw_article_bodies_fetched"] = 1
    unsafe["canonical_table_writes"] = 1

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454"],
        source_fetchers={"UNSAFE": lambda _: unsafe},
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    assert result["status"] == "failed"
    attempts = retrieval_source_attempts(conn, "retrieval-run-1")
    assert len(attempts) == 1
    assert attempts[0]["failure_class"] == "invalid_response"
    assert conn.execute("SELECT COUNT(*) FROM research_news_item").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0


def test_completed_after_cutoff_is_terminal_late_not_historical_backfill() -> None:
    conn = _connection()
    _initialize(conn, _run(cutoff="2026-09-01T18:00:02+08:00"))

    result = run_retrieval_worker(
        conn,
        run_id="retrieval-run-1",
        queries=["2454"],
        entity_refs=["2454"],
        source_fetchers={"CONTROLLED_NEWS_METADATA": lambda _: _success_result()},
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    assert result["status"] == "late"
    run = news_retrieval_run(conn, "retrieval-run-1")
    assert run["status"] == "late"
    assert run["late_reason"] == "retrieval_completed_after_run_cutoff"
    assert retrieval_worker_receipt(conn, "retrieval-run-1")["terminal_status"] == "late"
