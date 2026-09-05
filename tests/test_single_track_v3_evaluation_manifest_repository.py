from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.target_label_contract_v1 import (  # noqa: E402
    TARGET_LABEL_CONTRACT_VERSION,
    target_sample_id,
)
from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    ensure_single_track_v3_schema,
    rollback_single_track_v3_schema,
)
from evaluation import statistical_release_gate_v1 as gate  # noqa: E402
from repository.single_track_v3_outcome_evaluation_manifest_repository import (  # noqa: E402
    outcome_snapshot_manifest,
    paired_release_rows_from_snapshot_manifests,
    seal_outcome_snapshot_manifest,
    seal_statistical_evaluation_manifest,
    statistical_evaluation_manifest,
)
from repository.single_track_v3_prediction_manifest_repository import (  # noqa: E402
    prediction_snapshot_manifest,
    seal_prediction_snapshot_manifest,
    seal_statistical_gate_manifest,
    statistical_gate_manifest,
)
from repository.single_track_v3_repository import (  # noqa: E402
    canonical_answer_hash,
    record_target_outcomes,
    record_target_predictions,
    seal_canonical_analysis_artifact,
)


OUTCOME_REVISION = "official-adjusted-v1"
PREDICTION_CONTRACT = "PairedPredictionSnapshotV1"
ANALYSIS_CUTOFF = "2026-09-01T13:30:00+08:00"
SAMPLE_ID = target_sample_id(
    stock_code="2454",
    prediction_trade_date="2026-09-01",
    analysis_cutoff=ANALYSIS_CUTOFF,
    regime="normal",
)
HOLDOUT_MEMBERS = [
    {"sample_id": SAMPLE_ID, "target_key": "next_close_direction"},
    {"sample_id": SAMPLE_ID, "target_key": "next_open_gap"},
]


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _evaluator_digest() -> str:
    return _sha_bytes(Path(gate.__file__).read_bytes())


def _target_label_digest() -> str:
    from analysis import target_label_contract_v1

    return _sha_bytes(Path(target_label_contract_v1.__file__).read_bytes())


def _gate_input(
    *,
    gate_manifest_id: str = "gate-holdout-v1",
    sealed_at: str = "2026-08-31T12:01:00+08:00",
) -> dict:
    return {
        "gate_manifest_id": gate_manifest_id,
        "gate_version": gate.STATISTICAL_RELEASE_GATE_VERSION,
        "gate_spec": gate.GATE_SPEC,
        "gate_spec_hash": gate.GATE_SPEC_HASH,
        "evaluator_source_digest": _evaluator_digest(),
        "target_label_contract_version": TARGET_LABEL_CONTRACT_VERSION,
        "target_label_source_digest": _target_label_digest(),
        "holdout_partition_id": "untouched-holdout-2026q3-v1",
        "bootstrap_iterations": gate.BOOTSTRAP_ITERATIONS,
        "bootstrap_seed": gate.BOOTSTRAP_SEED,
        "bootstrap_block_days": gate.BOOTSTRAP_BLOCK_DAYS,
        "created_at": "2026-08-31T12:00:00+08:00",
        "sealed_at": sealed_at,
    }


def _analysis() -> dict:
    answer = "聯發科（2454）封存預測基準。"
    return {
        "analysis_id": "analysis-manifest-2454",
        "snapshot_id": "snapshot-manifest-2454",
        "snapshot_digest": _sha_bytes(b"snapshot-manifest-2454"),
        "request_received_at": ANALYSIS_CUTOFF,
        "analysis_cutoff": ANALYSIS_CUTOFF,
        "snapshot_sealed_at": "2026-09-01T13:30:01+08:00",
        "context_digest": _sha_bytes(b"context-manifest-2454"),
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
        "created_at": "2026-09-01T13:30:01+08:00",
    }


def _prediction_rows(
    *,
    candidate_failed: bool = False,
    synthetic_role_target: tuple[str, str] | None = None,
) -> list[dict]:
    rows = []
    for model_role in ("stable", "candidate"):
        for target_key in ("next_close_direction", "next_open_gap"):
            failed = candidate_failed and model_role == "candidate" and target_key == "next_open_gap"
            rows.append(
                {
                    "sample_id": SAMPLE_ID,
                    "analysis_id": "analysis-manifest-2454",
                    "model_role": model_role,
                    "target_key": target_key,
                    "regime": "normal",
                    "probabilities": {}
                    if failed
                    else {"up": 0.6, "flat": 0.3, "down": 0.1},
                    "eligible": 0 if failed else 1,
                    "completion_state": "failed" if failed else "completed",
                    "omission_reason": "model_timeout" if failed else None,
                    "analysis_cutoff": ANALYSIS_CUTOFF,
                    "event_cluster_id": None,
                    "event_type": None,
                    "large_safety_slice": False,
                    "synthetic": synthetic_role_target == (model_role, target_key),
                    "created_at": "2026-09-01T13:31:00+08:00",
                }
            )
    return rows


def _prediction_manifest_input(
    model_role: str,
    *,
    sealed_at: str = "2026-09-01T13:59:00+08:00",
) -> dict:
    return {
        "prediction_manifest_id": f"prediction-{model_role}-v1",
        "gate_manifest_id": "gate-holdout-v1",
        "model_role": model_role,
        "prediction_contract_version": PREDICTION_CONTRACT,
        "created_at": "2026-09-01T13:58:00+08:00",
        "sealed_at": sealed_at,
    }


def _outcome_rows(*, unavailable_target: str | None = None) -> list[dict]:
    rows = []
    for target_key in ("next_close_direction", "next_open_gap"):
        unavailable = target_key == unavailable_target
        rows.append(
            {
                "sample_id": SAMPLE_ID,
                "target_key": target_key,
                "outcome_revision": OUTCOME_REVISION,
                "stock_code": "2454",
                "prediction_trade_date": "2026-09-01",
                "outcome_trade_date": None if unavailable else "2026-09-02",
                "label": None if unavailable else "up",
                "t_close": None if unavailable else 100.0,
                "next_open": None if unavailable else 101.5,
                "next_close": None if unavailable else 102.0,
                "adjustment_basis": "official_adjusted",
                "quality_status": "unavailable" if unavailable else "ok",
                "availability_reason": "next_session_suspended" if unavailable else None,
                "available_at": "2026-09-02T13:31:00+08:00",
                "recorded_at": "2026-09-02T13:32:00+08:00",
            }
        )
    return rows


def _outcome_manifest_input(
    *,
    opened_at: str = "2026-09-02T14:00:00+08:00",
) -> dict:
    return {
        "outcome_manifest_id": "outcomes-holdout-v1",
        "gate_manifest_id": "gate-holdout-v1",
        "outcome_revision": OUTCOME_REVISION,
        "target_label_contract_version": TARGET_LABEL_CONTRACT_VERSION,
        "adjustment_basis": "official_adjusted",
        "holdout_opened_at": opened_at,
        "created_at": opened_at,
        "sealed_at": "2026-09-02T14:01:00+08:00",
    }


def _prepare_snapshots(
    conn: sqlite3.Connection,
    *,
    candidate_failed: bool = False,
    unavailable_target: str | None = None,
) -> tuple[dict, dict, dict, dict]:
    sealed_gate = seal_statistical_gate_manifest(conn, _gate_input(), HOLDOUT_MEMBERS)
    seal_canonical_analysis_artifact(conn, _analysis())
    record_target_predictions(
        conn,
        _prediction_rows(candidate_failed=candidate_failed),
    )
    stable = seal_prediction_snapshot_manifest(
        conn,
        _prediction_manifest_input("stable"),
    )
    candidate = seal_prediction_snapshot_manifest(
        conn,
        _prediction_manifest_input("candidate"),
    )
    record_target_outcomes(conn, _outcome_rows(unavailable_target=unavailable_target))
    outcomes = seal_outcome_snapshot_manifest(conn, _outcome_manifest_input())
    return sealed_gate, stable, candidate, outcomes


def _evaluation_input() -> dict:
    return {
        "evaluation_manifest_id": "evaluation-holdout-v1",
        "gate_manifest_id": "gate-holdout-v1",
        "stable_prediction_manifest_id": "prediction-stable-v1",
        "candidate_prediction_manifest_id": "prediction-candidate-v1",
        "outcome_manifest_id": "outcomes-holdout-v1",
        "evaluated_at": "2026-09-02T14:02:00+08:00",
        "created_at": "2026-09-02T14:02:01+08:00",
    }


def test_evaluation_manifest_schema_is_additive_and_rollback_safe() -> None:
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
        "statistical_gate_manifest",
        "statistical_gate_holdout_member",
        "prediction_snapshot_manifest",
        "prediction_snapshot_member",
        "outcome_snapshot_manifest",
        "outcome_snapshot_member",
        "statistical_evaluation_manifest",
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
    assert not ({
        "statistical_gate_manifest",
        "prediction_snapshot_manifest",
        "outcome_snapshot_manifest",
        "statistical_evaluation_manifest",
    } & remaining)


def test_gate_manifest_is_predeclared_exact_idempotent_and_fail_closed() -> None:
    conn = _connection()
    saved = seal_statistical_gate_manifest(conn, _gate_input(), HOLDOUT_MEMBERS)

    assert seal_statistical_gate_manifest(conn, _gate_input(), HOLDOUT_MEMBERS) == saved
    assert saved["gate_spec_hash"] == gate.GATE_SPEC_HASH
    assert saved["holdout_member_count"] == 2
    assert len(saved["holdout_partition_digest"]) == 64
    altered_spec = {**gate.GATE_SPEC, "bootstrap": {**gate.GATE_SPEC["bootstrap"], "iterations": 9_999}}
    with pytest.raises(ValueError, match="exactly match"):
        seal_statistical_gate_manifest(
            conn,
            {**_gate_input(gate_manifest_id="altered-gate"), "gate_spec": altered_spec},
            HOLDOUT_MEMBERS,
        )
    with pytest.raises(ValueError, match="duplicate"):
        seal_statistical_gate_manifest(
            conn,
            _gate_input(gate_manifest_id="duplicate-gate"),
            [HOLDOUT_MEMBERS[0], HOLDOUT_MEMBERS[0]],
        )
    with pytest.raises(ValueError, match="evaluator source"):
        seal_statistical_gate_manifest(
            conn,
            {
                **_gate_input(gate_manifest_id="wrong-evaluator-gate"),
                "evaluator_source_digest": "0" * 64,
            },
            HOLDOUT_MEMBERS,
        )


def test_prediction_snapshots_require_explicit_real_rows_and_survive_source_upsert() -> None:
    conn = _connection()
    seal_statistical_gate_manifest(conn, _gate_input(), HOLDOUT_MEMBERS)
    seal_canonical_analysis_artifact(conn, _analysis())
    source_rows = _prediction_rows(candidate_failed=True)
    record_target_predictions(conn, source_rows)
    stable = seal_prediction_snapshot_manifest(conn, _prediction_manifest_input("stable"))
    candidate = seal_prediction_snapshot_manifest(conn, _prediction_manifest_input("candidate"))
    candidate_before = candidate["member_digest"]

    changed = [
        {
            **row,
            "probabilities": {"up": 0.1, "flat": 0.2, "down": 0.7},
            "created_at": "2026-09-01T14:10:00+08:00",
        }
        for row in source_rows
        if row["model_role"] == "candidate"
        and row["target_key"] == "next_close_direction"
    ]
    record_target_predictions(conn, changed)

    assert prediction_snapshot_manifest(conn, candidate["prediction_manifest_id"])[
        "member_digest"
    ] == candidate_before
    failed_member = next(
        item for item in candidate["members"] if item["target_key"] == "next_open_gap"
    )
    assert failed_member["completion_state"] == "failed"
    assert failed_member["eligible"] is False
    assert stable["member_count"] == candidate["member_count"] == 2

    synthetic_conn = _connection()
    seal_statistical_gate_manifest(synthetic_conn, _gate_input(), HOLDOUT_MEMBERS)
    seal_canonical_analysis_artifact(synthetic_conn, _analysis())
    record_target_predictions(
        synthetic_conn,
        _prediction_rows(synthetic_role_target=("stable", "next_open_gap")),
    )
    with pytest.raises(ValueError, match="synthetic"):
        seal_prediction_snapshot_manifest(
            synthetic_conn,
            _prediction_manifest_input("stable"),
        )


def test_outcome_snapshot_requires_complete_partition_and_preserves_unavailable() -> None:
    conn = _connection()
    _, _, _, outcomes = _prepare_snapshots(
        conn,
        candidate_failed=True,
        unavailable_target="next_open_gap",
    )

    assert outcomes["member_count"] == 2
    unavailable = next(
        item for item in outcomes["members"] if item["target_key"] == "next_open_gap"
    )
    assert unavailable["quality_status"] == "unavailable"
    assert unavailable["label"] is None
    assert unavailable["availability_reason"] == "next_session_suspended"
    assert seal_outcome_snapshot_manifest(conn, _outcome_manifest_input()) == outcomes

    missing_conn = _connection()
    seal_statistical_gate_manifest(missing_conn, _gate_input(), HOLDOUT_MEMBERS)
    record_target_outcomes(missing_conn, _outcome_rows()[:1])
    with pytest.raises(ValueError, match="every holdout member"):
        seal_outcome_snapshot_manifest(missing_conn, _outcome_manifest_input())


def test_evaluation_runs_frozen_code_and_refuses_claimed_pass() -> None:
    conn = _connection()
    _prepare_snapshots(
        conn,
        candidate_failed=True,
        unavailable_target="next_open_gap",
    )
    changes_before = conn.total_changes
    rows, unavailable = paired_release_rows_from_snapshot_manifests(
        conn,
        stable_prediction_manifest_id="prediction-stable-v1",
        candidate_prediction_manifest_id="prediction-candidate-v1",
        outcome_manifest_id="outcomes-holdout-v1",
    )
    assert len(rows) == 1
    assert unavailable == 1
    assert rows[0]["candidate"]["completion_state"] == "completed"
    assert conn.total_changes == changes_before

    with pytest.raises(ValueError, match="does not match"):
        seal_statistical_evaluation_manifest(
            conn,
            {**_evaluation_input(), "evaluation_result": "pass_for_canary"},
        )
    saved = seal_statistical_evaluation_manifest(conn, _evaluation_input())
    assert saved["evaluation_result"] == "remain_shadow_insufficient_power"
    assert saved["holdout_member_count"] == 2
    assert saved["eligible_outcome_count"] == 1
    assert saved["outcome_unavailable_count"] == 1
    assert saved["synthetic_row_count"] == 0
    assert saved["evaluation"]["gate_spec_hash"] == gate.GATE_SPEC_HASH
    assert seal_statistical_evaluation_manifest(conn, _evaluation_input()) == saved
    assert statistical_evaluation_manifest(conn, "evaluation-holdout-v1") == saved


def test_evaluation_rejects_prediction_snapshot_sealed_after_holdout_open() -> None:
    conn = _connection()
    seal_statistical_gate_manifest(conn, _gate_input(), HOLDOUT_MEMBERS)
    seal_canonical_analysis_artifact(conn, _analysis())
    record_target_predictions(conn, _prediction_rows())
    seal_prediction_snapshot_manifest(
        conn,
        _prediction_manifest_input("stable", sealed_at="2026-09-02T14:00:30+08:00"),
    )
    seal_prediction_snapshot_manifest(
        conn,
        _prediction_manifest_input("candidate", sealed_at="2026-09-02T14:00:30+08:00"),
    )
    record_target_outcomes(conn, _outcome_rows())
    seal_outcome_snapshot_manifest(conn, _outcome_manifest_input())

    with pytest.raises(ValueError, match="sealed before the holdout opens"):
        seal_statistical_evaluation_manifest(conn, _evaluation_input())


def test_manifest_readers_are_read_only() -> None:
    conn = _connection()
    _prepare_snapshots(conn)
    changes_before = conn.total_changes

    assert statistical_gate_manifest(conn, "gate-holdout-v1") is not None
    assert prediction_snapshot_manifest(conn, "prediction-stable-v1") is not None
    assert outcome_snapshot_manifest(conn, "outcomes-holdout-v1") is not None
    assert statistical_evaluation_manifest(conn, "missing") is None
    assert conn.total_changes == changes_before
