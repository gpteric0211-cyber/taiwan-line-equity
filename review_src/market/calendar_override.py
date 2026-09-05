"""Shadow-only in-memory market calendar override prototype.

Overrides live only inside the current Python process.  They are not
written to SQLite and are not shared between CLI scripts, web workers, or
multiple uvicorn processes.  This limitation is intentional for Phase
Calendar-C.
"""

from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Any

from core.outlook_context import MarketCalendarOverride, ensure_taipei_aware
from market.calendar_data import normalize_calendar_date

SUPPORTED_OVERRIDE_TYPES = {"closed", "open", "special_closed", "typhoon_closed"}
_OVERRIDES: dict[tuple[str, str], MarketCalendarOverride] = {}
_LOCK = RLock()


def _market_key(market: str) -> str:
    return str(market or "").strip().upper()


def _normalize_expires_at(expires_at: datetime | str | None) -> datetime:
    if expires_at is None:
        raise ValueError("expires_at is required for calendar override")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if not isinstance(expires_at, datetime):
        raise TypeError(f"Unsupported expires_at type: {type(expires_at)!r}")
    return ensure_taipei_aware(expires_at)


def apply_override(
    market: str,
    value: Any,
    override_type: str,
    reason: str,
    expires_at: datetime | str | None,
    source: str = "manual_override",
    created_by: str = "manual",
) -> MarketCalendarOverride:
    """Apply a process-local override and return the stored contract."""

    market_key = _market_key(market)
    normalized_type = str(override_type or "").strip().lower()
    if normalized_type not in SUPPORTED_OVERRIDE_TYPES:
        raise ValueError(f"Unsupported override_type: {override_type!r}")
    if not str(reason or "").strip():
        raise ValueError("reason is required for calendar override")
    day = normalize_calendar_date(value, market_key).isoformat()
    override = MarketCalendarOverride(
        market=market_key,
        date=day,
        override_type=normalized_type,
        reason=str(reason),
        source=str(source or "manual_override"),
        created_at=ensure_taipei_aware(),
        created_by=str(created_by or "manual"),
        expires_at=_normalize_expires_at(expires_at),
    )
    with _LOCK:
        _OVERRIDES[(market_key, day)] = override
    return override


def get_active_override(
    market: str,
    value: Any,
    as_of_time: datetime | str | None = None,
) -> MarketCalendarOverride | None:
    """Return an active override, or None if missing/expired."""

    market_key = _market_key(market)
    day = normalize_calendar_date(value, market_key).isoformat()
    asof = ensure_taipei_aware(datetime.fromisoformat(as_of_time) if isinstance(as_of_time, str) else as_of_time)
    with _LOCK:
        override = _OVERRIDES.get((market_key, day))
        if override is None:
            return None
        if ensure_taipei_aware(override.expires_at) <= asof:
            _OVERRIDES.pop((market_key, day), None)
            return None
        return override


def clear_expired_overrides(as_of_time: datetime | str | None = None) -> int:
    """Remove expired overrides and return the number removed."""

    asof = ensure_taipei_aware(datetime.fromisoformat(as_of_time) if isinstance(as_of_time, str) else as_of_time)
    removed = 0
    with _LOCK:
        for key, override in list(_OVERRIDES.items()):
            if ensure_taipei_aware(override.expires_at) <= asof:
                _OVERRIDES.pop(key, None)
                removed += 1
    return removed


def list_overrides() -> list[dict[str, Any]]:
    """Return all in-memory overrides for debug/export use only."""

    with _LOCK:
        return [override.to_dict() for override in _OVERRIDES.values()]
