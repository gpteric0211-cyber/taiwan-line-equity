from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema
from evaluation.statistical_release_gate_v1 import evaluate_statistical_release_gate
from repository.single_track_v3_manifest_support import (
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    OFFICIAL_ADJUSTMENT_BASIS,
    TARGET_CLASSES,
    TARGET_LABEL_CONTRACT_VERSION,
    aware_timestamp,
    canonical_date,
    canonical_digest,
    canonical_json,
    current_evaluator_source_digest,
    current_target_label_source_digest,
    finite_positive_or_none,
    gate_manifest_row,
    gate_member_keys,
    json_value,
    manifest_savepoint,
    required_text,
    select_one,
    select_rows,
    validate_supplied_digest,
)
from repository.single_track_v3_prediction_manifest_repository import (
    prediction_snapshot_manifest,
)


def _outcome_source_rows(
    conn: sqlite3.Connection,
    gate_manifest_id: str,
    outcome_revision: str,
) -> list[dict[str, Any]]:
    rows = select_rows(
        conn,
        """
        SELECT
            h.sample_id AS holdout_sample_id,
            h.target_key AS holdout_target_key,
            o.*
        FROM statistical_gate_holdout_member h
        LEFT JOIN analysis_target_outcome o
          ON o.sample_id=h.sample_id
         AND o.target_key=h.target_key
         AND o.outcome_revision=?
        WHERE h.gate_manifest_id=?
        ORDER BY h.sample_id,h.target_key
        """,
        (outcome_revision, gate_manifest_id),
    )
    if any(row.get("sample_id") is None for row in rows):
        raise ValueError("every holdout member requires an explicit outcome row")
    return rows


def _outcome_member(
    source: Mapping[str, Any],
    *,
    outcome_revision: str,
    manifest_sealed_at: Any,
) -> dict[str, Any]:
    sample_id = required_text(source.get("sample_id"), "sample_id")
    target_key = required_text(source.get("target_key"), "target_key")
    if (
        sample_id != source.get("holdout_sample_id")
        or target_key != source.get("holdout_target_key")
    ):
        raise ValueError("outcome row does not match its holdout member")
    if source.get("outcome_revision") != outcome_revision:
        raise ValueError("outcome revision does not match its manifest")
    stock_code = required_text(source.get("stock_code"), "stock_code")
    if len(stock_code) != 4 or not stock_code.isdigit():
        raise ValueError("stock_code must be four digits")
    prediction_trade_date = canonical_date(
        source.get("prediction_trade_date"), "prediction_trade_date"
    )
    outcome_trade_date = source.get("outcome_trade_date")
    if outcome_trade_date is not None:
        outcome_trade_date = canonical_date(outcome_trade_date, "outcome_trade_date")
    adjustment_basis = required_text(source.get("adjustment_basis"), "adjustment_basis")
    if adjustment_basis != OFFICIAL_ADJUSTMENT_BASIS:
        raise ValueError("outcome adjustment_basis must be official_adjusted")
    quality_status = required_text(source.get("quality_status"), "quality_status")
    label = str(source.get("label") or "").strip() or None
    if quality_status == "ok":
        if label not in TARGET_CLASSES[target_key]:
            raise ValueError("eligible outcome label is invalid for its target")
        availability_reason = None
        prices = {
            field: finite_positive_or_none(source.get(field), field)
            for field in ("t_close", "next_open", "next_close")
        }
        if any(value is None for value in prices.values()):
            raise ValueError("eligible outcome requires all official adjusted prices")
        if outcome_trade_date is None:
            raise ValueError("eligible outcome requires outcome_trade_date")
    elif quality_status == "unavailable":
        if label is not None:
            raise ValueError("unavailable outcome cannot carry a label")
        availability_reason = required_text(
            source.get("availability_reason"), "availability_reason"
        )
        prices = {
            field: finite_positive_or_none(source.get(field), field)
            for field in ("t_close", "next_open", "next_close")
        }
    else:
        raise ValueError("outcome quality_status is invalid")
    available_at = aware_timestamp(source.get("available_at"), "available_at")
    recorded_at = aware_timestamp(source.get("recorded_at"), "recorded_at")
    if recorded_at < available_at:
        raise ValueError("outcome recorded_at cannot precede available_at")
    if recorded_at > aware_timestamp(manifest_sealed_at, "manifest.sealed_at"):
        raise ValueError("outcome was not recorded when its manifest was sealed")
    payload = {
        "contract": "OutcomeSnapshotMemberV1",
        "sample_id": sample_id,
        "target_key": target_key,
        "outcome_revision": outcome_revision,
        "stock_code": stock_code,
        "prediction_trade_date": prediction_trade_date,
        "outcome_trade_date": outcome_trade_date,
        "label": label,
        **prices,
        "adjustment_basis": adjustment_basis,
        "quality_status": quality_status,
        "availability_reason": availability_reason,
        "available_at": source.get("available_at"),
        "recorded_at": source.get("recorded_at"),
    }
    return {**payload, "row_digest": canonical_digest(payload)}


def _decode_outcome_manifest(
    conn: sqlite3.Connection,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(row)
    result["members"] = select_rows(
        conn,
        """
        SELECT * FROM outcome_snapshot_member
        WHERE outcome_manifest_id=? ORDER BY sample_id,target_key
        """,
        (str(result["outcome_manifest_id"]),),
    )
    return result


def outcome_snapshot_manifest(
    conn: sqlite3.Connection,
    outcome_manifest_id: str,
) -> dict[str, Any] | None:
    """Read one immutable outcome snapshot without source-table writes."""

    row = select_one(
        conn,
        "SELECT * FROM outcome_snapshot_manifest WHERE outcome_manifest_id=?",
        (str(outcome_manifest_id),),
    )
    return _decode_outcome_manifest(conn, row) if row is not None else None


def seal_outcome_snapshot_manifest(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Open and seal the predeclared holdout outcome revision as an immutable snapshot."""

    ensure_single_track_v3_schema(conn)
    row = dict(manifest)
    outcome_manifest_id = required_text(
        row.get("outcome_manifest_id"), "outcome_manifest_id"
    )
    gate_manifest_id = required_text(row.get("gate_manifest_id"), "gate_manifest_id")
    gate = gate_manifest_row(conn, gate_manifest_id)
    if gate["evaluator_source_digest"] != current_evaluator_source_digest():
        raise ValueError("evaluator source changed after the statistical gate was sealed")
    if gate["target_label_source_digest"] != current_target_label_source_digest():
        raise ValueError("target label source changed after the statistical gate was sealed")
    outcome_revision = required_text(row.get("outcome_revision"), "outcome_revision")
    target_label_contract_version = required_text(
        row.get("target_label_contract_version"), "target_label_contract_version"
    )
    if target_label_contract_version != TARGET_LABEL_CONTRACT_VERSION:
        raise ValueError("outcome manifest requires TargetLabelContractV1")
    adjustment_basis = required_text(row.get("adjustment_basis"), "adjustment_basis")
    if adjustment_basis != OFFICIAL_ADJUSTMENT_BASIS:
        raise ValueError("outcome manifest requires official_adjusted basis")
    created_at = aware_timestamp(row.get("created_at"), "created_at")
    holdout_opened_at = aware_timestamp(
        row.get("holdout_opened_at"), "holdout_opened_at"
    )
    sealed_at = aware_timestamp(row.get("sealed_at"), "sealed_at")
    if created_at != holdout_opened_at:
        raise ValueError("outcome manifest created_at must equal holdout_opened_at")
    if holdout_opened_at <= aware_timestamp(gate["sealed_at"], "gate.sealed_at"):
        raise ValueError("holdout cannot open before its statistical gate is sealed")
    if sealed_at < holdout_opened_at:
        raise ValueError("outcome manifest sealed_at cannot precede holdout_opened_at")
    source_rows = _outcome_source_rows(conn, gate_manifest_id, outcome_revision)
    members = [
        _outcome_member(
            source,
            outcome_revision=outcome_revision,
            manifest_sealed_at=row.get("sealed_at"),
        )
        for source in source_rows
    ]
    first_source = min(
        members,
        key=lambda item: aware_timestamp(item["available_at"], "outcome.available_at"),
    )
    last_source = max(
        members,
        key=lambda item: aware_timestamp(item["available_at"], "outcome.available_at"),
    )
    first_available_at = str(first_source["available_at"])
    last_available_at = str(last_source["available_at"])
    if aware_timestamp(last_available_at, "last_outcome_available_at") > holdout_opened_at:
        raise ValueError("holdout may open only after all selected outcomes are available")
    member_digest = canonical_digest([member["row_digest"] for member in members])
    validate_supplied_digest(row.get("member_digest"), member_digest, "member_digest")
    manifest_payload = {
        "contract": "OutcomeSnapshotManifestV1",
        "gate_manifest_key": gate["manifest_key"],
        "outcome_revision": outcome_revision,
        "target_label_contract_version": target_label_contract_version,
        "target_label_source_digest": gate["target_label_source_digest"],
        "adjustment_basis": adjustment_basis,
        "holdout_opened_at": row.get("holdout_opened_at"),
        "first_outcome_available_at": first_available_at,
        "last_outcome_available_at": last_available_at,
        "member_count": len(members),
        "member_digest": member_digest,
        "sealed_at": row.get("sealed_at"),
    }
    manifest_key = canonical_digest(manifest_payload)
    validate_supplied_digest(row.get("manifest_key"), manifest_key, "manifest_key")
    values = {
        "outcome_manifest_id": outcome_manifest_id,
        "manifest_key": manifest_key,
        "gate_manifest_id": gate_manifest_id,
        "outcome_revision": outcome_revision,
        "target_label_contract_version": target_label_contract_version,
        "target_label_source_digest": gate["target_label_source_digest"],
        "adjustment_basis": adjustment_basis,
        "holdout_opened_at": row.get("holdout_opened_at"),
        "first_outcome_available_at": first_available_at,
        "last_outcome_available_at": last_available_at,
        "member_count": len(members),
        "member_digest": member_digest,
        "sealed_at": row.get("sealed_at"),
        "created_at": row.get("created_at"),
    }
    columns = tuple(values)
    with manifest_savepoint(conn, "outcome_manifest"):
        existing = select_one(
            conn,
            "SELECT * FROM outcome_snapshot_manifest WHERE manifest_key=?",
            (manifest_key,),
        )
        if existing is None:
            try:
                conn.execute(
                    f"INSERT INTO outcome_snapshot_manifest({','.join(columns)}) "
                    f"VALUES({','.join(':' + column for column in columns)})",
                    values,
                )
                conn.executemany(
                    """
                    INSERT INTO outcome_snapshot_member(
                        outcome_manifest_id,sample_id,target_key,stock_code,
                        prediction_trade_date,outcome_trade_date,label,t_close,next_open,
                        next_close,adjustment_basis,quality_status,availability_reason,
                        available_at,recorded_at,row_digest
                    ) VALUES(
                        :outcome_manifest_id,:sample_id,:target_key,:stock_code,
                        :prediction_trade_date,:outcome_trade_date,:label,:t_close,:next_open,
                        :next_close,:adjustment_basis,:quality_status,:availability_reason,
                        :available_at,:recorded_at,:row_digest
                    )
                    """,
                    [
                        {"outcome_manifest_id": outcome_manifest_id, **member}
                        for member in members
                    ],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("outcome manifest identity conflicts") from exc
        else:
            if any(existing.get(column) != values.get(column) for column in columns):
                raise ValueError("outcome manifest key exists with different content")
            sealed = outcome_snapshot_manifest(conn, outcome_manifest_id)
            if sealed is None or [item["row_digest"] for item in sealed["members"]] != [
                item["row_digest"] for item in members
            ]:
                raise ValueError("outcome manifest members conflict with sealed content")
    saved = outcome_snapshot_manifest(conn, outcome_manifest_id)
    if saved is None:
        raise RuntimeError("outcome snapshot manifest could not be read back")
    return saved


def _prediction_payload(member: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eligible": bool(member["eligible"]),
        "completion_state": str(member["completion_state"]),
        "probabilities": dict(member["probabilities"]),
        "omission_reason": member.get("omission_reason"),
    }


def paired_release_rows_from_snapshot_manifests(
    conn: sqlite3.Connection,
    *,
    stable_prediction_manifest_id: str,
    candidate_prediction_manifest_id: str,
    outcome_manifest_id: str,
) -> tuple[list[dict[str, Any]], int]:
    """Build evaluator rows solely from immutable snapshots; perform no writes."""

    stable = prediction_snapshot_manifest(conn, stable_prediction_manifest_id)
    candidate = prediction_snapshot_manifest(conn, candidate_prediction_manifest_id)
    outcome = outcome_snapshot_manifest(conn, outcome_manifest_id)
    if stable is None or candidate is None or outcome is None:
        raise ValueError("evaluation snapshot manifest does not exist")
    if stable["model_role"] != "stable" or candidate["model_role"] != "candidate":
        raise ValueError("prediction manifest roles are reversed or invalid")
    gate_manifest_id = str(stable["gate_manifest_id"])
    if (
        candidate["gate_manifest_id"] != gate_manifest_id
        or outcome["gate_manifest_id"] != gate_manifest_id
    ):
        raise ValueError("evaluation manifests do not share one statistical gate")
    opened_at = aware_timestamp(outcome["holdout_opened_at"], "holdout_opened_at")
    if (
        aware_timestamp(stable["sealed_at"], "stable.sealed_at") >= opened_at
        or aware_timestamp(candidate["sealed_at"], "candidate.sealed_at") >= opened_at
    ):
        raise ValueError("prediction manifests must be sealed before the holdout opens")
    expected_keys = {
        (item["sample_id"], item["target_key"])
        for item in gate_member_keys(conn, gate_manifest_id)
    }
    stable_index = {
        (item["sample_id"], item["target_key"]): item for item in stable["members"]
    }
    candidate_index = {
        (item["sample_id"], item["target_key"]): item for item in candidate["members"]
    }
    outcome_index = {
        (item["sample_id"], item["target_key"]): item for item in outcome["members"]
    }
    if set(stable_index) != expected_keys or set(candidate_index) != expected_keys:
        raise ValueError("prediction snapshot membership differs from the sealed holdout")
    if set(outcome_index) != expected_keys:
        raise ValueError("outcome snapshot membership differs from the sealed holdout")
    rows: list[dict[str, Any]] = []
    unavailable = 0
    for identity in sorted(expected_keys):
        stable_member = stable_index[identity]
        candidate_member = candidate_index[identity]
        outcome_member = outcome_index[identity]
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
            if stable_member.get(field) != candidate_member.get(field):
                raise ValueError(f"paired prediction metadata mismatch: {field}")
        if stable_member.get("synthetic") or candidate_member.get("synthetic"):
            raise ValueError("synthetic prediction snapshots are forbidden")
        if aware_timestamp(
            outcome_member["available_at"], "outcome.available_at"
        ) <= aware_timestamp(stable_member["analysis_cutoff"], "analysis_cutoff"):
            raise ValueError("outcome must become available after the prediction cutoff")
        if outcome_member["quality_status"] != "ok":
            unavailable += 1
            continue
        rows.append(
            {
                "sample_id": identity[0],
                "stock_code": outcome_member["stock_code"],
                "prediction_trade_date": outcome_member["prediction_trade_date"],
                "target_key": identity[1],
                "regime": stable_member["regime"],
                "actual_label": outcome_member["label"],
                "outcome_revision": outcome["outcome_revision"],
                "event_cluster_id": stable_member.get("event_cluster_id"),
                "event_type": stable_member.get("event_type"),
                "large_safety_slice": bool(stable_member["large_safety_slice"]),
                "synthetic": False,
                "stable": _prediction_payload(stable_member),
                "candidate": _prediction_payload(candidate_member),
            }
        )
    return rows, unavailable


def _decode_evaluation_manifest(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["evaluation"] = json_value(result.pop("evaluation_json"), {})
    return result


def statistical_evaluation_manifest(
    conn: sqlite3.Connection,
    evaluation_manifest_id: str,
) -> dict[str, Any] | None:
    """Read a sealed evaluator result without rerunning or mutating evidence."""

    row = select_one(
        conn,
        "SELECT * FROM statistical_evaluation_manifest WHERE evaluation_manifest_id=?",
        (str(evaluation_manifest_id),),
    )
    return _decode_evaluation_manifest(row) if row is not None else None


def seal_statistical_evaluation_manifest(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the frozen evaluator over immutable manifests and seal its exact result."""

    ensure_single_track_v3_schema(conn)
    row = dict(manifest)
    evaluation_manifest_id = required_text(
        row.get("evaluation_manifest_id"), "evaluation_manifest_id"
    )
    stable_id = required_text(
        row.get("stable_prediction_manifest_id"),
        "stable_prediction_manifest_id",
    )
    candidate_id = required_text(
        row.get("candidate_prediction_manifest_id"),
        "candidate_prediction_manifest_id",
    )
    outcome_id = required_text(row.get("outcome_manifest_id"), "outcome_manifest_id")
    stable = prediction_snapshot_manifest(conn, stable_id)
    candidate = prediction_snapshot_manifest(conn, candidate_id)
    outcome = outcome_snapshot_manifest(conn, outcome_id)
    if stable is None or candidate is None or outcome is None:
        raise ValueError("evaluation snapshot manifest does not exist")
    gate_manifest_id = str(stable["gate_manifest_id"])
    gate = gate_manifest_row(conn, gate_manifest_id)
    if gate["evaluator_source_digest"] != current_evaluator_source_digest():
        raise ValueError("evaluator source changed after the statistical gate was sealed")
    if gate["target_label_source_digest"] != current_target_label_source_digest():
        raise ValueError("target label source changed after the statistical gate was sealed")
    if row.get("gate_manifest_id") not in (None, gate_manifest_id):
        raise ValueError("gate_manifest_id does not match prediction manifests")
    evaluated_at = aware_timestamp(row.get("evaluated_at"), "evaluated_at")
    created_at = aware_timestamp(row.get("created_at"), "created_at")
    if evaluated_at < aware_timestamp(outcome["sealed_at"], "outcome.sealed_at"):
        raise ValueError("evaluation cannot precede the sealed outcome snapshot")
    if created_at < evaluated_at:
        raise ValueError("evaluation created_at cannot precede evaluated_at")
    paired_rows, unavailable = paired_release_rows_from_snapshot_manifests(
        conn,
        stable_prediction_manifest_id=stable_id,
        candidate_prediction_manifest_id=candidate_id,
        outcome_manifest_id=outcome_id,
    )
    evaluation = evaluate_statistical_release_gate(paired_rows)
    evaluation_digest = canonical_digest(evaluation)
    validate_supplied_digest(
        row.get("evaluation_digest"), evaluation_digest, "evaluation_digest"
    )
    if row.get("evaluation_result") not in (None, evaluation["result"]):
        raise ValueError("evaluation_result does not match the frozen evaluator")
    holdout_member_count = int(gate["holdout_member_count"])
    if len(paired_rows) + unavailable != holdout_member_count:
        raise ValueError("evaluation denominator does not match the holdout manifest")
    evaluation_key = canonical_digest(
        {
            "contract": "StatisticalEvaluationManifestV1",
            "gate_manifest_key": gate["manifest_key"],
            "stable_prediction_manifest_key": stable["manifest_key"],
            "candidate_prediction_manifest_key": candidate["manifest_key"],
            "outcome_manifest_key": outcome["manifest_key"],
            "evaluation_digest": evaluation_digest,
            "evaluated_at": row.get("evaluated_at"),
        }
    )
    validate_supplied_digest(row.get("evaluation_key"), evaluation_key, "evaluation_key")
    values = {
        "evaluation_manifest_id": evaluation_manifest_id,
        "evaluation_key": evaluation_key,
        "gate_manifest_id": gate_manifest_id,
        "stable_prediction_manifest_id": stable_id,
        "candidate_prediction_manifest_id": candidate_id,
        "outcome_manifest_id": outcome_id,
        "gate_version": gate["gate_version"],
        "gate_spec_hash": gate["gate_spec_hash"],
        "evaluator_source_digest": gate["evaluator_source_digest"],
        "target_label_contract_version": gate["target_label_contract_version"],
        "target_label_source_digest": gate["target_label_source_digest"],
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "holdout_member_count": holdout_member_count,
        "eligible_outcome_count": len(paired_rows),
        "outcome_unavailable_count": unavailable,
        "synthetic_row_count": 0,
        "evaluation_result": evaluation["result"],
        "evaluation_json": canonical_json(evaluation),
        "evaluation_digest": evaluation_digest,
        "evaluated_at": row.get("evaluated_at"),
        "created_at": row.get("created_at"),
    }
    columns = tuple(values)
    with manifest_savepoint(conn, "evaluation_manifest"):
        existing = select_one(
            conn,
            "SELECT * FROM statistical_evaluation_manifest WHERE evaluation_key=?",
            (evaluation_key,),
        )
        if existing is None:
            try:
                conn.execute(
                    f"INSERT INTO statistical_evaluation_manifest({','.join(columns)}) "
                    f"VALUES({','.join(':' + column for column in columns)})",
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("statistical evaluation manifest identity conflicts") from exc
        elif any(existing.get(column) != values.get(column) for column in columns):
            raise ValueError("evaluation manifest key exists with different content")
    saved = statistical_evaluation_manifest(conn, evaluation_manifest_id)
    if saved is None:
        raise RuntimeError("statistical evaluation manifest could not be read back")
    return saved
