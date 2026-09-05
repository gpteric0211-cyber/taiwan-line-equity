from __future__ import annotations

import math
import sqlite3
from typing import Any, Iterable, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema
from repository.single_track_v3_manifest_support import (
    BOOTSTRAP_BLOCK_DAYS,
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    GATE_SPEC,
    GATE_SPEC_HASH,
    PREDICTION_COMPLETION_STATES,
    STATISTICAL_RELEASE_GATE_VERSION,
    TARGET_CLASSES,
    TARGET_LABEL_CONTRACT_VERSION,
    VALID_REGIMES,
    aware_timestamp,
    canonical_digest,
    canonical_json,
    current_evaluator_source_digest,
    current_target_label_source_digest,
    finite_probability,
    gate_manifest_row,
    gate_member_keys,
    json_object,
    json_value,
    manifest_savepoint,
    normalize_member_keys,
    required_text,
    select_one,
    select_rows,
    sha256_digest,
    validate_supplied_digest,
)


def _decode_gate(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(row)
    result["gate_spec"] = json_value(result.pop("gate_spec_json"), {})
    result["holdout_members"] = gate_member_keys(conn, str(result["gate_manifest_id"]))
    return result


def statistical_gate_manifest(
    conn: sqlite3.Connection,
    gate_manifest_id: str,
) -> dict[str, Any] | None:
    """Read one predeclared statistical gate without schema DDL or backfill."""

    row = select_one(
        conn,
        "SELECT * FROM statistical_gate_manifest WHERE gate_manifest_id=?",
        (str(gate_manifest_id),),
    )
    return _decode_gate(conn, row) if row is not None else None


def seal_statistical_gate_manifest(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
    holdout_members: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Seal the gate specification and untouched holdout identity before predictions."""

    ensure_single_track_v3_schema(conn)
    row = dict(manifest)
    gate_manifest_id = required_text(row.get("gate_manifest_id"), "gate_manifest_id")
    gate_version = required_text(row.get("gate_version"), "gate_version")
    if gate_version != STATISTICAL_RELEASE_GATE_VERSION:
        raise ValueError("gate_version must be StatisticalReleaseGateV1")
    gate_spec = row.get("gate_spec")
    if not isinstance(gate_spec, Mapping) or canonical_json(dict(gate_spec)) != canonical_json(
        GATE_SPEC
    ):
        raise ValueError("gate_spec must exactly match the frozen StatisticalReleaseGateV1")
    gate_spec_hash = validate_supplied_digest(
        row.get("gate_spec_hash"), GATE_SPEC_HASH, "gate_spec_hash"
    )
    evaluator_source_digest = sha256_digest(
        row.get("evaluator_source_digest"), "evaluator_source_digest"
    )
    if evaluator_source_digest != current_evaluator_source_digest():
        raise ValueError("evaluator_source_digest does not match the frozen evaluator source")
    target_label_contract_version = required_text(
        row.get("target_label_contract_version"), "target_label_contract_version"
    )
    if target_label_contract_version != TARGET_LABEL_CONTRACT_VERSION:
        raise ValueError("target_label_contract_version is not frozen TargetLabelContractV1")
    target_label_source_digest = sha256_digest(
        row.get("target_label_source_digest"), "target_label_source_digest"
    )
    if target_label_source_digest != current_target_label_source_digest():
        raise ValueError("target_label_source_digest does not match the frozen label source")
    bootstrap_iterations = int(row.get("bootstrap_iterations", BOOTSTRAP_ITERATIONS))
    bootstrap_seed = int(row.get("bootstrap_seed", BOOTSTRAP_SEED))
    bootstrap_block_days = int(row.get("bootstrap_block_days", BOOTSTRAP_BLOCK_DAYS))
    if (
        bootstrap_iterations != BOOTSTRAP_ITERATIONS
        or bootstrap_seed != BOOTSTRAP_SEED
        or bootstrap_block_days != BOOTSTRAP_BLOCK_DAYS
    ):
        raise ValueError("bootstrap contract must match the frozen statistical gate")
    holdout_partition_id = required_text(
        row.get("holdout_partition_id"), "holdout_partition_id"
    )
    members = normalize_member_keys(holdout_members)
    partition_digest = canonical_digest(
        {
            "contract": "StatisticalHoldoutPartitionV1",
            "holdout_partition_id": holdout_partition_id,
            "target_label_contract_version": target_label_contract_version,
            "members": members,
        }
    )
    validate_supplied_digest(
        row.get("holdout_partition_digest"),
        partition_digest,
        "holdout_partition_digest",
    )
    created_at = aware_timestamp(row.get("created_at"), "created_at")
    sealed_at = aware_timestamp(row.get("sealed_at"), "sealed_at")
    if sealed_at < created_at:
        raise ValueError("sealed_at cannot precede created_at")
    manifest_payload = {
        "contract": "StatisticalGateManifestV1",
        "gate_version": gate_version,
        "gate_spec_hash": gate_spec_hash,
        "evaluator_source_digest": evaluator_source_digest,
        "target_label_contract_version": target_label_contract_version,
        "target_label_source_digest": target_label_source_digest,
        "holdout_partition_id": holdout_partition_id,
        "holdout_partition_digest": partition_digest,
        "holdout_member_count": len(members),
        "bootstrap_iterations": bootstrap_iterations,
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_block_days": bootstrap_block_days,
        "sealed_at": row.get("sealed_at"),
    }
    manifest_key = canonical_digest(manifest_payload)
    validate_supplied_digest(row.get("manifest_key"), manifest_key, "manifest_key")
    values = {
        "gate_manifest_id": gate_manifest_id,
        "manifest_key": manifest_key,
        "gate_version": gate_version,
        "gate_spec_json": canonical_json(dict(gate_spec)),
        "gate_spec_hash": gate_spec_hash,
        "evaluator_source_digest": evaluator_source_digest,
        "target_label_contract_version": target_label_contract_version,
        "target_label_source_digest": target_label_source_digest,
        "holdout_partition_id": holdout_partition_id,
        "holdout_partition_digest": partition_digest,
        "holdout_member_count": len(members),
        "bootstrap_iterations": bootstrap_iterations,
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_block_days": bootstrap_block_days,
        "sealed_at": row.get("sealed_at"),
        "created_at": row.get("created_at"),
    }
    columns = tuple(values)
    with manifest_savepoint(conn, "gate_manifest"):
        existing = select_one(
            conn,
            "SELECT * FROM statistical_gate_manifest WHERE manifest_key=?",
            (manifest_key,),
        )
        if existing is None:
            try:
                conn.execute(
                    f"INSERT INTO statistical_gate_manifest({','.join(columns)}) "
                    f"VALUES({','.join(':' + column for column in columns)})",
                    values,
                )
                conn.executemany(
                    """
                    INSERT INTO statistical_gate_holdout_member(
                        gate_manifest_id,sample_id,target_key
                    ) VALUES(?,?,?)
                    """,
                    [
                        (gate_manifest_id, item["sample_id"], item["target_key"])
                        for item in members
                    ],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("statistical gate manifest identity conflicts") from exc
        else:
            if any(existing.get(column) != values.get(column) for column in columns):
                raise ValueError("gate manifest key exists with different content")
            if gate_member_keys(conn, gate_manifest_id) != members:
                raise ValueError("gate manifest members conflict with sealed content")
    saved = statistical_gate_manifest(conn, gate_manifest_id)
    if saved is None:
        raise RuntimeError("statistical gate manifest could not be read back")
    return saved


def _prediction_source_rows(
    conn: sqlite3.Connection,
    gate_manifest_id: str,
    model_role: str,
) -> list[dict[str, Any]]:
    rows = select_rows(
        conn,
        """
        SELECT
            h.sample_id AS holdout_sample_id,
            h.target_key AS holdout_target_key,
            p.*,
            a.analysis_cutoff AS canonical_analysis_cutoff
        FROM statistical_gate_holdout_member h
        LEFT JOIN analysis_target_prediction p
          ON p.sample_id=h.sample_id
         AND p.target_key=h.target_key
         AND p.model_role=?
        LEFT JOIN canonical_analysis_artifact a ON a.analysis_id=p.analysis_id
        WHERE h.gate_manifest_id=?
        ORDER BY h.sample_id,h.target_key
        """,
        (model_role, gate_manifest_id),
    )
    if any(row.get("sample_id") is None for row in rows):
        raise ValueError("every holdout member requires an explicit prediction row")
    return rows


def _prediction_member(
    source: Mapping[str, Any],
    *,
    model_role: str,
    gate_sealed_at: Any,
    manifest_sealed_at: Any,
) -> dict[str, Any]:
    sample_id = required_text(source.get("sample_id"), "sample_id")
    target_key = required_text(source.get("target_key"), "target_key")
    if (
        sample_id != source.get("holdout_sample_id")
        or target_key != source.get("holdout_target_key")
    ):
        raise ValueError("prediction row does not match its holdout member")
    regime = required_text(source.get("regime"), "regime")
    if regime not in VALID_REGIMES:
        raise ValueError("prediction regime is invalid")
    completion_state = required_text(source.get("completion_state"), "completion_state")
    if completion_state not in PREDICTION_COMPLETION_STATES:
        raise ValueError("prediction completion_state is not manifest-safe")
    eligible = int(bool(source.get("eligible")))
    probabilities = json_object(source.get("probability_json"), "probability_json")
    if completion_state == "completed":
        if eligible != 1 or set(probabilities) != set(TARGET_CLASSES[target_key]):
            raise ValueError("completed prediction requires all target probabilities")
        normalized_probabilities = {
            label: finite_probability(probabilities[label], f"probabilities.{label}")
            for label in TARGET_CLASSES[target_key]
        }
        if not math.isclose(
            sum(normalized_probabilities.values()), 1.0, abs_tol=1e-6
        ):
            raise ValueError("prediction probabilities must sum to one")
        omission_reason = None
    else:
        if eligible != 0 or probabilities:
            raise ValueError("non-completed prediction must be ineligible with no probabilities")
        normalized_probabilities = {}
        omission_reason = required_text(source.get("omission_reason"), "omission_reason")
    if int(bool(source.get("synthetic"))) != 0:
        raise ValueError("synthetic prediction rows are forbidden")
    analysis_id = required_text(source.get("analysis_id"), "analysis_id")
    analysis_cutoff_text = required_text(source.get("analysis_cutoff"), "analysis_cutoff")
    if source.get("canonical_analysis_cutoff") != analysis_cutoff_text:
        raise ValueError("prediction analysis cutoff does not match its canonical artifact")
    analysis_cutoff = aware_timestamp(analysis_cutoff_text, "analysis_cutoff")
    prediction_created = aware_timestamp(source.get("created_at"), "prediction.created_at")
    if prediction_created < aware_timestamp(gate_sealed_at, "gate.sealed_at"):
        raise ValueError("prediction predates the sealed holdout gate")
    if prediction_created < analysis_cutoff:
        raise ValueError("prediction creation cannot precede its analysis cutoff")
    if prediction_created > aware_timestamp(manifest_sealed_at, "manifest.sealed_at"):
        raise ValueError("prediction was not available when its manifest was sealed")
    event_cluster_id = source.get("event_cluster_id")
    event_type = source.get("event_type")
    if bool(event_cluster_id) != bool(event_type):
        raise ValueError("event_cluster_id and event_type must be present together")
    if regime == "material_event" and not event_cluster_id:
        raise ValueError("material-event prediction requires independent event metadata")
    payload = {
        "contract": "PredictionSnapshotMemberV1",
        "model_role": model_role,
        "sample_id": sample_id,
        "target_key": target_key,
        "analysis_id": analysis_id,
        "regime": regime,
        "analysis_cutoff": analysis_cutoff_text,
        "probabilities": normalized_probabilities,
        "eligible": eligible,
        "completion_state": completion_state,
        "omission_reason": omission_reason,
        "event_cluster_id": event_cluster_id,
        "event_type": event_type,
        "large_safety_slice": int(bool(source.get("large_safety_slice"))),
        "synthetic": 0,
        "prediction_created_at": source.get("created_at"),
    }
    return {**payload, "row_digest": canonical_digest(payload)}


def _decode_prediction_manifest(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(row)
    members = select_rows(
        conn,
        """
        SELECT * FROM prediction_snapshot_member
        WHERE prediction_manifest_id=? ORDER BY sample_id,target_key
        """,
        (str(result["prediction_manifest_id"]),),
    )
    for member in members:
        member["probabilities"] = json_value(member.pop("probability_json"), {})
        member["eligible"] = bool(member["eligible"])
        member["large_safety_slice"] = bool(member["large_safety_slice"])
        member["synthetic"] = bool(member["synthetic"])
    result["members"] = members
    return result


def prediction_snapshot_manifest(
    conn: sqlite3.Connection,
    prediction_manifest_id: str,
) -> dict[str, Any] | None:
    """Read an immutable prediction snapshot without source-table writes."""

    row = select_one(
        conn,
        "SELECT * FROM prediction_snapshot_manifest WHERE prediction_manifest_id=?",
        (str(prediction_manifest_id),),
    )
    return _decode_prediction_manifest(conn, row) if row is not None else None


def seal_prediction_snapshot_manifest(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Snapshot every predeclared prediction row for one model role."""

    ensure_single_track_v3_schema(conn)
    row = dict(manifest)
    prediction_manifest_id = required_text(
        row.get("prediction_manifest_id"), "prediction_manifest_id"
    )
    gate_manifest_id = required_text(row.get("gate_manifest_id"), "gate_manifest_id")
    gate = gate_manifest_row(conn, gate_manifest_id)
    model_role = required_text(row.get("model_role"), "model_role")
    if model_role not in {"stable", "candidate"}:
        raise ValueError("model_role must be stable or candidate")
    prediction_contract_version = required_text(
        row.get("prediction_contract_version"), "prediction_contract_version"
    )
    created_at = aware_timestamp(row.get("created_at"), "created_at")
    sealed_at = aware_timestamp(row.get("sealed_at"), "sealed_at")
    gate_sealed_at = aware_timestamp(gate["sealed_at"], "gate.sealed_at")
    if created_at < gate_sealed_at or sealed_at < created_at:
        raise ValueError("prediction manifest must be created after its sealed gate")
    source_rows = _prediction_source_rows(conn, gate_manifest_id, model_role)
    members = [
        _prediction_member(
            source,
            model_role=model_role,
            gate_sealed_at=gate["sealed_at"],
            manifest_sealed_at=row.get("sealed_at"),
        )
        for source in source_rows
    ]
    member_digest = canonical_digest([member["row_digest"] for member in members])
    validate_supplied_digest(row.get("member_digest"), member_digest, "member_digest")
    manifest_payload = {
        "contract": "PredictionSnapshotManifestV1",
        "gate_manifest_key": gate["manifest_key"],
        "model_role": model_role,
        "prediction_contract_version": prediction_contract_version,
        "member_count": len(members),
        "member_digest": member_digest,
        "sealed_at": row.get("sealed_at"),
    }
    manifest_key = canonical_digest(manifest_payload)
    validate_supplied_digest(row.get("manifest_key"), manifest_key, "manifest_key")
    values = {
        "prediction_manifest_id": prediction_manifest_id,
        "manifest_key": manifest_key,
        "gate_manifest_id": gate_manifest_id,
        "model_role": model_role,
        "prediction_contract_version": prediction_contract_version,
        "member_count": len(members),
        "member_digest": member_digest,
        "sealed_at": row.get("sealed_at"),
        "created_at": row.get("created_at"),
    }
    columns = tuple(values)
    with manifest_savepoint(conn, "prediction_manifest"):
        existing = select_one(
            conn,
            "SELECT * FROM prediction_snapshot_manifest WHERE manifest_key=?",
            (manifest_key,),
        )
        if existing is None:
            try:
                conn.execute(
                    f"INSERT INTO prediction_snapshot_manifest({','.join(columns)}) "
                    f"VALUES({','.join(':' + column for column in columns)})",
                    values,
                )
                conn.executemany(
                    """
                    INSERT INTO prediction_snapshot_member(
                        prediction_manifest_id,sample_id,target_key,analysis_id,regime,
                        analysis_cutoff,probability_json,eligible,completion_state,
                        omission_reason,event_cluster_id,event_type,large_safety_slice,
                        synthetic,prediction_created_at,row_digest
                    ) VALUES(
                        :prediction_manifest_id,:sample_id,:target_key,:analysis_id,:regime,
                        :analysis_cutoff,:probability_json,:eligible,:completion_state,
                        :omission_reason,:event_cluster_id,:event_type,:large_safety_slice,
                        :synthetic,:prediction_created_at,:row_digest
                    )
                    """,
                    [
                        {
                            "prediction_manifest_id": prediction_manifest_id,
                            **member,
                            "probability_json": canonical_json(member["probabilities"]),
                        }
                        for member in members
                    ],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("prediction manifest identity conflicts") from exc
        else:
            if any(existing.get(column) != values.get(column) for column in columns):
                raise ValueError("prediction manifest key exists with different content")
            sealed = prediction_snapshot_manifest(conn, prediction_manifest_id)
            if sealed is None or [item["row_digest"] for item in sealed["members"]] != [
                item["row_digest"] for item in members
            ]:
                raise ValueError("prediction manifest members conflict with sealed content")
    saved = prediction_snapshot_manifest(conn, prediction_manifest_id)
    if saved is None:
        raise RuntimeError("prediction snapshot manifest could not be read back")
    return saved
