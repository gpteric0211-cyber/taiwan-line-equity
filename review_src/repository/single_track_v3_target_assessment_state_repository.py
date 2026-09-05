"""Durable target-impact state transitions for Single-Track V3.

Identity construction and idempotent row creation remain in
`single_track_v3_assessment_repository`.  This module owns only target
dispatch, seal, retry, stale-completion, and watchdog transitions.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib
import sqlite3
from typing import Any, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema
from repository.single_track_v3_assessment_repository import (
    _ATTEMPT_FAILURE_CLASSES,
    _ATTEMPT_KINDS,
    _DIRECTION_PROBABILITY_KEYS,
    _MAGNITUDE_PROBABILITY_KEYS,
    _TARGET_FORBIDDEN_RESULT_FIELDS,
    _TARGET_REQUIRED_RESULT_FIELDS,
    _aware_timestamp,
    _begin_savepoint,
    _boolean_flag,
    _canonical_json,
    _contains_forbidden_key,
    _database_now_tpe,
    _probability_bucket,
    _release_savepoint,
    _required_text,
    _retry_available_at,
    _rollback_savepoint,
    _select_one,
    _string_list,
    _timestamp,
)


def begin_target_model_dispatch(
    conn: sqlite3.Connection,
    *,
    target_impact_assessment_id: str,
    attempt_id: str,
    attempt_kind: str,
    dispatch_started_at: str,
) -> dict[str, Any]:
    """Cross the target dispatch boundary; queue/lease wait does not call this function."""

    ensure_single_track_v3_schema(conn)
    attempt_kind = _required_text(attempt_kind, "attempt_kind")
    if attempt_kind not in _ATTEMPT_KINDS:
        raise ValueError("attempt_kind is invalid")
    attempt_id = _required_text(attempt_id, "attempt_id")
    dispatch = _aware_timestamp(dispatch_started_at, "dispatch_started_at")
    existing = _select_one(
        conn,
        "SELECT * FROM target_impact_assessment_attempt WHERE attempt_id=?",
        (attempt_id,),
    )
    if existing is not None:
        if (
            existing["target_impact_assessment_id"] != target_impact_assessment_id
            or existing["attempt_kind"] != attempt_kind
            or existing["dispatch_started_at"] != dispatch_started_at
        ):
            raise ValueError("attempt_id already exists with different content")
        return existing
    assessment = _select_one(
        conn,
        "SELECT * FROM target_impact_assessment WHERE target_impact_assessment_id=?",
        (target_impact_assessment_id,),
    )
    if assessment is None:
        raise KeyError("target impact assessment not found")
    if assessment["assessment_status"] not in {"queued", "retry_wait"}:
        raise ValueError("target impact assessment is not dispatchable")
    if assessment.get("next_attempt_at") is not None and dispatch < _aware_timestamp(
        assessment["next_attempt_at"], "next_attempt_at"
    ):
        raise ValueError("target retry delay has not elapsed")
    if int(assessment["target_attempt_count"]) >= 4:
        raise ValueError("target attempts exhausted")
    if attempt_kind == "contract_repair" and int(assessment["model_repair_count"]) >= 1:
        raise ValueError("target model repair already consumed")
    recovery_deadline = _aware_timestamp(
        assessment["target_recovery_deadline_at"], "target_recovery_deadline_at"
    )
    issue_deadline = _aware_timestamp(
        assessment["prediction_issue_deadline_at"], "prediction_issue_deadline_at"
    )
    if dispatch < _aware_timestamp(
        assessment["resolution_eligible_at"], "resolution_eligible_at"
    ):
        raise ValueError("target cannot dispatch before resolution eligibility")
    if dispatch >= min(recovery_deadline, issue_deadline):
        raise ValueError("target recovery/publish deadline elapsed before dispatch")
    hard_deadline = min(dispatch + timedelta(seconds=300), recovery_deadline, issue_deadline)
    attempt_no = int(assessment["target_attempt_count"]) + 1
    attempt_values = {
        "attempt_id": attempt_id,
        "target_impact_assessment_id": target_impact_assessment_id,
        "attempt_no": attempt_no,
        "attempt_kind": attempt_kind,
        "attempt_status": "dispatching",
        "dispatch_started_at": dispatch_started_at,
        "attempt_hard_deadline_at": _timestamp(hard_deadline),
        "completed_at": None,
        "result_digest": None,
        "validation_state": "pending",
        "failure_class": None,
        "failure_code": None,
        "created_at": dispatch_started_at,
        "updated_at": dispatch_started_at,
    }
    savepoint = "single_track_target_dispatch"
    _begin_savepoint(conn, savepoint)
    try:
        updated = conn.execute(
            """
            UPDATE target_impact_assessment SET
                assessment_status='dispatching',active_attempt_id=?,next_attempt_at=NULL,
                target_attempt_count=?,model_repair_count=?,
                state_version=state_version+1,updated_at=?
            WHERE target_impact_assessment_id=? AND state_version=?
              AND assessment_status IN ('queued','retry_wait') AND target_attempt_count<4
            """,
            (
                attempt_id,
                attempt_no,
                int(assessment["model_repair_count"])
                + (1 if attempt_kind == "contract_repair" else 0),
                dispatch_started_at,
                target_impact_assessment_id,
                int(assessment["state_version"]),
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("target dispatch lost compare-and-swap race")
        conn.execute(
            """
            INSERT INTO target_impact_assessment_attempt(
                attempt_id,target_impact_assessment_id,attempt_no,attempt_kind,
                attempt_status,dispatch_started_at,attempt_hard_deadline_at,
                completed_at,result_digest,validation_state,failure_class,
                failure_code,created_at,updated_at
            ) VALUES(
                :attempt_id,:target_impact_assessment_id,:attempt_no,:attempt_kind,
                :attempt_status,:dispatch_started_at,:attempt_hard_deadline_at,
                :completed_at,:result_digest,:validation_state,:failure_class,
                :failure_code,:created_at,:updated_at
            )
            """,
            attempt_values,
        )
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    return dict(attempt_values)


def _record_stale_target_completion(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    result_digest: str,
    completed_at: str,
) -> None:
    existing = _select_one(
        conn,
        """
        SELECT attempt_status,result_digest
        FROM target_impact_assessment_attempt WHERE attempt_id=?
        """,
        (attempt_id,),
    )
    if existing is None:
        raise KeyError("target attempt not found")
    if existing["attempt_status"] == "succeeded":
        if existing["result_digest"] != result_digest:
            raise ValueError("sealed target attempt conflicts with stale completion")
        return
    if existing["result_digest"] not in (None, result_digest):
        raise ValueError("stale target completion conflicts with existing audit digest")
    conn.execute(
        """
        UPDATE target_impact_assessment_attempt SET
            attempt_status='stale_completion',completed_at=?,result_digest=?,
            validation_state='pass',updated_at=?
        WHERE attempt_id=? AND attempt_status!='succeeded'
        """,
        (completed_at, result_digest, completed_at, attempt_id),
    )


def seal_target_impact_assessment_result(
    conn: sqlite3.Connection,
    *,
    target_impact_assessment_id: str,
    attempt_id: str,
    expected_state_version: int,
    result: Mapping[str, Any],
    completed_at: str,
    expected_promotion_epoch: int = 0,
) -> str | None:
    """Seal a typed target result; late or terminal-losing completions are audit-only."""

    ensure_single_track_v3_schema(conn)
    completed = _aware_timestamp(completed_at, "completed_at")
    payload = dict(result)
    missing = sorted(_TARGET_REQUIRED_RESULT_FIELDS - set(payload))
    if missing:
        raise ValueError(f"target result missing required fields: {','.join(missing)}")
    forbidden = _contains_forbidden_key(payload, _TARGET_FORBIDDEN_RESULT_FIELDS)
    if forbidden is not None:
        raise ValueError(f"target result contains prohibited price/return field: {forbidden}")
    target_materiality = str(payload["target_materiality"])
    if target_materiality not in {
        "critical", "high", "medium", "low", "unknown_pending"
    }:
        raise ValueError("target_materiality is invalid")
    target_direction = str(payload["target_direction"])
    if target_direction not in {"positive", "negative", "mixed", "neutral", "unknown"}:
        raise ValueError("target_direction is invalid")
    target_impact_magnitude = str(payload["target_impact_magnitude"])
    if target_impact_magnitude not in {
        "negligible", "low", "medium", "high", "extreme", "unknown_pending"
    }:
        raise ValueError("target_impact_magnitude is invalid")
    regime_selection = str(payload["regime_selection"])
    if regime_selection not in {
        "normal", "material_event", "material_pending", "suppressed_pending"
    }:
        raise ValueError("regime_selection is invalid")
    target_relationship_type = str(payload["target_relationship_type"])
    if target_relationship_type not in {
        "direct_company", "parent_subsidiary_group", "supply_chain", "customer",
        "peer", "incidental_mention", "unresolved",
    }:
        raise ValueError("target_relationship_type is invalid")
    priced_in_state = str(payload["priced_in_state"])
    if priced_in_state not in {"already_priced", "still_developing", "mixed", "unknown"}:
        raise ValueError("priced_in_state is invalid")
    calibration_state = str(payload["calibration_state"])
    if calibration_state not in {"unavailable", "shadow", "calibrated", "rejected"}:
        raise ValueError("calibration_state is invalid")
    probability_fields_present = {
        field for field in ("direction_probabilities", "magnitude_probabilities")
        if field in payload
    }
    if calibration_state == "calibrated":
        if probability_fields_present != {
            "direction_probabilities", "magnitude_probabilities"
        }:
            raise ValueError("calibrated target result requires both backend probability fields")
        direction = _probability_bucket(
            payload["direction_probabilities"],
            field="direction_probabilities",
            expected_keys=_DIRECTION_PROBABILITY_KEYS,
        )
        magnitude = _probability_bucket(
            payload["magnitude_probabilities"],
            field="magnitude_probabilities",
            expected_keys=_MAGNITUDE_PROBABILITY_KEYS,
        )
    else:
        if probability_fields_present:
            raise ValueError(
                "unreleased Qwen target result cannot supply calibrated probability fields"
            )
        direction = None
        magnitude = None
    eligible_for_explanation = _boolean_flag(
        payload["eligible_for_explanation"], "eligible_for_explanation"
    )
    eligible_for_weight = _boolean_flag(
        payload["eligible_for_weight"], "eligible_for_weight"
    )
    if eligible_for_weight and calibration_state != "calibrated":
        raise ValueError("uncalibrated target result cannot be eligible for weight")
    normalized_result = {
        "calibration_state": calibration_state,
        "counterevidence": _string_list(payload["counterevidence"], "counterevidence"),
        "direction_probabilities": direction,
        "drivers": _string_list(payload["drivers"], "drivers"),
        "eligible_for_explanation": bool(eligible_for_explanation),
        "eligible_for_weight": bool(eligible_for_weight),
        "evidence_ids": _string_list(payload["evidence_ids"], "evidence_ids", unique=True),
        "magnitude_probabilities": magnitude,
        "priced_in_state": priced_in_state,
        "regime_selection": regime_selection,
        "target_direction": target_direction,
        "target_impact_magnitude": target_impact_magnitude,
        "target_materiality": target_materiality,
        "target_relationship_type": target_relationship_type,
        "uncertainty": _string_list(payload["uncertainty"], "uncertainty"),
    }
    result_json = _canonical_json(normalized_result)
    result_digest = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    attempt = _select_one(
        conn,
        "SELECT * FROM target_impact_assessment_attempt WHERE attempt_id=?",
        (attempt_id,),
    )
    if (
        attempt is None
        or attempt["target_impact_assessment_id"] != target_impact_assessment_id
    ):
        raise KeyError("target attempt not found")
    if attempt["attempt_status"] == "succeeded":
        if attempt["result_digest"] != result_digest:
            raise ValueError("sealed target attempt conflicts with retry content")
        return result_digest
    hard_deadline = _aware_timestamp(
        attempt["attempt_hard_deadline_at"], "attempt_hard_deadline_at"
    )
    if completed > hard_deadline:
        if _aware_timestamp(_database_now_tpe(conn), "database_now") < hard_deadline:
            raise ValueError("target completed_at is ahead of the database clock")
        record_target_attempt_failure(
            conn,
            target_impact_assessment_id=target_impact_assessment_id,
            attempt_id=attempt_id,
            expected_state_version=expected_state_version,
            expected_promotion_epoch=expected_promotion_epoch,
            failure_class="timeout",
            failure_code="attempt_hard_deadline_elapsed",
        )
        _record_stale_target_completion(
            conn,
            attempt_id=attempt_id,
            result_digest=result_digest,
            completed_at=completed_at,
        )
        return None
    database_now = _database_now_tpe(conn)
    target = _select_one(
        conn,
        "SELECT * FROM target_impact_assessment WHERE target_impact_assessment_id=?",
        (target_impact_assessment_id,),
    )
    if target is None:
        raise KeyError("target impact assessment not found")
    if eligible_for_weight:
        if (
            target_materiality == "unknown_pending"
            or target_direction == "unknown"
            or target_impact_magnitude == "unknown_pending"
            or regime_selection not in {"normal", "material_event"}
            or target_relationship_type == "unresolved"
        ):
            raise ValueError("incomplete target contract cannot be eligible for weight")
        weight_version = str(target.get("weight_version") or "").lower()
        if not weight_version or "candidate" in weight_version or "unreleased" in weight_version:
            raise ValueError("unreleased weight_version cannot be eligible for weight")
    target_deadline = min(
        _aware_timestamp(target["target_recovery_deadline_at"], "target_recovery_deadline_at"),
        _aware_timestamp(target["prediction_issue_deadline_at"], "prediction_issue_deadline_at"),
    )
    if _aware_timestamp(database_now, "database_now") >= target_deadline:
        record_target_attempt_failure(
            conn,
            target_impact_assessment_id=target_impact_assessment_id,
            attempt_id=attempt_id,
            expected_state_version=expected_state_version,
            expected_promotion_epoch=expected_promotion_epoch,
            failure_class="deadline_elapsed",
            failure_code="target_recovery_deadline_elapsed",
        )
        _record_stale_target_completion(
            conn,
            attempt_id=attempt_id,
            result_digest=result_digest,
            completed_at=completed_at,
        )
        return None
    savepoint = "single_track_target_seal"
    _begin_savepoint(conn, savepoint)
    try:
        updated = conn.execute(
            """
            UPDATE target_impact_assessment SET
                assessment_status='complete',decision_status=?,
                validation_state='pass',direction_probabilities_json=?,
                magnitude_probabilities_json=?,drivers_json=?,counterevidence_json=?,
                uncertainty_json=?,evidence_ids_json=?,priced_in_state=?,
                eligible_for_explanation=?,eligible_for_weight=?,calibration_state=?,
                target_materiality=?,target_direction=?,target_impact_magnitude=?,
                regime_selection=?,target_relationship_type=?,
                result_digest=?,sealed_at=?,active_attempt_id=NULL,next_attempt_at=NULL,
                state_version=state_version+1,updated_at=?
            WHERE target_impact_assessment_id=? AND state_version=? AND promotion_epoch=?
              AND assessment_status='dispatching' AND active_attempt_id=?
              AND julianday(?) < julianday(target_recovery_deadline_at)
              AND julianday(?) < julianday(prediction_issue_deadline_at)
            """,
            (
                "predictive" if eligible_for_weight else "explanation_only",
                _canonical_json(direction) if direction is not None else None,
                _canonical_json(magnitude) if magnitude is not None else None,
                _canonical_json(normalized_result["drivers"]),
                _canonical_json(normalized_result["counterevidence"]),
                _canonical_json(normalized_result["uncertainty"]),
                _canonical_json(normalized_result["evidence_ids"]),
                priced_in_state,
                eligible_for_explanation,
                eligible_for_weight,
                calibration_state,
                target_materiality,
                target_direction,
                target_impact_magnitude,
                regime_selection,
                target_relationship_type,
                result_digest,
                completed_at,
                completed_at,
                target_impact_assessment_id,
                int(expected_state_version),
                int(expected_promotion_epoch),
                attempt_id,
                database_now,
                database_now,
            ),
        )
        if updated.rowcount != 1:
            _record_stale_target_completion(
                conn,
                attempt_id=attempt_id,
                result_digest=result_digest,
                completed_at=completed_at,
            )
            _release_savepoint(conn, savepoint)
            return None
        attempt_updated = conn.execute(
            """
            UPDATE target_impact_assessment_attempt SET
                attempt_status='succeeded',completed_at=?,result_digest=?,
                validation_state='pass',updated_at=?
            WHERE attempt_id=? AND attempt_status='dispatching'
            """,
            (completed_at, result_digest, completed_at, attempt_id),
        )
        if attempt_updated.rowcount != 1:
            raise RuntimeError("target attempt was not sealable")
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    return result_digest


def record_target_attempt_failure(
    conn: sqlite3.Connection,
    *,
    target_impact_assessment_id: str,
    attempt_id: str,
    expected_state_version: int,
    expected_promotion_epoch: int,
    failure_class: str,
    failure_code: str,
    predicted_runtime_seconds: int = 300,
) -> dict[str, Any]:
    """Retry or terminalize only this target; shared content and sibling targets are untouched."""

    ensure_single_track_v3_schema(conn)
    failure_class = _required_text(failure_class, "failure_class")
    failure_code = _required_text(failure_code, "failure_code")
    if failure_class not in _ATTEMPT_FAILURE_CLASSES:
        raise ValueError("failure_class is invalid")
    predicted_runtime_seconds = int(predicted_runtime_seconds)
    if predicted_runtime_seconds < 1 or predicted_runtime_seconds > 300:
        raise ValueError("predicted_runtime_seconds must be between 1 and 300")
    target = _select_one(
        conn,
        "SELECT * FROM target_impact_assessment WHERE target_impact_assessment_id=?",
        (target_impact_assessment_id,),
    )
    attempt = _select_one(
        conn,
        "SELECT * FROM target_impact_assessment_attempt WHERE attempt_id=?",
        (attempt_id,),
    )
    if (
        target is None
        or attempt is None
        or attempt["target_impact_assessment_id"] != target_impact_assessment_id
    ):
        raise KeyError("target assessment attempt not found")
    if target["assessment_status"] in {
        "complete",
        "blocked_dependency_terminal",
        "superseded",
    }:
        return {
            "applied": False,
            "assessment_status": target["assessment_status"],
            "next_attempt_at": target.get("next_attempt_at"),
        }
    if target["assessment_status"] == "failed_terminal":
        if (
            attempt.get("failure_class") not in (None, failure_class)
            or attempt.get("failure_code") not in (None, failure_code)
        ):
            raise ValueError("target failure replay conflicts with immutable attempt outcome")
        return {
            "applied": False,
            "assessment_status": "failed_terminal",
            "next_attempt_at": None,
        }
    if attempt["attempt_status"] != "dispatching":
        if (
            attempt.get("failure_class") == failure_class
            and attempt.get("failure_code") == failure_code
        ):
            return {
                "applied": False,
                "assessment_status": target["assessment_status"],
                "next_attempt_at": target.get("next_attempt_at"),
            }
        raise ValueError("target attempt failure is not recordable from its current state")
    database_now = _database_now_tpe(conn)
    now = _aware_timestamp(database_now, "database_now")
    if now < _aware_timestamp(attempt["dispatch_started_at"], "dispatch_started_at"):
        raise ValueError("target failure cannot precede durable dispatch")
    hard_deadline = _aware_timestamp(
        attempt["attempt_hard_deadline_at"], "attempt_hard_deadline_at"
    )
    if failure_class == "timeout" and now < hard_deadline:
        raise ValueError("timeout cannot be recorded before the attempt hard deadline")
    recovery_deadline = min(
        _aware_timestamp(target["target_recovery_deadline_at"], "target_recovery_deadline_at"),
        _aware_timestamp(target["prediction_issue_deadline_at"], "prediction_issue_deadline_at"),
    )
    attempt_no = int(attempt["attempt_no"])
    terminal_reason: str | None = None
    if failure_class == "deadline_elapsed":
        terminal_reason = "target_recovery_deadline_elapsed"
    elif failure_class == "permanent":
        terminal_reason = "target_permanent_failure"
    elif attempt_no >= 4:
        terminal_reason = "target_attempts_exhausted"
    elif now >= recovery_deadline:
        terminal_reason = "target_recovery_deadline_elapsed"
    attempt_status = (
        "timed_out" if failure_class in {"timeout", "deadline_elapsed"} else
        "permanent_failure" if failure_class == "permanent" else
        "retryable_failure"
    )
    savepoint = "single_track_target_failure"
    _begin_savepoint(conn, savepoint)
    try:
        attempt_updated = conn.execute(
            """
            UPDATE target_impact_assessment_attempt SET
                attempt_status=?,completed_at=?,failure_class=?,failure_code=?,
                validation_state=?,updated_at=?
            WHERE attempt_id=? AND attempt_status='dispatching'
            """,
            (
                attempt_status,
                database_now,
                failure_class,
                failure_code,
                "reject" if failure_class in {"malformed", "validator_reject", "permanent"} else "pending",
                database_now,
                attempt_id,
            ),
        )
        if attempt_updated.rowcount != 1:
            raise RuntimeError("target attempt failure lost compare-and-swap race")
        if terminal_reason is not None:
            target_updated = conn.execute(
                """
                UPDATE target_impact_assessment SET
                    assessment_status='failed_terminal',
                    decision_status='suppressed_unresolved',validation_state='reject',
                    eligible_for_explanation=0,eligible_for_weight=0,
                    terminal_reason_code=?,terminal_at=?,
                    promotion_epoch=promotion_epoch+1,
                    active_attempt_id=NULL,next_attempt_at=NULL,
                    state_version=state_version+1,updated_at=?
                WHERE target_impact_assessment_id=? AND state_version=? AND promotion_epoch=?
                  AND assessment_status='dispatching' AND active_attempt_id=?
                """,
                (
                    terminal_reason,
                    database_now,
                    database_now,
                    target_impact_assessment_id,
                    int(expected_state_version),
                    int(expected_promotion_epoch),
                    attempt_id,
                ),
            )
            if target_updated.rowcount != 1:
                raise RuntimeError("target terminal transition lost compare-and-swap race")
            result = {
                "applied": True,
                "assessment_status": "failed_terminal",
                "next_attempt_at": None,
            }
        else:
            retry_at = _retry_available_at(
                logical_key=str(target["target_impact_key"]),
                attempt_no=attempt_no,
                failed_at=now,
            )
            if retry_at is not None and (
                retry_at + timedelta(seconds=predicted_runtime_seconds) > recovery_deadline
            ):
                retry_at = None
            retry_at_text = _timestamp(retry_at) if retry_at is not None else None
            target_updated = conn.execute(
                """
                UPDATE target_impact_assessment SET
                    assessment_status='retry_wait',active_attempt_id=NULL,
                    next_attempt_at=?,terminal_reason_code=NULL,
                    state_version=state_version+1,updated_at=?
                WHERE target_impact_assessment_id=? AND state_version=? AND promotion_epoch=?
                  AND assessment_status='dispatching' AND active_attempt_id=?
                """,
                (
                    retry_at_text,
                    database_now,
                    target_impact_assessment_id,
                    int(expected_state_version),
                    int(expected_promotion_epoch),
                    attempt_id,
                ),
            )
            if target_updated.rowcount != 1:
                raise RuntimeError("target retry transition lost compare-and-swap race")
            result = {
                "applied": True,
                "assessment_status": "retry_wait",
                "next_attempt_at": retry_at_text,
            }
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    return result


def terminalize_expired_target_assessment(
    conn: sqlite3.Connection,
    *,
    target_impact_assessment_id: str,
    expected_state_version: int,
    expected_promotion_epoch: int,
) -> dict[str, Any]:
    """Watchdog transition for one expired target without touching content or siblings."""

    ensure_single_track_v3_schema(conn)
    target = _select_one(
        conn,
        "SELECT * FROM target_impact_assessment WHERE target_impact_assessment_id=?",
        (target_impact_assessment_id,),
    )
    if target is None:
        raise KeyError("target impact assessment not found")
    if target["assessment_status"] in {
        "complete",
        "failed_terminal",
        "blocked_dependency_terminal",
        "superseded",
    }:
        return {"applied": False, "assessment_status": target["assessment_status"]}
    if target["assessment_status"] not in {"queued", "retry_wait"}:
        raise ValueError("active target attempts must be resolved through attempt failure")
    database_now = _database_now_tpe(conn)
    deadline = min(
        _aware_timestamp(target["target_recovery_deadline_at"], "target_recovery_deadline_at"),
        _aware_timestamp(target["prediction_issue_deadline_at"], "prediction_issue_deadline_at"),
    )
    if _aware_timestamp(database_now, "database_now") < deadline:
        return {"applied": False, "assessment_status": target["assessment_status"]}
    updated = conn.execute(
        """
        UPDATE target_impact_assessment SET
            assessment_status='failed_terminal',
            decision_status='suppressed_unresolved',validation_state='reject',
            eligible_for_explanation=0,eligible_for_weight=0,
            terminal_reason_code='target_recovery_deadline_elapsed',terminal_at=?,
            promotion_epoch=promotion_epoch+1,
            active_attempt_id=NULL,next_attempt_at=NULL,
            state_version=state_version+1,updated_at=?
        WHERE target_impact_assessment_id=? AND state_version=? AND promotion_epoch=?
          AND assessment_status IN ('queued','retry_wait')
          AND active_attempt_id IS NULL
          AND (
              julianday(target_recovery_deadline_at)<=julianday(?)
              OR julianday(prediction_issue_deadline_at)<=julianday(?)
          )
        """,
        (
            database_now,
            database_now,
            target_impact_assessment_id,
            int(expected_state_version),
            int(expected_promotion_epoch),
            database_now,
            database_now,
        ),
    )
    if updated.rowcount != 1:
        return {"applied": False, "assessment_status": target["assessment_status"]}
    return {"applied": True, "assessment_status": "failed_terminal"}
