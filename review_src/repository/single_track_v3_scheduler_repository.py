from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema


_TERMINAL_RUN_STATES = {"success", "partial", "failed", "late", "skipped"}
_RUN_TRANSITIONS = {
    "queued": {"running", "late", "skipped"},
    "running": {"success", "partial", "failed", "late"},
}
_RUN_JSON_FIELDS = {
    "source_coverage": "source_coverage_json",
    "source_failures": "source_failures_json",
}


def _json(value: Any, default: Any) -> str:
    normalized = default if value is None else value
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _aware_timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
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


def _run_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["source_coverage"] = _json_load(
        result.pop("source_coverage_json"),
        {},
    )
    result["source_failures"] = _json_load(
        result.pop("source_failures_json"),
        [],
    )
    return result


def news_retrieval_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> dict[str, Any] | None:
    """Read one durable run without bootstrapping schema or writing the database."""

    row = _select_one(
        conn,
        "SELECT * FROM news_retrieval_run WHERE run_id=?",
        (str(run_id),),
    )
    return _run_from_row(row) if row is not None else None


def create_news_retrieval_run(
    conn: sqlite3.Connection,
    run: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Create one immutable-idempotent durable scheduler run."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(run)
    for field in (
        "run_id",
        "idempotency_key",
        "slot_key",
        "target_trade_date",
        "source_policy_version",
        "calendar_revision",
    ):
        row[field] = str(row.get(field) or "").strip()
        if not row[field]:
            raise ValueError(f"{field} is required")
    row["status"] = str(row.get("status") or "queued").strip()
    for field in ("scheduled_for", "cutoff_at", "created_at", "updated_at"):
        _aware_timestamp(row.get(field), field)
    for field in ("started_at", "completed_at"):
        if row.get(field) is not None:
            _aware_timestamp(row.get(field), field)
    if row["status"] in _TERMINAL_RUN_STATES and not row.get("completed_at"):
        raise ValueError("terminal scheduler run requires completed_at")
    row["source_coverage_json"] = _json(row.get("source_coverage"), {})
    row["source_failures_json"] = _json(row.get("source_failures"), [])
    columns = (
        "run_id",
        "idempotency_key",
        "slot_key",
        "target_trade_date",
        "scheduled_for",
        "cutoff_at",
        "started_at",
        "completed_at",
        "status",
        "source_policy_version",
        "calendar_revision",
        "source_coverage_json",
        "source_failures_json",
        "late_reason",
        "created_at",
        "updated_at",
    )
    values = {column: row.get(column) for column in columns}
    try:
        conn.execute(
            f"""
            INSERT INTO news_retrieval_run({','.join(columns)})
            VALUES({','.join(':' + column for column in columns)})
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("scheduler run identity conflicts with existing content") from exc

    existing = _select_one(
        conn,
        "SELECT * FROM news_retrieval_run WHERE idempotency_key=?",
        (row["idempotency_key"],),
    )
    if existing is None:
        raise RuntimeError("scheduler run could not be read back")
    if any(existing.get(column) != values.get(column) for column in columns):
        raise ValueError("scheduler idempotency key already exists with different content")
    return _run_from_row(existing)


def news_retrieval_runs_between(
    conn: sqlite3.Connection,
    *,
    scheduled_from: str,
    scheduled_to: str,
) -> list[dict[str, Any]]:
    """Read scheduler runs in a bounded window without schema DDL or writes."""

    start = _aware_timestamp(scheduled_from, "scheduled_from")
    end = _aware_timestamp(scheduled_to, "scheduled_to")
    if end < start:
        raise ValueError("scheduled_to cannot precede scheduled_from")
    cursor = conn.execute(
        """
        SELECT * FROM news_retrieval_run
        WHERE julianday(scheduled_for)>=julianday(?)
          AND julianday(scheduled_for)<=julianday(?)
        ORDER BY scheduled_for,slot_key,run_id
        """,
        (scheduled_from, scheduled_to),
    )
    columns = [str(item[0]) for item in cursor.description or ()]
    rows = []
    for source in cursor.fetchall():
        row = dict(source) if isinstance(source, sqlite3.Row) else dict(
            zip(columns, source, strict=True)
        )
        rows.append(_run_from_row(row))
    return rows


def _tick_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for field in (
        "planned_run_ids",
        "materialized_run_ids",
        "catch_up_run_ids",
        "skipped_run_ids",
    ):
        result[field] = _json_load(result.pop(f"{field}_json"), [])
    return result


def scheduler_tick(
    conn: sqlite3.Connection,
    tick_key: str,
) -> dict[str, Any] | None:
    """Read one immutable scheduler tick receipt without side effects."""

    row = _select_one(
        conn,
        "SELECT * FROM single_track_v3_scheduler_tick WHERE tick_key=?",
        (str(tick_key),),
    )
    return _tick_from_row(row) if row is not None else None


def seal_scheduler_tick(
    conn: sqlite3.Connection,
    tick: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Seal one immutable scheduler tick receipt after its run declarations exist."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(tick)
    for field in (
        "tick_id",
        "tick_key",
        "observed_at",
        "calendar_revision",
        "source_policy_version",
        "schedule_contract_version",
        "tick_digest",
        "created_at",
    ):
        row[field] = str(row.get(field) or "").strip()
        if not row[field]:
            raise ValueError(f"{field} is required")
    _aware_timestamp(row["observed_at"], "observed_at")
    _aware_timestamp(row["created_at"], "created_at")
    for field in ("tick_key", "tick_digest"):
        value = row[field].lower()
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError(f"{field} must be a SHA-256 digest")
        row[field] = value
    for field in (
        "planned_run_ids",
        "materialized_run_ids",
        "catch_up_run_ids",
        "skipped_run_ids",
    ):
        values = sorted(str(value) for value in row.get(field) or [] if str(value))
        if len(values) != len(set(values)):
            raise ValueError(f"{field} must not contain duplicates")
        row[f"{field}_json"] = _json(values, [])
    columns = (
        "tick_id",
        "tick_key",
        "observed_at",
        "calendar_revision",
        "source_policy_version",
        "schedule_contract_version",
        "planned_run_ids_json",
        "materialized_run_ids_json",
        "catch_up_run_ids_json",
        "skipped_run_ids_json",
        "tick_digest",
        "created_at",
    )
    values = {column: row.get(column) for column in columns}
    try:
        conn.execute(
            f"INSERT INTO single_track_v3_scheduler_tick({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)}) "
            "ON CONFLICT(tick_key) DO NOTHING",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("scheduler tick identity conflicts with existing content") from exc
    existing = _select_one(
        conn,
        "SELECT * FROM single_track_v3_scheduler_tick WHERE tick_key=?",
        (row["tick_key"],),
    )
    if existing is None:
        raise RuntimeError("scheduler tick could not be read back")
    if any(existing.get(column) != values.get(column) for column in columns):
        raise ValueError("scheduler tick key already exists with different content")
    return _tick_from_row(existing)


def transition_news_retrieval_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    expected_status: str,
    new_status: str,
    updated_at: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    source_coverage: Mapping[str, Any] | None = None,
    source_failures: list[Mapping[str, Any]] | None = None,
    late_reason: str | None = None,
    ensure_schema: bool = True,
) -> bool:
    """Apply one compare-and-swap run transition; stale workers receive False."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    _aware_timestamp(updated_at, "updated_at")
    if started_at is not None:
        _aware_timestamp(started_at, "started_at")
    if completed_at is not None:
        _aware_timestamp(completed_at, "completed_at")
    if new_status not in _RUN_TRANSITIONS.get(expected_status, set()):
        raise ValueError(
            f"invalid scheduler run transition: {expected_status}->{new_status}"
        )
    if new_status in _TERMINAL_RUN_STATES and completed_at is None:
        raise ValueError("terminal scheduler run requires completed_at")
    result = conn.execute(
        """
        UPDATE news_retrieval_run SET
            status=?,
            started_at=COALESCE(?,started_at),
            completed_at=COALESCE(?,completed_at),
            source_coverage_json=COALESCE(?,source_coverage_json),
            source_failures_json=COALESCE(?,source_failures_json),
            late_reason=COALESCE(?,late_reason),
            updated_at=?
        WHERE run_id=? AND status=?
        """,
        (
            str(new_status),
            started_at,
            completed_at,
            _json(source_coverage, {}) if source_coverage is not None else None,
            _json(source_failures, []) if source_failures is not None else None,
            late_reason,
            updated_at,
            str(run_id),
            str(expected_status),
        ),
    )
    return result.rowcount == 1


def acquire_scheduler_lease(
    conn: sqlite3.Connection,
    *,
    lease_key: str,
    run_id: str | None,
    owner_id: str,
    lease_token: str,
    acquired_at: str,
    expires_at: str,
    ensure_schema: bool = True,
) -> bool:
    """Acquire an absent or expired lease without stealing a live lease."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    acquired = _aware_timestamp(acquired_at, "acquired_at")
    expires = _aware_timestamp(expires_at, "expires_at")
    if expires <= acquired:
        raise ValueError("expires_at must be after acquired_at")
    for field, value in (
        ("lease_key", lease_key),
        ("owner_id", owner_id),
        ("lease_token", lease_token),
    ):
        if not str(value or "").strip():
            raise ValueError(f"{field} is required")
    conn.execute(
        """
        INSERT INTO single_track_v3_job_lease(
            lease_key,run_id,owner_id,lease_token,acquired_at,
            heartbeat_at,expires_at,state,released_at
        ) VALUES(?,?,?,?,?,?,?,'active',NULL)
        ON CONFLICT(lease_key) DO UPDATE SET
            run_id=excluded.run_id,
            owner_id=excluded.owner_id,
            lease_token=excluded.lease_token,
            acquired_at=excluded.acquired_at,
            heartbeat_at=excluded.heartbeat_at,
            expires_at=excluded.expires_at,
            state='active',
            released_at=NULL
        WHERE single_track_v3_job_lease.state!='active'
           OR julianday(single_track_v3_job_lease.expires_at)<=julianday(excluded.acquired_at)
        """,
        (
            str(lease_key),
            str(run_id) if run_id is not None else None,
            str(owner_id),
            str(lease_token),
            acquired_at,
            acquired_at,
            expires_at,
        ),
    )
    current = _select_one(
        conn,
        "SELECT owner_id,lease_token,state FROM single_track_v3_job_lease WHERE lease_key=?",
        (str(lease_key),),
    )
    return bool(
        current
        and current.get("owner_id") == str(owner_id)
        and current.get("lease_token") == str(lease_token)
        and current.get("state") == "active"
    )


def record_scheduler_heartbeat(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
    lease_key: str,
    lease_token: str,
    run_id: str | None,
    worker_identity_digest: str,
    state: str,
    heartbeat_at: str,
    expires_at: str,
    metadata: Mapping[str, Any] | None = None,
    ensure_schema: bool = True,
) -> bool:
    """Extend and record a heartbeat only for the current live lease token."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    heartbeat = _aware_timestamp(heartbeat_at, "heartbeat_at")
    expires = _aware_timestamp(expires_at, "expires_at")
    if expires <= heartbeat:
        raise ValueError("expires_at must be after heartbeat_at")
    digest = str(worker_identity_digest or "").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("worker_identity_digest must be a lowercase SHA-256 digest")
    renewed = conn.execute(
        """
        UPDATE single_track_v3_job_lease
        SET heartbeat_at=?,expires_at=?
        WHERE lease_key=? AND lease_token=? AND state='active'
          AND run_id IS ?
          AND julianday(expires_at)>julianday(?)
        """,
        (
            heartbeat_at,
            expires_at,
            str(lease_key),
            str(lease_token),
            str(run_id) if run_id is not None else None,
            heartbeat_at,
        ),
    )
    if renewed.rowcount != 1:
        return False
    heartbeat_values = (
        str(worker_id),
        heartbeat_at,
        str(lease_key),
        str(run_id) if run_id is not None else None,
        digest,
        str(state),
        _json(metadata, {}),
    )
    conn.execute(
        """
        INSERT INTO single_track_v3_worker_heartbeat(
            worker_id,heartbeat_at,lease_key,run_id,
            worker_identity_digest,state,metadata_json
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(worker_id,heartbeat_at) DO NOTHING
        """,
        heartbeat_values,
    )
    existing = _select_one(
        conn,
        """
        SELECT worker_id,heartbeat_at,lease_key,run_id,
               worker_identity_digest,state,metadata_json
        FROM single_track_v3_worker_heartbeat
        WHERE worker_id=? AND heartbeat_at=?
        """,
        (str(worker_id), heartbeat_at),
    )
    if existing is None or tuple(existing.values()) != heartbeat_values:
        raise ValueError("heartbeat identity already exists with different content")
    return True


def release_scheduler_lease(
    conn: sqlite3.Connection,
    *,
    lease_key: str,
    lease_token: str,
    released_at: str,
    ensure_schema: bool = True,
) -> bool:
    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    _aware_timestamp(released_at, "released_at")
    result = conn.execute(
        """
        UPDATE single_track_v3_job_lease
        SET state='released',released_at=?,heartbeat_at=?,expires_at=?
        WHERE lease_key=? AND lease_token=? AND state='active'
        """,
        (released_at, released_at, released_at, str(lease_key), str(lease_token)),
    )
    return result.rowcount == 1


def enqueue_scheduler_outbox(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> str:
    """Enqueue one immutable, deduplicated scheduler event."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(item)
    for field in ("outbox_id", "event_type", "aggregate_key", "dedupe_key"):
        row[field] = str(row.get(field) or "").strip()
        if not row[field]:
            raise ValueError(f"{field} is required")
    for field in ("available_at", "created_at", "updated_at"):
        _aware_timestamp(row.get(field), field)
    for field in ("claimed_at", "delivered_at"):
        if row.get(field) is not None:
            _aware_timestamp(row.get(field), field)
    row["payload_json"] = _json(row.get("payload"), {})
    row["status"] = str(row.get("status") or "pending")
    row["attempt_count"] = int(row.get("attempt_count") or 0)
    columns = (
        "outbox_id",
        "run_id",
        "event_type",
        "aggregate_key",
        "dedupe_key",
        "payload_json",
        "status",
        "available_at",
        "claimed_at",
        "delivered_at",
        "attempt_count",
        "last_error_code",
        "created_at",
        "updated_at",
    )
    values = {column: row.get(column) for column in columns}
    try:
        conn.execute(
            f"""
            INSERT INTO single_track_v3_outbox({','.join(columns)})
            VALUES({','.join(':' + column for column in columns)})
            ON CONFLICT(dedupe_key) DO NOTHING
            """,
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("outbox identity conflicts with existing content") from exc
    existing = _select_one(
        conn,
        "SELECT * FROM single_track_v3_outbox WHERE dedupe_key=?",
        (row["dedupe_key"],),
    )
    if existing is None:
        raise RuntimeError("outbox item could not be read back")
    if any(existing.get(column) != values.get(column) for column in columns):
        raise ValueError("outbox dedupe key already exists with different content")
    return str(existing["outbox_id"])
