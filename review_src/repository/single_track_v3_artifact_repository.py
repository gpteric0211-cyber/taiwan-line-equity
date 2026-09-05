from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema


_PREMARKET_SLOTS = {
    "evening_1800",
    "evening_2100",
    "preopen_0600",
    "preopen_final_scan",
}
_PREMARKET_STATUSES = {"sealed", "partial", "incomplete", "late"}
_DELTA_TERMINAL_STATUSES = {"sealed", "partial", "incomplete"}
_RUN_TO_PREMARKET_STATUS = {
    "success": {"sealed"},
    "partial": {"partial", "incomplete"},
    "failed": {"incomplete"},
    "late": {"late", "partial", "incomplete"},
}
_FORBIDDEN_PAYLOAD_KEYS = {
    "raw_body",
    "article_body",
    "full_article_body",
    "full_text",
    "html_body",
    "ocr_text",
}


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact payload must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _date_text(value: Any, field: str) -> str:
    text = _required_text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be a canonical ISO-8601 date")
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


def _contains_forbidden_payload_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().casefold()
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                return normalized
            found = _contains_forbidden_payload_key(nested)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _contains_forbidden_payload_key(nested)
            if found is not None:
                return found
    return None


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result = dict(value)
    forbidden = _contains_forbidden_payload_key(result)
    if forbidden is not None:
        raise ValueError(f"{field} contains forbidden field: {forbidden}")
    _canonical_json(result)
    return result


def _json_list(value: Any, field: str, *, maximum_items: int) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    result = list(value)
    if len(result) > maximum_items:
        raise ValueError(f"{field} exceeds its item limit")
    forbidden = _contains_forbidden_payload_key(result)
    if forbidden is not None:
        raise ValueError(f"{field} contains forbidden field: {forbidden}")
    _canonical_json(result)
    return result


def _id_list(value: Any, field: str, *, maximum_items: int) -> list[str]:
    items = _json_list(value, field, maximum_items=maximum_items)
    normalized = sorted(_required_text(item, field) for item in items)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


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


def _rows(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
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


def _decode_premarket(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["event_revision_ids"] = _json_load(
        result.pop("event_revision_ids_json"), []
    )
    result["source_snapshot_ids"] = _json_load(
        result.pop("source_snapshot_ids_json"), []
    )
    result["source_coverage"] = _json_load(result.pop("source_coverage_json"), {})
    result["source_failures"] = _json_load(result.pop("source_failures_json"), [])
    return result


def _decode_delta(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["event_revision_ids"] = _json_load(
        result.pop("event_revision_ids_json"), []
    )
    result["invalidated_analysis_ids"] = _json_load(
        result.pop("invalidated_analysis_ids_json"), []
    )
    result["source_coverage"] = _json_load(result.pop("source_coverage_json"), {})
    return result


def _run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = _select_one(
        conn,
        "SELECT * FROM news_retrieval_run WHERE run_id=?",
        (run_id,),
    )
    if row is None:
        raise ValueError("run_id must reference a retrieval run")
    return row


def _event_revisions(
    conn: sqlite3.Connection,
    event_revision_ids: list[str],
    *,
    cutoff_at: datetime,
    sealed_by: datetime,
) -> list[dict[str, Any]]:
    if not event_revision_ids:
        return []
    placeholders = ",".join("?" for _ in event_revision_ids)
    rows = _rows(
        conn,
        f"SELECT * FROM event_revision WHERE event_revision_id IN ({placeholders})",
        tuple(event_revision_ids),
    )
    by_id = {str(row["event_revision_id"]): row for row in rows}
    if set(by_id) != set(event_revision_ids):
        raise ValueError("event_revision_ids must reference sealed event revisions")
    ordered = [by_id[event_revision_id] for event_revision_id in event_revision_ids]
    for row in ordered:
        if _aware_timestamp(row["available_at"], "event.available_at") > cutoff_at:
            raise ValueError("event revision is not available at the artifact cutoff")
        if _aware_timestamp(row["sealed_at"], "event.sealed_at") > sealed_by:
            raise ValueError("event revision was not sealed when the artifact was created")
    return ordered


def _source_snapshots(
    conn: sqlite3.Connection,
    source_snapshot_ids: list[str],
    *,
    run_id: str,
    target_trade_date: str,
    cutoff_at: datetime,
    sealed_by: datetime,
) -> list[dict[str, Any]]:
    if not source_snapshot_ids:
        return []
    placeholders = ",".join("?" for _ in source_snapshot_ids)
    rows = _rows(
        conn,
        f"SELECT * FROM single_track_v3_source_snapshot_receipt "
        f"WHERE snapshot_id IN ({placeholders})",
        tuple(source_snapshot_ids),
    )
    by_id = {str(row["snapshot_id"]): row for row in rows}
    if set(by_id) != set(source_snapshot_ids):
        raise ValueError("source_snapshot_ids must reference sealed source snapshots")
    ordered = [by_id[snapshot_id] for snapshot_id in source_snapshot_ids]
    for row in ordered:
        if str(row["run_id"]) != run_id:
            raise ValueError("source snapshot must belong to the artifact retrieval run")
        if str(row["target_trade_date"]) != target_trade_date:
            raise ValueError("source snapshot target_trade_date must match the artifact")
        if _aware_timestamp(row["cutoff_at"], "source_snapshot.cutoff_at") != cutoff_at:
            raise ValueError("source snapshot cutoff must match the artifact cutoff")
        if row.get("available_at") is not None and _aware_timestamp(
            row["available_at"], "source_snapshot.available_at"
        ) > cutoff_at:
            raise ValueError("source snapshot is not available at the artifact cutoff")
        if _aware_timestamp(row["sealed_at"], "source_snapshot.sealed_at") > sealed_by:
            raise ValueError("source snapshot was not sealed when the artifact was created")
    return ordered


def _validate_digest(value: Any, expected: str, field: str) -> str:
    if value is None:
        return expected
    supplied = _required_text(value, field).casefold()
    if supplied != expected:
        raise ValueError(f"{field} does not match canonical content")
    return expected


def premarket_artifact_key(artifact: Mapping[str, Any]) -> str:
    """Build the deterministic identity for one scheduled premarket artifact."""

    return _digest(
        {
            "contract": "PremarketIntelligenceArtifactV2",
            "run_id": _required_text(artifact.get("run_id"), "run_id"),
            "target_trade_date": _date_text(
                artifact.get("target_trade_date"), "target_trade_date"
            ),
            "slot_key": _required_text(artifact.get("slot_key"), "slot_key"),
            "cutoff_at": _required_text(artifact.get("cutoff_at"), "cutoff_at"),
            "source_policy_version": _required_text(
                artifact.get("source_policy_version"), "source_policy_version"
            ),
            "calendar_revision": _required_text(
                artifact.get("calendar_revision"), "calendar_revision"
            ),
        }
    )


def premarket_intelligence_artifact(
    conn: sqlite3.Connection,
    artifact_id: str,
) -> dict[str, Any] | None:
    """Read one premarket artifact without schema DDL or repair work."""

    row = _select_one(
        conn,
        "SELECT * FROM premarket_intelligence_artifact WHERE artifact_id=?",
        (str(artifact_id),),
    )
    return _decode_premarket(row) if row is not None else None


def seal_premarket_intelligence_artifact(
    conn: sqlite3.Connection,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal one immutable upstream artifact from a terminal scheduled retrieval run."""

    ensure_single_track_v3_schema(conn)
    row = dict(artifact)
    artifact_id = _required_text(row.get("artifact_id"), "artifact_id")
    run_id = _required_text(row.get("run_id"), "run_id")
    target_trade_date = _date_text(
        row.get("target_trade_date"), "target_trade_date"
    )
    slot_key = _required_text(row.get("slot_key"), "slot_key")
    if slot_key not in _PREMARKET_SLOTS:
        raise ValueError("premarket slot_key must be one of the four scheduled slots")
    artifact_status = _required_text(row.get("artifact_status"), "artifact_status")
    if artifact_status not in _PREMARKET_STATUSES:
        raise ValueError("artifact_status is invalid")
    cutoff_at = _aware_timestamp(row.get("cutoff_at"), "cutoff_at")
    sealed_at = _aware_timestamp(row.get("sealed_at"), "sealed_at")
    created_at = _aware_timestamp(row.get("created_at"), "created_at")
    if sealed_at < cutoff_at:
        raise ValueError("sealed_at cannot precede cutoff_at")
    if created_at > sealed_at:
        raise ValueError("created_at cannot be later than sealed_at")
    source_policy_version = _required_text(
        row.get("source_policy_version"), "source_policy_version"
    )
    calendar_revision = _required_text(
        row.get("calendar_revision"), "calendar_revision"
    )
    event_revision_ids = _id_list(
        row.get("event_revision_ids", []),
        "event_revision_ids",
        maximum_items=512,
    )
    source_snapshot_ids = _id_list(
        row.get("source_snapshot_ids", []),
        "source_snapshot_ids",
        maximum_items=1024,
    )
    source_coverage = _mapping(row.get("source_coverage", {}), "source_coverage")
    source_failures = _json_list(
        row.get("source_failures", []), "source_failures", maximum_items=128
    )

    run = _run(conn, run_id)
    if (
        str(run["target_trade_date"]) != target_trade_date
        or str(run["slot_key"]) != slot_key
        or str(run["cutoff_at"]) != str(row.get("cutoff_at"))
        or str(run["source_policy_version"]) != source_policy_version
        or str(run["calendar_revision"]) != calendar_revision
    ):
        raise ValueError("premarket artifact does not match its retrieval run identity")
    allowed_statuses = _RUN_TO_PREMARKET_STATUS.get(str(run["status"]), set())
    if artifact_status not in allowed_statuses:
        raise ValueError("premarket artifact status is incompatible with retrieval run status")
    if run.get("completed_at") is None:
        raise ValueError("premarket artifact requires a completed retrieval run")
    if _aware_timestamp(run["completed_at"], "run.completed_at") > sealed_at:
        raise ValueError("premarket artifact cannot be sealed before its run completes")
    if _canonical_json(source_coverage) != str(run["source_coverage_json"]):
        raise ValueError("source_coverage must exactly match the retrieval run")
    if _canonical_json(source_failures) != str(run["source_failures_json"]):
        raise ValueError("source_failures must exactly match the retrieval run")

    revisions = _event_revisions(
        conn,
        event_revision_ids,
        cutoff_at=cutoff_at,
        sealed_by=sealed_at,
    )
    _source_snapshots(
        conn,
        source_snapshot_ids,
        run_id=run_id,
        target_trade_date=target_trade_date,
        cutoff_at=cutoff_at,
        sealed_by=sealed_at,
    )
    watermark = None
    if revisions:
        watermark_row = max(
            revisions,
            key=lambda item: _aware_timestamp(
                item["available_at"], "event.available_at"
            ),
        )
        watermark = str(watermark_row["available_at"])
    if row.get("event_watermark") not in (None, watermark):
        raise ValueError("event_watermark must equal the latest included availability")

    artifact_key = premarket_artifact_key(row)
    _validate_digest(row.get("artifact_key"), artifact_key, "artifact_key")
    digest_payload = {
        "contract": "PremarketIntelligenceArtifactV2",
        "artifact_key": artifact_key,
        "run_id": run_id,
        "target_trade_date": target_trade_date,
        "slot_key": slot_key,
        "cutoff_at": row.get("cutoff_at"),
        "event_watermark": watermark,
        "event_revision_ids": event_revision_ids,
        "source_snapshot_ids": source_snapshot_ids,
        "source_coverage": source_coverage,
        "source_failures": source_failures,
        "artifact_status": artifact_status,
        "source_policy_version": source_policy_version,
        "calendar_revision": calendar_revision,
        "sealed_at": row.get("sealed_at"),
    }
    artifact_digest = _validate_digest(
        row.get("artifact_digest"), _digest(digest_payload), "artifact_digest"
    )
    values = {
        "artifact_id": artifact_id,
        "artifact_key": artifact_key,
        "run_id": run_id,
        "target_trade_date": target_trade_date,
        "slot_key": slot_key,
        "cutoff_at": row.get("cutoff_at"),
        "event_watermark": watermark,
        "event_revision_ids_json": _canonical_json(event_revision_ids),
        "source_snapshot_ids_json": _canonical_json(source_snapshot_ids),
        "source_coverage_json": _canonical_json(source_coverage),
        "source_failures_json": _canonical_json(source_failures),
        "artifact_status": artifact_status,
        "source_policy_version": source_policy_version,
        "calendar_revision": calendar_revision,
        "artifact_digest": artifact_digest,
        "sealed_at": row.get("sealed_at"),
        "created_at": row.get("created_at"),
    }
    columns = tuple(values)
    try:
        conn.execute(
            f"INSERT INTO premarket_intelligence_artifact({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)}) "
            "ON CONFLICT(artifact_key) DO NOTHING",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("premarket artifact identity conflicts with existing content") from exc
    existing = _select_one(
        conn,
        "SELECT * FROM premarket_intelligence_artifact WHERE artifact_key=?",
        (artifact_key,),
    )
    if existing is None:
        raise RuntimeError("premarket artifact could not be read back")
    if any(existing.get(column) != values.get(column) for column in columns):
        raise ValueError("premarket artifact key already exists with different content")
    return _decode_premarket(existing)


def latest_premarket_intelligence_artifact_at(
    conn: sqlite3.Connection,
    *,
    target_trade_date: str,
    analysis_cutoff: str,
) -> dict[str, Any] | None:
    """Select the latest sealed slot visible at cutoff, never another trade date."""

    target = _date_text(target_trade_date, "target_trade_date")
    cutoff = _aware_timestamp(analysis_cutoff, "analysis_cutoff")
    candidates = []
    for row in _rows(
        conn,
        "SELECT * FROM premarket_intelligence_artifact WHERE target_trade_date=?",
        (target,),
    ):
        if _aware_timestamp(row["cutoff_at"], "artifact.cutoff_at") > cutoff:
            continue
        if _aware_timestamp(row["sealed_at"], "artifact.sealed_at") > cutoff:
            continue
        candidates.append(row)
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda item: (
            _aware_timestamp(item["cutoff_at"], "artifact.cutoff_at"),
            _aware_timestamp(item["sealed_at"], "artifact.sealed_at"),
            str(item["artifact_id"]),
        ),
    )
    return _decode_premarket(selected)


def event_delta_key(delta: Mapping[str, Any]) -> str:
    """Build the deterministic identity for one newly detected event set."""

    return _digest(
        {
            "contract": "EventDeltaArtifactV1",
            "run_id": delta.get("run_id"),
            "target_trade_date": _date_text(
                delta.get("target_trade_date"), "target_trade_date"
            ),
            "base_premarket_artifact_id": delta.get(
                "base_premarket_artifact_id"
            ),
            "cutoff_at": _required_text(delta.get("cutoff_at"), "cutoff_at"),
            "detected_at": _required_text(delta.get("detected_at"), "detected_at"),
            "event_revision_ids": _id_list(
                delta.get("event_revision_ids", []),
                "event_revision_ids",
                maximum_items=512,
            ),
            "source_policy_version": _required_text(
                delta.get("source_policy_version"), "source_policy_version"
            ),
            "calendar_revision": _required_text(
                delta.get("calendar_revision"), "calendar_revision"
            ),
        }
    )


def event_delta_artifact(
    conn: sqlite3.Connection,
    delta_artifact_id: str,
) -> dict[str, Any] | None:
    """Read one event-delta artifact without schema DDL or repair work."""

    row = _select_one(
        conn,
        "SELECT * FROM event_delta_artifact WHERE delta_artifact_id=?",
        (str(delta_artifact_id),),
    )
    return _decode_delta(row) if row is not None else None


def create_event_delta_artifact(
    conn: sqlite3.Connection,
    delta: Mapping[str, Any],
) -> dict[str, Any]:
    """Create an immutable pending delta; sealing is a separate CAS transition."""

    ensure_single_track_v3_schema(conn)
    row = dict(delta)
    delta_artifact_id = _required_text(
        row.get("delta_artifact_id"), "delta_artifact_id"
    )
    target_trade_date = _date_text(
        row.get("target_trade_date"), "target_trade_date"
    )
    cutoff_at = _aware_timestamp(row.get("cutoff_at"), "cutoff_at")
    detected_at = _aware_timestamp(row.get("detected_at"), "detected_at")
    created_at = _aware_timestamp(row.get("created_at"), "created_at")
    updated_at = _aware_timestamp(row.get("updated_at"), "updated_at")
    if detected_at < cutoff_at:
        raise ValueError("detected_at cannot precede cutoff_at")
    if created_at < detected_at or updated_at < created_at:
        raise ValueError("delta timestamps are not monotonic")
    if row.get("delta_status", "pending") != "pending":
        raise ValueError("new event delta must begin in pending status")
    if row.get("artifact_digest") is not None or row.get("sealed_at") is not None:
        raise ValueError("pending event delta cannot already be sealed")
    source_policy_version = _required_text(
        row.get("source_policy_version"), "source_policy_version"
    )
    calendar_revision = _required_text(
        row.get("calendar_revision"), "calendar_revision"
    )
    event_revision_ids = _id_list(
        row.get("event_revision_ids", []),
        "event_revision_ids",
        maximum_items=512,
    )
    if not event_revision_ids:
        raise ValueError("event delta requires at least one event revision")
    invalidated_analysis_ids = _id_list(
        row.get("invalidated_analysis_ids", []),
        "invalidated_analysis_ids",
        maximum_items=512,
    )
    source_coverage = _mapping(row.get("source_coverage", {}), "source_coverage")
    revisions = _event_revisions(
        conn,
        event_revision_ids,
        cutoff_at=cutoff_at,
        sealed_by=detected_at,
    )

    base_id = row.get("base_premarket_artifact_id")
    if base_id is not None:
        base_id = _required_text(base_id, "base_premarket_artifact_id")
        base = _select_one(
            conn,
            "SELECT * FROM premarket_intelligence_artifact WHERE artifact_id=?",
            (base_id,),
        )
        if base is None:
            raise ValueError("base_premarket_artifact_id does not exist")
        if (
            str(base["target_trade_date"]) != target_trade_date
            or str(base["source_policy_version"]) != source_policy_version
            or str(base["calendar_revision"]) != calendar_revision
        ):
            raise ValueError("event delta does not match its base premarket artifact")
        base_cutoff = _aware_timestamp(base["cutoff_at"], "base.cutoff_at")
        if base_cutoff >= cutoff_at:
            raise ValueError("event delta cutoff must be later than its base artifact")
        if any(
            _aware_timestamp(item["available_at"], "event.available_at")
            <= base_cutoff
            for item in revisions
        ):
            raise ValueError("event delta may contain only post-base event revisions")

    run_id = row.get("run_id")
    if run_id is not None:
        run_id = _required_text(run_id, "run_id")
        run = _run(conn, run_id)
        if str(run["status"]) not in _RUN_TO_PREMARKET_STATUS:
            raise ValueError("event delta requires a terminal retrieval run")
        if run.get("completed_at") is None or _aware_timestamp(
            run["completed_at"], "run.completed_at"
        ) > created_at:
            raise ValueError("event delta requires a completed retrieval run")
        if (
            str(run["target_trade_date"]) != target_trade_date
            or str(run["source_policy_version"]) != source_policy_version
            or str(run["calendar_revision"]) != calendar_revision
        ):
            raise ValueError("event delta does not match its retrieval run")
        if _aware_timestamp(run["cutoff_at"], "run.cutoff_at") > cutoff_at:
            raise ValueError("event delta cutoff cannot precede the retrieval run cutoff")
        if _canonical_json(source_coverage) != str(run["source_coverage_json"]):
            raise ValueError("source_coverage must exactly match the retrieval run")

    if invalidated_analysis_ids:
        placeholders = ",".join("?" for _ in invalidated_analysis_ids)
        analyses = _rows(
            conn,
            f"SELECT analysis_id,analysis_cutoff FROM canonical_analysis_artifact "
            f"WHERE analysis_id IN ({placeholders})",
            tuple(invalidated_analysis_ids),
        )
        if {str(item["analysis_id"]) for item in analyses} != set(
            invalidated_analysis_ids
        ):
            raise ValueError("invalidated_analysis_ids must reference canonical artifacts")
        latest_event_availability = max(
            _aware_timestamp(item["available_at"], "event.available_at")
            for item in revisions
        )
        if any(
            _aware_timestamp(item["analysis_cutoff"], "analysis.analysis_cutoff")
            >= latest_event_availability
            for item in analyses
        ):
            raise ValueError("an invalidated analysis must predate a delta event")

    delta_key_value = event_delta_key({**row, "event_revision_ids": event_revision_ids})
    _validate_digest(row.get("delta_key"), delta_key_value, "delta_key")
    values = {
        "delta_artifact_id": delta_artifact_id,
        "delta_key": delta_key_value,
        "run_id": run_id,
        "target_trade_date": target_trade_date,
        "base_premarket_artifact_id": base_id,
        "cutoff_at": row.get("cutoff_at"),
        "detected_at": row.get("detected_at"),
        "event_revision_ids_json": _canonical_json(event_revision_ids),
        "invalidated_analysis_ids_json": _canonical_json(invalidated_analysis_ids),
        "source_coverage_json": _canonical_json(source_coverage),
        "delta_status": "pending",
        "source_policy_version": source_policy_version,
        "calendar_revision": calendar_revision,
        "artifact_digest": None,
        "sealed_at": None,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    columns = tuple(values)
    try:
        conn.execute(
            f"INSERT INTO event_delta_artifact({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)}) "
            "ON CONFLICT(delta_key) DO NOTHING",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("event delta identity conflicts with existing content") from exc
    existing = _select_one(
        conn,
        "SELECT * FROM event_delta_artifact WHERE delta_key=?",
        (delta_key_value,),
    )
    if existing is None:
        raise RuntimeError("event delta could not be read back")
    immutable_columns = tuple(
        column
        for column in columns
        if column not in {"delta_status", "artifact_digest", "sealed_at", "updated_at"}
    )
    if any(existing.get(column) != values.get(column) for column in immutable_columns):
        raise ValueError("event delta key already exists with different content")
    return _decode_delta(existing)


def seal_event_delta_artifact(
    conn: sqlite3.Connection,
    delta_artifact_id: str,
    *,
    expected_status: str,
    new_status: str,
    sealed_at: str,
    updated_at: str,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Seal a pending event delta by CAS; exact replays are idempotent."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    if expected_status != "pending":
        raise ValueError("event delta seal expected_status must be pending")
    if new_status not in _DELTA_TERMINAL_STATUSES:
        raise ValueError("event delta terminal status is invalid")
    sealed = _aware_timestamp(sealed_at, "sealed_at")
    updated = _aware_timestamp(updated_at, "updated_at")
    existing = _select_one(
        conn,
        "SELECT * FROM event_delta_artifact WHERE delta_artifact_id=?",
        (str(delta_artifact_id),),
    )
    if existing is None:
        raise ValueError("event delta artifact does not exist")
    if sealed < _aware_timestamp(existing["detected_at"], "delta.detected_at"):
        raise ValueError("sealed_at cannot precede detected_at")
    if updated < sealed:
        raise ValueError("updated_at cannot precede sealed_at")
    digest_payload = {
        "contract": "EventDeltaArtifactV1",
        "delta_key": existing["delta_key"],
        "run_id": existing["run_id"],
        "target_trade_date": existing["target_trade_date"],
        "base_premarket_artifact_id": existing["base_premarket_artifact_id"],
        "cutoff_at": existing["cutoff_at"],
        "detected_at": existing["detected_at"],
        "event_revision_ids": _json_load(existing["event_revision_ids_json"], []),
        "invalidated_analysis_ids": _json_load(
            existing["invalidated_analysis_ids_json"], []
        ),
        "source_coverage": _json_load(existing["source_coverage_json"], {}),
        "delta_status": new_status,
        "source_policy_version": existing["source_policy_version"],
        "calendar_revision": existing["calendar_revision"],
        "sealed_at": sealed_at,
    }
    artifact_digest = _digest(digest_payload)
    if existing["delta_status"] != "pending":
        if (
            existing["delta_status"] == new_status
            and existing["artifact_digest"] == artifact_digest
            and existing["sealed_at"] == sealed_at
            and existing["updated_at"] == updated_at
        ):
            return _decode_delta(existing)
        raise ValueError("event delta is already terminal with different content")
    result = conn.execute(
        """
        UPDATE event_delta_artifact SET
            delta_status=?,artifact_digest=?,sealed_at=?,updated_at=?
        WHERE delta_artifact_id=? AND delta_status=?
        """,
        (
            new_status,
            artifact_digest,
            sealed_at,
            updated_at,
            str(delta_artifact_id),
            expected_status,
        ),
    )
    if result.rowcount != 1:
        concurrent = event_delta_artifact(conn, str(delta_artifact_id))
        if concurrent is None:
            raise RuntimeError("event delta disappeared during sealing")
        if (
            concurrent["delta_status"] == new_status
            and concurrent["artifact_digest"] == artifact_digest
            and concurrent["sealed_at"] == sealed_at
            and concurrent["updated_at"] == updated_at
        ):
            return concurrent
        raise ValueError("event delta compare-and-swap lost to a conflicting transition")
    saved = event_delta_artifact(conn, str(delta_artifact_id))
    if saved is None:
        raise RuntimeError("sealed event delta could not be read back")
    return saved


def event_delta_artifacts_available_at(
    conn: sqlite3.Connection,
    *,
    target_trade_date: str,
    analysis_cutoff: str,
) -> list[dict[str, Any]]:
    """Read point-in-time deltas, projecting not-yet-sealed rows as pending."""

    target = _date_text(target_trade_date, "target_trade_date")
    cutoff = _aware_timestamp(analysis_cutoff, "analysis_cutoff")
    result = []
    for source in _rows(
        conn,
        "SELECT * FROM event_delta_artifact WHERE target_trade_date=?",
        (target,),
    ):
        if _aware_timestamp(source["created_at"], "delta.created_at") > cutoff:
            continue
        row = _decode_delta(source)
        if row["sealed_at"] is not None and _aware_timestamp(
            row["sealed_at"], "delta.sealed_at"
        ) > cutoff:
            row["delta_status"] = "pending"
            row["artifact_digest"] = None
            row["sealed_at"] = None
            row["updated_at"] = row["created_at"]
        result.append(row)
    result.sort(
        key=lambda item: (
            _aware_timestamp(item["detected_at"], "delta.detected_at"),
            str(item["delta_artifact_id"]),
        )
    )
    return result
