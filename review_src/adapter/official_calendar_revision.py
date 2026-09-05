from __future__ import annotations

"""Fail-closed normalization for an official exact TWSE session-set source."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


TPE = ZoneInfo("Asia/Taipei")
OFFICIAL_SESSION_SOURCE_CONTRACT_VERSION = "OfficialTWSEExactSessionSourceV1"
OFFICIAL_SESSION_NORMALIZER_VERSION = "OfficialTWSESessionNormalizerV1"
_SESSION_STATES = {
    "scheduled",
    "cancelled",
    "delayed",
    "special_session",
    "early_close",
}
_OPEN_SESSION_STATES = _SESSION_STATES - {"cancelled"}


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
        raise ValueError("official calendar payload must be finite JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _required_digest(value: Any, field: str) -> str:
    text = _required_text(value, field).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _tpe_timestamp(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
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


def normalize_official_calendar_revision_source(
    payload: Mapping[str, Any],
    *,
    sealed_at: str,
) -> dict[str, Any]:
    """Normalize only explicit official sessions; holiday-only inputs are rejected."""

    if not isinstance(payload, Mapping):
        raise ValueError("official calendar source payload must be an object")
    source = dict(payload)
    if source.get("contract_version") != OFFICIAL_SESSION_SOURCE_CONTRACT_VERSION:
        raise ValueError("official calendar source contract_version is unsupported")
    if source.get("source_type") != "official_exact_session_schedule":
        raise ValueError("official calendar source must provide an exact session schedule")
    if source.get("authority_tier") != "canonical_official":
        raise ValueError("official calendar source must be canonical_official")
    if source.get("timezone") != "Asia/Taipei":
        raise ValueError("official calendar source timezone must be Asia/Taipei")
    source_id = _required_text(source.get("source_id"), "source_id")
    source_url = _required_text(source.get("source_url"), "source_url")
    parsed_url = urlsplit(source_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise ValueError("official calendar source_url must be an absolute HTTPS URL")
    source_digest = _required_digest(
        source.get("source_document_digest"), "source_document_digest"
    )
    published = _tpe_timestamp(source.get("published_at"), "published_at")
    available = _tpe_timestamp(source.get("available_at"), "available_at")
    sealed = _tpe_timestamp(sealed_at, "sealed_at")
    if available < published:
        raise ValueError("official calendar cannot be available before publication")
    if sealed < available:
        raise ValueError("official calendar cannot be sealed before availability")
    session_policy_version = _required_text(
        source.get("session_policy_version"), "session_policy_version"
    )
    raw_sessions = source.get("sessions")
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise ValueError(
            "official exact session set is required; holiday/closure dates alone are insufficient"
        )

    sessions: list[dict[str, Any]] = []
    for index, raw_session in enumerate(raw_sessions):
        if not isinstance(raw_session, Mapping):
            raise ValueError(f"sessions[{index}] must be an object")
        item = dict(raw_session)
        trade_date = _required_text(item.get("trade_date"), f"sessions[{index}].trade_date")
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"sessions[{index}].trade_date must be YYYY-MM-DD") from exc
        state = _required_text(item.get("session_state"), f"sessions[{index}].session_state")
        if state not in _SESSION_STATES:
            raise ValueError(f"sessions[{index}].session_state is invalid")
        if item.get("official_state_explicit") is not True:
            raise ValueError(f"sessions[{index}] lacks explicit official state evidence")
        cancellation_reason = str(item.get("cancellation_reason") or "").strip() or None
        if state == "cancelled":
            if item.get("scheduled_open_at") is not None or item.get("scheduled_close_at") is not None:
                raise ValueError(f"sessions[{index}] cancelled state cannot contain open/close")
            if cancellation_reason is None:
                raise ValueError(f"sessions[{index}] cancelled state requires a reason")
            open_text = close_text = None
        else:
            if item.get("official_open_close_explicit") is not True:
                raise ValueError(f"sessions[{index}] lacks explicit official open/close evidence")
            opened = _tpe_timestamp(
                item.get("scheduled_open_at"), f"sessions[{index}].scheduled_open_at"
            )
            closed = _tpe_timestamp(
                item.get("scheduled_close_at"), f"sessions[{index}].scheduled_close_at"
            )
            if opened.date().isoformat() != trade_date or closed.date().isoformat() != trade_date:
                raise ValueError(f"sessions[{index}] timestamps must fall on trade_date")
            if closed <= opened:
                raise ValueError(f"sessions[{index}] close must be after open")
            if cancellation_reason is not None:
                raise ValueError(f"sessions[{index}] open state cannot contain cancellation_reason")
            open_text, close_text = _iso(opened), _iso(closed)
        sessions.append(
            {
                "trade_date": trade_date,
                "session_state": state,
                "scheduled_open_at": open_text,
                "scheduled_close_at": close_text,
                "cancellation_reason": cancellation_reason,
                "source_evidence_digest": _required_digest(
                    item.get("source_evidence_digest"),
                    f"sessions[{index}].source_evidence_digest",
                ),
                "created_at": _iso(sealed),
            }
        )
    sessions.sort(key=lambda item: item["trade_date"])
    trade_dates = [item["trade_date"] for item in sessions]
    if len(trade_dates) != len(set(trade_dates)):
        raise ValueError("official calendar exact session set contains duplicate trade dates")
    if not any(item["session_state"] in _OPEN_SESSION_STATES for item in sessions):
        raise ValueError("official calendar exact session set has no open session")

    revision_identity = {
        "available_at": _iso(available),
        "contract_version": OFFICIAL_SESSION_SOURCE_CONTRACT_VERSION,
        "published_at": _iso(published),
        "session_policy_version": session_policy_version,
        "sessions": [
            {
                key: item[key]
                for key in (
                    "trade_date",
                    "session_state",
                    "scheduled_open_at",
                    "scheduled_close_at",
                    "cancellation_reason",
                    "source_evidence_digest",
                )
            }
            for item in sessions
        ],
        "source_digest": source_digest,
        "source_id": source_id,
        "source_url": source_url,
    }
    revision_key = _digest(revision_identity)
    supplied_revision = str(source.get("calendar_revision") or "").strip()
    calendar_revision = supplied_revision or f"official-calendar:{revision_key}"
    return {
        "normalizer_version": OFFICIAL_SESSION_NORMALIZER_VERSION,
        "revision": {
            "calendar_revision": calendar_revision,
            "source_id": source_id,
            "source_url": source_url,
            "source_digest": source_digest,
            "session_policy_version": session_policy_version,
            "revision_published_at": _iso(published),
            "revision_available_at": _iso(available),
            "timezone": "Asia/Taipei",
            "sealed_at": _iso(sealed),
            "created_at": _iso(sealed),
        },
        "sessions": sessions,
        "source_contract_digest": revision_key,
        "source_assertions": {
            "exact_session_set": True,
            "heuristic_regular_hours_used": False,
            "holiday_only_source_used": False,
            "official_state_required_per_session": True,
            "official_open_close_required_per_open_session": True,
        },
    }
