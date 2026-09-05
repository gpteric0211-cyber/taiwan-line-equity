from __future__ import annotations

import ast
import hashlib
import json
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
    rollback_single_track_v3_schema,
)
from repository.single_track_v3_artifact_production_repository import (  # noqa: E402
    ZERO_GPU_PRODUCER_VERSION,
    artifact_production_receipt,
    content_terminal_artifact,
    pending_zero_gpu_outbox_items_at,
    produce_zero_gpu_outbox_artifact,
    seal_event_delta_for_zero_gpu_production,
)
from repository.single_track_v3_artifact_repository import (  # noqa: E402
    create_event_delta_artifact,
)
from repository.single_track_v3_assessment_repository import (  # noqa: E402
    begin_content_model_dispatch,
    create_content_assessment,
    create_event_cluster,
    create_event_revision,
    create_target_impact_assessment,
    record_content_attempt_failure,
)
from repository.single_track_v3_repository import (  # noqa: E402
    canonical_analysis_artifact,
    canonical_answer_hash,
    seal_canonical_analysis_artifact,
    upsert_event_evidence,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)


TPE = ZoneInfo("Asia/Taipei")


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now(*, days: int = 0, minutes: int = 0, seconds: int = 0) -> str:
    return (
        datetime.now(TPE)
        + timedelta(days=days, minutes=minutes, seconds=seconds)
    ).isoformat()


def _canonical_artifact() -> dict:
    answer = "聯發科（2454）事件前封存分析。"
    return {
        "analysis_id": "analysis-before-event-delta",
        "snapshot_id": "snapshot-before-event-delta",
        "snapshot_digest": _sha("snapshot-before-event-delta"),
        "request_received_at": "2026-09-02T07:00:00+08:00",
        "analysis_cutoff": "2026-09-02T07:00:00+08:00",
        "snapshot_sealed_at": "2026-09-02T07:00:01+08:00",
        "context_digest": _sha("context-before-event-delta"),
        "component_snapshot_ids": [],
        "event_watermark": None,
        "source_policy_version": "SourceAuthorityPolicyV1",
        "weight_version": "CandidateWeightV1",
        "formula_version": "TechnicalFormulaV1-candidate",
        "referee_version": "practical-status-v1",
        "model_digest": None,
        "prompt_version": "prompt-v1",
        "validator_version": "validator-v1",
        "renderer_version": "renderer-v1",
        "entity_registry_version": "StockEntityRegistryV1",
        "conversation_projection_version": "ConversationProjectionV1",
        "response_style_version": "ResponseStyleV1",
        "coverage": {},
        "omissions": [],
        "conflicts": [],
        "validity": "valid",
        "superseded_by_event_ids": [],
        "superseded_reason": None,
        "canonical_answer_text": answer,
        "canonical_answer_text_hash": canonical_answer_hash(answer),
        "created_at": "2026-09-02T07:00:01+08:00",
    }


def _canonical_event() -> dict:
    available_at = "2026-09-02T07:05:00+08:00"
    return {
        "event_id": "canonical-event-post-final",
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
        "fingerprint": "fingerprint-canonical-event-post-final",
        "dedup_cluster": "cluster-post-final",
        "relevance": 1.0,
        "directness": 1.0,
        "materiality": "material",
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


def _prepare_event_delta(
    conn: sqlite3.Connection,
    *,
    invalidates_analysis: bool = True,
) -> tuple[str, dict | None]:
    create_event_cluster(
        conn,
        {
            "event_cluster_id": "cluster-post-final",
            "dedup_key": "issuer:2454:event:post-final",
            "event_type": "company_disclosure",
            "entity_refs": ["TWSE:2454"],
            "cluster_state": "active",
            "first_available_at": "2026-09-02T07:05:00+08:00",
            "last_seen_at": "2026-09-02T07:05:30+08:00",
            "created_at": "2026-09-02T07:05:30+08:00",
            "updated_at": "2026-09-02T07:05:30+08:00",
        },
    )
    create_event_revision(
        conn,
        {
            "event_revision_id": "revision-post-final",
            "event_cluster_id": "cluster-post-final",
            "revision_no": 1,
            "revision_digest": _sha("revision-post-final"),
            "content_evidence_digest": _sha("evidence-post-final"),
            "content_cutoff": "2026-09-02T07:05:00+08:00",
            "event_type": "company_disclosure",
            "verification_state": "verified",
            "materiality": "material",
            "key_points": ["bounded material event"],
            "short_excerpt": "bounded excerpt",
            "source_refs": [{"source_id": "mops", "url_hash": _sha("mops-post-final")}],
            "price_reaction": {},
            "supersedes_revision_id": None,
            "available_at": "2026-09-02T07:05:00+08:00",
            "hot_content_expires_at": "2026-09-08T07:05:00+08:00",
            "sealed_at": "2026-09-02T07:05:30+08:00",
            "created_at": "2026-09-02T07:05:30+08:00",
        },
    )
    run = create_news_retrieval_run(
        conn,
        {
            "run_id": "run-post-final-sentinel",
            "idempotency_key": "2026-09-02:sentinel:post-final:v1",
            "slot_key": "high_signal_sentinel_15m",
            "target_trade_date": "2026-09-02",
            "scheduled_for": "2026-09-02T07:00:00+08:00",
            "cutoff_at": "2026-09-02T07:06:00+08:00",
            "started_at": "2026-09-02T07:00:00+08:00",
            "completed_at": "2026-09-02T07:07:00+08:00",
            "status": "success",
            "source_policy_version": "SourceAuthorityPolicyV1",
            "calendar_revision": "twse-calendar-2026-v1",
            "source_coverage": {"official": "ok"},
            "source_failures": [],
            "late_reason": None,
            "created_at": "2026-09-02T07:00:00+08:00",
            "updated_at": "2026-09-02T07:07:00+08:00",
        },
    )
    upsert_event_evidence(conn, [_canonical_event()])
    conn.execute(
        """
        INSERT INTO news_run_event(run_id,event_id,event_revision_id,event_action,linked_at)
        VALUES(?,?,?,?,?)
        """,
        (
            run["run_id"],
            "canonical-event-post-final",
            "revision-post-final",
            "discovered",
            "2026-09-02T07:07:00+08:00",
        ),
    )
    analysis_before = None
    invalidated_analysis_ids: list[str] = []
    if invalidates_analysis:
        seal_canonical_analysis_artifact(conn, _canonical_artifact())
        analysis_before = canonical_analysis_artifact(conn, "analysis-before-event-delta")
        invalidated_analysis_ids = ["analysis-before-event-delta"]
    create_event_delta_artifact(
        conn,
        {
            "delta_artifact_id": "delta-post-final",
            "run_id": run["run_id"],
            "target_trade_date": "2026-09-02",
            "base_premarket_artifact_id": None,
            "cutoff_at": "2026-09-02T07:06:00+08:00",
            "detected_at": "2026-09-02T07:06:00+08:00",
            "event_revision_ids": ["revision-post-final"],
            "invalidated_analysis_ids": invalidated_analysis_ids,
            "source_coverage": run["source_coverage"],
            "delta_status": "pending",
            "source_policy_version": "SourceAuthorityPolicyV1",
            "calendar_revision": "twse-calendar-2026-v1",
            "created_at": "2026-09-02T07:07:00+08:00",
            "updated_at": "2026-09-02T07:07:00+08:00",
        },
    )
    sealed = seal_event_delta_for_zero_gpu_production(
        conn,
        "delta-post-final",
        expected_status="pending",
        new_status="sealed",
        sealed_at="2026-09-02T07:08:00+08:00",
        updated_at="2026-09-02T07:08:00+08:00",
    )
    return sealed["outbox_id"], analysis_before


def _prepare_content_terminal(conn: sqlite3.Connection) -> str:
    content_cutoff = _now(minutes=-10)
    create_event_cluster(
        conn,
        {
            "event_cluster_id": "cluster-content-terminal",
            "dedup_key": "content-terminal-event",
            "event_type": "company_disclosure",
            "entity_refs": ["TWSE:2330"],
            "cluster_state": "active",
            "first_available_at": _now(minutes=-20),
            "last_seen_at": _now(minutes=-15),
            "created_at": _now(minutes=-20),
            "updated_at": _now(minutes=-15),
        },
    )
    create_event_revision(
        conn,
        {
            "event_revision_id": "revision-content-terminal",
            "event_cluster_id": "cluster-content-terminal",
            "revision_no": 1,
            "revision_digest": _sha("revision-content-terminal"),
            "content_evidence_digest": _sha("content-evidence-terminal"),
            "content_cutoff": content_cutoff,
            "event_type": "company_disclosure",
            "verification_state": "verified",
            "materiality": "material",
            "key_points": ["bounded event"],
            "short_excerpt": "bounded excerpt",
            "source_refs": [{"source_id": "mops", "url_hash": _sha("terminal-url")}],
            "price_reaction": {},
            "supersedes_revision_id": None,
            "available_at": _now(minutes=-20),
            "hot_content_expires_at": _now(days=1),
            "sealed_at": _now(minutes=-14),
            "created_at": _now(minutes=-14),
        },
    )
    content = create_content_assessment(
        conn,
        {
            "content_assessment_id": "content-terminal",
            "event_revision_id": "revision-content-terminal",
            "content_evidence_digest": _sha("content-evidence-terminal"),
            "content_cutoff": content_cutoff,
            "model_digest": _sha("content-model"),
            "prompt_version": "ContentPromptV1",
            "schema_version": "ContentSchemaV1",
            "validator_version": "ContentValidatorV1",
            "contract_version": "ContentAssessmentContractV1",
            "content_generation": 1,
            "content_eligible_at": _now(minutes=-5),
            "content_recovery_deadline_at": _now(minutes=60),
            "created_at": _now(minutes=-5),
            "updated_at": _now(minutes=-5),
        },
    )
    create_target_impact_assessment(
        conn,
        {
            "target_impact_assessment_id": "target-content-terminal",
            "content_assessment_id": content["content_assessment_id"],
            "target_entity_id": "TWSE:2330",
            "target_trade_date": (datetime.now(TPE).date() + timedelta(days=1)).isoformat(),
            "forecast_target_id": "NEXT_SESSION_CLOSE_DIRECTION",
            "target_fact_snapshot_id": "snapshot-terminal",
            "target_fact_snapshot_digest": _sha("snapshot-terminal"),
            "target_context_digest": _sha("context-terminal"),
            "model_digest": _sha("target-model"),
            "prompt_version": "TargetPromptV1",
            "schema_version": "TargetSchemaV1",
            "validator_version": "TargetValidatorV1",
            "contract_version": "TargetImpactContractV1",
            "calibrator_version": "unreleased-shadow-v1",
            "target_generation": 1,
            "resolution_eligible_at": _now(minutes=-4),
            "prediction_issue_deadline_at": _now(minutes=120),
            "created_at": _now(minutes=-4),
            "updated_at": _now(minutes=-4),
        },
    )
    attempt = begin_content_model_dispatch(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id="content-terminal-attempt",
        attempt_kind="initial",
        dispatch_started_at=_now(minutes=-2),
    )
    terminal = record_content_attempt_failure(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        failure_class="permanent",
        failure_code="invalid_contract_permanent",
    )
    return str(terminal["outbox_id"])


def test_zero_gpu_producer_schema_is_additive_and_rollback_safe() -> None:
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
        "content_terminal_artifact",
        "single_track_v3_artifact_production_receipt",
    } <= tables
    assert SINGLE_TRACK_V3_SCHEMA_VERSION == "single-track-v3-v11-stage1.1"
    rollback_single_track_v3_schema(conn, allow_destructive=True)
    remaining = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "legacy_market_table" in remaining
    assert "content_terminal_artifact" not in remaining
    assert "single_track_v3_artifact_production_receipt" not in remaining


def test_content_terminal_outbox_produces_one_idempotent_suppression_artifact() -> None:
    conn = _connection()
    outbox_id = _prepare_content_terminal(conn)
    produced_at = _now(seconds=5)
    changes_before_read = conn.total_changes
    pending = pending_zero_gpu_outbox_items_at(
        conn,
        available_at=produced_at,
    )
    assert [item["outbox_id"] for item in pending] == [outbox_id]
    assert conn.total_changes == changes_before_read

    receipt = produce_zero_gpu_outbox_artifact(
        conn,
        outbox_id,
        produced_at=produced_at,
    )
    assert receipt["producer_version"] == ZERO_GPU_PRODUCER_VERSION
    assert receipt["production_status"] == "applied"
    assert receipt["output_artifact_type"] == "content_terminal_state"
    assert receipt["zero_gpu_model_calls"] == 0
    artifact = content_terminal_artifact(conn, receipt["output_artifact_ids"][0])
    assert artifact is not None
    assert artifact["user_visible_state"] == "suppressed_unresolved"
    assert artifact["terminal_reason_code"] == "content_permanent_failure"
    assert conn.execute(
        "SELECT status FROM single_track_v3_outbox WHERE outbox_id=?",
        (outbox_id,),
    ).fetchone()[0] == "delivered"
    assert produce_zero_gpu_outbox_artifact(
        conn,
        outbox_id,
        produced_at=_now(seconds=10),
    ) == receipt
    assert conn.execute(
        "SELECT attempt_count FROM single_track_v3_outbox WHERE outbox_id=?",
        (outbox_id,),
    ).fetchone()[0] == 1


def test_event_delta_producer_only_supersedes_metadata_and_keeps_answer_bytes() -> None:
    conn = _connection()
    outbox_id, before = _prepare_event_delta(conn)
    assert before is not None
    pending = pending_zero_gpu_outbox_items_at(
        conn,
        available_at="2026-09-02T07:08:00+08:00",
    )
    assert [item["outbox_id"] for item in pending] == [outbox_id]

    receipt = produce_zero_gpu_outbox_artifact(
        conn,
        outbox_id,
        produced_at="2026-09-02T07:08:01+08:00",
    )

    after = canonical_analysis_artifact(conn, "analysis-before-event-delta")
    assert after is not None
    assert receipt["production_status"] == "applied"
    assert receipt["output_artifact_ids"] == ["analysis-before-event-delta"]
    assert receipt["output_artifact_type"] == "canonical_analysis_invalidation"
    assert receipt["zero_gpu_model_calls"] == 0
    assert after["validity"] == "superseded"
    assert after["superseded_by_event_ids"] == ["canonical-event-post-final"]
    assert after["canonical_answer_text"] == before["canonical_answer_text"]
    assert after["canonical_answer_text_hash"] == before["canonical_answer_text_hash"]
    assert produce_zero_gpu_outbox_artifact(
        conn,
        outbox_id,
        produced_at="2026-09-02T07:09:00+08:00",
    ) == receipt


def test_event_delta_without_explicit_targets_is_a_delivered_no_op() -> None:
    conn = _connection()
    outbox_id, _ = _prepare_event_delta(conn, invalidates_analysis=False)

    receipt = produce_zero_gpu_outbox_artifact(
        conn,
        outbox_id,
        produced_at="2026-09-02T07:08:01+08:00",
    )

    assert receipt["production_status"] == "no_op"
    assert receipt["output_artifact_type"] == "none"
    assert receipt["output_artifact_ids"] == []
    assert conn.execute(
        "SELECT status FROM single_track_v3_outbox WHERE outbox_id=?",
        (outbox_id,),
    ).fetchone()[0] == "delivered"


def test_delivered_replay_fails_closed_if_outbox_payload_is_altered() -> None:
    conn = _connection()
    outbox_id = _prepare_content_terminal(conn)
    produce_zero_gpu_outbox_artifact(
        conn,
        outbox_id,
        produced_at=_now(seconds=5),
    )
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM single_track_v3_outbox WHERE outbox_id=?",
            (outbox_id,),
        ).fetchone()[0]
    )
    payload["terminal_reason_code"] = "altered_after_delivery"
    conn.execute(
        "UPDATE single_track_v3_outbox SET payload_json=? WHERE outbox_id=?",
        (json.dumps(payload, sort_keys=True), outbox_id),
    )

    with pytest.raises(ValueError, match="receipt conflicts"):
        produce_zero_gpu_outbox_artifact(
            conn,
            outbox_id,
            produced_at=_now(seconds=10),
        )


def test_tampered_delta_payload_rolls_back_receipt_delivery_and_invalidation() -> None:
    conn = _connection()
    outbox_id, before = _prepare_event_delta(conn)
    assert before is not None
    source = conn.execute(
        "SELECT payload_json FROM single_track_v3_outbox WHERE outbox_id=?",
        (outbox_id,),
    ).fetchone()[0]
    payload = json.loads(source)
    payload["delta_artifact_digest"] = "0" * 64
    conn.execute(
        "UPDATE single_track_v3_outbox SET payload_json=? WHERE outbox_id=?",
        (json.dumps(payload, sort_keys=True), outbox_id),
    )

    with pytest.raises(ValueError, match="conflicts"):
        produce_zero_gpu_outbox_artifact(
            conn,
            outbox_id,
            produced_at="2026-09-02T07:08:01+08:00",
        )

    after = canonical_analysis_artifact(conn, "analysis-before-event-delta")
    assert after is not None
    assert after["validity"] == before["validity"] == "valid"
    assert artifact_production_receipt(conn, outbox_id) is None
    assert conn.execute(
        "SELECT status FROM single_track_v3_outbox WHERE outbox_id=?",
        (outbox_id,),
    ).fetchone()[0] == "pending"


def test_producer_module_has_no_model_adapter_or_service_import_boundary() -> None:
    source_path = (
        REVIEW_SRC
        / "repository"
        / "single_track_v3_artifact_production_repository.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    assert not any(name.startswith("adapter") for name in imported_modules)
    assert not any(name.startswith("services") for name in imported_modules)
    assert not any("qwen" in name.casefold() for name in imported_modules)
