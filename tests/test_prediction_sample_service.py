from __future__ import annotations

import sqlite3

import pytest

from repository.single_track_v3_repository import seal_canonical_analysis_artifact
from services.prediction_sample_service import record_paired_prediction_sample


def _artifact() -> dict:
    return {
        "analysis_id": "analysis-2454-20260901",
        "snapshot_id": "snapshot-2454-20260901",
        "snapshot_digest": "snapshot-digest",
        "request_received_at": "2026-09-01T14:00:00+08:00",
        "analysis_cutoff": "2026-09-01T14:00:00+08:00",
        "snapshot_sealed_at": "2026-09-01T14:00:01+08:00",
        "context_digest": "context-digest",
        "component_snapshot_ids": [],
        "event_watermark": None,
        "source_policy_version": "SourceAuthorityPolicyV1",
        "weight_version": "StableWeightV1",
        "formula_version": "TechnicalFormulaV1",
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
        "canonical_payload": {"code": "2454", "trade_date": "2026-09-01"},
        "canonical_answer_text": "封存分析",
        "created_at": "2026-09-01T14:00:01+08:00",
    }


def _probabilities() -> dict[str, dict[str, float]]:
    return {
        "next_open_gap": {"up": 0.4, "flat": 0.35, "down": 0.25},
        "continuation_reversal": {
            "continuation": 0.4,
            "neutral": 0.35,
            "reversal": 0.25,
        },
        "next_close_direction": {"up": 0.45, "flat": 0.3, "down": 0.25},
    }


def test_prediction_sample_records_a_real_paired_three_target_forecast() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    seal_canonical_analysis_artifact(conn, _artifact())

    result = record_paired_prediction_sample(
        conn,
        analysis_id="analysis-2454-20260901",
        regime="normal",
        stable_probabilities=_probabilities(),
        candidate_probabilities=_probabilities(),
        created_at="2026-09-01T14:00:02+08:00",
    )

    assert result["prediction_rows_written"] == 6
    assert result["synthetic"] is False
    assert conn.execute("SELECT COUNT(*) FROM analysis_target_prediction").fetchone()[0] == 6


def test_prediction_sample_rejects_unpaired_or_unscaled_probabilities() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    seal_canonical_analysis_artifact(conn, _artifact())
    invalid = _probabilities()
    invalid["next_open_gap"] = {"up": 0.5, "flat": 0.5, "down": 0.5}

    with pytest.raises(ValueError, match="probability_sum_invalid"):
        record_paired_prediction_sample(
            conn,
            analysis_id="analysis-2454-20260901",
            regime="normal",
            stable_probabilities=_probabilities(),
            candidate_probabilities=invalid,
            created_at="2026-09-01T14:00:02+08:00",
        )

    assert conn.execute("SELECT COUNT(*) FROM analysis_target_prediction").fetchone()[0] == 0
