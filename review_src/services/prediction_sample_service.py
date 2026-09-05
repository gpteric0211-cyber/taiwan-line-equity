from __future__ import annotations

"""Strict paired stable/candidate prediction sample recorder."""

import json
import math
import re
import sqlite3
from datetime import datetime
from typing import Any, Mapping

from analysis.target_label_contract_v1 import TARGET_CLASSES, target_sample_id
from repository.single_track_v3_repository import record_target_predictions


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _probabilities(value: Any, *, target: str, role: str) -> dict[str, float]:
    supplied = _mapping(value)
    required = set(TARGET_CLASSES[target])
    if set(supplied) != required:
        raise ValueError(f"{role}:{target}:probability_classes_invalid")
    result: dict[str, float] = {}
    for key in TARGET_CLASSES[target]:
        raw = supplied[key]
        if isinstance(raw, bool):
            raise ValueError(f"{role}:{target}:probability_invalid")
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{role}:{target}:probability_invalid") from exc
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError(f"{role}:{target}:probability_out_of_range")
        result[key] = number
    if not math.isclose(sum(result.values()), 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError(f"{role}:{target}:probability_sum_invalid")
    return result


def _artifact(conn: sqlite3.Connection, analysis_id: str) -> dict[str, Any]:
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT analysis_id,analysis_cutoff,canonical_payload_json
            FROM canonical_analysis_artifact WHERE analysis_id=?
            """,
            (analysis_id,),
        ).fetchone()
        if row is None:
            raise ValueError("analysis_id_not_found")
        return dict(row)
    finally:
        conn.row_factory = old_factory


def record_paired_prediction_sample(
    conn: sqlite3.Connection,
    *,
    analysis_id: str,
    regime: str,
    stable_probabilities: Mapping[str, Mapping[str, float]],
    candidate_probabilities: Mapping[str, Mapping[str, float]],
    created_at: str,
    event_cluster_id: str | None = None,
    event_type: str | None = None,
    large_safety_slice: bool = False,
) -> dict[str, Any]:
    """Persist a real paired forecast; never derives probabilities from prose."""

    if regime not in {"normal", "material_event"}:
        raise ValueError("regime_invalid")
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at_invalid") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise ValueError("created_at_must_be_offset_aware")
    source = _artifact(conn, str(analysis_id))
    try:
        payload = json.loads(str(source.get("canonical_payload_json") or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("canonical_payload_invalid") from exc
    code = str(payload.get("code") or "").strip() if isinstance(payload, dict) else ""
    prediction_date = str(payload.get("trade_date") or "").strip() if isinstance(payload, dict) else ""
    if not re.fullmatch(r"\d{4}", code) or not prediction_date:
        raise ValueError("canonical_prediction_identity_missing")
    if regime == "material_event" and (not event_cluster_id or not event_type):
        raise ValueError("material_event_metadata_missing")
    cutoff = str(source.get("analysis_cutoff") or "")
    try:
        cutoff_value = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("analysis_cutoff_invalid") from exc
    if cutoff_value.tzinfo is None or cutoff_value.utcoffset() is None:
        raise ValueError("analysis_cutoff_must_be_offset_aware")
    if created < cutoff_value:
        raise ValueError("prediction_created_before_analysis_cutoff")
    sample_id = target_sample_id(
        stock_code=code,
        prediction_trade_date=prediction_date,
        analysis_cutoff=cutoff,
        regime=regime,
    )
    rows: list[dict[str, Any]] = []
    for target in TARGET_CLASSES:
        stable = _probabilities(
            stable_probabilities.get(target), target=target, role="stable"
        )
        candidate = _probabilities(
            candidate_probabilities.get(target), target=target, role="candidate"
        )
        for role, values in (("stable", stable), ("candidate", candidate)):
            rows.append(
                {
                    "sample_id": sample_id,
                    "analysis_id": analysis_id,
                    "model_role": role,
                    "target_key": target,
                    "regime": regime,
                    "probabilities": values,
                    "eligible": 1,
                    "completion_state": "completed",
                    "omission_reason": None,
                    "analysis_cutoff": cutoff,
                    "event_cluster_id": event_cluster_id,
                    "event_type": event_type,
                    "large_safety_slice": bool(large_safety_slice),
                    "synthetic": False,
                    "created_at": created.isoformat(timespec="seconds"),
                }
            )
    written = record_target_predictions(conn, rows)
    return {
        "sample_id": sample_id,
        "stock_code": code,
        "prediction_trade_date": prediction_date,
        "analysis_cutoff": cutoff,
        "regime": regime,
        "prediction_rows_written": written,
        "synthetic": False,
    }
