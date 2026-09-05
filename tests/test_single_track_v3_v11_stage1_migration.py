from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_contract_migration import (  # noqa: E402
    V11_STAGE1_SCHEMA_CONTRACT_VERSION,
    apply_v11_stage1_contract_schema,
    audit_v11_stage1_duplicates,
    v11_stage1_required_columns,
)
from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    SINGLE_TRACK_V3_TABLES,
    ensure_single_track_v3_schema,
)


def _connect(path: Path | str = ":memory:") -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_v11_contract_migration_is_additive_idempotent_and_integrity_safe() -> None:
    conn = _connect()
    conn.execute("CREATE TABLE legacy_marker(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO legacy_marker(value) VALUES('preserved')")

    ensure_single_track_v3_schema(conn)

    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert set(SINGLE_TRACK_V3_TABLES) <= tables
    assert "legacy_marker" in tables
    assert len(SINGLE_TRACK_V3_TABLES) == 44
    for table, required_columns in v11_stage1_required_columns().items():
        actual = {
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table}")')
        }
        assert set(required_columns) <= actual

    state = conn.execute(
        "SELECT * FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()
    assert state["schema_version"] == SINGLE_TRACK_V3_SCHEMA_VERSION
    assert state["schema_version"] == "single-track-v3-v11-stage1.1"
    assert state["migration_contract_version"] == V11_STAGE1_SCHEMA_CONTRACT_VERSION
    assert state["rollback_strategy"] == "verified_backup_restore"
    assert datetime.fromisoformat(state["applied_at"]).utcoffset() is not None
    assert audit_v11_stage1_duplicates(conn)["passed"] is True
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    changes_before_retry = conn.total_changes
    applied_at_before_retry = state["applied_at"]
    ensure_single_track_v3_schema(conn)
    state_after_retry = conn.execute(
        "SELECT * FROM single_track_v3_schema_state WHERE singleton_id=1"
    ).fetchone()
    assert conn.total_changes == changes_before_retry
    assert state_after_retry["applied_at"] == applied_at_before_retry


def test_v11_migration_rollback_is_verified_by_consistent_backup_restore(
    tmp_path: Path,
) -> None:
    active_path = tmp_path / "active.sqlite3"
    backup_path = tmp_path / "pre_migration.sqlite3"
    restored_path = tmp_path / "restored.sqlite3"

    with _connect(active_path) as active:
        active.execute("CREATE TABLE legacy_marker(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        active.execute("INSERT INTO legacy_marker(value) VALUES('before-migration')")
        active.commit()
        with _connect(backup_path) as backup:
            active.backup(backup)
        ensure_single_track_v3_schema(active)
        active.commit()
        assert active.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    with _connect(backup_path) as backup, _connect(restored_path) as restored:
        backup.backup(restored)
        restored.commit()
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT value FROM legacy_marker").fetchone()[0] == "before-migration"
        restored_tables = {
            str(row[0])
            for row in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert not (set(SINGLE_TRACK_V3_TABLES) & restored_tables)


def test_duplicate_audit_fails_closed_before_unique_index_creation() -> None:
    conn = _connect()
    ensure_single_track_v3_schema(conn)
    conn.execute("DROP INDEX idx_target_prediction_contribution_v11")
    values = {
        "analysis_id": "analysis-1",
        "model_role": "candidate",
        "target_key": "next_close_direction",
        "regime": "normal",
        "probability_json": "{}",
        "eligible": 1,
        "completion_state": "completed",
        "analysis_cutoff": "2026-09-03T07:00:00+08:00",
        "event_revision_id": "revision-1",
        "target_entity_id": "2454",
        "target_trade_date": "2026-09-03",
        "weight_version": "candidate-unreleased",
        "created_at": "2026-09-03T07:00:01+08:00",
    }
    for sample_id in ("sample-1", "sample-2"):
        conn.execute(
            """
            INSERT INTO analysis_target_prediction(
                sample_id,analysis_id,model_role,target_key,regime,probability_json,
                eligible,completion_state,analysis_cutoff,event_revision_id,
                target_entity_id,target_trade_date,weight_version,created_at
            ) VALUES(
                :sample_id,:analysis_id,:model_role,:target_key,:regime,:probability_json,
                :eligible,:completion_state,:analysis_cutoff,:event_revision_id,
                :target_entity_id,:target_trade_date,:weight_version,:created_at
            )
            """,
            {**values, "sample_id": sample_id},
        )

    audit = audit_v11_stage1_duplicates(conn)
    assert audit["passed"] is False
    assert audit["checks"]["target_prediction_contribution"] == 1
    with pytest.raises(RuntimeError, match="duplicate audit failed"):
        apply_v11_stage1_contract_schema(conn)
