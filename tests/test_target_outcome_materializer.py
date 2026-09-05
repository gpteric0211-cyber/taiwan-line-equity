from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from repository.single_track_v3_repository import (
    record_target_predictions,
    seal_canonical_analysis_artifact,
)
from services.target_outcome_materializer import materialize_available_target_outcomes


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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


def _market_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE history_price(
            date TEXT NOT NULL, code TEXT NOT NULL, open REAL, close REAL,
            source TEXT, source_quality TEXT, fetched_at REAL,
            PRIMARY KEY(date,code)
        );
        CREATE TABLE corporate_actions(
            code TEXT NOT NULL, date TEXT NOT NULL, is_confirmed INTEGER NOT NULL
        );
        """
    )


def test_materializer_records_only_post_cutoff_official_t_plus_one_outcomes() -> None:
    conn = _connection()
    seal_canonical_analysis_artifact(conn, _artifact())
    sample_id = "target-v1-sample"
    record_target_predictions(
        conn,
        [
            {
                "sample_id": sample_id,
                "analysis_id": "analysis-2454-20260901",
                "model_role": role,
                "target_key": target,
                "regime": "normal",
                "probabilities": {"up": 0.4, "flat": 0.3, "down": 0.3},
                "eligible": 1,
                "completion_state": "completed",
                "omission_reason": None,
                "analysis_cutoff": "2026-09-01T14:00:00+08:00",
                "event_cluster_id": None,
                "event_type": None,
                "large_safety_slice": False,
                "synthetic": False,
                "created_at": "2026-09-01T14:00:01+08:00",
            }
            for role in ("stable", "candidate")
            for target in (
                "next_open_gap",
                "continuation_reversal",
                "next_close_direction",
            )
        ],
    )
    _market_tables(conn)
    fetched_t = datetime(2026, 9, 1, 13, 40, tzinfo=ZoneInfo("Asia/Taipei")).timestamp()
    fetched_next = datetime(2026, 9, 2, 13, 40, tzinfo=ZoneInfo("Asia/Taipei")).timestamp()
    conn.executemany(
        """
        INSERT INTO history_price(date,code,open,close,source,source_quality,fetched_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        [
            ("2026-09-01", "2454", 100.0, 100.0, "TWSE", "official", fetched_t),
            ("2026-09-01", "9999", 10.0, 10.0, "TWSE", "official", fetched_t),
            ("2026-09-02", "2454", 102.0, 104.0, "TWSE", "official", fetched_next),
            *[
                ("2026-09-02", f"{code:04d}", 10.0, 10.0, "TWSE", "official", fetched_next)
                for code in range(1000, 1500)
                if code != 2454
            ],
        ],
    )

    result = materialize_available_target_outcomes(
        conn,
        recorded_at="2026-09-02T14:00:00+08:00",
    )

    assert result["prediction_samples_seen"] == 1
    assert result["outcome_rows_ready"] == 3
    rows = conn.execute(
        "SELECT target_key,label,quality_status FROM analysis_target_outcome ORDER BY target_key"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("continuation_reversal", "continuation", "ok"),
        ("next_close_direction", "up", "ok"),
        ("next_open_gap", "up", "ok"),
    ]


def test_materializer_does_not_fabricate_predictions() -> None:
    conn = _connection()
    seal_canonical_analysis_artifact(conn, _artifact())
    _market_tables(conn)

    result = materialize_available_target_outcomes(conn)

    assert result["prediction_samples_seen"] == 0
    assert result["outcome_rows_written"] == 0
