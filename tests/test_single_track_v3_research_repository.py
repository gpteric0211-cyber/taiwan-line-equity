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
    rollback_single_track_v3_schema,
)
from repository.single_track_v3_assessment_repository import (  # noqa: E402
    create_event_cluster,
    create_event_revision,
)
from repository.single_track_v3_research_repository import (  # noqa: E402
    prune_expired_research_content,
    record_research_news_item,
    research_news_item,
    research_news_items_available_at,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _run(run_id: str, slot_key: str, scheduled_for: str) -> dict:
    return {
        "run_id": run_id,
        "idempotency_key": f"{run_id}:SourceAuthorityPolicyV1",
        "slot_key": slot_key,
        "target_trade_date": "2026-09-02",
        "scheduled_for": scheduled_for,
        "cutoff_at": scheduled_for,
        "started_at": None,
        "completed_at": None,
        "status": "queued",
        "source_policy_version": "SourceAuthorityPolicyV1",
        "calendar_revision": "twse-calendar-2026-v1",
        "source_coverage": {},
        "source_failures": [],
        "late_reason": None,
        "created_at": scheduled_for,
        "updated_at": scheduled_for,
    }


def _prepare_runs(conn: sqlite3.Connection) -> None:
    create_news_retrieval_run(
        conn,
        _run("run-evening-1800", "evening_1800", "2026-09-01T18:00:00+08:00"),
    )
    create_news_retrieval_run(
        conn,
        _run("run-evening-2100", "evening_2100", "2026-09-01T21:00:00+08:00"),
    )


def _item(**overrides: object) -> dict:
    item = {
        "news_item_id": "news-item-1",
        "source_id": "mops",
        "source_class": "canonical_official",
        "publisher": "公開資訊觀測站",
        "source_url": "https://mops.twse.com.tw/example",
        "publisher_published_at": "2026-09-01T17:55:00+08:00",
        "publisher_published_date": "2026-09-01",
        "publisher_time_verified": True,
        "index_seen_at": None,
        "first_retrieved_at": "2026-09-01T18:05:00+08:00",
        "last_retrieved_at": "2026-09-01T18:05:00+08:00",
        "available_at": "2026-09-01T18:04:00+08:00",
        "effective_tw_trade_date": "2026-09-02",
        "verification_state": "primary_verified",
        "title": "公司發布重大訊息",
        "key_points": ["董事會通過資本支出"],
        "short_excerpt": "必要且有界的短摘錄",
        "content_hash": "a" * 64,
        "event_fingerprint": "b" * 64,
        "dedup_cluster": "issuer:2330:event:20260901",
        "entity_refs": ["TWSE:2330"],
        "event_cluster_id": None,
        "event_revision_id": None,
        "source_rights": {
            "allow_fetch": True,
            "allow_model": True,
            "allow_display": True,
            "allow_store_excerpt": True,
            "content_retention_days": 7,
            "attribution": "公開資訊觀測站",
        },
        "source_policy_version": "SourceAuthorityPolicyV1",
        "retention_class": "canonical_disclosure",
        "content_expires_at": "2026-09-06T18:05:00+08:00",
        "untrusted_text": True,
        "raw_body_retained": False,
        "first_run_id": "run-evening-1800",
        "last_run_id": "run-evening-1800",
        "created_at": "2026-09-01T18:05:00+08:00",
        "updated_at": "2026-09-01T18:05:00+08:00",
    }
    item.update(overrides)
    return item


def _prepare_event_revision(conn: sqlite3.Connection) -> None:
    create_event_cluster(
        conn,
        {
            "event_cluster_id": "cluster-1",
            "dedup_key": "issuer:2330:event:20260901",
            "event_type": "company_disclosure",
            "entity_refs": ["TWSE:2330"],
            "cluster_state": "active",
            "first_available_at": "2026-09-01T18:04:00+08:00",
            "last_seen_at": "2026-09-01T18:05:00+08:00",
            "created_at": "2026-09-01T18:05:00+08:00",
            "updated_at": "2026-09-01T18:05:00+08:00",
        },
    )
    create_event_revision(
        conn,
        {
            "event_revision_id": "revision-1",
            "event_cluster_id": "cluster-1",
            "revision_no": 1,
            "revision_digest": "c" * 64,
            "content_evidence_digest": "d" * 64,
            "content_cutoff": "2026-09-01T18:05:00+08:00",
            "event_type": "company_disclosure",
            "verification_state": "verified",
            "materiality": "material",
            "key_points": ["董事會通過資本支出"],
            "short_excerpt": "有界摘錄",
            "source_refs": [{"source_id": "mops", "url_hash": "e" * 64}],
            "price_reaction": {},
            "supersedes_revision_id": None,
            "available_at": "2026-09-01T18:04:00+08:00",
            "hot_content_expires_at": "2026-09-08T18:04:00+08:00",
            "sealed_at": "2026-09-01T18:06:00+08:00",
            "created_at": "2026-09-01T18:06:00+08:00",
        },
    )


def test_research_news_schema_is_additive_versioned_and_rollback_safe() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE legacy_market_table(id INTEGER PRIMARY KEY)")

    ensure_single_track_v3_schema(conn)

    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(research_news_item)").fetchall()
    }
    assert {
        "first_retrieved_at",
        "last_retrieved_at",
        "available_at",
        "publisher_time_verified",
        "publisher_published_date",
        "content_expires_at",
        "content_pruned_at",
        "raw_body_retained",
    } <= columns
    assert conn.execute(
        "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()[0] == SINGLE_TRACK_V3_SCHEMA_VERSION

    rollback_single_track_v3_schema(conn, allow_destructive=True)
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='legacy_market_table'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='research_news_item'"
    ).fetchone()[0] == 0


def test_stage810_research_table_adds_date_only_publisher_metadata() -> None:
    conn = _connection()
    ensure_single_track_v3_schema(conn)
    conn.execute("DROP TABLE research_news_item")
    conn.execute(
        """
        CREATE TABLE research_news_item (
            news_item_id TEXT PRIMARY KEY,
            available_at TEXT NOT NULL,
            verification_state TEXT NOT NULL,
            source_class TEXT NOT NULL,
            content_pruned_at TEXT,
            content_expires_at TEXT NOT NULL,
            dedup_cluster TEXT NOT NULL,
            event_revision_id TEXT
        )
        """
    )
    conn.execute(
        "UPDATE single_track_v3_schema_state SET schema_version=? WHERE singleton_id=1",
        ("single-track-v3-stage8.10",),
    )

    ensure_single_track_v3_schema(conn)

    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(research_news_item)").fetchall()
    }
    assert "publisher_published_date" in columns
    assert conn.execute(
        "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()[0] == "single-track-v3-v11-stage1.1"


def test_research_news_item_preserves_first_availability_and_bounded_retention() -> None:
    conn = _connection()
    _prepare_runs(conn)
    saved = record_research_news_item(conn, _item())
    assert saved["first_retrieved_at"] == "2026-09-01T18:05:00+08:00"
    assert saved["available_at"] == "2026-09-01T18:04:00+08:00"
    assert saved["publisher_published_date"] == "2026-09-01"
    assert saved["raw_body_retained"] is False

    revised = _item(
        last_retrieved_at="2026-09-01T21:05:00+08:00",
        available_at="2026-09-01T18:03:00+08:00",
        verification_state="secondary_corroborated",
        last_run_id="run-evening-2100",
        updated_at="2026-09-01T21:05:00+08:00",
    )
    saved = record_research_news_item(conn, revised)
    assert saved["first_retrieved_at"] == "2026-09-01T18:05:00+08:00"
    assert saved["available_at"] == "2026-09-01T18:03:00+08:00"
    assert saved["last_run_id"] == "run-evening-2100"

    with pytest.raises(ValueError, match="cannot move later"):
        record_research_news_item(
            conn,
            {**revised, "available_at": "2026-09-01T18:04:00+08:00"},
        )
    with pytest.raises(ValueError, match="cannot be extended"):
        record_research_news_item(
            conn,
            {**revised, "content_expires_at": "2026-09-07T18:05:00+08:00"},
        )
    with pytest.raises(ValueError, match="immutable identity"):
        record_research_news_item(
            conn,
            {**revised, "first_retrieved_at": "2026-09-01T18:04:59+08:00"},
        )


def test_research_news_rejects_raw_content_rights_violations_and_gdelt_time_aliasing() -> None:
    conn = _connection()
    _prepare_runs(conn)

    with pytest.raises(ValueError, match="forbidden field: raw_body"):
        record_research_news_item(conn, {**_item(), "raw_body": "full article"})
    with pytest.raises(ValueError, match="untrusted_text=true"):
        record_research_news_item(conn, _item(untrusted_text=False))
    denied_rights = {
        **_item()["source_rights"],
        "allow_store_excerpt": False,
    }
    with pytest.raises(ValueError, match="do not allow storing an excerpt"):
        record_research_news_item(conn, _item(source_rights=denied_rights))
    with pytest.raises(ValueError, match="retention limit"):
        record_research_news_item(
            conn,
            _item(content_expires_at="2026-09-09T18:05:00+08:00"),
        )
    with pytest.raises(ValueError, match="GDELT publisher time requires"):
        record_research_news_item(
            conn,
            _item(
                news_item_id="gdelt-1",
                source_id="gdelt-doc",
                source_class="news_radar",
                publisher="GDELT discovery index",
                publisher_time_verified=False,
                index_seen_at="2026-09-01T18:02:00+08:00",
                content_hash="f" * 64,
                event_fingerprint="1" * 64,
                retention_class="hot_news",
            ),
        )
    with pytest.raises(ValueError, match="ISO-8601 date"):
        record_research_news_item(
            conn,
            _item(publisher_published_date="September 1, 2026"),
        )
    with pytest.raises(ValueError, match="after first retrieval"):
        record_research_news_item(
            conn,
            _item(publisher_published_date="2026-09-02"),
        )

    gdelt = record_research_news_item(
        conn,
        _item(
            news_item_id="gdelt-1",
            source_id="gdelt-doc",
            source_class="news_radar",
            publisher="GDELT discovery index",
            publisher_published_at=None,
            publisher_time_verified=False,
            index_seen_at="2026-09-01T18:02:00+08:00",
            content_hash="f" * 64,
            event_fingerprint="1" * 64,
            retention_class="hot_news",
        ),
    )
    assert gdelt["publisher_published_at"] is None
    assert gdelt["index_seen_at"] == "2026-09-01T18:02:00+08:00"


def test_prune_removes_hot_content_but_preserves_metadata_and_event_revision() -> None:
    conn = _connection()
    _prepare_runs(conn)
    _prepare_event_revision(conn)
    record_research_news_item(
        conn,
        _item(event_cluster_id="cluster-1", event_revision_id="revision-1"),
    )

    assert prune_expired_research_content(
        conn,
        pruned_at="2026-09-06T18:06:00+08:00",
    ) == 1
    saved = research_news_item(conn, "news-item-1")
    assert saved is not None
    assert saved["title"] == ""
    assert saved["key_points"] == []
    assert saved["short_excerpt"] == ""
    assert saved["content_hash"] == "a" * 64
    assert saved["source_url"] == "https://mops.twse.com.tw/example"
    assert saved["verification_state"] == "expired"
    assert conn.execute(
        "SELECT count(*) FROM event_revision WHERE event_revision_id='revision-1'"
    ).fetchone()[0] == 1
    with pytest.raises(ValueError, match="cannot be restored"):
        record_research_news_item(
            conn,
            _item(
                event_cluster_id="cluster-1",
                event_revision_id="revision-1",
                last_retrieved_at="2026-09-06T18:07:00+08:00",
                updated_at="2026-09-06T18:07:00+08:00",
            ),
        )


def test_research_news_cutoff_reader_is_point_in_time_and_read_only() -> None:
    conn = _connection()
    _prepare_runs(conn)
    record_research_news_item(conn, _item())
    record_research_news_item(
        conn,
        _item(
            news_item_id="news-item-2",
            source_id="licensed-wire",
            source_class="licensed_secondary",
            publisher="Licensed Wire",
            source_url="https://news.example/second",
            publisher_published_at="2026-09-01T18:06:00+08:00",
            first_retrieved_at="2026-09-01T18:07:00+08:00",
            last_retrieved_at="2026-09-01T18:07:00+08:00",
            available_at="2026-09-01T18:06:00+08:00",
            content_hash="2" * 64,
            event_fingerprint="3" * 64,
            dedup_cluster="wire:event:2",
            content_expires_at="2026-09-06T18:07:00+08:00",
            retention_class="hot_news",
            created_at="2026-09-01T18:07:00+08:00",
            updated_at="2026-09-01T18:07:00+08:00",
        ),
    )
    changes_before = conn.total_changes

    selected = research_news_items_available_at(
        conn,
        "2026-09-01T18:05:00+08:00",
        verification_states={"primary_verified"},
    )

    assert [item["news_item_id"] for item in selected] == ["news-item-1"]
    assert conn.total_changes == changes_before
