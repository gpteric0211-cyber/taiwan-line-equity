"""Durable content-assessment state transitions for Single-Track V3.

Identity construction and idempotent row creation remain in
`single_track_v3_assessment_repository`.  This module owns only dispatch,
seal, retry, watchdog, terminal-cascade, and reconciliation transitions.
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
    _CONTENT_FORBIDDEN_RESULT_FIELDS,
    _CONTENT_REQUIRED_RESULT_FIELDS,
    _CONTENT_TERMINAL_REASONS,
    _aware_timestamp,
    _begin_savepoint,
    _canonical_json,
    _contains_forbidden_key,
    _database_now_tpe,
    _insert_idempotent,
    _json_load,
    _release_savepoint,
    _required_text,
    _retry_available_at,
    _rollback_savepoint,
    _select_one,
    _sha256,
    _string_list,
    _timestamp,
    build_target_impact_key,
)


def begin_content_model_dispatch(
    conn: sqlite3.Connection,
    *,
    content_assessment_id: str,
    attempt_id: str,
    attempt_kind: str,
    dispatch_started_at: str,
) -> dict[str, Any]:
    """Cross the durable dispatch boundary and consume exactly one content attempt."""

    ensure_single_track_v3_schema(conn)
    attempt_kind = _required_text(attempt_kind, "attempt_kind")
    if attempt_kind not in _ATTEMPT_KINDS:
        raise ValueError("attempt_kind is invalid")
    attempt_id = _required_text(attempt_id, "attempt_id")
    dispatch = _aware_timestamp(dispatch_started_at, "dispatch_started_at")
    existing = _select_one(
        conn,
        "SELECT * FROM content_assessment_attempt WHERE attempt_id=?",
        (attempt_id,),
    )
    if existing is not None:
        if (
            existing["content_assessment_id"] != content_assessment_id
            or existing["attempt_kind"] != attempt_kind
            or existing["dispatch_started_at"] != dispatch_started_at
        ):
            raise ValueError("attempt_id already exists with different content")
        return existing
    assessment = _select_one(
        conn,
        "SELECT * FROM content_assessment WHERE content_assessment_id=?",
        (content_assessment_id,),
    )
    if assessment is None:
        raise KeyError("content assessment not found")
    if assessment["assessment_status"] not in {"queued", "retry_wait"}:
        raise ValueError("content assessment is not dispatchable")
    if assessment.get("next_attempt_at") is not None and dispatch < _aware_timestamp(
        assessment["next_attempt_at"], "next_attempt_at"
    ):
        raise ValueError("content retry delay has not elapsed")
    if int(assessment["attempt_count"]) >= 4:
        raise ValueError("content attempts exhausted")
    if attempt_kind == "contract_repair" and int(assessment["model_repair_count"]) >= 1:
        raise ValueError("content model repair already consumed")
    recovery_deadline = _aware_timestamp(
        assessment["content_recovery_deadline_at"], "content_recovery_deadline_at"
    )
    if dispatch < _aware_timestamp(
        assessment["content_eligible_at"], "content_eligible_at"
    ):
        raise ValueError("content cannot dispatch before eligibility")
    if dispatch >= recovery_deadline:
        raise ValueError("content recovery deadline elapsed before dispatch")
    hard_deadline = min(dispatch + timedelta(seconds=300), recovery_deadline)
    attempt_no = int(assessment["attempt_count"]) + 1
    attempt_values = {
        "attempt_id": attempt_id,
        "content_assessment_id": content_assessment_id,
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
    savepoint = "single_track_content_dispatch"
    _begin_savepoint(conn, savepoint)
    try:
        updated = conn.execute(
            """
            UPDATE content_assessment SET
                assessment_status='dispatching',active_attempt_id=?,next_attempt_at=NULL,
                attempt_count=?,model_repair_count=?,
                state_version=state_version+1,updated_at=?
            WHERE content_assessment_id=? AND state_version=?
              AND assessment_status IN ('queued','retry_wait') AND attempt_count<4
            """,
            (
                attempt_id,
                attempt_no,
                int(assessment["model_repair_count"])
                + (1 if attempt_kind == "contract_repair" else 0),
                dispatch_started_at,
                content_assessment_id,
                int(assessment["state_version"]),
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("content dispatch lost compare-and-swap race")
        conn.execute(
            """
            INSERT INTO content_assessment_attempt(
                attempt_id,content_assessment_id,attempt_no,attempt_kind,
                attempt_status,dispatch_started_at,attempt_hard_deadline_at,
                completed_at,result_digest,validation_state,failure_class,
                failure_code,created_at,updated_at
            ) VALUES(
                :attempt_id,:content_assessment_id,:attempt_no,:attempt_kind,
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


def _final_target_key_from_row(
    content: Mapping[str, Any],
    target: Mapping[str, Any],
    content_result_digest: str,
) -> str:
    return build_target_impact_key(
        content_assessment_key=str(content["content_assessment_key"]),
        content_result_digest=content_result_digest,
        target_entity_id=str(target["target_entity_id"]),
        target_trade_date=str(target["target_trade_date"]),
        forecast_target_id=str(target["forecast_target_id"]),
        target_fact_snapshot_digest=str(target["target_fact_snapshot_digest"]),
        target_context_digest=str(target["target_context_digest"]),
        model_digest=str(target["model_digest"]),
        prompt_version=str(target["prompt_version"]),
        schema_version=str(target["schema_version"]),
        validator_version=str(target["validator_version"]),
        contract_version=str(target["contract_version"]),
        calibrator_version=str(target["calibrator_version"]),
        weight_version=str(target.get("weight_version") or "candidate-unreleased"),
    )


def _activate_waiting_targets(
    conn: sqlite3.Connection,
    *,
    content: Mapping[str, Any],
    content_result_digest: str,
    updated_at: str,
) -> int:
    cursor = conn.execute(
        """
        SELECT * FROM target_impact_assessment
        WHERE content_assessment_id=?
          AND assessment_status='waiting_for_content'
          AND target_attempt_count=0
        ORDER BY target_impact_assessment_id
        """,
        (content["content_assessment_id"],),
    )
    columns = [str(column[0]) for column in cursor.description or ()]
    targets = [dict(zip(columns, row)) for row in cursor.fetchall()]
    activated = 0
    for target in targets:
        final_key = _final_target_key_from_row(content, target, content_result_digest)
        result = conn.execute(
            """
            UPDATE target_impact_assessment SET
                target_impact_key=?,content_result_digest=?,
                assessment_status='queued',state_version=state_version+1,
                updated_at=?
            WHERE target_impact_assessment_id=?
              AND assessment_status='waiting_for_content'
              AND target_attempt_count=0
            """,
            (
                final_key,
                content_result_digest,
                updated_at,
                target["target_impact_assessment_id"],
            ),
        )
        if result.rowcount != 1:
            raise RuntimeError("waiting target activation lost compare-and-swap race")
        activated += 1
    return activated


def _record_stale_content_completion(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    result_digest: str,
    completed_at: str,
) -> None:
    existing = _select_one(
        conn,
        "SELECT attempt_status,result_digest FROM content_assessment_attempt WHERE attempt_id=?",
        (attempt_id,),
    )
    if existing is None:
        raise KeyError("content attempt not found")
    if existing["attempt_status"] == "succeeded":
        if existing["result_digest"] != result_digest:
            raise ValueError("sealed content attempt conflicts with stale completion")
        return
    if existing["result_digest"] not in (None, result_digest):
        raise ValueError("stale content completion conflicts with existing audit digest")
    conn.execute(
        """
        UPDATE content_assessment_attempt SET
            attempt_status='stale_completion',completed_at=?,result_digest=?,
            validation_state='pass',updated_at=?
        WHERE attempt_id=? AND attempt_status!='succeeded'
        """,
        (completed_at, result_digest, completed_at, attempt_id),
    )


def seal_content_assessment_result(
    conn: sqlite3.Connection,
    *,
    content_assessment_id: str,
    attempt_id: str,
    expected_state_version: int,
    result: Mapping[str, Any],
    completed_at: str,
    expected_promotion_epoch: int = 0,
) -> str | None:
    """Seal one on-time validator-pass content result without any per-stock output."""

    ensure_single_track_v3_schema(conn)
    completed = _aware_timestamp(completed_at, "completed_at")
    result_payload = dict(result)
    missing = sorted(_CONTENT_REQUIRED_RESULT_FIELDS - set(result_payload))
    if missing:
        raise ValueError(f"content result missing required fields: {','.join(missing)}")
    forbidden = _contains_forbidden_key(result_payload, _CONTENT_FORBIDDEN_RESULT_FIELDS)
    if forbidden is not None:
        raise ValueError(f"content result contains prohibited field: {forbidden}")
    unexpected = sorted(set(result_payload) - _CONTENT_REQUIRED_RESULT_FIELDS)
    if unexpected:
        raise ValueError(f"content result contains unexpected fields: {','.join(unexpected)}")
    verification_state = str(result_payload["verification_state"])
    if verification_state not in {"unverified", "verified", "conflicted", "rejected"}:
        raise ValueError("content verification_state is invalid")
    materiality = str(result_payload["materiality"])
    if materiality not in {"material", "non_material", "unknown"}:
        raise ValueError("content materiality is invalid")
    event_type = _required_text(result_payload["event_type"], "event_type")
    if len(event_type) > 80:
        raise ValueError("event_type exceeds the bounded length")
    result_payload = {
        "event_facts": _string_list(result_payload["event_facts"], "event_facts"),
        "verification_state": verification_state,
        "event_type": event_type,
        "materiality": materiality,
        "transmission_paths": _string_list(
            result_payload["transmission_paths"], "transmission_paths"
        ),
        "counterevidence": _string_list(
            result_payload["counterevidence"], "counterevidence"
        ),
        "uncertainty": _string_list(result_payload["uncertainty"], "uncertainty"),
        "evidence_ids": _string_list(
            result_payload["evidence_ids"], "evidence_ids", unique=True
        ),
    }
    content = _select_one(
        conn,
        "SELECT * FROM content_assessment WHERE content_assessment_id=?",
        (content_assessment_id,),
    )
    if content is None:
        raise KeyError("content assessment not found")
    revision = _select_one(
        conn,
        "SELECT event_revision_id,source_refs_json FROM event_revision WHERE event_revision_id=?",
        (content["event_revision_id"],),
    )
    if revision is None:
        raise ValueError("content event revision does not exist")
    allowed_evidence_ids = {str(revision["event_revision_id"])}
    for source_ref in _json_load(revision.get("source_refs_json"), []):
        if not isinstance(source_ref, Mapping):
            continue
        for field in ("news_item_id", "source_id"):
            value = str(source_ref.get(field) or "").strip()
            if value:
                allowed_evidence_ids.add(value)
    if not result_payload["evidence_ids"] or not set(result_payload["evidence_ids"]) <= allowed_evidence_ids:
        raise ValueError("content result references unknown evidence")
    attempt = _select_one(
        conn,
        "SELECT * FROM content_assessment_attempt WHERE attempt_id=?",
        (attempt_id,),
    )
    if attempt is None or attempt["content_assessment_id"] != content_assessment_id:
        raise KeyError("content attempt not found")
    if attempt["attempt_status"] == "succeeded":
        digest = _sha256(result_payload)
        if attempt["result_digest"] != digest:
            raise ValueError("sealed content attempt conflicts with retry content")
        return digest
    hard_deadline = _aware_timestamp(
        attempt["attempt_hard_deadline_at"], "attempt_hard_deadline_at"
    )
    result_json = _canonical_json(result_payload)
    result_digest = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    if completed > hard_deadline:
        if _aware_timestamp(_database_now_tpe(conn), "database_now") < hard_deadline:
            raise ValueError("content completed_at is ahead of the database clock")
        record_content_attempt_failure(
            conn,
            content_assessment_id=content_assessment_id,
            attempt_id=attempt_id,
            expected_state_version=expected_state_version,
            expected_promotion_epoch=expected_promotion_epoch,
            failure_class="timeout",
            failure_code="attempt_hard_deadline_elapsed",
        )
        _record_stale_content_completion(
            conn,
            attempt_id=attempt_id,
            result_digest=result_digest,
            completed_at=completed_at,
        )
        return None
    database_now = _database_now_tpe(conn)
    if _aware_timestamp(database_now, "database_now") >= _aware_timestamp(
        content["content_recovery_deadline_at"], "content_recovery_deadline_at"
    ):
        record_content_attempt_failure(
            conn,
            content_assessment_id=content_assessment_id,
            attempt_id=attempt_id,
            expected_state_version=expected_state_version,
            expected_promotion_epoch=expected_promotion_epoch,
            failure_class="deadline_elapsed",
            failure_code="content_recovery_deadline_elapsed",
        )
        _record_stale_content_completion(
            conn,
            attempt_id=attempt_id,
            result_digest=result_digest,
            completed_at=completed_at,
        )
        return None
    savepoint = "single_track_content_seal"
    _begin_savepoint(conn, savepoint)
    try:
        updated = conn.execute(
            """
            UPDATE content_assessment SET
                assessment_status='complete',validation_state='pass',
                result_json=?,result_digest=?,sealed_at=?,active_attempt_id=NULL,
                state_version=state_version+1,updated_at=?
            WHERE content_assessment_id=? AND state_version=?
              AND promotion_epoch=?
              AND assessment_status='dispatching' AND active_attempt_id=?
              AND julianday(?) < julianday(content_recovery_deadline_at)
            """,
            (
                result_json,
                result_digest,
                completed_at,
                completed_at,
                content_assessment_id,
                int(expected_state_version),
                int(expected_promotion_epoch),
                attempt_id,
                database_now,
            ),
        )
        if updated.rowcount != 1:
            _record_stale_content_completion(
                conn,
                attempt_id=attempt_id,
                result_digest=result_digest,
                completed_at=completed_at,
            )
            _release_savepoint(conn, savepoint)
            return None
        attempt_updated = conn.execute(
            """
            UPDATE content_assessment_attempt SET
                attempt_status='succeeded',completed_at=?,result_digest=?,
                validation_state='pass',updated_at=?
            WHERE attempt_id=? AND attempt_status='dispatching'
            """,
            (completed_at, result_digest, completed_at, attempt_id),
        )
        if attempt_updated.rowcount != 1:
            raise RuntimeError("content attempt was not sealable")
        _activate_waiting_targets(
            conn,
            content=content,
            content_result_digest=result_digest,
            updated_at=completed_at,
        )
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    return result_digest


def _content_terminal_transition_id(
    content: Mapping[str, Any],
    *,
    attempt_id: str,
    terminal_reason_code: str,
) -> str:
    return _sha256(
        {
            "attempt_id": attempt_id,
            "content_assessment_key": content["content_assessment_key"],
            "next_promotion_epoch": int(content["promotion_epoch"]) + 1,
            "terminal_reason_code": terminal_reason_code,
            "transition": "CONTENT_TERMINAL_CASCADE",
        }
    )


def _enqueue_content_terminal_outbox(
    conn: sqlite3.Connection,
    *,
    content: Mapping[str, Any],
    terminal_transition_id: str,
    terminal_reason_code: str,
    terminal_at: str,
    blocked_target_count: int,
) -> str:
    outbox_id = f"content-terminal:{terminal_transition_id}"
    dedupe_key = f"CONTENT_TERMINAL_CASCADE:{terminal_transition_id}"
    existing = _select_one(
        conn,
        "SELECT * FROM single_track_v3_outbox WHERE dedupe_key=?",
        (dedupe_key,),
    )
    if existing is not None:
        payload = _json_load(existing.get("payload_json"), {})
        if (
            existing.get("outbox_id") != outbox_id
            or existing.get("aggregate_key") != str(content["content_assessment_id"])
            or payload.get("terminal_transition_id") != terminal_transition_id
            or payload.get("terminal_reason_code") != terminal_reason_code
        ):
            raise ValueError("content terminal outbox replay conflicts with immutable identity")
        return str(existing["outbox_id"])
    values = {
        "outbox_id": outbox_id,
        "run_id": None,
        "event_type": "CONTENT_TERMINAL_CASCADE",
        "aggregate_key": str(content["content_assessment_id"]),
        "dedupe_key": dedupe_key,
        "payload_json": _canonical_json(
            {
                "blocked_target_count": int(blocked_target_count),
                "content_assessment_id": content["content_assessment_id"],
                "content_assessment_key": content["content_assessment_key"],
                "content_generation": int(content["content_generation"]),
                "terminal_reason_code": terminal_reason_code,
                "terminal_transition_id": terminal_transition_id,
                "user_visible_state": "suppressed_unresolved",
            }
        ),
        "status": "pending",
        "available_at": terminal_at,
        "claimed_at": None,
        "delivered_at": None,
        "attempt_count": 0,
        "last_error_code": None,
        "created_at": terminal_at,
        "updated_at": terminal_at,
    }
    columns = tuple(values)
    saved = _insert_idempotent(
        conn,
        table="single_track_v3_outbox",
        identity_column="dedupe_key",
        columns=columns,
        values=values,
    )
    return str(saved["outbox_id"])


def _block_waiting_targets(
    conn: sqlite3.Connection,
    *,
    content: Mapping[str, Any],
    terminal_reason_code: str,
    terminal_at: str,
) -> int:
    result = conn.execute(
        """
        UPDATE target_impact_assessment SET
            assessment_status='blocked_dependency_terminal',
            decision_status='suppressed_unresolved',
            eligible_for_explanation=0,eligible_for_weight=0,
            terminal_reason_code='upstream_content_terminal',
            upstream_terminal_reason_code=?,terminal_at=?,
            active_attempt_id=NULL,next_attempt_at=NULL,
            state_version=state_version+1,updated_at=?
        WHERE content_assessment_id=?
          AND assessment_status='waiting_for_content'
          AND target_attempt_count=0
        """,
        (
            terminal_reason_code,
            terminal_at,
            terminal_at,
            content["content_assessment_id"],
        ),
    )
    return int(result.rowcount)


def _apply_content_terminal_cascade(
    conn: sqlite3.Connection,
    *,
    content: Mapping[str, Any],
    expected_state_version: int,
    expected_promotion_epoch: int,
    expected_active_attempt_id: str,
    terminal_reason_code: str,
    last_failure_code: str,
    terminal_at: str,
) -> dict[str, Any]:
    if terminal_reason_code not in _CONTENT_TERMINAL_REASONS:
        raise ValueError("content terminal reason is invalid")
    transition_id = _content_terminal_transition_id(
        content,
        attempt_id=expected_active_attempt_id,
        terminal_reason_code=terminal_reason_code,
    )
    updated = conn.execute(
        """
        UPDATE content_assessment SET
            assessment_status='failed_terminal',validation_state='reject',
            terminal_transition_id=?,terminal_reason_code=?,last_failure_code=?,
            terminal_at=?,promotion_epoch=promotion_epoch+1,
            active_attempt_id=NULL,next_attempt_at=NULL,
            state_version=state_version+1,updated_at=?
        WHERE content_assessment_id=? AND state_version=? AND promotion_epoch=?
          AND assessment_status='dispatching' AND active_attempt_id=?
        """,
        (
            transition_id,
            terminal_reason_code,
            last_failure_code,
            terminal_at,
            terminal_at,
            content["content_assessment_id"],
            int(expected_state_version),
            int(expected_promotion_epoch),
            expected_active_attempt_id,
        ),
    )
    if updated.rowcount != 1:
        return {
            "applied": False,
            "blocked_target_count": 0,
            "outbox_id": None,
            "terminal_transition_id": None,
        }
    blocked_target_count = _block_waiting_targets(
        conn,
        content=content,
        terminal_reason_code=terminal_reason_code,
        terminal_at=terminal_at,
    )
    outbox_id = _enqueue_content_terminal_outbox(
        conn,
        content=content,
        terminal_transition_id=transition_id,
        terminal_reason_code=terminal_reason_code,
        terminal_at=terminal_at,
        blocked_target_count=blocked_target_count,
    )
    return {
        "applied": True,
        "blocked_target_count": blocked_target_count,
        "outbox_id": outbox_id,
        "terminal_transition_id": transition_id,
    }


def record_content_attempt_failure(
    conn: sqlite3.Connection,
    *,
    content_assessment_id: str,
    attempt_id: str,
    expected_state_version: int,
    expected_promotion_epoch: int,
    failure_class: str,
    failure_code: str,
    predicted_runtime_seconds: int = 300,
) -> dict[str, Any]:
    """Record one typed failure and atomically retry or terminal-cascade the content."""

    ensure_single_track_v3_schema(conn)
    failure_class = _required_text(failure_class, "failure_class")
    failure_code = _required_text(failure_code, "failure_code")
    if failure_class not in _ATTEMPT_FAILURE_CLASSES:
        raise ValueError("failure_class is invalid")
    predicted_runtime_seconds = int(predicted_runtime_seconds)
    if predicted_runtime_seconds < 1 or predicted_runtime_seconds > 300:
        raise ValueError("predicted_runtime_seconds must be between 1 and 300")
    content = _select_one(
        conn,
        "SELECT * FROM content_assessment WHERE content_assessment_id=?",
        (content_assessment_id,),
    )
    attempt = _select_one(
        conn,
        "SELECT * FROM content_assessment_attempt WHERE attempt_id=?",
        (attempt_id,),
    )
    if content is None or attempt is None or attempt["content_assessment_id"] != content_assessment_id:
        raise KeyError("content assessment attempt not found")
    if content["assessment_status"] in {"complete", "superseded"}:
        return {
            "applied": False,
            "assessment_status": content["assessment_status"],
            "blocked_target_count": 0,
            "next_attempt_at": content.get("next_attempt_at"),
            "outbox_id": None,
            "terminal_transition_id": content.get("terminal_transition_id"),
        }
    if content["assessment_status"] == "failed_terminal":
        if (
            attempt.get("failure_class") not in (None, failure_class)
            or attempt.get("failure_code") not in (None, failure_code)
        ):
            raise ValueError("content failure replay conflicts with immutable attempt outcome")
        return {
            "applied": False,
            "assessment_status": "failed_terminal",
            "blocked_target_count": 0,
            "next_attempt_at": None,
            "outbox_id": None,
            "terminal_transition_id": content.get("terminal_transition_id"),
        }
    if attempt["attempt_status"] != "dispatching":
        if (
            attempt.get("failure_class") == failure_class
            and attempt.get("failure_code") == failure_code
        ):
            return {
                "applied": False,
                "assessment_status": content["assessment_status"],
                "blocked_target_count": 0,
                "next_attempt_at": content.get("next_attempt_at"),
                "outbox_id": None,
                "terminal_transition_id": content.get("terminal_transition_id"),
            }
        raise ValueError("content attempt failure is not recordable from its current state")
    database_now = _database_now_tpe(conn)
    now = _aware_timestamp(database_now, "database_now")
    if now < _aware_timestamp(attempt["dispatch_started_at"], "dispatch_started_at"):
        raise ValueError("content failure cannot precede durable dispatch")
    hard_deadline = _aware_timestamp(
        attempt["attempt_hard_deadline_at"], "attempt_hard_deadline_at"
    )
    if failure_class == "timeout" and now < hard_deadline:
        raise ValueError("timeout cannot be recorded before the attempt hard deadline")
    recovery_deadline = _aware_timestamp(
        content["content_recovery_deadline_at"], "content_recovery_deadline_at"
    )
    attempt_no = int(attempt["attempt_no"])
    terminal_reason_code: str | None = None
    if failure_class == "deadline_elapsed":
        terminal_reason_code = "content_recovery_deadline_elapsed"
    elif failure_class == "permanent":
        terminal_reason_code = "content_permanent_failure"
    elif attempt_no >= 4:
        terminal_reason_code = "content_attempts_exhausted"
    elif now >= recovery_deadline:
        terminal_reason_code = "content_recovery_deadline_elapsed"
    attempt_status = (
        "timed_out" if failure_class in {"timeout", "deadline_elapsed"} else
        "permanent_failure" if failure_class == "permanent" else
        "retryable_failure"
    )
    savepoint = "single_track_content_failure"
    _begin_savepoint(conn, savepoint)
    try:
        attempt_updated = conn.execute(
            """
            UPDATE content_assessment_attempt SET
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
            raise RuntimeError("content attempt failure lost compare-and-swap race")
        if terminal_reason_code is not None:
            cascade = _apply_content_terminal_cascade(
                conn,
                content=content,
                expected_state_version=expected_state_version,
                expected_promotion_epoch=expected_promotion_epoch,
                expected_active_attempt_id=attempt_id,
                terminal_reason_code=terminal_reason_code,
                last_failure_code=failure_code,
                terminal_at=database_now,
            )
            if not cascade["applied"]:
                raise RuntimeError("content terminal cascade lost compare-and-swap race")
            result = {
                **cascade,
                "assessment_status": "failed_terminal",
                "next_attempt_at": None,
            }
        else:
            retry_at = _retry_available_at(
                logical_key=str(content["content_assessment_key"]),
                attempt_no=attempt_no,
                failed_at=now,
            )
            if retry_at is not None and (
                retry_at + timedelta(seconds=predicted_runtime_seconds) > recovery_deadline
            ):
                retry_at = None
            retry_at_text = _timestamp(retry_at) if retry_at is not None else None
            content_updated = conn.execute(
                """
                UPDATE content_assessment SET
                    assessment_status='retry_wait',active_attempt_id=NULL,
                    next_attempt_at=?,last_failure_code=?,
                    state_version=state_version+1,updated_at=?
                WHERE content_assessment_id=? AND state_version=? AND promotion_epoch=?
                  AND assessment_status='dispatching' AND active_attempt_id=?
                """,
                (
                    retry_at_text,
                    failure_code,
                    database_now,
                    content_assessment_id,
                    int(expected_state_version),
                    int(expected_promotion_epoch),
                    attempt_id,
                ),
            )
            if content_updated.rowcount != 1:
                raise RuntimeError("content retry transition lost compare-and-swap race")
            result = {
                "applied": True,
                "assessment_status": "retry_wait",
                "blocked_target_count": 0,
                "next_attempt_at": retry_at_text,
                "outbox_id": None,
                "terminal_transition_id": None,
            }
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    return result


def terminalize_expired_content_assessment(
    conn: sqlite3.Connection,
    *,
    content_assessment_id: str,
    expected_state_version: int,
    expected_promotion_epoch: int,
) -> dict[str, Any]:
    """Watchdog transition for an expired queued/retry-wait content with no active attempt."""

    ensure_single_track_v3_schema(conn)
    content = _select_one(
        conn,
        "SELECT * FROM content_assessment WHERE content_assessment_id=?",
        (content_assessment_id,),
    )
    if content is None:
        raise KeyError("content assessment not found")
    if content["assessment_status"] in {"complete", "failed_terminal", "superseded"}:
        return {
            "applied": False,
            "assessment_status": content["assessment_status"],
            "blocked_target_count": 0,
            "outbox_id": None,
        }
    if content["assessment_status"] not in {"queued", "retry_wait"}:
        raise ValueError("active content attempts must be resolved through attempt failure")
    database_now = _database_now_tpe(conn)
    if _aware_timestamp(database_now, "database_now") < _aware_timestamp(
        content["content_recovery_deadline_at"], "content_recovery_deadline_at"
    ):
        return {
            "applied": False,
            "assessment_status": content["assessment_status"],
            "blocked_target_count": 0,
            "outbox_id": None,
        }
    transition_id = _content_terminal_transition_id(
        content,
        attempt_id="no-active-attempt",
        terminal_reason_code="content_recovery_deadline_elapsed",
    )
    savepoint = "single_track_content_watchdog"
    _begin_savepoint(conn, savepoint)
    try:
        updated = conn.execute(
            """
            UPDATE content_assessment SET
                assessment_status='failed_terminal',validation_state='reject',
                terminal_transition_id=?,
                terminal_reason_code='content_recovery_deadline_elapsed',
                last_failure_code=COALESCE(last_failure_code,'recovery_deadline_elapsed'),
                terminal_at=?,promotion_epoch=promotion_epoch+1,
                active_attempt_id=NULL,next_attempt_at=NULL,
                state_version=state_version+1,updated_at=?
            WHERE content_assessment_id=? AND state_version=? AND promotion_epoch=?
              AND assessment_status IN ('queued','retry_wait')
              AND active_attempt_id IS NULL
              AND julianday(content_recovery_deadline_at)<=julianday(?)
            """,
            (
                transition_id,
                database_now,
                database_now,
                content_assessment_id,
                int(expected_state_version),
                int(expected_promotion_epoch),
                database_now,
            ),
        )
        if updated.rowcount != 1:
            result = {
                "applied": False,
                "assessment_status": content["assessment_status"],
                "blocked_target_count": 0,
                "outbox_id": None,
            }
        else:
            blocked = _block_waiting_targets(
                conn,
                content=content,
                terminal_reason_code="content_recovery_deadline_elapsed",
                terminal_at=database_now,
            )
            outbox_id = _enqueue_content_terminal_outbox(
                conn,
                content=content,
                terminal_transition_id=transition_id,
                terminal_reason_code="content_recovery_deadline_elapsed",
                terminal_at=database_now,
                blocked_target_count=blocked,
            )
            result = {
                "applied": True,
                "assessment_status": "failed_terminal",
                "blocked_target_count": blocked,
                "outbox_id": outbox_id,
            }
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    return result


def reconcile_waiting_on_terminal_content(conn: sqlite3.Connection) -> dict[str, int]:
    """Repair only stranded waiting rows after restart; no GPU/model work is performed."""

    ensure_single_track_v3_schema(conn)
    cursor = conn.execute(
        """
        SELECT DISTINCT c.*
        FROM content_assessment c
        JOIN target_impact_assessment t
          ON t.content_assessment_id=c.content_assessment_id
        WHERE c.assessment_status='failed_terminal'
          AND c.terminal_transition_id IS NOT NULL
          AND t.assessment_status='waiting_for_content'
        ORDER BY c.content_assessment_id
        """
    )
    columns = [str(column[0]) for column in cursor.description or ()]
    contents = [dict(zip(columns, row)) for row in cursor.fetchall()]
    repaired = 0
    for content in contents:
        terminal_at = str(content["terminal_at"])
        count = _block_waiting_targets(
            conn,
            content=content,
            terminal_reason_code=str(content["terminal_reason_code"]),
            terminal_at=terminal_at,
        )
        repaired += count
        _enqueue_content_terminal_outbox(
            conn,
            content=content,
            terminal_transition_id=str(content["terminal_transition_id"]),
            terminal_reason_code=str(content["terminal_reason_code"]),
            terminal_at=terminal_at,
            blocked_target_count=count,
        )
    remaining = int(
        conn.execute(
            """
            SELECT count(*)
            FROM target_impact_assessment t
            JOIN content_assessment c
              ON c.content_assessment_id=t.content_assessment_id
            WHERE c.assessment_status='failed_terminal'
              AND t.assessment_status='waiting_for_content'
            """
        ).fetchone()[0]
    )
    return {"repaired": repaired, "remaining": remaining}
