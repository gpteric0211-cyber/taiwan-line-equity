from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Mapping

from analysis.target_label_contract_v1 import OFFICIAL_ADJUSTMENT_BASIS
from evaluation.statistical_release_gate_v1 import (
    GATE_SPEC_HASH,
    STATISTICAL_RELEASE_GATE_VERSION,
    evaluate_statistical_release_gate,
)


REQUIRED_EVIDENCE_TABLES = {
    "analysis_target_prediction",
    "analysis_target_outcome",
}
DEFAULT_OUTCOME_REVISION = "official-adjusted-v1"


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _prediction_payload(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "eligible": False,
            "completion_state": "missing",
            "probabilities": {},
        }
    return {
        "eligible": bool(row.get("eligible")),
        "completion_state": str(row.get("completion_state") or "missing"),
        "probabilities": _json_object(row.get("probability_json")),
        "omission_reason": str(row.get("omission_reason") or "") or None,
    }


def _available_after_cutoff(available_at: str, cutoff: str) -> bool:
    try:
        return datetime.fromisoformat(available_at) > datetime.fromisoformat(cutoff)
    except (TypeError, ValueError):
        return False


def load_paired_release_rows(
    conn: sqlite3.Connection,
    *,
    outcome_revision: str = DEFAULT_OUTCOME_REVISION,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Load stable/candidate rows read-only while retaining failures in the denominator."""

    old_row_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        outcomes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM analysis_target_outcome
                WHERE outcome_revision=? AND quality_status='ok' AND label IS NOT NULL
                ORDER BY prediction_trade_date,target_key,sample_id
                """,
                (str(outcome_revision),),
            ).fetchall()
        ]
        predictions = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.*
                FROM analysis_target_prediction p
                JOIN analysis_target_outcome o
                  ON o.sample_id=p.sample_id AND o.target_key=p.target_key
                WHERE o.outcome_revision=? AND o.quality_status='ok' AND o.label IS NOT NULL
                ORDER BY p.sample_id,p.target_key,p.model_role
                """,
                (str(outcome_revision),),
            ).fetchall()
        ]
    finally:
        conn.row_factory = old_row_factory

    prediction_index = {
        (str(row["sample_id"]), str(row["target_key"]), str(row["model_role"])): row
        for row in predictions
    }
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for outcome in outcomes:
        sample_id = str(outcome.get("sample_id") or "")
        target = str(outcome.get("target_key") or "")
        stable = prediction_index.get((sample_id, target, "stable"))
        candidate = prediction_index.get((sample_id, target, "candidate"))
        metadata_source = stable or candidate
        if metadata_source is None:
            errors.append(f"prediction_metadata_missing:{sample_id}:{target}")
            continue
        if str(outcome.get("adjustment_basis") or "") != OFFICIAL_ADJUSTMENT_BASIS:
            errors.append(f"outcome_adjustment_basis_invalid:{sample_id}:{target}")
        if stable and candidate:
            for field in (
                "analysis_id",
                "target_key",
                "regime",
                "analysis_cutoff",
                "event_cluster_id",
                "event_type",
                "large_safety_slice",
                "synthetic",
            ):
                if stable.get(field) != candidate.get(field):
                    errors.append(f"paired_prediction_metadata_mismatch:{sample_id}:{target}:{field}")
        cutoff = str(metadata_source.get("analysis_cutoff") or "")
        available_at = str(outcome.get("available_at") or "")
        if not _available_after_cutoff(available_at, cutoff):
            errors.append(f"outcome_not_available_after_prediction_cutoff:{sample_id}:{target}")
        rows.append(
            {
                "sample_id": sample_id,
                "stock_code": str(outcome.get("stock_code") or ""),
                "prediction_trade_date": str(outcome.get("prediction_trade_date") or ""),
                "target_key": target,
                "regime": str(metadata_source.get("regime") or ""),
                "actual_label": str(outcome.get("label") or ""),
                "outcome_revision": str(outcome.get("outcome_revision") or ""),
                "event_cluster_id": metadata_source.get("event_cluster_id"),
                "event_type": metadata_source.get("event_type"),
                "large_safety_slice": bool(metadata_source.get("large_safety_slice")),
                "synthetic": bool(metadata_source.get("synthetic")),
                "stable": _prediction_payload(stable),
                "candidate": _prediction_payload(candidate),
            }
        )
    return rows, list(dict.fromkeys(errors))


def evaluate_release_evidence_database(
    conn: sqlite3.Connection,
    *,
    outcome_revision: str = DEFAULT_OUTCOME_REVISION,
) -> dict[str, Any]:
    """Inspect a database without schema creation, migration, or outcome backfill."""

    table_names = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    missing = sorted(REQUIRED_EVIDENCE_TABLES - table_names)
    if missing:
        gate = evaluate_statistical_release_gate([])
        return {
            "evidence_contract": "StatisticalReleaseEvidenceV1",
            "gate_version": STATISTICAL_RELEASE_GATE_VERSION,
            "gate_spec_hash": GATE_SPEC_HASH,
            "outcome_revision": str(outcome_revision),
            "read_only": True,
            "schema_ready": False,
            "missing_tables": missing,
            "paired_rows": 0,
            "evidence_errors": ["production_evidence_schema_not_migrated"],
            "gate": gate,
        }
    rows, errors = load_paired_release_rows(conn, outcome_revision=outcome_revision)
    gate = evaluate_statistical_release_gate(rows) if not errors else {
        "gate_version": STATISTICAL_RELEASE_GATE_VERSION,
        "gate_spec_hash": GATE_SPEC_HASH,
        "result": "invalid_evidence",
        "reason_codes": errors,
        "regimes": {},
    }
    return {
        "evidence_contract": "StatisticalReleaseEvidenceV1",
        "gate_version": STATISTICAL_RELEASE_GATE_VERSION,
        "gate_spec_hash": GATE_SPEC_HASH,
        "outcome_revision": str(outcome_revision),
        "read_only": True,
        "schema_ready": True,
        "missing_tables": [],
        "paired_rows": len(rows),
        "evidence_errors": errors,
        "gate": gate,
    }
