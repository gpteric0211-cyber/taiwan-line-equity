"""Shadow-only market calendar status helpers.

Calendar-C wires the Calendar-B status helper to the shadow
``calendar_data`` and process-local ``calendar_override`` prototypes.
It still does not import ``app.py``, write DB, or call official market
calendar APIs.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from core.outlook_context import (
    MarketCalendarOverride,
    MarketCalendarStatus,
    classify_closed_session_freshness,
    ensure_taipei_aware,
)
from market.calendar_data import (
    US_EASTERN,
    get_calendar_reason,
    get_calendar_source,
    is_holiday,
    is_trading_day,
    normalize_calendar_date,
)
from market.calendar_override import get_active_override


def _override_to_trading_day(override: MarketCalendarOverride) -> bool:
    return override.override_type == "open"


def _decision(market: str, value: Any, as_of_time: datetime) -> tuple[bool, str, str | None, MarketCalendarOverride | None]:
    market_key = str(market or "").upper()
    day = normalize_calendar_date(value, market_key)
    override = get_active_override(market_key, day, as_of_time=as_of_time)
    if override is not None:
        return _override_to_trading_day(override), "manual_override", override.reason, override
    return (
        is_trading_day(market_key, day),
        get_calendar_source(market_key, day),
        get_calendar_reason(market_key, day),
        None,
    )


def _twse_trading_day(day: date, as_of_time: datetime) -> bool:
    return _decision("TWSE", day, as_of_time)[0]


def _previous_twse_trading_day(day: date, as_of_time: datetime) -> str | None:
    cursor = day - timedelta(days=1)
    for _ in range(370):
        if _twse_trading_day(cursor, as_of_time):
            return cursor.isoformat()
        cursor -= timedelta(days=1)
    return None


def get_next_twse_trading_day_after(
    session_date: date | str | datetime,
    as_of_time: datetime | None = None,
) -> str | None:
    """Return next TWSE trading date using override > data > weekday heuristic."""

    asof = ensure_taipei_aware(as_of_time)
    cursor = normalize_calendar_date(session_date, "TWSE") + timedelta(days=1)
    for _ in range(370):
        if _twse_trading_day(cursor, asof):
            return cursor.isoformat()
        cursor += timedelta(days=1)
    return None


def _status_source_confidence(source: str) -> str:
    if source == "manual_override":
        return "override"
    if source == "static_holiday_list":
        return "static_prototype"
    return "heuristic"


def _override_kwargs(override: MarketCalendarOverride | None) -> dict[str, Any]:
    return {
        "override_active": override is not None,
        "override_reason": override.reason if override else None,
        "override_expires_at": override.expires_at if override else None,
    }


def _twse_status(as_of_time: datetime) -> MarketCalendarStatus:
    asof = ensure_taipei_aware(as_of_time)
    day = asof.date()
    minutes = asof.hour * 60 + asof.minute
    is_trading, source, reason, override = _decision("TWSE", day, asof)
    is_open = is_trading and (9 * 60 <= minutes <= 13 * 60 + 30)
    if override is not None and override.override_type in {"closed", "special_closed", "typhoon_closed"}:
        session = override.override_type
    elif not is_trading and is_holiday("TWSE", day):
        session = "holiday"
    elif not is_trading:
        session = "closed"
    elif is_open:
        session = "open"
    else:
        session = "closed"
    latest_complete = day.isoformat() if is_trading and minutes >= 15 * 60 + 30 else _previous_twse_trading_day(day, asof)
    return MarketCalendarStatus(
        market="TWSE",
        timezone="Asia/Taipei",
        as_of_time=asof,
        date=day.isoformat(),
        is_trading_day=is_trading,
        is_open=is_open,
        session=session,
        reason=reason,
        previous_trading_day=_previous_twse_trading_day(day, asof),
        next_trading_day=get_next_twse_trading_day_after(day, asof),
        latest_complete_trade_date=latest_complete,
        latest_available_time=asof if latest_complete else None,
        calendar_confidence=_status_source_confidence(source),
        source=source,
        **_override_kwargs(override),
    )


def _previous_us_trading_day(day: date, as_of_time: datetime) -> str | None:
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if _decision("US", cursor, as_of_time)[0]:
            return cursor.isoformat()
        cursor -= timedelta(days=1)
    return None


def _next_us_trading_day(day: date, as_of_time: datetime) -> str | None:
    cursor = day
    for _ in range(14):
        if _decision("US", cursor, as_of_time)[0]:
            return cursor.isoformat()
        cursor += timedelta(days=1)
    return None


def _us_status(as_of_time: datetime) -> MarketCalendarStatus:
    asof_tpe = ensure_taipei_aware(as_of_time)
    asof_et = asof_tpe.astimezone(US_EASTERN)
    day = asof_et.date()
    minutes = asof_et.hour * 60 + asof_et.minute
    is_trading, source, reason, override = _decision("US", day, asof_et)
    pre = is_trading and (4 * 60 <= minutes < 9 * 60 + 30)
    regular = is_trading and (9 * 60 + 30 <= minutes < 16 * 60)
    after = is_trading and (16 * 60 <= minutes <= 20 * 60)
    if regular:
        session = "regular"
    elif pre:
        session = "pre_market"
    elif after:
        session = "after_hours"
    elif override is not None and override.override_type in {"closed", "special_closed", "typhoon_closed"}:
        session = override.override_type
    elif not is_trading and is_holiday("US", day):
        session = "holiday"
    else:
        previous = day.isoformat() if is_trading and minutes >= 16 * 60 else _previous_us_trading_day(day, asof_et)
        if previous:
            closed_at = datetime.combine(date.fromisoformat(previous), time(16, 0), tzinfo=US_EASTERN)
            session = classify_closed_session_freshness(closed_at, market="US", as_of_time=asof_tpe)
        else:
            session = "closed"
    previous_day = day.isoformat() if is_trading and minutes >= 16 * 60 else _previous_us_trading_day(day, asof_et)
    return MarketCalendarStatus(
        market="US",
        timezone="America/New_York",
        as_of_time=asof_et,
        date=day.isoformat(),
        is_trading_day=is_trading,
        is_open=regular,
        session=session,
        reason=reason,
        previous_trading_day=previous_day,
        next_trading_day=_next_us_trading_day(day, asof_et),
        latest_complete_trade_date=previous_day,
        latest_available_time=asof_et if session in {"regular", "pre_market", "after_hours"} else None,
        calendar_confidence=_status_source_confidence(source),
        source=source,
        **_override_kwargs(override),
    )


def _taifex_status(as_of_time: datetime) -> MarketCalendarStatus:
    asof = ensure_taipei_aware(as_of_time)
    day = asof.date()
    minutes = asof.hour * 60 + asof.minute
    is_trading, source, reason, override = _decision("TAIFEX", day, asof)
    in_day = is_trading and (8 * 60 + 45 <= minutes <= 13 * 60 + 45)
    in_night = is_trading and (15 * 60 <= minutes <= 23 * 60 + 59)
    early_night = is_trading and (minutes <= 5 * 60)
    if in_day:
        session = "day"
        is_open = True
    elif in_night or early_night:
        session = "night"
        is_open = True
    elif override is not None and override.override_type in {"closed", "special_closed", "typhoon_closed"}:
        session = override.override_type
        is_open = False
    elif not is_trading and is_holiday("TAIFEX", day):
        session = "holiday"
        is_open = False
    else:
        previous = _previous_twse_trading_day(day, asof)
        if previous:
            closed_at = datetime.combine(date.fromisoformat(previous), time(5, 0), tzinfo=asof.tzinfo)
            session = classify_closed_session_freshness(closed_at, market="TAIFEX", as_of_time=asof)
        else:
            session = "closed"
        is_open = False
    belongs_to = get_next_twse_trading_day_after(day, asof)
    return MarketCalendarStatus(
        market="TAIFEX",
        timezone="Asia/Taipei",
        as_of_time=asof,
        date=day.isoformat(),
        is_trading_day=is_trading,
        is_open=is_open,
        session=session,
        reason=reason,
        previous_trading_day=_previous_twse_trading_day(day, asof),
        next_trading_day=belongs_to,
        latest_complete_trade_date=_previous_twse_trading_day(day, asof) if not is_open else None,
        latest_available_time=asof if is_open else None,
        calendar_confidence=_status_source_confidence(source),
        source=source,
        **_override_kwargs(override),
    )


def get_market_calendar_status(
    market: Literal["TWSE", "US", "TAIFEX"] | str,
    as_of_time: datetime | None = None,
) -> MarketCalendarStatus:
    """Return shadow-only market calendar status for the requested market."""

    market_key = str(market or "").upper()
    asof = ensure_taipei_aware(as_of_time)
    if market_key == "TWSE":
        return _twse_status(asof)
    if market_key == "US":
        return _us_status(asof)
    if market_key == "TAIFEX":
        return _taifex_status(asof)
    return MarketCalendarStatus(
        market=market_key or "UNKNOWN",
        timezone="Asia/Taipei",
        as_of_time=asof,
        date=asof.date().isoformat(),
        is_trading_day=False,
        is_open=False,
        session="unknown",
        reason="unknown market",
        previous_trading_day=None,
        next_trading_day=None,
        latest_complete_trade_date=None,
        latest_available_time=None,
        calendar_confidence="low",
        source="unknown",
    )
