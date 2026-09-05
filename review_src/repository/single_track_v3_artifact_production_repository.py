from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema
from repository.single_track_v3_artifact_repository import (
    event_delta_artifact,
    seal_event_delta_artifact,
)
from repository.single_track_v3_repository import supersede_artifact_with_events
from repository.single_track_v3_scheduler_repository import enqueue_scheduler_outbox


ZERO_GPU_PRODUCER_VERSION = "SingleTrackV3ZeroGpuArtifactProducerV1"
_SUPPORTED_EVENT_TYPES = {"CONTENT_TERMINAL_CASCADE", "EVENT_DELTA_SEALED"}


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("producer payload must be finite JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _aware_timestamp(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _select_one(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any] | None:
    cursor = conn.execute(query, parameters)
    source = cursor.fetchone()
    if source is None:
        return None
    if isinstance(source, sqlite3.Row):
        return dict(source)
    columns = [str(item[0]) for item in cursor.description or ()]
    return dict(zip(columns, source, strict=True))


def _select_rows(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = conn.execute(query, parameters)
    columns = [str(item[0]) for item in cursor.description or ()]
    result = []
    for source in cursor.fetchall():
        if isinstance(source, sqlite3.Row):
            result.append(dict(source))
        else:
            result.append(dict(zip(columns, source, strict=True)))
    return result


def _id_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    result = sorted(_required_text(item, field) for item in value)
    if len(result) > 512:
        raise ValueError(f"{field} exceeds its item limit")
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _begin_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f'SAVEPOINT "{name}"')


def _rollback_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f'ROLLBACK TO SAVEPOINT "{name}"')
    conn.execute(f'RELEASE SAVEPOINT "{name}"')


def _release_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f'RELEASE SAVEPOINT "{name}"')


def _decode_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["output_artifact_ids"] = _json_load(
        result.pop("output_artifact_ids_json"), []
    )
    return result


def _validate_receipt_replay(
    conn: sqlite3.Connection,
    outbox: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    """Fail closed if an allegedly completed production record was altered."""

    payload = _json_load(outbox.get("payload_json"), None)
    if not isinstance(payload, Mapping):
        raise ValueError("delivered outbox payload must remain an object")
    expected_payload_digest = _digest(dict(payload))
    if (
        outbox.get("status") != "delivered"
        or outbox.get("event_type") not in _SUPPORTED_EVENT_TYPES
        or receipt.get("input_event_type") != outbox.get("event_type")
        or receipt.get("input_payload_digest") != expected_payload_digest
        or receipt.get("producer_version") != ZERO_GPU_PRODUCER_VERSION
        or int(receipt.get("zero_gpu_model_calls", -1)) != 0
        or int(outbox.get("attempt_count") or 0) != 1
        or outbox.get("last_error_code") is not None
        or outbox.get("claimed_at") != receipt.get("produced_at")
        or outbox.get("delivered_at") != receipt.get("produced_at")
    ):
        raise ValueError("production receipt conflicts with its delivered outbox")
    output_ids = receipt.get("output_artifact_ids")
    if not isinstance(output_ids, list) or any(
        not isinstance(item, str) or not item for item in output_ids
    ):
        raise ValueError("production receipt output IDs are invalid")
    if receipt.get("output_artifact_type") == "content_terminal_state":
        if len(output_ids) != 1:
            raise ValueError("content terminal receipt must bind exactly one artifact")
        artifact = content_terminal_artifact(conn, output_ids[0])
        if (
            artifact is None
            or artifact.get("source_outbox_id") != outbox.get("outbox_id")
            or artifact.get("artifact_digest") != receipt.get("output_digest")
        ):
            raise ValueError("content terminal receipt lost its immutable artifact")
    elif receipt.get("output_artifact_type") == "canonical_analysis_invalidation":
        if not output_ids:
            raise ValueError("canonical invalidation receipt must bind an artifact")
    elif receipt.get("output_artifact_type") == "none" and output_ids:
        raise ValueError("no-op production receipt must not bind output artifacts")


def artifact_production_receipt(
    conn: sqlite3.Connection,
    outbox_id: str,
) -> dict[str, Any] | None:
    """Read one immutable production receipt without schema DDL or retries."""

    row = _select_one(
        conn,
        "SELECT * FROM single_track_v3_artifact_production_receipt WHERE outbox_id=?",
        (str(outbox_id),),
    )
    return _decode_receipt(row) if row is not None else None


def content_terminal_artifact(
    conn: sqlite3.Connection,
    artifact_id: str,
) -> dict[str, Any] | None:
    """Read one deterministic terminal-state artifact without side effects."""

    return _select_one(
        conn,
        "SELECT * FROM content_terminal_artifact WHERE artifact_id=?",
        (str(artifact_id),),
    )


def pending_zero_gpu_outbox_items_at(
    conn: sqlite3.Connection,
    *,
    available_at: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Select bounded supported pending work without claiming or writing it."""

    cutoff = _aware_timestamp(available_at, "available_at")
    bounded_limit = int(limit)
    if bounded_limit < 1 or bounded_limit > 1_000:
        raise ValueError("limit must be between 1 and 1000")
    result = []
    for row in _select_rows(
        conn,
        """
        SELECT * FROM single_track_v3_outbox
        WHERE status='pending'
          AND event_type IN ('CONTENT_TERMINAL_CASCADE','EVENT_DELTA_SEALED')
        ORDER BY available_at,created_at,outbox_id
        """,
    ):
        if _aware_timestamp(row["available_at"], "outbox.available_at") > cutoff:
            continue
        item = dict(row)
        item["payload"] = _json_load(item.pop("payload_json"), {})
        result.append(item)
        if len(result) >= bounded_limit:
            break
    return result


def enqueue_sealed_event_delta_outbox(
    conn: sqlite3.Connection,
    delta_artifact_id: str,
    *,
    ensure_schema: bool = True,
) -> str:
    """Enqueue one immutable delta event using its seal time as deterministic availability."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    delta = event_delta_artifact(conn, str(delta_artifact_id))
    if delta is None:
        raise ValueError("event delta artifact does not exist")
    if delta["delta_status"] not in {"sealed", "partial", "incomplete"}:
        raise ValueError("event delta must be terminal before outbox enqueue")
    if not delta.get("artifact_digest") or not delta.get("sealed_at"):
        raise ValueError("terminal event delta is missing its seal digest")
    payload = {
        "base_premarket_artifact_id": delta.get("base_premarket_artifact_id"),
        "delta_artifact_id": delta["delta_artifact_id"],
        "delta_artifact_digest": delta["artifact_digest"],
        "delta_status": delta["delta_status"],
        "event_revision_ids": list(delta["event_revision_ids"]),
        "invalidated_analysis_ids": list(delta["invalidated_analysis_ids"]),
        "target_trade_date": delta["target_trade_date"],
        "user_visible_state": "superseded_pending_reanalysis",
    }
    outbox_id = f"event-delta:{delta['delta_key']}"
    return enqueue_scheduler_outbox(
        conn,
        {
            "outbox_id": outbox_id,
            "run_id": delta.get("run_id"),
            "event_type": "EVENT_DELTA_SEALED",
            "aggregate_key": delta["delta_artifact_id"],
            "dedupe_key": f"EVENT_DELTA_SEALED:{delta['delta_key']}",
            "payload": payload,
            "status": "pending",
            "available_at": delta["sealed_at"],
            "claimed_at": None,
            "delivered_at": None,
            "attempt_count": 0,
            "last_error_code": None,
            "created_at": delta["sealed_at"],
            "updated_at": delta["sealed_at"],
        },
        ensure_schema=False,
    )


def seal_event_delta_for_zero_gpu_production(
    conn: sqlite3.Connection,
    delta_artifact_id: str,
    *,
    expected_status: str,
    new_status: str,
    sealed_at: str,
    updated_at: str,
) -> dict[str, Any]:
    """Atomically seal a delta and enqueue deterministic zero-GPU production work."""

    ensure_single_track_v3_schema(conn)
    savepoint = "single_track_delta_zero_gpu_enqueue"
    _begin_savepoint(conn, savepoint)
    try:
        delta = seal_event_delta_artifact(
            conn,
            delta_artifact_id,
            expected_status=expected_status,
            new_status=new_status,
            sealed_at=sealed_at,
            updated_at=updated_at,
            ensure_schema=False,
        )
        outbox_id = enqueue_sealed_event_delta_outbox(
            conn,
            delta_artifact_id,
            ensure_schema=False,
        )
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    return {"delta_artifact": delta, "outbox_id": outbox_id}


def _produce_content_terminal(
    conn: sqlite3.Connection,
    outbox: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    produced_at: str,
) -> tuple[str, list[str], str, str]:
    content_assessment_id = _required_text(
        payload.get("content_assessment_id"), "content_assessment_id"
    )
    transition_id = _required_text(
        payload.get("terminal_transition_id"), "terminal_transition_id"
    )
    terminal_reason_code = _required_text(
        payload.get("terminal_reason_code"), "terminal_reason_code"
    )
    if payload.get("user_visible_state") != "suppressed_unresolved":
        raise ValueError("content terminal payload has an invalid user-visible state")
    content = _select_one(
        conn,
        "SELECT * FROM content_assessment WHERE content_assessment_id=?",
        (content_assessment_id,),
    )
    if content is None:
        raise ValueError("content terminal payload references missing assessment")
    if (
        content["assessment_status"] != "failed_terminal"
        or content["terminal_transition_id"] != transition_id
        or content["terminal_reason_code"] != terminal_reason_code
        or content["content_assessment_key"] != payload.get("content_assessment_key")
        or int(content["content_generation"]) != int(payload.get("content_generation") or 0)
    ):
        raise ValueError("content terminal payload conflicts with durable terminal state")
    artifact_payload = {
        "contract": "ContentTerminalArtifactV1",
        "content_assessment_id": content_assessment_id,
        "content_assessment_key": content["content_assessment_key"],
        "content_generation": int(content["content_generation"]),
        "terminal_reason_code": terminal_reason_code,
        "terminal_transition_id": transition_id,
        "user_visible_state": "suppressed_unresolved",
    }
    artifact_digest = _digest(artifact_payload)
    artifact_id = f"content-terminal-artifact:{transition_id}"
    values = {
        "artifact_id": artifact_id,
        "terminal_transition_id": transition_id,
        "content_assessment_id": content_assessment_id,
        "content_assessment_key": content["content_assessment_key"],
        "content_generation": int(content["content_generation"]),
        "terminal_reason_code": terminal_reason_code,
        "user_visible_state": "suppressed_unresolved",
        "source_outbox_id": outbox["outbox_id"],
        "artifact_digest": artifact_digest,
        "sealed_at": produced_at,
        "created_at": produced_at,
    }
    columns = tuple(values)
    existing = _select_one(
        conn,
        "SELECT * FROM content_terminal_artifact WHERE terminal_transition_id=?",
        (transition_id,),
    )
    if existing is None:
        conn.execute(
            f"INSERT INTO content_terminal_artifact({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)})",
            values,
        )
    elif any(existing.get(column) != values.get(column) for column in columns):
        raise ValueError("content terminal artifact replay conflicts with sealed content")
    return "applied", [artifact_id], "content_terminal_state", artifact_digest


def _verified_material_event_ids(
    conn: sqlite3.Connection,
    event_revision_ids: Iterable[str],
) -> list[dict[str, Any]]:
    result = []
    for revision_id in sorted(set(event_revision_ids)):
        rows = _select_rows(
            conn,
            """
            SELECT DISTINCT
                n.event_id,
                e.available_at,
                e.verification_state AS evidence_verification_state,
                e.materiality AS evidence_materiality,
                r.verification_state AS revision_verification_state,
                r.materiality AS revision_materiality
            FROM news_run_event n
            JOIN canonical_event_evidence e ON e.event_id=n.event_id
            JOIN event_revision r ON r.event_revision_id=n.event_revision_id
            WHERE n.event_revision_id=?
            """,
            (revision_id,),
        )
        eligible = [
            row
            for row in rows
            if row["evidence_verification_state"] == "verified"
            and row["evidence_materiality"] == "material"
            and row["revision_verification_state"] == "verified"
            and row["revision_materiality"] == "material"
        ]
        event_ids = {str(row["event_id"]) for row in eligible}
        if len(event_ids) > 1:
            raise ValueError("one event revision maps to conflicting canonical event IDs")
        if eligible:
            result.append(eligible[0])
    return result


def _produce_event_delta(
    conn: sqlite3.Connection,
    outbox: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    produced_at: str,
) -> tuple[str, list[str], str, str]:
    delta_artifact_id = _required_text(
        payload.get("delta_artifact_id"), "delta_artifact_id"
    )
    delta = event_delta_artifact(conn, delta_artifact_id)
    if delta is None:
        raise ValueError("event delta payload references a missing artifact")
    event_revision_ids = _id_list(
        payload.get("event_revision_ids"), "event_revision_ids"
    )
    invalidated_analysis_ids = _id_list(
        payload.get("invalidated_analysis_ids"), "invalidated_analysis_ids"
    )
    if (
        outbox["aggregate_key"] != delta_artifact_id
        or payload.get("delta_artifact_digest") != delta.get("artifact_digest")
        or payload.get("delta_status") != delta.get("delta_status")
        or event_revision_ids != sorted(delta.get("event_revision_ids") or [])
        or invalidated_analysis_ids != sorted(delta.get("invalidated_analysis_ids") or [])
        or payload.get("target_trade_date") != delta.get("target_trade_date")
        or payload.get("base_premarket_artifact_id")
        != delta.get("base_premarket_artifact_id")
        or payload.get("user_visible_state") != "superseded_pending_reanalysis"
    ):
        raise ValueError("event delta outbox payload conflicts with its sealed artifact")
    if not invalidated_analysis_ids:
        output_digest = _digest(
            {
                "contract": "EventDeltaNoOpV1",
                "delta_artifact_id": delta_artifact_id,
                "reason_code": "no_explicit_invalidation_targets",
            }
        )
        return "no_op", [], "none", output_digest
    verified_events = _verified_material_event_ids(conn, event_revision_ids)
    if not verified_events:
        raise ValueError("event delta has invalidation targets but no verified material event")
    analysis_event_ids: dict[str, list[str]] = {}
    for analysis_id in invalidated_analysis_ids:
        analysis = _select_one(
            conn,
            "SELECT analysis_cutoff FROM canonical_analysis_artifact WHERE analysis_id=?",
            (analysis_id,),
        )
        if analysis is None:
            raise ValueError("event delta invalidation target does not exist")
        cutoff = _aware_timestamp(analysis["analysis_cutoff"], "analysis.analysis_cutoff")
        later_event_ids = sorted(
            str(event["event_id"])
            for event in verified_events
            if _aware_timestamp(event["available_at"], "event.available_at") > cutoff
        )
        if not later_event_ids:
            raise ValueError("event delta has no later verified material event for an analysis")
        analysis_event_ids[analysis_id] = later_event_ids
    for analysis_id, event_ids in analysis_event_ids.items():
        supersede_artifact_with_events(
            conn,
            analysis_id,
            event_ids,
            reason=f"event_delta:{delta_artifact_id}:verified_material_post_cutoff",
            ensure_schema=False,
        )
    output_ids = sorted(analysis_event_ids)
    output_digest = _digest(
        {
            "contract": "CanonicalAnalysisInvalidationProductionV1",
            "analysis_event_ids": analysis_event_ids,
            "delta_artifact_id": delta_artifact_id,
            "produced_at": produced_at,
        }
    )
    return "applied", output_ids, "canonical_analysis_invalidation", output_digest


def produce_zero_gpu_outbox_artifact(
    conn: sqlite3.Connection,
    outbox_id: str,
    *,
    produced_at: str,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Atomically produce typed artifacts/invalidation with zero model or GPU calls."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    _aware_timestamp(produced_at, "produced_at")
    outbox = _select_one(
        conn,
        "SELECT * FROM single_track_v3_outbox WHERE outbox_id=?",
        (str(outbox_id),),
    )
    if outbox is None:
        raise ValueError("outbox item does not exist")
    existing_receipt = artifact_production_receipt(conn, str(outbox_id))
    if existing_receipt is not None:
        _validate_receipt_replay(conn, outbox, existing_receipt)
        return existing_receipt
    if outbox["event_type"] not in _SUPPORTED_EVENT_TYPES:
        raise ValueError("outbox event type is not supported by the zero-GPU producer")
    if outbox["status"] != "pending":
        raise ValueError("outbox item is not pending and has no production receipt")
    if _aware_timestamp(outbox["available_at"], "outbox.available_at") > _aware_timestamp(
        produced_at, "produced_at"
    ):
        raise ValueError("outbox item is not available for production yet")
    payload = _json_load(outbox["payload_json"], None)
    if not isinstance(payload, Mapping):
        raise ValueError("outbox payload must be an object")
    input_payload_digest = _digest(dict(payload))
    savepoint = "single_track_zero_gpu_producer"
    _begin_savepoint(conn, savepoint)
    try:
        if outbox["event_type"] == "CONTENT_TERMINAL_CASCADE":
            status, output_ids, output_type, output_digest = _produce_content_terminal(
                conn,
                outbox,
                payload,
                produced_at=produced_at,
            )
        else:
            status, output_ids, output_type, output_digest = _produce_event_delta(
                conn,
                outbox,
                payload,
                produced_at=produced_at,
            )
        receipt_id = f"artifact-production:{_digest({'outbox_id': outbox_id, 'producer': ZERO_GPU_PRODUCER_VERSION, 'input': input_payload_digest})}"
        receipt_values = {
            "receipt_id": receipt_id,
            "outbox_id": str(outbox_id),
            "input_event_type": outbox["event_type"],
            "input_payload_digest": input_payload_digest,
            "producer_version": ZERO_GPU_PRODUCER_VERSION,
            "production_status": status,
            "output_artifact_type": output_type,
            "output_artifact_ids_json": _canonical_json(output_ids),
            "output_digest": output_digest,
            "zero_gpu_model_calls": 0,
            "produced_at": produced_at,
            "created_at": produced_at,
        }
        columns = tuple(receipt_values)
        conn.execute(
            f"INSERT INTO single_track_v3_artifact_production_receipt({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)})",
            receipt_values,
        )
        delivered = conn.execute(
            """
            UPDATE single_track_v3_outbox SET
                status='delivered',claimed_at=?,delivered_at=?,
                attempt_count=attempt_count+1,last_error_code=NULL,updated_at=?
            WHERE outbox_id=? AND status='pending'
            """,
            (produced_at, produced_at, produced_at, str(outbox_id)),
        )
        if delivered.rowcount != 1:
            raise RuntimeError("zero-GPU outbox delivery lost compare-and-swap")
    except Exception:
        _rollback_savepoint(conn, savepoint)
        raise
    _release_savepoint(conn, savepoint)
    saved = artifact_production_receipt(conn, str(outbox_id))
    if saved is None:
        raise RuntimeError("artifact production receipt could not be read back")
    return saved
