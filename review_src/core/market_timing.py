from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.market_calendar_cache import taiwan_market_day_status


TPE = ZoneInfo("Asia/Taipei")
TAIWAN_OPEN = time(9, 0)
TAIWAN_CLOSE = time(13, 30)


def parse_market_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TPE)
    return parsed.astimezone(TPE)


def next_taiwan_trading_date(value: str | date, *, strictly_after: bool = True) -> str:
    day = date.fromisoformat(str(value)[:10]) if not isinstance(value, date) else value
    if strictly_after:
        day += timedelta(days=1)
    for _ in range(370):
        if taiwan_market_day_status(day).get("is_trading_day"):
            return day.isoformat()
        day += timedelta(days=1)
    raise ValueError("unable to resolve the next Taiwan trading date")


def availability_contract(
    *,
    fetched_at: Any,
    published_at: Any = None,
) -> dict[str, str | None]:
    """Build conservative point-in-time fields for an imported observation.

    The system cannot use an item before it was fetched, even when its source
    publication time is earlier.  Therefore ``available_at`` is based on the
    local fetch timestamp whenever that timestamp is valid.
    """

    available = parse_market_timestamp(fetched_at) or parse_market_timestamp(published_at)
    if available is None:
        return {
            "available_at": None,
            "market_session": "unknown",
            "effective_tw_trade_date": None,
        }
    day_status = taiwan_market_day_status(available.date())
    is_trading_day = bool(day_status.get("is_trading_day"))
    local_time = available.timetz().replace(tzinfo=None)
    if not is_trading_day:
        session = "market_closed"
    elif local_time < TAIWAN_OPEN:
        session = "pre_market"
    elif local_time <= TAIWAN_CLOSE:
        session = "intraday"
    else:
        session = "post_market"

    if is_trading_day and local_time < TAIWAN_OPEN:
        effective_date = available.date().isoformat()
    else:
        effective_date = next_taiwan_trading_date(available.date(), strictly_after=True)
    return {
        "available_at": available.isoformat(timespec="seconds"),
        "market_session": session,
        "effective_tw_trade_date": effective_date,
    }


def analysis_cutoff_for_reference(
    reference_date: str | date,
    *,
    explicit_cutoff: Any = None,
    now: datetime | None = None,
) -> str:
    """Return an offset-aware cutoff, capped at the current local time."""

    explicit = parse_market_timestamp(explicit_cutoff)
    if explicit is not None:
        return explicit.isoformat(timespec="seconds")
    day = date.fromisoformat(str(reference_date)[:10])
    current = (now or datetime.now(TPE)).astimezone(TPE)
    end_of_day = datetime.combine(day, time(23, 59, 59), tzinfo=TPE)
    return min(current, end_of_day).isoformat(timespec="seconds")


def available_at_or_before_cutoff(available_at: Any, analysis_cutoff: Any) -> bool:
    available = parse_market_timestamp(available_at)
    cutoff = parse_market_timestamp(analysis_cutoff)
    return bool(available is not None and cutoff is not None and available <= cutoff)
