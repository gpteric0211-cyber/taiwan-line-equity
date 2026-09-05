"""Shadow-only market calendar data prototype.

This module is intentionally read-only and side-effect free.  It does
not fetch official market calendars, does not write SQLite, and does not
pretend to be a complete TWSE / TAIFEX / US calendar source.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

try:
    from core.config import TAIWAN_MARKET_HOLIDAYS
except Exception:
    TAIWAN_MARKET_HOLIDAYS: set[str] = set()

from core.outlook_context import TAIPEI_TZ, ensure_taipei_aware, zoneinfo_or_fixed

US_EASTERN = zoneinfo_or_fixed("America/New_York", -5)
SUPPORTED_MARKETS = {"TWSE", "US", "TAIFEX"}


def _market_key(market: str) -> str:
    return str(market or "").strip().upper()


def normalize_calendar_date(value: Any, market: str | None = None) -> date:
    """Normalize str/date/datetime inputs into a date for the requested market."""

    market_key = _market_key(market)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TAIPEI_TZ)
        if market_key == "US":
            return value.astimezone(US_EASTERN).date()
        return value.astimezone(TAIPEI_TZ).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("calendar date is empty")
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            return date.fromisoformat(text[:10])
    raise TypeError(f"Unsupported calendar date type: {type(value)!r}")


def is_holiday(market: str, value: Any) -> bool:
    """Return whether the date is a known static holiday in this shadow prototype."""

    market_key = _market_key(market)
    day = normalize_calendar_date(value, market_key)
    if market_key == "TWSE":
        return day.isoformat() in TAIWAN_MARKET_HOLIDAYS
    if market_key == "TAIFEX":
        # Shadow prototype: TAIFEX follows TWSE holiday list for now.
        return day.isoformat() in TAIWAN_MARKET_HOLIDAYS
    if market_key == "US":
        # US official holiday calendar is intentionally not implemented yet.
        return False
    return False


def is_trading_day(market: str, value: Any) -> bool:
    """Return weekday/static-list trading-day heuristic for the market."""

    market_key = _market_key(market)
    day = normalize_calendar_date(value, market_key)
    if market_key not in SUPPORTED_MARKETS:
        return False
    return day.weekday() < 5 and not is_holiday(market_key, day)


def get_calendar_source(market: str, value: Any) -> str:
    """Return the source label used by this shadow calendar decision."""

    market_key = _market_key(market)
    day = normalize_calendar_date(value, market_key)
    if market_key in {"TWSE", "TAIFEX"} and day.isoformat() in TAIWAN_MARKET_HOLIDAYS:
        return "static_holiday_list"
    return "weekday_heuristic"


def get_calendar_reason(market: str, value: Any) -> str | None:
    """Return a human/debug reason for the current shadow calendar decision."""

    market_key = _market_key(market)
    day = normalize_calendar_date(value, market_key)
    if market_key not in SUPPORTED_MARKETS:
        return "unknown_market"
    if is_holiday(market_key, day):
        return "static_holiday_list"
    if day.weekday() >= 5:
        return "weekend"
    if market_key == "US":
        return "US official holiday calendar not implemented; weekday heuristic"
    if market_key == "TAIFEX":
        return "TAIFEX official calendar not implemented; TWSE heuristic"
    return "weekday_heuristic"
