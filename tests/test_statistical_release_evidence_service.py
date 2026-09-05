from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from services.statistical_release_evidence_service import (  # noqa: E402
    evaluate_release_evidence_database,
    load_paired_release_rows,
)


def _prediction(conn: sqlite3.Connection, role: str, *, cutoff: str = "2026-08-31T13:45:00+08:00") -> None:
    conn.execute(
        """
        INSERT INTO analysis_target_prediction(
            sample_id,analysis_id,model_role,target_key,regime,probability_json,
            eligible,completion_state,omission_reason,analysis_cutoff,
            event_cluster_id,event_type,large_safety_slice,synthetic,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "sample-1",
            "analysis-1",
            role,
            "next_open_gap",
            "normal",
            json.dumps({"up": 0.6, "flat": 0.2, "down": 0.2}),
            1,
            "completed",
            None,
            cutoff,
            None,
            None,
            0,
            0,
            "2026-08-31T13:45:01+08:00",
        ),
    )


def _outcome(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO analysis_target_outcome(
            sample_id,target_key,outcome_revision,stock_code,prediction_trade_date,
            outcome_trade_date,label,t_close,next_open,next_close,adjustment_basis,
            quality_status,availability_reason,available_at,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "sample-1",
            "next_open_gap",
            "official-adjusted-v1",
            "2330",
            "2026-08-31",
            "2026-09-01",
            "up",
            100,
            102,
            103,
            "official_adjusted",
            "ok",
            None,
            "2026-09-01T14:00:00+08:00",
            "2026-09-01T14:01:00+08:00",
        ),
    )


def test_missing_production_schema_is_read_only_insufficient_evidence() -> None:
    conn = sqlite3.connect(":memory:")

    result = evaluate_release_evidence_database(conn)

    assert result["read_only"] is True
    assert result["schema_ready"] is False
    assert result["paired_rows"] == 0
    assert result["gate"]["result"] == "remain_shadow_insufficient_power"


def test_loader_retains_a_missing_candidate_in_the_denominator() -> None:
    conn = sqlite3.connect(":memory:")
    ensure_single_track_v3_schema(conn)
    _prediction(conn, "stable")
    _outcome(conn)

    rows, errors = load_paired_release_rows(conn)

    assert errors == []
    assert len(rows) == 1
    assert rows[0]["stable"]["completion_state"] == "completed"
    assert rows[0]["candidate"]["completion_state"] == "missing"
    result = evaluate_release_evidence_database(conn)
    assert result["paired_rows"] == 1
    assert result["gate"]["result"] == "remain_shadow_insufficient_power"


def test_pair_metadata_mismatch_invalidates_evidence() -> None:
    conn = sqlite3.connect(":memory:")
    ensure_single_track_v3_schema(conn)
    _prediction(conn, "stable")
    _prediction(conn, "candidate", cutoff="2026-08-31T13:46:00+08:00")
    _outcome(conn)

    result = evaluate_release_evidence_database(conn)

    assert result["gate"]["result"] == "invalid_evidence"
    assert any("paired_prediction_metadata_mismatch" in reason for reason in result["evidence_errors"])
