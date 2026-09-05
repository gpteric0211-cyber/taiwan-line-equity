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
from repository import (  # noqa: E402
    single_track_v3_content_assessment_state_repository as content_state_repository,
    single_track_v3_target_assessment_state_repository as target_state_repository,
)
from repository.single_track_v3_assessment_repository import (  # noqa: E402
    begin_content_model_dispatch,
    begin_target_model_dispatch,
    build_content_assessment_key,
    create_content_assessment,
    create_event_cluster,
    create_event_revision,
    create_target_impact_assessment,
    reconcile_waiting_on_terminal_content,
    record_content_attempt_failure,
    record_target_attempt_failure,
    seal_content_assessment_result,
    seal_target_impact_assessment_result,
    terminalize_expired_content_assessment,
    terminalize_expired_target_assessment,
)


_TPE = ZoneInfo("Asia/Taipei")
_BASE_TIME = datetime.now(_TPE).replace(microsecond=0)

@pytest.fixture(autouse=True)
def refresh_fixture_clock(monkeypatch):
    # Another test's duration must not consume this test's recovery deadlines.
    monkeypatch.setattr(sys.modules[__name__], "_BASE_TIME", datetime.now(_TPE).replace(microsecond=0))


def _at(*, minutes: int = 0, days: int = 0, seconds: int = 0) -> str:
    return (_BASE_TIME + timedelta(days=days, minutes=minutes, seconds=seconds)).isoformat(
        timespec="seconds"
    )


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _create_event(conn: sqlite3.Connection) -> None:
    create_event_cluster(
        conn,
        {
            "event_cluster_id": "cluster-1",
            "dedup_key": "issuer:2330:event:20260901",
            "event_type": "corporate_announcement",
            "entity_refs": ["TWSE:2330"],
            "cluster_state": "active",
            "first_available_at": _at(minutes=-20),
            "last_seen_at": _at(minutes=-15),
            "created_at": _at(minutes=-20),
            "updated_at": _at(minutes=-15),
        },
    )
    create_event_revision(
        conn,
        {
            "event_revision_id": "revision-1",
            "event_cluster_id": "cluster-1",
            "revision_no": 1,
            "revision_digest": "a" * 64,
            "content_evidence_digest": "b" * 64,
            "content_cutoff": _at(minutes=-10),
            "event_type": "corporate_announcement",
            "verification_state": "verified",
            "materiality": "material",
            "key_points": ["board-approved capacity expansion"],
            "short_excerpt": "bounded licensed excerpt",
            "source_refs": [{"source_id": "mops", "url_hash": "c" * 64}],
            "price_reaction": {},
            "supersedes_revision_id": None,
            "available_at": _at(minutes=-20),
            "hot_content_expires_at": _at(minutes=-20, days=7),
            "sealed_at": _at(minutes=-14),
            "created_at": _at(minutes=-14),
        },
    )


def _content() -> dict:
    return {
        "content_assessment_id": "content-1",
        "event_revision_id": "revision-1",
        "content_evidence_digest": "b" * 64,
        "content_cutoff": _at(minutes=-10),
        "model_digest": "d" * 64,
        "prompt_version": "ContentPromptV1",
        "schema_version": "ContentSchemaV1",
        "validator_version": "ContentValidatorV1",
        "contract_version": "ContentAssessmentContractV1",
        "content_generation": 1,
        "content_eligible_at": _at(minutes=-5),
        "content_recovery_deadline_at": _at(minutes=60),
        "created_at": _at(minutes=-5),
        "updated_at": _at(minutes=-5),
    }


def _content_result() -> dict:
    return {
        "event_facts": ["capacity expansion was approved"],
        "verification_state": "verified",
        "event_type": "corporate_announcement",
        "materiality": "material",
        "transmission_paths": ["future capacity"],
        "counterevidence": ["execution timing remains uncertain"],
        "uncertainty": ["future demand"],
        "evidence_ids": ["revision-1"],
    }


def _sealed_content(conn: sqlite3.Connection) -> tuple[dict, str]:
    _create_event(conn)
    content = create_content_assessment(conn, _content())
    attempt = begin_content_model_dispatch(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id="content-attempt-1",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-2),
    )
    assert attempt["attempt_hard_deadline_at"] == _at(minutes=3)
    result_digest = seal_content_assessment_result(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        result=_content_result(),
        completed_at=_at(minutes=-1),
    )
    return content, result_digest


def test_split_state_repositories_preserve_the_legacy_import_contract() -> None:
    assert begin_content_model_dispatch is content_state_repository.begin_content_model_dispatch
    assert seal_content_assessment_result is content_state_repository.seal_content_assessment_result
    assert begin_target_model_dispatch is target_state_repository.begin_target_model_dispatch
    assert (
        seal_target_impact_assessment_result
        is target_state_repository.seal_target_impact_assessment_result
    )


def _target(target_entity_id: str, *, target_id: str) -> dict:
    return {
        "target_impact_assessment_id": target_id,
        "content_assessment_id": "content-1",
        "target_entity_id": target_entity_id,
        "target_trade_date": (_BASE_TIME.date() + timedelta(days=1)).isoformat(),
        "forecast_target_id": "NEXT_SESSION_CLOSE_DIRECTION",
        "target_fact_snapshot_id": f"snapshot-{target_entity_id}",
        "target_fact_snapshot_digest": "e" * 64,
        "target_context_digest": "f" * 64,
        "model_digest": "1" * 64,
        "prompt_version": "TargetPromptV1",
        "schema_version": "TargetSchemaV1",
        "validator_version": "TargetValidatorV1",
        "contract_version": "TargetImpactContractV1",
        "calibrator_version": "unreleased-shadow-v1",
        "target_generation": 1,
        "resolution_eligible_at": _at(minutes=-58),
        "prediction_issue_deadline_at": _at(minutes=120),
        "created_at": _at(minutes=-58),
        "updated_at": _at(minutes=-58),
    }


def _target_result(*, eligible_for_weight: bool = False) -> dict:
    result = {
        "target_materiality": "high",
        "target_direction": "positive",
        "target_impact_magnitude": "medium",
        "regime_selection": "material_event",
        "target_relationship_type": "direct_company",
        "drivers": ["verified event and exposure"],
        "counterevidence": ["priced-in uncertainty"],
        "uncertainty": ["execution timing"],
        "priced_in_state": "still_developing",
        "evidence_ids": ["revision-1", "snapshot-TWSE:2330"],
        "eligible_for_explanation": True,
        "eligible_for_weight": eligible_for_weight,
        "calibration_state": "calibrated" if eligible_for_weight else "shadow",
    }
    if eligible_for_weight:
        result["direction_probabilities"] = {
            "positive": 0.55,
            "neutral": 0.30,
            "negative": 0.15,
        }
        result["magnitude_probabilities"] = {
            "negligible": 0.20,
            "low": 0.35,
            "medium": 0.30,
            "high": 0.10,
            "extreme": 0.05,
        }
    return result


def test_assessment_schema_is_additive_versioned_and_body_free() -> None:
    conn = _connection()
    conn.execute("CREATE TABLE legacy_market_table(id INTEGER PRIMARY KEY)")
    ensure_single_track_v3_schema(conn)

    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "event_cluster",
        "event_revision",
        "content_assessment",
        "content_assessment_attempt",
        "target_impact_assessment",
        "target_impact_assessment_attempt",
    } <= tables
    assert "legacy_market_table" in tables
    assert conn.execute(
        "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()[0] == SINGLE_TRACK_V3_SCHEMA_VERSION
    revision_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(event_revision)").fetchall()
    }
    assert "article_body" not in revision_columns
    assert "raw_article_body" not in revision_columns


def test_event_revision_rejects_raw_body_and_overlong_retention() -> None:
    conn = _connection()
    cluster = {
        "event_cluster_id": "cluster-1",
        "dedup_key": "issuer:2330:event:20260901",
        "event_type": "corporate_announcement",
        "entity_refs": ["TWSE:2330"],
        "cluster_state": "active",
        "first_available_at": _at(minutes=-20),
        "last_seen_at": _at(minutes=-15),
        "created_at": _at(minutes=-20),
        "updated_at": _at(minutes=-15),
    }
    create_event_cluster(conn, cluster)
    revision = {
        "event_revision_id": "revision-1",
        "event_cluster_id": "cluster-1",
        "revision_no": 1,
        "revision_digest": "a" * 64,
        "content_evidence_digest": "b" * 64,
        "content_cutoff": _at(minutes=-10),
        "event_type": "corporate_announcement",
        "verification_state": "verified",
        "materiality": "material",
        "key_points": [],
        "source_refs": [],
        "price_reaction": {},
        "available_at": _at(minutes=-20),
        "hot_content_expires_at": _at(minutes=-20, days=7),
        "sealed_at": _at(minutes=-14),
        "created_at": _at(minutes=-14),
    }
    with pytest.raises(ValueError, match="retention is prohibited"):
        create_event_revision(conn, {**revision, "article_body": "must not persist"})
    with pytest.raises(ValueError, match="retention is prohibited"):
        create_event_revision(
            conn,
            {**revision, "source_refs": [{"source_id": "mops", "full_text": "no"}]},
        )
    with pytest.raises(ValueError, match="0 and 7 calendar days"):
        create_event_revision(
            conn,
            {**revision, "hot_content_expires_at": _at(minutes=-20, days=7, seconds=1)},
        )


def test_content_key_is_target_independent_and_insert_is_idempotent() -> None:
    conn = _connection()
    _create_event(conn)
    created = create_content_assessment(conn, _content())
    same = create_content_assessment(conn, _content())

    assert same == created
    assert created["content_assessment_key"] == build_content_assessment_key(
        event_revision_id="revision-1",
        content_evidence_digest="b" * 64,
        content_cutoff=_at(minutes=-10),
        model_digest="d" * 64,
        prompt_version="ContentPromptV1",
        schema_version="ContentSchemaV1",
        validator_version="ContentValidatorV1",
        contract_version="ContentAssessmentContractV1",
    )
    assert created["attempt_count"] == 0
    conflicting = _content()
    conflicting["content_assessment_id"] = "content-conflict"
    with pytest.raises(ValueError, match="different content"):
        create_content_assessment(conn, conflicting)


def test_content_attempt_begins_only_at_dispatch_and_seal_rejects_prohibited_fields() -> None:
    conn = _connection()
    _create_event(conn)
    content = create_content_assessment(conn, _content())
    assert conn.execute("SELECT count(*) FROM content_assessment_attempt").fetchone()[0] == 0

    attempt = begin_content_model_dispatch(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id="content-attempt-1",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-2),
    )
    assert begin_content_model_dispatch(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id="content-attempt-1",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-2),
    ) == attempt
    assert conn.execute(
        "SELECT attempt_count FROM content_assessment WHERE content_assessment_id='content-1'"
    ).fetchone()[0] == 1
    with pytest.raises(ValueError, match="prohibited field"):
        seal_content_assessment_result(
            conn,
            content_assessment_id="content-1",
            attempt_id="content-attempt-1",
            expected_state_version=1,
            result={**_content_result(), "stock_code": "2330"},
            completed_at=_at(minutes=-1),
        )
    with pytest.raises(ValueError, match="prohibited field"):
        seal_content_assessment_result(
            conn,
            content_assessment_id="content-1",
            attempt_id="content-attempt-1",
            expected_state_version=1,
            result={**_content_result(), "raw_article_body": "must not be retained"},
            completed_at=_at(minutes=-1),
        )
    with pytest.raises(ValueError, match="unknown evidence"):
        seal_content_assessment_result(
            conn,
            content_assessment_id="content-1",
            attempt_id="content-attempt-1",
            expected_state_version=1,
            result={**_content_result(), "evidence_ids": ["not-in-packet"]},
            completed_at=_at(minutes=-1),
        )
    with pytest.raises(ValueError, match="prohibited field"):
        seal_content_assessment_result(
            conn,
            content_assessment_id="content-1",
            attempt_id="content-attempt-1",
            expected_state_version=1,
            result={**_content_result(), "uncertainty": [{"stock_code": "2330"}]},
            completed_at=_at(minutes=-1),
        )


def test_one_sealed_content_can_create_independent_targets_without_rerun() -> None:
    conn = _connection()
    _, content_result_digest = _sealed_content(conn)

    targets = [
        create_target_impact_assessment(
            conn,
            _target(entity, target_id=f"target-{entity}"),
        )
        for entity in ("TWSE:2330", "TWSE:2303", "TWSE:2454")
    ]

    assert len({row["target_impact_key"] for row in targets}) == 3
    assert {row["content_result_digest"] for row in targets} == {content_result_digest}
    assert conn.execute("SELECT count(*) FROM content_assessment").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM content_assessment_attempt").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM target_impact_assessment").fetchone()[0] == 3
    assert create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-TWSE:2330"),
    ) == targets[0]


def test_weight_version_is_part_of_target_identity() -> None:
    conn = _connection()
    _sealed_content(conn)
    first = create_target_impact_assessment(
        conn,
        {**_target("TWSE:2330", target_id="target-weight-a"), "weight_version": "candidate-a"},
    )
    second = create_target_impact_assessment(
        conn,
        {
            **_target("TWSE:2330", target_id="target-weight-b"),
            "weight_version": "candidate-b",
            "target_generation": 2,
        },
    )

    assert first["target_impact_key"] != second["target_impact_key"]
    assert conn.execute("SELECT count(*) FROM target_impact_assessment").fetchone()[0] == 2


def test_target_dispatch_clamps_hard_deadline_and_attempts_are_bounded() -> None:
    conn = _connection()
    _sealed_content(conn)
    target = create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-2330"),
    )
    assert target["resolution_start_deadline_at"] == _at(minutes=-48)
    assert target["resolution_deadline_at"] == _at(minutes=-38)
    assert target["target_recovery_deadline_at"] == _at(minutes=2)
    assert target["target_attempt_count"] == 0

    attempt = begin_target_model_dispatch(
        conn,
        target_impact_assessment_id="target-2330",
        attempt_id="target-attempt-1",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-1),
    )
    assert attempt["attempt_hard_deadline_at"] == _at(minutes=2)
    assert conn.execute(
        "SELECT target_attempt_count FROM target_impact_assessment "
        "WHERE target_impact_assessment_id='target-2330'"
    ).fetchone()[0] == 1

    for attempt_no in (2, 3, 4):
        conn.execute(
            """
            UPDATE target_impact_assessment
            SET assessment_status='retry_wait',active_attempt_id=NULL,
                state_version=state_version+1
            WHERE target_impact_assessment_id='target-2330'
            """
        )
        begin_target_model_dispatch(
            conn,
            target_impact_assessment_id="target-2330",
            attempt_id=f"target-attempt-{attempt_no}",
            attempt_kind="retry",
            dispatch_started_at=_at(minutes=-1),
        )
    conn.execute(
        """
        UPDATE target_impact_assessment
        SET assessment_status='retry_wait',active_attempt_id=NULL,
            state_version=state_version+1
        WHERE target_impact_assessment_id='target-2330'
        """
    )
    with pytest.raises(ValueError, match="attempts exhausted"):
        begin_target_model_dispatch(
            conn,
            target_impact_assessment_id="target-2330",
            attempt_id="target-attempt-5",
            attempt_kind="retry",
            dispatch_started_at=_at(minutes=-1),
        )
    assert conn.execute(
        "SELECT count(*) FROM target_impact_assessment_attempt "
        "WHERE target_impact_assessment_id='target-2330'"
    ).fetchone()[0] == 4


def test_target_waits_without_attempt_before_content_is_sealed() -> None:
    conn = _connection()
    _create_event(conn)
    create_content_assessment(conn, _content())
    target = create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-2330"),
    )
    assert target["assessment_status"] == "waiting_for_content"
    assert target["content_result_digest"] is None
    assert target["target_attempt_count"] == 0
    assert conn.execute("SELECT count(*) FROM target_impact_assessment_attempt").fetchone()[0] == 0


def test_content_success_wins_cas_and_activates_waiting_targets() -> None:
    conn = _connection()
    _create_event(conn)
    content = create_content_assessment(conn, _content())
    waiting = create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-2330"),
    )
    waiting_key = waiting["target_impact_key"]
    attempt = begin_content_model_dispatch(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id="content-attempt-success",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-2),
    )
    digest = seal_content_assessment_result(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        result=_content_result(),
        completed_at=_at(minutes=-1),
    )

    target = dict(
        conn.execute(
            "SELECT * FROM target_impact_assessment WHERE target_impact_assessment_id='target-2330'"
        ).fetchone()
    )
    assert target["assessment_status"] == "queued"
    assert target["content_result_digest"] == digest
    assert target["target_impact_key"] != waiting_key
    assert target["target_attempt_count"] == 0
    losing_terminal = record_content_attempt_failure(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        failure_class="permanent",
        failure_code="late_worker_failure",
    )
    assert losing_terminal["applied"] is False
    assert losing_terminal["assessment_status"] == "complete"
    assert conn.execute(
        "SELECT count(*) FROM single_track_v3_outbox WHERE event_type='CONTENT_TERMINAL_CASCADE'"
    ).fetchone()[0] == 0


def test_terminal_wins_cas_late_success_is_stale_and_replay_is_idempotent() -> None:
    conn = _connection()
    _create_event(conn)
    content = create_content_assessment(conn, _content())
    for entity in ("TWSE:2330", "TWSE:2303", "TWSE:2454"):
        create_target_impact_assessment(
            conn,
            _target(entity, target_id=f"target-{entity}"),
        )
    attempt = begin_content_model_dispatch(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id="content-attempt-terminal",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-2),
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
    assert terminal["applied"] is True
    assert terminal["blocked_target_count"] == 3
    assert terminal["assessment_status"] == "failed_terminal"
    assert conn.execute(
        "SELECT count(*) FROM target_impact_assessment "
        "WHERE assessment_status='blocked_dependency_terminal' AND target_attempt_count=0"
    ).fetchone()[0] == 3
    assert conn.execute(
        "SELECT count(*) FROM single_track_v3_outbox WHERE event_type='CONTENT_TERMINAL_CASCADE'"
    ).fetchone()[0] == 1

    assert seal_content_assessment_result(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        result=_content_result(),
        completed_at=_at(minutes=-1),
    ) is None
    assert conn.execute(
        "SELECT attempt_status FROM content_assessment_attempt WHERE attempt_id=?",
        (attempt["attempt_id"],),
    ).fetchone()[0] == "stale_completion"
    replay = record_content_attempt_failure(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        failure_class="permanent",
        failure_code="invalid_contract_permanent",
    )
    assert replay["applied"] is False
    assert conn.execute(
        "SELECT count(*) FROM single_track_v3_outbox WHERE event_type='CONTENT_TERMINAL_CASCADE'"
    ).fetchone()[0] == 1

    post_terminal = create_target_impact_assessment(
        conn,
        _target("TWSE:2881", target_id="target-post-terminal"),
    )
    assert post_terminal["assessment_status"] == "blocked_dependency_terminal"
    assert post_terminal["target_attempt_count"] == 0
    conn.execute(
        """
        UPDATE target_impact_assessment
        SET assessment_status='waiting_for_content',decision_status='pending',
            terminal_reason_code=NULL,upstream_terminal_reason_code=NULL,terminal_at=NULL
        WHERE target_impact_assessment_id='target-post-terminal'
        """
    )
    reconciled = reconcile_waiting_on_terminal_content(conn)
    assert reconciled == {"repaired": 1, "remaining": 0}
    assert conn.execute(
        "SELECT count(*) FROM single_track_v3_outbox WHERE event_type='CONTENT_TERMINAL_CASCADE'"
    ).fetchone()[0] == 1


def test_fourth_content_timeout_blocks_three_targets_and_attempts_are_exact() -> None:
    conn = _connection()
    _create_event(conn)
    content_item = _content()
    content_item["content_eligible_at"] = _at(minutes=-10)
    content_item["created_at"] = _at(minutes=-10)
    content_item["updated_at"] = _at(minutes=-10)
    content = create_content_assessment(conn, content_item)
    for entity in ("TWSE:2330", "TWSE:2303", "TWSE:2454"):
        create_target_impact_assessment(
            conn,
            _target(entity, target_id=f"target-timeout-{entity}"),
        )

    for attempt_no in range(1, 5):
        if attempt_no > 1:
            conn.execute(
                "UPDATE content_assessment SET next_attempt_at=? WHERE content_assessment_id=?",
                (_at(minutes=-7), content["content_assessment_id"]),
            )
        attempt = begin_content_model_dispatch(
            conn,
            content_assessment_id=content["content_assessment_id"],
            attempt_id=f"content-timeout-{attempt_no}",
            attempt_kind="initial" if attempt_no == 1 else "retry",
            dispatch_started_at=_at(minutes=-6),
        )
        state_version = conn.execute(
            "SELECT state_version FROM content_assessment WHERE content_assessment_id=?",
            (content["content_assessment_id"],),
        ).fetchone()[0]
        transition = record_content_attempt_failure(
            conn,
            content_assessment_id=content["content_assessment_id"],
            attempt_id=attempt["attempt_id"],
            expected_state_version=state_version,
            expected_promotion_epoch=0,
            failure_class="timeout" if attempt_no == 4 else "worker_loss",
            failure_code=f"worker_failure_{attempt_no}",
        )
        assert transition["applied"] is True

    assert transition["assessment_status"] == "failed_terminal"
    assert transition["blocked_target_count"] == 3
    terminal_row = conn.execute(
        "SELECT attempt_count,promotion_epoch,terminal_reason_code "
        "FROM content_assessment WHERE content_assessment_id=?",
        (content["content_assessment_id"],),
    ).fetchone()
    assert tuple(terminal_row) == (4, 1, "content_attempts_exhausted")
    assert conn.execute("SELECT count(*) FROM content_assessment_attempt").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM target_impact_assessment_attempt").fetchone()[0] == 0


def test_target_result_seals_typed_shadow_and_single_target_failure_is_isolated() -> None:
    conn = _connection()
    _sealed_content(conn)
    primary = create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-seal-2330"),
    )
    sibling = create_target_impact_assessment(
        conn,
        _target("TWSE:2303", target_id="target-fail-2303"),
    )
    primary_attempt = begin_target_model_dispatch(
        conn,
        target_impact_assessment_id=primary["target_impact_assessment_id"],
        attempt_id="target-seal-attempt",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-1),
    )
    digest = seal_target_impact_assessment_result(
        conn,
        target_impact_assessment_id=primary["target_impact_assessment_id"],
        attempt_id=primary_attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        result=_target_result(),
        completed_at=_at(seconds=-30),
    )
    saved_primary = dict(
        conn.execute(
            "SELECT * FROM target_impact_assessment WHERE target_impact_assessment_id=?",
            (primary["target_impact_assessment_id"],),
        ).fetchone()
    )
    assert saved_primary["result_digest"] == digest
    assert saved_primary["assessment_status"] == "complete"
    assert saved_primary["decision_status"] == "explanation_only"
    assert saved_primary["eligible_for_weight"] == 0
    assert saved_primary["direction_probabilities_json"] is None
    assert saved_primary["magnitude_probabilities_json"] is None
    assert saved_primary["target_materiality"] == "high"
    assert saved_primary["target_direction"] == "positive"
    assert saved_primary["target_impact_magnitude"] == "medium"
    assert saved_primary["regime_selection"] == "material_event"

    sibling_attempt = begin_target_model_dispatch(
        conn,
        target_impact_assessment_id=sibling["target_impact_assessment_id"],
        attempt_id="target-fail-attempt",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-1),
    )
    failed = record_target_attempt_failure(
        conn,
        target_impact_assessment_id=sibling["target_impact_assessment_id"],
        attempt_id=sibling_attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        failure_class="permanent",
        failure_code="target_context_permanent",
    )
    assert failed["assessment_status"] == "failed_terminal"
    assert conn.execute(
        "SELECT assessment_status FROM content_assessment WHERE content_assessment_id='content-1'"
    ).fetchone()[0] == "complete"
    assert conn.execute(
        "SELECT assessment_status FROM target_impact_assessment "
        "WHERE target_impact_assessment_id='target-seal-2330'"
    ).fetchone()[0] == "complete"


def test_watchdog_uses_database_time_and_blocks_expired_waiters() -> None:
    conn = _connection()
    _create_event(conn)
    content = create_content_assessment(conn, _content())
    create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-watchdog"),
    )
    conn.execute(
        "UPDATE content_assessment SET content_recovery_deadline_at=? WHERE content_assessment_id=?",
        (_at(minutes=-1), content["content_assessment_id"]),
    )
    result = terminalize_expired_content_assessment(
        conn,
        content_assessment_id=content["content_assessment_id"],
        expected_state_version=0,
        expected_promotion_epoch=0,
    )
    assert result["applied"] is True
    assert result["blocked_target_count"] == 1
    assert conn.execute(
        "SELECT assessment_status FROM target_impact_assessment "
        "WHERE target_impact_assessment_id='target-watchdog'"
    ).fetchone()[0] == "blocked_dependency_terminal"


def test_target_validator_rejects_nan_price_target_and_untyped_boolean() -> None:
    conn = _connection()
    _sealed_content(conn)
    target = create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-invalid-result"),
    )
    attempt = begin_target_model_dispatch(
        conn,
        target_impact_assessment_id=target["target_impact_assessment_id"],
        attempt_id="target-invalid-attempt",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-1),
    )
    nan_result = _target_result()
    nan_result["calibration_state"] = "calibrated"
    nan_result["direction_probabilities"] = {
        "positive": float("nan"),
        "neutral": 0.5,
        "negative": 0.5,
    }
    nan_result["magnitude_probabilities"] = {
        "negligible": 0.20,
        "low": 0.35,
        "medium": 0.30,
        "high": 0.10,
        "extreme": 0.05,
    }
    with pytest.raises(ValueError, match="between 0 and 1"):
        seal_target_impact_assessment_result(
            conn,
            target_impact_assessment_id=target["target_impact_assessment_id"],
            attempt_id=attempt["attempt_id"],
            expected_state_version=1,
            result=nan_result,
            completed_at=_at(seconds=-30),
        )
    with pytest.raises(ValueError, match="unreleased Qwen target result"):
        seal_target_impact_assessment_result(
            conn,
            target_impact_assessment_id=target["target_impact_assessment_id"],
            attempt_id=attempt["attempt_id"],
            expected_state_version=1,
            result={
                **_target_result(),
                "direction_probabilities": {
                    "positive": 0.5,
                    "neutral": 0.3,
                    "negative": 0.2,
                },
            },
            completed_at=_at(seconds=-30),
        )
    with pytest.raises(ValueError, match="prohibited price/return field"):
        seal_target_impact_assessment_result(
            conn,
            target_impact_assessment_id=target["target_impact_assessment_id"],
            attempt_id=attempt["attempt_id"],
            expected_state_version=1,
            result={**_target_result(), "price_target": 999},
            completed_at=_at(seconds=-30),
        )
    with pytest.raises(ValueError, match="must be a boolean"):
        seal_target_impact_assessment_result(
            conn,
            target_impact_assessment_id=target["target_impact_assessment_id"],
            attempt_id=attempt["attempt_id"],
            expected_state_version=1,
            result={**_target_result(), "eligible_for_weight": "false"},
            completed_at=_at(seconds=-30),
        )


def test_late_target_completion_consumes_attempt_and_cannot_promote() -> None:
    conn = _connection()
    _sealed_content(conn)
    target = create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-late"),
    )
    attempt = begin_target_model_dispatch(
        conn,
        target_impact_assessment_id=target["target_impact_assessment_id"],
        attempt_id="target-late-attempt",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-6),
    )
    assert attempt["attempt_hard_deadline_at"] == _at(minutes=-1)
    assert seal_target_impact_assessment_result(
        conn,
        target_impact_assessment_id=target["target_impact_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        result=_target_result(),
        completed_at=_at(seconds=-1),
    ) is None
    saved = dict(
        conn.execute(
            "SELECT * FROM target_impact_assessment WHERE target_impact_assessment_id=?",
            (target["target_impact_assessment_id"],),
        ).fetchone()
    )
    assert saved["assessment_status"] == "retry_wait"
    assert saved["target_attempt_count"] == 1
    assert saved["result_digest"] is None
    assert saved["eligible_for_weight"] == 0
    assert conn.execute(
        "SELECT attempt_status FROM target_impact_assessment_attempt WHERE attempt_id=?",
        (attempt["attempt_id"],),
    ).fetchone()[0] == "stale_completion"


def test_retry_delay_is_deterministic_and_cannot_be_bypassed() -> None:
    conn = _connection()
    _create_event(conn)
    content = create_content_assessment(conn, _content())
    attempt = begin_content_model_dispatch(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id="content-retry-delay-1",
        attempt_kind="initial",
        dispatch_started_at=_at(minutes=-2),
    )
    transition = record_content_attempt_failure(
        conn,
        content_assessment_id=content["content_assessment_id"],
        attempt_id=attempt["attempt_id"],
        expected_state_version=1,
        expected_promotion_epoch=0,
        failure_class="worker_loss",
        failure_code="worker_lost_after_dispatch",
    )
    failed_at = datetime.fromisoformat(
        conn.execute(
            "SELECT completed_at FROM content_assessment_attempt WHERE attempt_id=?",
            (attempt["attempt_id"],),
        ).fetchone()[0]
    )
    retry_at = datetime.fromisoformat(transition["next_attempt_at"])
    retry_seed = hashlib.sha256(
        f"{content['content_assessment_key']}:1".encode("utf-8")
    ).digest()
    expected_jitter_seconds = int.from_bytes(retry_seed[:2], "big") % 31
    assert retry_at - failed_at == timedelta(seconds=60 + expected_jitter_seconds)
    with pytest.raises(ValueError, match="retry delay has not elapsed"):
        begin_content_model_dispatch(
            conn,
            content_assessment_id=content["content_assessment_id"],
            attempt_id="content-retry-too-early",
            attempt_kind="retry",
            dispatch_started_at=_at(seconds=1),
        )


def test_target_watchdog_terminates_only_expired_target() -> None:
    conn = _connection()
    _sealed_content(conn)
    expired = create_target_impact_assessment(
        conn,
        _target("TWSE:2330", target_id="target-expired"),
    )
    sibling = create_target_impact_assessment(
        conn,
        _target("TWSE:2303", target_id="target-still-live"),
    )
    conn.execute(
        "UPDATE target_impact_assessment SET target_recovery_deadline_at=? "
        "WHERE target_impact_assessment_id=?",
        (_at(minutes=-1), expired["target_impact_assessment_id"]),
    )
    result = terminalize_expired_target_assessment(
        conn,
        target_impact_assessment_id=expired["target_impact_assessment_id"],
        expected_state_version=0,
        expected_promotion_epoch=0,
    )
    assert result == {"applied": True, "assessment_status": "failed_terminal"}
    assert conn.execute(
        "SELECT assessment_status FROM target_impact_assessment "
        "WHERE target_impact_assessment_id=?",
        (sibling["target_impact_assessment_id"],),
    ).fetchone()[0] == "queued"
    assert conn.execute(
        "SELECT assessment_status FROM content_assessment WHERE content_assessment_id='content-1'"
    ).fetchone()[0] == "complete"
