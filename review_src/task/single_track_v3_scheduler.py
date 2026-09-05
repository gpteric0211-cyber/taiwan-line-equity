from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from repository.single_track_v3_artifact_production_repository import (
    pending_zero_gpu_outbox_items_at,
    produce_zero_gpu_outbox_artifact,
)
from repository.single_track_v3_calendar_repository import (
    calendar_revision,
    latest_calendar_revision_at,
    next_open_session_after,
)
from repository.single_track_v3_scheduler_repository import (
    create_news_retrieval_run,
    news_retrieval_runs_between,
    scheduler_tick,
    seal_scheduler_tick,
)
from task.single_track_v3_retrieval_worker import retrieval_worker_schema_ready


TPE = ZoneInfo("Asia/Taipei")
SCHEDULE_CONTRACT_VERSION = "SingleTrackV3DurableScheduleV1"
REQUIRED_SCHEDULER_TABLES = {
    "news_retrieval_run",
    "single_track_v3_outbox",
    "single_track_v3_calendar_revision",
    "single_track_v3_calendar_session",
    "single_track_v3_scheduler_tick",
    "single_track_v3_artifact_production_receipt",
}
_OPEN_SESSION_STATES = {"scheduled", "delayed", "special_session", "early_close"}
_FIXED_SLOT_SPECS = (
    ("evening_1800", -1, time(18, 0), time(18, 15)),
    ("evening_2100", -1, time(21, 0), time(21, 15)),
    ("preopen_0600", 0, time(6, 0), time(6, 15)),
    ("preopen_final_scan", 0, time(6, 45), time(7, 0)),
)


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
        raise ValueError("scheduler payload must be finite JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _tpe_timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError(f"{field} must use the Asia/Taipei UTC+08:00 offset")
    return parsed.astimezone(TPE)


def _iso(value: datetime) -> str:
    return value.astimezone(TPE).isoformat(timespec="seconds")


def _combine(day: date, local_time: time) -> datetime:
    return datetime.combine(day, local_time, tzinfo=TPE)


def _floor_quarter(value: datetime) -> datetime:
    return value.replace(minute=(value.minute // 15) * 15, second=0, microsecond=0)


def _next_open_session(
    sessions: Iterable[Mapping[str, Any]],
    cutoff: datetime,
) -> dict[str, Any] | None:
    candidates = []
    for source in sessions:
        row = dict(source)
        if row.get("session_state") not in _OPEN_SESSION_STATES:
            continue
        opened = _tpe_timestamp(row.get("scheduled_open_at"), "scheduled_open_at")
        if opened > cutoff:
            candidates.append((opened, str(row.get("session_id") or ""), row))
    return min(candidates, default=None, key=lambda item: (item[0], item[1]))[2] if candidates else None


def _run_identity(
    *,
    calendar_revision: str,
    target_trade_date: str,
    slot_key: str,
    scheduled_for: str,
) -> tuple[str, str]:
    identity = {
        "calendar_revision": calendar_revision,
        "contract": SCHEDULE_CONTRACT_VERSION,
        "scheduled_for": scheduled_for,
        "slot_key": slot_key,
        "target_trade_date": target_trade_date,
    }
    digest = _digest(identity)
    return f"news-run:{digest}", f"{SCHEDULE_CONTRACT_VERSION}:{digest}"


def _declaration(
    *,
    calendar_revision: str,
    source_policy_version: str,
    target_trade_date: str,
    slot_key: str,
    scheduled: datetime,
    cutoff: datetime,
    observed: datetime,
    status: str,
    late_reason: str | None,
    catch_up: bool,
    source_failures: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    scheduled_text = _iso(scheduled)
    run_id, idempotency_key = _run_identity(
        calendar_revision=calendar_revision,
        target_trade_date=target_trade_date,
        slot_key=slot_key,
        scheduled_for=scheduled_text,
    )
    terminal = status in {"skipped", "late", "failed", "partial", "success"}
    observed_text = _iso(observed)
    return {
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "slot_key": slot_key,
        "target_trade_date": target_trade_date,
        "scheduled_for": scheduled_text,
        "cutoff_at": _iso(cutoff),
        "started_at": None,
        "completed_at": observed_text if terminal else None,
        "status": status,
        "source_policy_version": source_policy_version,
        "calendar_revision": calendar_revision,
        "source_coverage": {},
        "source_failures": list(source_failures or []),
        "late_reason": late_reason,
        "created_at": observed_text,
        "updated_at": observed_text,
        "_catch_up": bool(catch_up),
    }


def scheduler_schema_ready(conn: sqlite3.Connection) -> bool:
    """Check required source tables without creating or altering any schema."""

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    return REQUIRED_SCHEDULER_TABLES <= tables


def build_due_run_declarations(
    calendar: Mapping[str, Any],
    *,
    observed_at: str,
    source_policy_version: str,
    existing_idempotency_keys: Iterable[str] = (),
    fixed_audit_lookback_hours: int = 72,
    fixed_catch_up_hours: int = 36,
    sentinel_audit_lookback_hours: int = 24,
) -> list[dict[str, Any]]:
    """Plan fixed slots and the 24-hour sentinel without writing or backdating data."""

    observed = _tpe_timestamp(observed_at, "observed_at")
    revision_id = str(calendar.get("calendar_revision") or "").strip()
    if not revision_id:
        raise ValueError("calendar_revision is required")
    available = _tpe_timestamp(calendar.get("revision_available_at"), "revision_available_at")
    sealed = _tpe_timestamp(calendar.get("sealed_at"), "calendar.sealed_at")
    if available > observed or sealed > observed:
        raise ValueError("calendar revision was not visible at the scheduler cutoff")
    source_policy = str(source_policy_version or "").strip()
    if not source_policy:
        raise ValueError("source_policy_version is required")
    if fixed_audit_lookback_hours < fixed_catch_up_hours or fixed_catch_up_hours < 1:
        raise ValueError("fixed catch-up limits are invalid")
    if sentinel_audit_lookback_hours < 1 or sentinel_audit_lookback_hours > 168:
        raise ValueError("sentinel audit lookback must be between 1 and 168 hours")
    sessions = [dict(item) for item in calendar.get("sessions") or []]
    if not sessions:
        raise ValueError("calendar revision has no sessions")
    existing = {str(value) for value in existing_idempotency_keys}
    declarations: list[dict[str, Any]] = []

    fixed_start = observed - timedelta(hours=fixed_audit_lookback_hours)
    for session in sessions:
        if session.get("session_state") not in _OPEN_SESSION_STATES:
            continue
        trade_day = date.fromisoformat(str(session["trade_date"]))
        for slot_key, day_offset, slot_time, deadline_time in _FIXED_SLOT_SPECS:
            scheduled_day = trade_day + timedelta(days=day_offset)
            scheduled = _combine(scheduled_day, slot_time)
            if scheduled > observed or scheduled < fixed_start:
                continue
            if day_offset == 0:
                planned_deadline = _combine(trade_day, deadline_time)
            else:
                planned_deadline = _combine(scheduled_day, deadline_time)
            if observed <= planned_deadline:
                status = "queued"
                cutoff = planned_deadline
                late_reason = None
                catch_up = False
                failures: list[dict[str, str]] = []
            elif observed - scheduled <= timedelta(hours=fixed_catch_up_hours):
                status = "queued"
                cutoff = observed + timedelta(minutes=15)
                late_reason = "boot_wake_catch_up_point_in_time_only"
                catch_up = True
                failures = []
            else:
                status = "skipped"
                cutoff = observed
                late_reason = "catch_up_window_elapsed_no_historical_cutoff_backfill"
                catch_up = True
                failures = [
                    {
                        "failure_class": "scheduler_late",
                        "failure_code": "fixed_slot_catch_up_window_elapsed",
                        "source": "scheduler",
                    }
                ]
            declaration = _declaration(
                calendar_revision=revision_id,
                source_policy_version=source_policy,
                target_trade_date=str(session["trade_date"]),
                slot_key=slot_key,
                scheduled=scheduled,
                cutoff=cutoff,
                observed=observed,
                status=status,
                late_reason=late_reason,
                catch_up=catch_up,
                source_failures=failures,
            )
            if declaration["idempotency_key"] not in existing:
                declarations.append(declaration)

    current_quarter = _floor_quarter(observed)
    sentinel_start = _floor_quarter(
        observed - timedelta(hours=sentinel_audit_lookback_hours)
    )
    cursor = sentinel_start
    while cursor <= current_quarter:
        current = cursor == current_quarter
        if current:
            cutoff = cursor + timedelta(minutes=15)
            status = "queued"
            late_reason = None
            catch_up = False
            failures = []
        else:
            cutoff = observed
            status = "skipped"
            late_reason = "sentinel_interval_missed_no_historical_cutoff_backfill"
            catch_up = True
            failures = [
                {
                    "failure_class": "scheduler_late",
                    "failure_code": "sentinel_interval_missed",
                    "source": "scheduler",
                }
            ]
        target_session = _next_open_session(sessions, cutoff)
        if target_session is None:
            raise ValueError("calendar revision has no future open session for sentinel target")
        declaration = _declaration(
            calendar_revision=revision_id,
            source_policy_version=source_policy,
            target_trade_date=str(target_session["trade_date"]),
            slot_key="high_signal_sentinel_15m",
            scheduled=cursor,
            cutoff=cutoff,
            observed=observed,
            status=status,
            late_reason=late_reason,
            catch_up=catch_up,
            source_failures=failures,
        )
        if declaration["idempotency_key"] not in existing:
            declarations.append(declaration)
        cursor += timedelta(minutes=15)

    return sorted(
        declarations,
        key=lambda item: (item["scheduled_for"], item["slot_key"], item["run_id"]),
    )


def scheduler_tick_key(
    *,
    observed_at: str,
    calendar_revision: str,
    source_policy_version: str,
) -> str:
    return _digest(
        {
            "calendar_revision": str(calendar_revision),
            "contract": SCHEDULE_CONTRACT_VERSION,
            "observed_at": _iso(_tpe_timestamp(observed_at, "observed_at")),
            "source_policy_version": str(source_policy_version),
        }
    )


def run_scheduler_tick(
    conn: sqlite3.Connection,
    *,
    observed_at: str,
    source_policy_version: str,
    calendar_revision_value: str | None = None,
) -> dict[str, Any]:
    """Materialize one idempotent tick; caller controls the DB transaction/commit."""

    if not scheduler_schema_ready(conn):
        raise RuntimeError("Single-Track scheduler schema is not initialized")
    observed = _tpe_timestamp(observed_at, "observed_at")
    if calendar_revision_value:
        calendar = calendar_revision(conn, str(calendar_revision_value))
        if calendar is None:
            raise ValueError("requested calendar revision does not exist")
    else:
        calendar = latest_calendar_revision_at(conn, visible_at=_iso(observed))
        if calendar is None:
            raise RuntimeError("no sealed calendar revision is visible at this tick")
    tick_key_value = scheduler_tick_key(
        observed_at=_iso(observed),
        calendar_revision=str(calendar["calendar_revision"]),
        source_policy_version=source_policy_version,
    )
    existing_tick = scheduler_tick(conn, tick_key_value)
    if existing_tick is not None:
        result = dict(existing_tick)
        result["replayed"] = True
        return result
    window_start = _iso(observed - timedelta(hours=72))
    existing_runs = news_retrieval_runs_between(
        conn,
        scheduled_from=window_start,
        scheduled_to=_iso(observed),
    )
    declarations = build_due_run_declarations(
        calendar,
        observed_at=_iso(observed),
        source_policy_version=source_policy_version,
        existing_idempotency_keys=(item["idempotency_key"] for item in existing_runs),
    )
    run_ids = [str(item["run_id"]) for item in declarations]
    catch_up_ids = [str(item["run_id"]) for item in declarations if item["_catch_up"]]
    skipped_ids = [str(item["run_id"]) for item in declarations if item["status"] == "skipped"]
    digest_payload = {
        "calendar_revision": calendar["calendar_revision"],
        "contract": SCHEDULE_CONTRACT_VERSION,
        "declarations": [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in declarations
        ],
        "observed_at": _iso(observed),
        "source_policy_version": source_policy_version,
    }
    tick_digest = _digest(digest_payload)
    savepoint = "single_track_scheduler_tick"
    conn.execute(f'SAVEPOINT "{savepoint}"')
    try:
        for declaration in declarations:
            create_news_retrieval_run(
                conn,
                {key: value for key, value in declaration.items() if not key.startswith("_")},
                ensure_schema=False,
            )
        saved = seal_scheduler_tick(
            conn,
            {
                "tick_id": f"scheduler-tick:{tick_key_value}",
                "tick_key": tick_key_value,
                "observed_at": _iso(observed),
                "calendar_revision": calendar["calendar_revision"],
                "source_policy_version": source_policy_version,
                "schedule_contract_version": SCHEDULE_CONTRACT_VERSION,
                "planned_run_ids": run_ids,
                "materialized_run_ids": run_ids,
                "catch_up_run_ids": catch_up_ids,
                "skipped_run_ids": skipped_ids,
                "tick_digest": tick_digest,
                "created_at": _iso(observed),
            },
            ensure_schema=False,
        )
    except Exception:
        conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
        conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        raise
    conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
    saved["replayed"] = False
    return saved


def process_zero_gpu_artifacts(
    conn: sqlite3.Connection,
    *,
    observed_at: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Process bounded DB-only outbox work without invoking adapters or model services."""

    if not scheduler_schema_ready(conn):
        raise RuntimeError("Single-Track scheduler schema is not initialized")
    observed_text = _iso(_tpe_timestamp(observed_at, "observed_at"))
    pending = pending_zero_gpu_outbox_items_at(
        conn,
        available_at=observed_text,
        limit=limit,
    )
    receipts = []
    failures = []
    for item in pending:
        try:
            receipt = produce_zero_gpu_outbox_artifact(
                conn,
                str(item["outbox_id"]),
                produced_at=observed_text,
                ensure_schema=False,
            )
            receipts.append(str(receipt["receipt_id"]))
        except (RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            failures.append(
                {
                    "error_class": type(exc).__name__,
                    "outbox_id": str(item["outbox_id"]),
                    "reason_code": "zero_gpu_artifact_production_failed",
                }
            )
    return {
        "attempted": len(pending),
        "failed": failures,
        "produced_receipt_ids": receipts,
        "zero_gpu_model_calls": 0,
    }


def scheduler_preflight(
    conn: sqlite3.Connection,
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Return a read-only readiness report; runtime retrieval remains fail closed."""

    observed_text = _iso(_tpe_timestamp(observed_at, "observed_at"))
    schema_ready = scheduler_schema_ready(conn)
    calendar = (
        latest_calendar_revision_at(conn, visible_at=observed_text)
        if schema_ready
        else None
    )
    next_session = None
    if calendar is not None:
        next_session = next_open_session_after(
            conn,
            calendar_revision_value=str(calendar["calendar_revision"]),
            cutoff_at=observed_text,
        )
    retrieval_worker_ready = schema_ready and retrieval_worker_schema_ready(conn)
    reason_codes = []
    if not schema_ready:
        reason_codes.append("single_track_v3_schema_not_initialized")
    if calendar is None:
        reason_codes.append("sealed_calendar_revision_unavailable")
    elif next_session is None:
        reason_codes.append("future_calendar_session_unavailable")
    if not retrieval_worker_ready:
        reason_codes.append("durable_retrieval_worker_schema_not_initialized")
    return {
        "contract_version": "SingleTrackV3SchedulerPreflightV1",
        "observed_at": observed_text,
        "schema_ready": schema_ready,
        "calendar_ready": calendar is not None and next_session is not None,
        "calendar_revision": calendar.get("calendar_revision") if calendar else None,
        "next_trade_date": next_session.get("trade_date") if next_session else None,
        "runner_source_ready": True,
        "zero_gpu_producer_ready": schema_ready,
        "retrieval_worker_ready": retrieval_worker_ready,
        "install_ready": not reason_codes,
        "reason_codes": reason_codes,
        "production_writes": 0,
    }
