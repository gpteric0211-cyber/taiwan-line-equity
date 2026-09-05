from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_memory_config import LineMemorySettings  # noqa: E402
from core.line_memory_identity import load_or_create_key_material  # noqa: E402
from core.line_memory_schema import LINE_MEMORY_SCHEMA_VERSION  # noqa: E402
from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    SINGLE_TRACK_V3_TABLES,
    ensure_single_track_v3_schema,
    rollback_single_track_v3_schema,
)
from repository.line_conversation_repository import LineConversationRepository  # noqa: E402
from repository.single_track_v3_repository import (  # noqa: E402
    active_alias_candidates,
    canonical_analysis_artifact,
    canonical_answer_hash,
    events_available_at_cutoff,
    prune_technical_components,
    record_target_outcomes,
    record_target_predictions,
    seal_canonical_analysis_artifact,
    supersede_artifact_with_events,
    technical_components_for_snapshot,
    upsert_evidence_facts,
    upsert_event_evidence,
    upsert_stock_entity_aliases,
    upsert_technical_components,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _event(event_id: str, available_at: str, *, materiality: str = "non_material") -> dict:
    return {
        "event_id": event_id,
        "entity_refs": ["2454"],
        "event_type": "company_disclosure",
        "source_id": "mops",
        "source_class": "canonical_official",
        "publisher": "MOPS",
        "source_url": "https://example.invalid/official",
        "publisher_published_at": available_at,
        "index_seen_at": available_at,
        "retrieved_at": available_at,
        "available_at": available_at,
        "effective_tw_trade_date": "2026-09-02",
        "verification_state": "verified",
        "rights": "metadata_and_short_excerpt",
        "untrusted_text": "必要短摘錄",
        "fingerprint": f"fingerprint-{event_id}",
        "dedup_cluster": f"cluster-{event_id}",
        "relevance": 1.0,
        "directness": 1.0,
        "materiality": materiality,
        "magnitude": 0.8,
        "surprise": 0.7,
        "direction": "mixed",
        "confidence": 0.95,
        "horizon": "next_session",
        "affected_claim_ids": ["next-day-outlook"],
        "invalidation_scope": "next_day_outlook",
        "corroborating_event_ids": [],
        "recorded_at": available_at,
    }


def _fact(fact_id: str, available_at: str) -> dict:
    return {
        "fact_id": fact_id,
        "entity": "2454",
        "field": "close",
        "value": 1520.0,
        "unit_currency": "TWD",
        "period": "daily",
        "trade_date": "2026-09-01",
        "as_of": "2026-09-01T13:30:00+08:00",
        "available_at": available_at,
        "source_market_timestamp": "2026-09-01T13:30:00+08:00",
        "session": "close",
        "adjustment_basis": "official_adjusted",
        "authority_tier": "canonical_official",
        "quality": "ok",
        "availability_reason": None,
        "use_scope": "referee",
        "snapshot_id": "snapshot-2454-20260901",
        "provenance": {"source": "TWSE"},
        "formula_source_version": "daily-ohlcv-v1",
        "recorded_at": available_at,
    }


def _artifact() -> dict:
    answer = "聯發科（2454）截至 2026-09-01 收盤的封存分析。"
    return {
        "analysis_id": "analysis-2454-20260901",
        "snapshot_id": "snapshot-2454-20260901",
        "snapshot_digest": "snapshot-digest",
        "request_received_at": "2026-09-01T14:00:00+08:00",
        "analysis_cutoff": "2026-09-01T14:00:00+08:00",
        "snapshot_sealed_at": "2026-09-01T14:00:01+08:00",
        "context_digest": "context-digest",
        "component_snapshot_ids": ["technical-2454-20260901"],
        "source_revision_ids": ["source-revision:official-close-20260901"],
        "normalized_request_key": "1" * 64,
        "target_entity_id": "2454",
        "target_trade_date": "2026-09-02",
        "analysis_session": "close_batch",
        "selected_profile": "focused-16k-v1",
        "canonical_core_digest": "2" * 64,
        "projection_digest": "3" * 64,
        "render_digest": "4" * 64,
        "artifact_contract_version": "AnalysisSessionBoundaryContractV1",
        "event_watermark": "2026-09-01T13:59:00+08:00",
        "source_policy_version": "SourceAuthorityPolicyV1",
        "weight_version": "CandidateWeightV1",
        "formula_version": "TechnicalFormulaV1-candidate",
        "referee_version": "practical-status-v1",
        "model_digest": "model-digest",
        "prompt_version": "prompt-v1",
        "validator_version": "validator-v1",
        "renderer_version": "renderer-v1",
        "entity_registry_version": "StockEntityRegistryV1",
        "conversation_projection_version": "ConversationProjectionV1",
        "response_style_version": "ResponseStyleV1",
        "coverage": {"market": 1.0},
        "omissions": [],
        "conflicts": [],
        "validity": "valid",
        "superseded_by_event_ids": [],
        "superseded_reason": None,
        "canonical_answer_text": answer,
        "canonical_answer_text_hash": canonical_answer_hash(answer),
        "created_at": "2026-09-01T14:00:01+08:00",
    }


def test_schema_is_idempotent_and_guarded_rollback_preserves_legacy_table() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE history_price(date TEXT,code TEXT)")

    ensure_single_track_v3_schema(conn)
    ensure_single_track_v3_schema(conn)

    version = conn.execute(
        "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()[0]
    assert version == SINGLE_TRACK_V3_SCHEMA_VERSION
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert set(SINGLE_TRACK_V3_TABLES) <= tables
    with pytest.raises(PermissionError):
        rollback_single_track_v3_schema(conn)

    rollback_single_track_v3_schema(conn, allow_destructive=True)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "history_price" in tables
    assert not (set(SINGLE_TRACK_V3_TABLES) & tables)


def test_artifact_rejects_after_cutoff_evidence_and_later_material_event_supersedes() -> None:
    conn = _connection()
    ensure_single_track_v3_schema(conn)
    upsert_evidence_facts(
        conn,
        [
            _fact("fact-before", "2026-09-01T13:59:00+08:00"),
            _fact("fact-after", "2026-09-01T14:01:00+08:00"),
        ],
    )
    upsert_event_evidence(
        conn,
        [
            _event("event-before", "2026-09-01T13:58:00+08:00"),
            _event("event-after", "2026-09-01T14:02:00+08:00", materiality="material"),
        ],
    )

    with pytest.raises(ValueError, match="facts after analysis cutoff"):
        seal_canonical_analysis_artifact(
            conn,
            _artifact(),
            fact_claim_ids={"fact-after": "close-claim"},
        )
    with pytest.raises(ValueError, match="events after analysis cutoff"):
        seal_canonical_analysis_artifact(
            conn,
            _artifact(),
            fact_claim_ids={"fact-before": "close-claim"},
            event_use_scopes={"event-after": "direction"},
        )

    seal_canonical_analysis_artifact(
        conn,
        _artifact(),
        fact_claim_ids={"fact-before": "close-claim"},
        event_use_scopes={"event-before": "background"},
    )
    saved = canonical_analysis_artifact(conn, "analysis-2454-20260901")
    assert saved is not None
    assert saved["fact_ids"] == ["fact-before"]
    assert saved["event_ids"] == ["event-before"]
    assert saved["canonical_answer_text_hash"] == canonical_answer_hash(
        saved["canonical_answer_text"]
    )
    assert saved["canonical_payload"] == {}
    assert saved["source_revision_ids"] == ["source-revision:official-close-20260901"]
    assert saved["normalized_request_key"] == "1" * 64
    assert saved["canonical_core_digest"] == "2" * 64
    assert saved["projection_digest"] == "3" * 64
    assert saved["render_digest"] == "4" * 64
    assert [row["event_id"] for row in events_available_at_cutoff(
        conn, "2026-09-01T14:00:00+08:00"
    )] == ["event-before"]

    assert supersede_artifact_with_events(
        conn,
        "analysis-2454-20260901",
        ["event-after"],
        reason="verified material event became available after cutoff",
    ) == ["event-after"]
    superseded = canonical_analysis_artifact(conn, "analysis-2454-20260901")
    assert superseded is not None
    assert superseded["validity"] == "superseded"
    assert superseded["superseded_by_event_ids"] == ["event-after"]


def test_answer_hash_mismatch_is_rejected() -> None:
    conn = _connection()
    artifact = _artifact()
    artifact["canonical_answer_text_hash"] = "0" * 64
    with pytest.raises(ValueError, match="does not match"):
        seal_canonical_analysis_artifact(conn, artifact)


def test_technical_component_upsert_is_idempotent_and_retains_at_least_600_days() -> None:
    conn = _connection()
    start = date(2024, 1, 1)
    rows = []
    for offset in range(601):
        trade_date = (start + timedelta(days=offset)).isoformat()
        rows.append(
            {
                "trade_date": trade_date,
                "stock_code": "2454",
                "indicator_key": "trend",
                "component_key": "ma5",
                "value": float(offset),
                "value_text": None,
                "unit": "TWD",
                "parameters": {"period": 5},
                "formula_version": "TechnicalFormulaV1-candidate",
                "input_snapshot_digest": f"digest-{offset}",
                "input_start_date": trade_date,
                "input_end_date": trade_date,
                "input_row_count": 5,
                "adjustment_basis": "official_adjusted",
                "source_quality": "official",
                "data_quality": "ok",
                "availability_reason": None,
                "decision_ready": 1,
                "quality_reason": "ok",
                "computed_at": f"{trade_date}T14:00:00+08:00",
            }
        )
    assert upsert_technical_components(conn, rows) == 601
    assert upsert_technical_components(conn, [rows[-1]]) == 0
    result = prune_technical_components(conn, retain_trading_days=10)
    assert result["retained_trading_days"] == 600
    assert result["deleted_rows"] == 1
    latest = technical_components_for_snapshot(
        conn,
        "2454",
        rows[-1]["trade_date"],
        formula_version="TechnicalFormulaV1-candidate",
    )
    assert latest[0]["parameters"] == {"period": 5}


def test_alias_registry_and_paired_target_records_are_additive() -> None:
    conn = _connection()
    ensure_single_track_v3_schema(conn)
    upsert_stock_entity_aliases(
        conn,
        [
            {
                "alias": "星宇",
                "stock_code": "2646",
                "canonical_name": "星宇航空股份有限公司",
                "trading_name": "星宇航空",
                "source": "reviewed_alias",
                "effective_from": "2023-04-21",
                "effective_to": None,
                "confidence": 1.0,
                "ambiguity_set": [],
                "registry_version": "StockEntityRegistryV1",
                "reviewed": 1,
                "approved": 1,
                "approval_id": "stage0-v11-user-approval",
                "source_evidence_digest": "a" * 64,
                "updated_at": "2026-09-01T14:00:00+08:00",
            }
        ],
    )
    candidates = active_alias_candidates(
        conn,
        " 星宇 ",
        effective_date="2026-09-01",
        registry_version="StockEntityRegistryV1",
    )
    assert [row["stock_code"] for row in candidates] == ["2646"]
    approved_candidates = active_alias_candidates(
        conn,
        "星宇",
        effective_date="2026-09-01",
        registry_version="StockEntityRegistryV1",
        approved_only=True,
    )
    assert [row["approval_id"] for row in approved_candidates] == [
        "stage0-v11-user-approval"
    ]

    seal_canonical_analysis_artifact(conn, _artifact())
    predictions = [
        {
            "sample_id": "2454-20260901-close",
            "analysis_id": "analysis-2454-20260901",
            "model_role": role,
            "target_key": "next_close_direction",
            "regime": "material_event",
            "probabilities": {"up": 0.4, "flat": 0.4, "down": 0.2},
            "eligible": 1,
            "completion_state": "completed",
            "omission_reason": None,
            "analysis_cutoff": "2026-09-01T14:00:00+08:00",
            "event_cluster_id": "cluster-2454-20260901",
            "event_type": "material_information",
            "event_revision_id": "revision-2454-20260901",
            "target_entity_id": "2454",
            "target_trade_date": "2026-09-02",
            "weight_version": "candidate-unreleased",
            "contribution_state": "shadow_zero_weight",
            "large_safety_slice": True,
            "synthetic": False,
            "created_at": "2026-09-01T14:00:01+08:00",
        }
        for role in ("stable", "candidate")
    ]
    assert record_target_predictions(conn, predictions) == 2
    saved_predictions = conn.execute(
        """
        SELECT model_role,event_cluster_id,event_type,large_safety_slice,synthetic,
               event_revision_id,target_entity_id,target_trade_date,weight_version,
               contribution_state
        FROM analysis_target_prediction ORDER BY model_role
        """
    ).fetchall()
    assert [tuple(row) for row in saved_predictions] == [
        (
            "candidate", "cluster-2454-20260901", "material_information", 1, 0,
            "revision-2454-20260901", "2454", "2026-09-02",
            "candidate-unreleased", "shadow_zero_weight",
        ),
        (
            "stable", "cluster-2454-20260901", "material_information", 1, 0,
            "revision-2454-20260901", "2454", "2026-09-02",
            "candidate-unreleased", "shadow_zero_weight",
        ),
    ]
    assert record_target_outcomes(
        conn,
        [
            {
                "sample_id": "2454-20260901-close",
                "target_key": "next_close_direction",
                "outcome_revision": "official-adjusted-v1",
                "stock_code": "2454",
                "prediction_trade_date": "2026-09-01",
                "outcome_trade_date": "2026-09-02",
                "label": "flat",
                "t_close": 1520.0,
                "next_open": 1525.0,
                "next_close": 1528.0,
                "adjustment_basis": "official_adjusted",
                "quality_status": "ok",
                "availability_reason": None,
                "available_at": "2026-09-02T14:00:00+08:00",
                "recorded_at": "2026-09-02T14:01:00+08:00",
            }
        ],
    ) == 1


def test_existing_prediction_table_receives_additive_stage7_columns() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE analysis_target_prediction (
            sample_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            model_role TEXT NOT NULL,
            target_key TEXT NOT NULL,
            regime TEXT NOT NULL,
            probability_json TEXT NOT NULL,
            eligible INTEGER NOT NULL,
            completion_state TEXT NOT NULL,
            omission_reason TEXT,
            analysis_cutoff TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(sample_id, model_role, target_key)
        )
        """
    )

    ensure_single_track_v3_schema(conn)

    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(analysis_target_prediction)").fetchall()
    }
    assert {
        "event_cluster_id",
        "event_type",
        "large_safety_slice",
        "synthetic",
        "event_revision_id",
        "target_entity_id",
        "target_trade_date",
        "weight_version",
        "contribution_state",
    } <= columns


def test_line_projection_is_encrypted_bounded_and_uses_existing_ttl(tmp_path: Path) -> None:
    settings = LineMemorySettings(
        storage="sqlite",
        database_path=tmp_path / "memory.sqlite3",
        key_file=tmp_path / "memory.key",
        channel_namespace="test",
        raw_retention_seconds=86400,
        summary_retention_seconds=30 * 86400,
        recent_exchange_limit=8,
        prompt_character_budget=6000,
        compaction_trigger=3,
        compaction_batch_size=6,
        compaction_timeout_seconds=60,
        privacy_notice_version="2026-08-28",
        long_term_approved=True,
    )
    repository = LineConversationRepository(
        settings,
        load_or_create_key_material(settings.key_file),
    )
    repository.initialize()
    now = 1000.0
    with repository._connection() as conn:
        conn.execute(
            """
            INSERT INTO line_memory_subject(
                scope_key,principal_key,source_type,key_version,privacy_notice_version,
                created_at,updated_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            ("scope", "principal", "user", 1, "2026-08-28", now, now, now + 30 * 86400),
        )
        conn.commit()

    projection_id = repository.store_projection(
        "scope",
        projection={"active_stock": "2646", "last_intent": "outlook"},
        projection_version="ConversationProjectionV1",
        turn_count=12,
        token_count=4000,
        last_analysis_id="analysis-2646",
        last_analysis_cutoff="2026-09-01T14:00:00+08:00",
        now=now,
    )
    assert projection_id > 0
    loaded = repository.active_projection("scope", now=now + 1)
    assert loaded["active_stock"] == "2646"
    assert loaded["turn_count"] == 12
    assert loaded["token_count"] == 4000
    assert loaded["expires_at"] == now + settings.summary_retention_seconds
    assert LINE_MEMORY_SCHEMA_VERSION == "line-memory-v3"
    assert b"2646" not in settings.database_path.read_bytes()
    with pytest.raises(ValueError, match="turn_count"):
        repository.store_projection(
            "scope",
            projection={},
            projection_version="ConversationProjectionV1",
            turn_count=13,
            token_count=1,
            now=now,
        )
    assert repository.purge_expired(
        now=now + settings.summary_retention_seconds + 1
    )["projections"] == 1
