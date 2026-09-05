from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from core.single_track_v3_schema import ensure_single_track_v3_schema


TPE = ZoneInfo("Asia/Taipei")
_OPEN_SESSION_STATES = {"scheduled", "delayed", "special_session", "early_close"}
_SESSION_STATES = {*_OPEN_SESSION_STATES, "cancelled"}


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
        raise ValueError("calendar payload must be finite JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _require_digest(value: Any, field: str) -> str:
    text = _required_text(value, field).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return text


def _tpe_timestamp(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    local = parsed.astimezone(TPE)
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError(f"{field} must use the Asia/Taipei UTC+08:00 offset")
    return local


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


def _normalize_session(
    source: Mapping[str, Any],
    *,
    calendar_revision_value: str,
    default_created_at: str,
) -> dict[str, Any]:
    row = dict(source)
    trade_date = _required_text(row.get("trade_date"), "session.trade_date")
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("session.trade_date must be YYYY-MM-DD") from exc
    state = _required_text(row.get("session_state"), "session.session_state")
    if state not in _SESSION_STATES:
        raise ValueError("session.session_state is invalid")
    cancellation_reason = str(row.get("cancellation_reason") or "").strip() or None
    if state == "cancelled":
        if row.get("scheduled_open_at") is not None or row.get("scheduled_close_at") is not None:
            raise ValueError("cancelled calendar session must not contain open/close timestamps")
        if cancellation_reason is None:
            raise ValueError("cancelled calendar session requires cancellation_reason")
        open_at = close_at = None
    else:
        open_text = _required_text(row.get("scheduled_open_at"), "session.scheduled_open_at")
        close_text = _required_text(row.get("scheduled_close_at"), "session.scheduled_close_at")
        open_value = _tpe_timestamp(open_text, "session.scheduled_open_at")
        close_value = _tpe_timestamp(close_text, "session.scheduled_close_at")
        if open_value.date().isoformat() != trade_date:
            raise ValueError("session open date must equal trade_date")
        if close_value <= open_value:
            raise ValueError("session close must be later than its open")
        if cancellation_reason is not None:
            raise ValueError("open calendar session cannot contain cancellation_reason")
        open_at, close_at = open_text, close_text
    session_id = f"calendar-session:{_digest({'calendar_revision': calendar_revision_value, 'trade_date': trade_date})}"
    return {
        "session_id": session_id,
        "calendar_revision": calendar_revision_value,
        "trade_date": trade_date,
        "session_state": state,
        "scheduled_open_at": open_at,
        "scheduled_close_at": close_at,
        "cancellation_reason": cancellation_reason,
        "source_evidence_digest": _require_digest(
            row.get("source_evidence_digest"), "session.source_evidence_digest"
        ),
        "created_at": _required_text(
            row.get("created_at") or default_created_at,
            "session.created_at",
        ),
    }


def calendar_revision(
    conn: sqlite3.Connection,
    calendar_revision_value: str,
) -> dict[str, Any] | None:
    """Read an immutable calendar revision without DDL or database writes."""

    row = _select_one(
        conn,
        "SELECT * FROM single_track_v3_calendar_revision WHERE calendar_revision=?",
        (str(calendar_revision_value),),
    )
    if row is None:
        return None
    row["sessions"] = calendar_sessions(conn, str(calendar_revision_value))
    return row


def calendar_sessions(
    conn: sqlite3.Connection,
    calendar_revision_value: str,
) -> list[dict[str, Any]]:
    """Read all sessions in one frozen revision without side effects."""

    return _select_rows(
        conn,
        """
        SELECT * FROM single_track_v3_calendar_session
        WHERE calendar_revision=?
        ORDER BY trade_date,session_id
        """,
        (str(calendar_revision_value),),
    )


def latest_calendar_revision_at(
    conn: sqlite3.Connection,
    *,
    visible_at: str,
) -> dict[str, Any] | None:
    """Select the newest revision that was both available and sealed by a PIT cutoff."""

    cutoff = _tpe_timestamp(visible_at, "visible_at")
    visible = []
    for row in _select_rows(
        conn,
        """
        SELECT * FROM single_track_v3_calendar_revision
        ORDER BY revision_available_at DESC,sealed_at DESC,calendar_revision DESC
        """,
    ):
        if (
            _tpe_timestamp(row["revision_available_at"], "revision_available_at") <= cutoff
            and _tpe_timestamp(row["sealed_at"], "sealed_at") <= cutoff
        ):
            visible.append(row)
    if not visible:
        return None
    selected = visible[0]
    selected["sessions"] = calendar_sessions(conn, selected["calendar_revision"])
    return selected


def next_open_session_after(
    conn: sqlite3.Connection,
    *,
    calendar_revision_value: str,
    cutoff_at: str,
) -> dict[str, Any] | None:
    """Return the first formal session whose scheduled open is after the cutoff."""

    cutoff = _tpe_timestamp(cutoff_at, "cutoff_at")
    candidates = []
    for row in calendar_sessions(conn, calendar_revision_value):
        if row["session_state"] not in _OPEN_SESSION_STATES:
            continue
        open_at = _tpe_timestamp(row["scheduled_open_at"], "scheduled_open_at")
        if open_at > cutoff:
            candidates.append((open_at, row))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]["session_id"]))[1]


def seal_calendar_revision(
    conn: sqlite3.Connection,
    revision: Mapping[str, Any],
    sessions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Atomically seal a versioned official-calendar revision and exact session set."""

    ensure_single_track_v3_schema(conn)
    row = dict(revision)
    revision_id = _required_text(row.get("calendar_revision"), "calendar_revision")
    published_text = _required_text(
        row.get("revision_published_at"), "revision_published_at"
    )
    available_text = _required_text(
        row.get("revision_available_at"), "revision_available_at"
    )
    sealed_text = _required_text(row.get("sealed_at"), "sealed_at")
    created_text = _required_text(row.get("created_at"), "created_at")
    published = _tpe_timestamp(published_text, "revision_published_at")
    available = _tpe_timestamp(available_text, "revision_available_at")
    sealed = _tpe_timestamp(sealed_text, "sealed_at")
    _tpe_timestamp(created_text, "created_at")
    if available < published:
        raise ValueError("calendar revision cannot be available before publication")
    if sealed < available:
        raise ValueError("calendar revision cannot be sealed before availability")
    timezone_name = _required_text(row.get("timezone") or "Asia/Taipei", "timezone")
    if timezone_name != "Asia/Taipei":
        raise ValueError("calendar timezone must be Asia/Taipei")
    normalized_sessions = sorted(
        (
            _normalize_session(
                item,
                calendar_revision_value=revision_id,
                default_created_at=created_text,
            )
            for item in sessions
        ),
        key=lambda item: (item["trade_date"], item["session_id"]),
    )
    if not normalized_sessions or not any(
        item["session_state"] in _OPEN_SESSION_STATES for item in normalized_sessions
    ):
        raise ValueError("calendar revision requires at least one formal open session")
    trade_dates = [item["trade_date"] for item in normalized_sessions]
    if len(set(trade_dates)) != len(trade_dates):
        raise ValueError("calendar revision contains duplicate trade dates")
    digest_payload = {
        "contract": "SingleTrackV3CalendarRevisionV1",
        "calendar_revision": revision_id,
        "source_id": _required_text(row.get("source_id"), "source_id"),
        "source_url": _required_text(row.get("source_url"), "source_url"),
        "source_digest": _require_digest(row.get("source_digest"), "source_digest"),
        "session_policy_version": _required_text(
            row.get("session_policy_version"), "session_policy_version"
        ),
        "revision_published_at": published_text,
        "revision_available_at": available_text,
        "timezone": timezone_name,
        "sealed_at": sealed_text,
        "sessions": [
            {
                key: session[key]
                for key in (
                    "session_id",
                    "trade_date",
                    "session_state",
                    "scheduled_open_at",
                    "scheduled_close_at",
                    "cancellation_reason",
                    "source_evidence_digest",
                )
            }
            for session in normalized_sessions
        ],
    }
    revision_digest = _digest(digest_payload)
    supplied_digest = row.get("revision_digest")
    if supplied_digest is not None and _require_digest(
        supplied_digest, "revision_digest"
    ) != revision_digest:
        raise ValueError("calendar revision digest does not match its exact session set")
    values = {
        "calendar_revision": revision_id,
        "source_id": digest_payload["source_id"],
        "source_url": digest_payload["source_url"],
        "source_digest": digest_payload["source_digest"],
        "session_policy_version": digest_payload["session_policy_version"],
        "revision_published_at": published_text,
        "revision_available_at": available_text,
        "timezone": timezone_name,
        "revision_digest": revision_digest,
        "sealed_at": sealed_text,
        "created_at": created_text,
    }
    savepoint = "single_track_calendar_revision_seal"
    conn.execute(f'SAVEPOINT "{savepoint}"')
    try:
        columns = tuple(values)
        conn.execute(
            f"INSERT INTO single_track_v3_calendar_revision({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)}) "
            "ON CONFLICT(calendar_revision) DO NOTHING",
            values,
        )
        session_columns = tuple(normalized_sessions[0])
        conn.executemany(
            f"INSERT INTO single_track_v3_calendar_session({','.join(session_columns)}) "
            f"VALUES({','.join(':' + column for column in session_columns)}) "
            "ON CONFLICT(session_id) DO NOTHING",
            normalized_sessions,
        )
        existing = _select_one(
            conn,
            "SELECT * FROM single_track_v3_calendar_revision WHERE calendar_revision=?",
            (revision_id,),
        )
        existing_sessions = calendar_sessions(conn, revision_id)
        if existing is None or any(existing.get(column) != value for column, value in values.items()):
            raise ValueError("calendar revision identity conflicts with sealed content")
        if existing_sessions != normalized_sessions:
            raise ValueError("calendar revision session set conflicts with sealed content")
    except Exception:
        conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
        conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        raise
    conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
    saved = calendar_revision(conn, revision_id)
    if saved is None:
        raise RuntimeError("calendar revision could not be read back")
    return saved
