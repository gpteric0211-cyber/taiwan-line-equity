from __future__ import annotations

import hashlib
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
from repository.single_track_v3_artifact_repository import (  # noqa: E402
    create_event_delta_artifact,
    event_delta_artifact,
    event_delta_artifacts_available_at,
    latest_premarket_intelligence_artifact_at,
    premarket_intelligence_artifact,
    seal_event_delta_artifact,
    seal_premarket_intelligence_artifact,
)
from repository.single_track_v3_assessment_repository import (  # noqa: E402
    create_event_cluster,
    create_event_revision,
)
from repository.single_track_v3_repository import (  # noqa: E402
    canonical_answer_hash,
    seal_canonical_analysis_artifact,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)
from repository.single_track_v3_source_snapshot_repository import (  # noqa: E402
    seal_source_snapshot_receipt,
)


TARGET_DATE = "2026-09-02"
SOURCE_POLICY = "SourceAuthorityPolicyV1"
CALENDAR_REVISION = "twse-calendar-2026-v1"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run(
    conn: sqlite3.Connection,
    run_id: str,
    slot_key: str,
    *,
    scheduled_for: str,
    cutoff_at: str,
    completed_at: str,
    status: str = "success",
) -> dict:
    coverage = {"official": "ok", "slot": slot_key}
    failures = [] if status == "success" else [{"code": "source_timeout"}]
    return create_news_retrieval_run(
        conn,
        {
            "run_id": run_id,
            "idempotency_key": f"{TARGET_DATE}:{slot_key}:{run_id}:{SOURCE_POLICY}",
            "slot_key": slot_key,
            "target_trade_date": TARGET_DATE,
            "scheduled_for": scheduled_for,
            "cutoff_at": cutoff_at,
            "started_at": scheduled_for,
            "completed_at": completed_at,
            "status": status,
            "source_policy_version": SOURCE_POLICY,
            "calendar_revision": CALENDAR_REVISION,
            "source_coverage": coverage,
            "source_failures": failures,
            "late_reason": None,
            "created_at": scheduled_for,
            "updated_at": completed_at,
        },
    )


def _revision(
    conn: sqlite3.Connection,
    revision_id: str,
    *,
    available_at: str,
    sealed_at: str,
) -> str:
    cluster_id = f"cluster-{revision_id}"
    create_event_cluster(
        conn,
        {
            "event_cluster_id": cluster_id,
            "dedup_key": f"dedup:{revision_id}",
            "event_type": "company_disclosure",
            "entity_refs": ["TWSE:2454"],
            "cluster_state": "active",
            "first_available_at": available_at,
            "last_seen_at": sealed_at,
            "created_at": sealed_at,
            "updated_at": sealed_at,
        },
    )
    create_event_revision(
        conn,
        {
            "event_revision_id": revision_id,
            "event_cluster_id": cluster_id,
            "revision_no": 1,
            "revision_digest": _sha(f"revision:{revision_id}"),
            "content_evidence_digest": _sha(f"evidence:{revision_id}"),
            "content_cutoff": available_at,
            "event_type": "company_disclosure",
            "verification_state": "verified",
            "materiality": "material",
            "key_points": [f"bounded-{revision_id}"],
            "short_excerpt": "bounded excerpt",
            "source_refs": [{"source_id": "mops", "url_hash": _sha(revision_id)}],
            "price_reaction": {},
            "supersedes_revision_id": None,
            "available_at": available_at,
            "hot_content_expires_at": "2026-09-08T00:00:00+08:00",
            "sealed_at": sealed_at,
            "created_at": sealed_at,
        },
    )
    return revision_id


def _premarket(
    conn: sqlite3.Connection,
    run: dict,
    artifact_id: str,
    event_revision_ids: list[str],
) -> dict:
    return seal_premarket_intelligence_artifact(
        conn,
        {
            "artifact_id": artifact_id,
            "run_id": run["run_id"],
            "target_trade_date": run["target_trade_date"],
            "slot_key": run["slot_key"],
            "cutoff_at": run["cutoff_at"],
            "event_revision_ids": event_revision_ids,
            "source_coverage": run["source_coverage"],
            "source_failures": run["source_failures"],
            "artifact_status": "sealed",
            "source_policy_version": run["source_policy_version"],
            "calendar_revision": run["calendar_revision"],
            "sealed_at": run["completed_at"],
            "created_at": run["completed_at"],
        },
    )


def _source_snapshot(
    conn: sqlite3.Connection,
    run: dict,
    *,
    source_key: str = "us_market_snapshot",
    scope_key: str = "us_market_taiwan_night",
    authority_tier: str = "canonical_normalized_supplemental",
    source_id: str = "YAHOO_FINANCE_CHART",
    available_at: str = "2026-09-01T18:04:00+08:00",
    sealed_at: str = "2026-09-01T18:05:30+08:00",
) -> dict:
    return seal_source_snapshot_receipt(
        conn,
        {
            "run_id": run["run_id"],
            "source_key": source_key,
            "scope_key": scope_key,
            "target_entity_id": "*",
            "target_trade_date": run["target_trade_date"],
            "cutoff_at": run["cutoff_at"],
            "source_as_of_date": "2026-09-01",
            "available_at": available_at,
            "source_id": source_id,
            "authority_tier": authority_tier,
            "source_quality": (
                "official" if authority_tier == "canonical_official" else "supplemental"
            ),
            "snapshot_status": "ok",
            "availability_reason": None,
            "payload": {
                "rows": [
                    {
                        "symbol": "TX" if authority_tier == "canonical_official" else "^GSPC",
                        "as_of": "2026-09-01",
                        "change_pct": 0.4,
                    }
                ]
            },
            "provenance": {"selection": "latest_visible_at_cutoff"},
            "row_count": 1,
            "sealed_at": sealed_at,
            "created_at": sealed_at,
        },
    )


def _base_artifact_set(conn: sqlite3.Connection) -> tuple[dict, list[str]]:
    revisions = [
        _revision(
            conn,
            "revision-1800",
            available_at="2026-09-01T18:04:00+08:00",
            sealed_at="2026-09-01T18:04:30+08:00",
        ),
        _revision(
            conn,
            "revision-2100",
            available_at="2026-09-01T21:04:00+08:00",
            sealed_at="2026-09-01T21:04:30+08:00",
        ),
        _revision(
            conn,
            "revision-0600",
            available_at="2026-09-02T06:04:00+08:00",
            sealed_at="2026-09-02T06:04:30+08:00",
        ),
        _revision(
            conn,
            "revision-final",
            available_at="2026-09-02T06:58:00+08:00",
            sealed_at="2026-09-02T06:58:30+08:00",
        ),
    ]
    run_1800 = _run(
        conn,
        "run-1800",
        "evening_1800",
        scheduled_for="2026-09-01T18:00:00+08:00",
        cutoff_at="2026-09-01T18:05:00+08:00",
        completed_at="2026-09-01T18:06:00+08:00",
    )
    _premarket(conn, run_1800, "artifact-1800", revisions[:1])
    _run(
        conn,
        "run-2100-without-artifact",
        "evening_2100",
        scheduled_for="2026-09-01T21:00:00+08:00",
        cutoff_at="2026-09-01T21:05:00+08:00",
        completed_at="2026-09-01T21:06:00+08:00",
    )
    run_0600 = _run(
        conn,
        "run-0600",
        "preopen_0600",
        scheduled_for="2026-09-02T06:00:00+08:00",
        cutoff_at="2026-09-02T06:05:00+08:00",
        completed_at="2026-09-02T06:06:00+08:00",
    )
    _premarket(conn, run_0600, "artifact-0600", revisions[:3])
    run_final = _run(
        conn,
        "run-final",
        "preopen_final_scan",
        scheduled_for="2026-09-02T06:55:00+08:00",
        cutoff_at="2026-09-02T06:59:00+08:00",
        completed_at="2026-09-02T06:59:30+08:00",
    )
    return _premarket(conn, run_final, "artifact-final", revisions), revisions


def _canonical_artifact() -> dict:
    answer = "聯發科（2454）事件前封存分析。"
    return {
        "analysis_id": "analysis-before-late-event",
        "snapshot_id": "snapshot-before-late-event",
        "snapshot_digest": _sha("snapshot-before-late-event"),
        "request_received_at": "2026-09-02T06:58:00+08:00",
        "analysis_cutoff": "2026-09-02T06:58:00+08:00",
        "snapshot_sealed_at": "2026-09-02T06:58:01+08:00",
        "context_digest": _sha("context-before-late-event"),
        "component_snapshot_ids": [],
        "event_watermark": None,
        "source_policy_version": SOURCE_POLICY,
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
        "created_at": "2026-09-02T06:58:01+08:00",
    }


def test_artifact_schema_is_additive_versioned_and_rollback_safe() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE legacy_market_table(id INTEGER PRIMARY KEY)")

    ensure_single_track_v3_schema(conn)

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"premarket_intelligence_artifact", "event_delta_artifact"} <= tables
    assert {name for name in tables if "analysis_artifact" in name} == {
        "canonical_analysis_artifact"
    }
    assert conn.execute(
        "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()[0] == SINGLE_TRACK_V3_SCHEMA_VERSION
    assert SINGLE_TRACK_V3_SCHEMA_VERSION == "single-track-v3-v11-stage1.1"

    rollback_single_track_v3_schema(conn, allow_destructive=True)
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='legacy_market_table'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' "
        "AND name IN ('premarket_intelligence_artifact','event_delta_artifact')"
    ).fetchone()[0] == 0


def test_premarket_seal_is_point_in_time_immutable_and_idempotent() -> None:
    conn = _connection()
    included = _revision(
        conn,
        "revision-included",
        available_at="2026-09-01T18:04:00+08:00",
        sealed_at="2026-09-01T18:04:30+08:00",
    )
    extra = _revision(
        conn,
        "revision-extra",
        available_at="2026-09-01T18:04:10+08:00",
        sealed_at="2026-09-01T18:04:40+08:00",
    )
    too_late = _revision(
        conn,
        "revision-too-late",
        available_at="2026-09-01T18:05:01+08:00",
        sealed_at="2026-09-01T18:05:30+08:00",
    )
    run = _run(
        conn,
        "run-seal",
        "evening_1800",
        scheduled_for="2026-09-01T18:00:00+08:00",
        cutoff_at="2026-09-01T18:05:00+08:00",
        completed_at="2026-09-01T18:06:00+08:00",
    )
    artifact = {
        "artifact_id": "artifact-seal",
        "run_id": run["run_id"],
        "target_trade_date": TARGET_DATE,
        "slot_key": "evening_1800",
        "cutoff_at": run["cutoff_at"],
        "event_revision_ids": [included],
        "source_coverage": run["source_coverage"],
        "source_failures": run["source_failures"],
        "artifact_status": "sealed",
        "source_policy_version": SOURCE_POLICY,
        "calendar_revision": CALENDAR_REVISION,
        "sealed_at": run["completed_at"],
        "created_at": run["completed_at"],
    }

    saved = seal_premarket_intelligence_artifact(conn, artifact)
    assert seal_premarket_intelligence_artifact(conn, artifact) == saved
    assert saved["event_watermark"] == "2026-09-01T18:04:00+08:00"
    assert len(saved["artifact_digest"]) == 64
    with pytest.raises(ValueError, match="different content"):
        seal_premarket_intelligence_artifact(
            conn,
            {**artifact, "event_revision_ids": [included, extra]},
        )
    with pytest.raises(ValueError, match="not available at the artifact cutoff"):
        seal_premarket_intelligence_artifact(
            conn,
            {
                **artifact,
                "artifact_id": "artifact-late",
                "event_revision_ids": [too_late],
            },
        )
    with pytest.raises(ValueError, match="four scheduled slots"):
        seal_premarket_intelligence_artifact(
            conn,
            {**artifact, "slot_key": "high_signal_sentinel_15m"},
        )


def test_premarket_artifact_binds_only_source_snapshots_visible_for_its_run() -> None:
    conn = _connection()
    run = _run(
        conn,
        "run-source-snapshots",
        "evening_1800",
        scheduled_for="2026-09-01T18:00:00+08:00",
        cutoff_at="2026-09-01T18:05:00+08:00",
        completed_at="2026-09-01T18:06:00+08:00",
    )
    us_market = _source_snapshot(conn, run)
    sealed_too_late = _source_snapshot(
        conn,
        run,
        source_key="taifex_night_snapshot",
        authority_tier="canonical_official",
        source_id="TAIFEX_DAILY_MARKET_REPORT_FUT",
        sealed_at="2026-09-01T18:07:00+08:00",
    )
    artifact = {
        "artifact_id": "artifact-source-snapshots",
        "run_id": run["run_id"],
        "target_trade_date": TARGET_DATE,
        "slot_key": "evening_1800",
        "cutoff_at": run["cutoff_at"],
        "event_revision_ids": [],
        "source_snapshot_ids": [us_market["snapshot_id"]],
        "source_coverage": run["source_coverage"],
        "source_failures": run["source_failures"],
        "artifact_status": "sealed",
        "source_policy_version": SOURCE_POLICY,
        "calendar_revision": CALENDAR_REVISION,
        "sealed_at": run["completed_at"],
        "created_at": run["completed_at"],
    }

    saved = seal_premarket_intelligence_artifact(conn, artifact)

    assert saved["source_snapshot_ids"] == [us_market["snapshot_id"]]
    assert seal_premarket_intelligence_artifact(conn, artifact) == saved
    with pytest.raises(ValueError, match="different content"):
        seal_premarket_intelligence_artifact(
            conn,
            {**artifact, "source_snapshot_ids": []},
        )
    with pytest.raises(ValueError, match="was not sealed"):
        seal_premarket_intelligence_artifact(
            conn,
            {
                **artifact,
                "artifact_id": "artifact-too-late-source-snapshot",
                "source_snapshot_ids": [sealed_too_late["snapshot_id"]],
            },
        )


def test_premarket_selector_falls_back_only_within_the_same_target_date() -> None:
    conn = _connection()
    _base_artifact_set(conn)
    changes_before = conn.total_changes

    assert latest_premarket_intelligence_artifact_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-01T17:59:00+08:00",
    ) is None
    at_2000 = latest_premarket_intelligence_artifact_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-01T20:00:00+08:00",
    )
    at_2200 = latest_premarket_intelligence_artifact_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-01T22:00:00+08:00",
    )
    at_0630 = latest_premarket_intelligence_artifact_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-02T06:30:00+08:00",
    )
    at_0700 = latest_premarket_intelligence_artifact_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-02T07:00:00+08:00",
    )

    assert at_2000 and at_2000["slot_key"] == "evening_1800"
    assert at_2200 and at_2200["slot_key"] == "evening_1800"
    assert at_0630 and at_0630["slot_key"] == "preopen_0600"
    assert at_0700 and at_0700["slot_key"] == "preopen_final_scan"
    assert latest_premarket_intelligence_artifact_at(
        conn,
        target_trade_date="2026-09-03",
        analysis_cutoff="2026-09-03T07:00:00+08:00",
    ) is None
    assert conn.total_changes == changes_before


def test_post_final_event_delta_does_not_mutate_canonical_or_premarket_artifacts() -> None:
    conn = _connection()
    base, _ = _base_artifact_set(conn)
    seal_canonical_analysis_artifact(conn, _canonical_artifact())
    canonical_before = dict(
        conn.execute(
            "SELECT * FROM canonical_analysis_artifact "
            "WHERE analysis_id='analysis-before-late-event'"
        ).fetchone()
    )
    premarket_before = dict(
        conn.execute(
            "SELECT * FROM premarket_intelligence_artifact WHERE artifact_id='artifact-final'"
        ).fetchone()
    )
    late_revision = _revision(
        conn,
        "revision-post-final",
        available_at="2026-09-02T07:05:00+08:00",
        sealed_at="2026-09-02T07:05:30+08:00",
    )
    run = _run(
        conn,
        "run-sentinel",
        "high_signal_sentinel_15m",
        scheduled_for="2026-09-02T07:00:00+08:00",
        cutoff_at="2026-09-02T07:06:00+08:00",
        completed_at="2026-09-02T07:07:00+08:00",
    )
    delta_input = {
        "delta_artifact_id": "delta-post-final",
        "run_id": run["run_id"],
        "target_trade_date": TARGET_DATE,
        "base_premarket_artifact_id": base["artifact_id"],
        "cutoff_at": run["cutoff_at"],
        "detected_at": "2026-09-02T07:06:00+08:00",
        "event_revision_ids": [late_revision],
        "invalidated_analysis_ids": ["analysis-before-late-event"],
        "source_coverage": run["source_coverage"],
        "delta_status": "pending",
        "source_policy_version": SOURCE_POLICY,
        "calendar_revision": CALENDAR_REVISION,
        "artifact_digest": None,
        "sealed_at": None,
        "created_at": "2026-09-02T07:07:00+08:00",
        "updated_at": "2026-09-02T07:07:00+08:00",
    }

    pending = create_event_delta_artifact(conn, delta_input)
    assert pending["delta_status"] == "pending"
    sealed = seal_event_delta_artifact(
        conn,
        pending["delta_artifact_id"],
        expected_status="pending",
        new_status="sealed",
        sealed_at="2026-09-02T07:08:00+08:00",
        updated_at="2026-09-02T07:08:00+08:00",
    )
    assert sealed["event_revision_ids"] == [late_revision]
    assert len(sealed["artifact_digest"]) == 64
    assert create_event_delta_artifact(conn, delta_input) == sealed
    assert seal_event_delta_artifact(
        conn,
        pending["delta_artifact_id"],
        expected_status="pending",
        new_status="sealed",
        sealed_at="2026-09-02T07:08:00+08:00",
        updated_at="2026-09-02T07:08:00+08:00",
    ) == sealed
    with pytest.raises(ValueError, match="already terminal"):
        seal_event_delta_artifact(
            conn,
            pending["delta_artifact_id"],
            expected_status="pending",
            new_status="partial",
            sealed_at="2026-09-02T07:08:00+08:00",
            updated_at="2026-09-02T07:08:00+08:00",
        )

    canonical_after = dict(
        conn.execute(
            "SELECT * FROM canonical_analysis_artifact "
            "WHERE analysis_id='analysis-before-late-event'"
        ).fetchone()
    )
    premarket_after = dict(
        conn.execute(
            "SELECT * FROM premarket_intelligence_artifact WHERE artifact_id='artifact-final'"
        ).fetchone()
    )
    assert canonical_after == canonical_before
    assert premarket_after == premarket_before
    assert premarket_intelligence_artifact(conn, "artifact-final") == base
    assert event_delta_artifact(conn, "delta-post-final") == sealed


def test_event_delta_reader_reconstructs_pending_point_in_time_without_writes() -> None:
    conn = _connection()
    base, _ = _base_artifact_set(conn)
    late_revision = _revision(
        conn,
        "revision-pit-delta",
        available_at="2026-09-02T07:05:00+08:00",
        sealed_at="2026-09-02T07:05:30+08:00",
    )
    delta = create_event_delta_artifact(
        conn,
        {
            "delta_artifact_id": "delta-pit",
            "run_id": None,
            "target_trade_date": TARGET_DATE,
            "base_premarket_artifact_id": base["artifact_id"],
            "cutoff_at": "2026-09-02T07:06:00+08:00",
            "detected_at": "2026-09-02T07:06:00+08:00",
            "event_revision_ids": [late_revision],
            "invalidated_analysis_ids": [],
            "source_coverage": {"official": "ok"},
            "delta_status": "pending",
            "source_policy_version": SOURCE_POLICY,
            "calendar_revision": CALENDAR_REVISION,
            "created_at": "2026-09-02T07:07:00+08:00",
            "updated_at": "2026-09-02T07:07:00+08:00",
        },
    )
    seal_event_delta_artifact(
        conn,
        delta["delta_artifact_id"],
        expected_status="pending",
        new_status="partial",
        sealed_at="2026-09-02T07:08:00+08:00",
        updated_at="2026-09-02T07:08:00+08:00",
    )
    changes_before = conn.total_changes

    assert event_delta_artifacts_available_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-02T07:06:59+08:00",
    ) == []
    before_seal = event_delta_artifacts_available_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-02T07:07:30+08:00",
    )
    after_seal = event_delta_artifacts_available_at(
        conn,
        target_trade_date=TARGET_DATE,
        analysis_cutoff="2026-09-02T07:08:00+08:00",
    )
    assert before_seal[0]["delta_status"] == "pending"
    assert before_seal[0]["artifact_digest"] is None
    assert after_seal[0]["delta_status"] == "partial"
    assert len(after_seal[0]["artifact_digest"]) == 64
    assert event_delta_artifacts_available_at(
        conn,
        target_trade_date="2026-09-03",
        analysis_cutoff="2026-09-03T07:08:00+08:00",
    ) == []
    assert conn.total_changes == changes_before


def test_event_delta_rejects_pre_base_and_after_cutoff_evidence() -> None:
    conn = _connection()
    base, revisions = _base_artifact_set(conn)
    after_cutoff = _revision(
        conn,
        "revision-after-delta-cutoff",
        available_at="2026-09-02T07:07:00+08:00",
        sealed_at="2026-09-02T07:07:30+08:00",
    )
    template = {
        "delta_artifact_id": "delta-invalid",
        "run_id": None,
        "target_trade_date": TARGET_DATE,
        "base_premarket_artifact_id": base["artifact_id"],
        "cutoff_at": "2026-09-02T07:06:00+08:00",
        "detected_at": "2026-09-02T07:08:00+08:00",
        "invalidated_analysis_ids": [],
        "source_coverage": {"official": "ok"},
        "delta_status": "pending",
        "source_policy_version": SOURCE_POLICY,
        "calendar_revision": CALENDAR_REVISION,
        "created_at": "2026-09-02T07:08:00+08:00",
        "updated_at": "2026-09-02T07:08:00+08:00",
    }
    with pytest.raises(ValueError, match="post-base"):
        create_event_delta_artifact(
            conn,
            {**template, "event_revision_ids": [revisions[-1]]},
        )
    with pytest.raises(ValueError, match="not available at the artifact cutoff"):
        create_event_delta_artifact(
            conn,
            {
                **template,
                "delta_artifact_id": "delta-after-cutoff",
                "event_revision_ids": [after_cutoff],
            },
        )
