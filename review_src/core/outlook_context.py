"""Shadow-only outlook context contracts.

These dataclasses are Phase Calendar-B scaffolding for the future
"next Taiwan trading day outlook" pipeline.  They are intentionally
side-effect free and do not replace the production next_day_outlook
calculation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zoneinfo_or_fixed(key: str, fallback_hours: int) -> timezone | ZoneInfo:
    """Return IANA timezone when available, otherwise a fixed-offset fallback."""

    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=fallback_hours), name=key)


TAIPEI_TZ = zoneinfo_or_fixed("Asia/Taipei", 8)

US_CLOSED_BUT_FRESH_HOURS = 12
US_PREVIOUS_COMPLETE_HOURS = 36
TAIFEX_CLOSED_BUT_FRESH_HOURS = 12

UNUSABLE_FRESHNESS = {"unavailable", "invalid"}


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    return value


def ensure_taipei_aware(value: datetime | None = None) -> datetime:
    """Return a timezone-aware datetime in Asia/Taipei."""

    dt = value or datetime.now(TAIPEI_TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TAIPEI_TZ)
    return dt.astimezone(TAIPEI_TZ)


@dataclass(frozen=True)
class MarketCalendarStatus:
    market: str
    timezone: str
    as_of_time: datetime
    date: str
    is_trading_day: bool
    is_open: bool
    session: str
    reason: str | None
    previous_trading_day: str | None
    next_trading_day: str | None
    latest_complete_trade_date: str | None
    latest_available_time: datetime | None
    calendar_confidence: str
    source: str
    override_active: bool = False
    override_reason: str | None = None
    override_expires_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class FactorFreshness:
    factor: str
    market: str
    data_time: datetime | None
    data_trade_date: str | None
    published_at: datetime | None
    freshness: str
    confidence: str
    usable: bool
    data_quality: str
    reason: str | None
    calendar_context_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class NextTradingDayOutlookContext:
    as_of_time: datetime
    target_market: str
    target_trade_date: str
    display_title: str = "下一個台股交易日展望"
    calendar_status: dict[str, Any] = field(default_factory=dict)
    calendar_debug: dict[str, Any] = field(default_factory=dict)
    factor_freshness: list[FactorFreshness] = field(default_factory=list)
    calendar_confidence: str = "heuristic"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["factor_freshness"] = [item.to_dict() for item in self.factor_freshness]
        return _json_ready(data)


@dataclass(frozen=True)
class FactorWeightAdjustment:
    factor: str
    base_weight: float
    freshness_multiplier: float
    usable: bool
    raw_effective_weight: float
    capped_effective_weight: float
    normalized_weight: float
    cap_applied: bool
    floor_applied: bool
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class MarketCalendarOverride:
    market: str
    date: str
    override_type: str
    reason: str
    source: str
    created_at: datetime
    created_by: str
    expires_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


def is_factor_usable(factor: FactorFreshness) -> bool:
    """Return whether a factor may participate in shadow weighting.

    Unavailable or invalid data is not a neutral 50-point signal.  It is
    simply unusable and must not be included in score normalization.
    """

    return bool(factor.usable) and factor.freshness not in UNUSABLE_FRESHNESS


def apply_holiday_dampener(normalized_score: float, holiday_dampener: float) -> float:
    """Compress score amplitude around 50 without changing factor weights."""

    dampener = max(0.0, min(1.0, float(holiday_dampener)))
    return 50.0 + (float(normalized_score) - 50.0) * dampener


def classify_closed_session_freshness(
    closed_at: datetime | None,
    *,
    market: str,
    as_of_time: datetime | None = None,
) -> str:
    """Classify whether a recently closed session is still fresh."""

    if closed_at is None:
        return "unavailable"
    asof = ensure_taipei_aware(as_of_time)
    closed = closed_at if closed_at.tzinfo else closed_at.replace(tzinfo=TAIPEI_TZ)
    age_hours = max(0.0, (asof - closed.astimezone(TAIPEI_TZ)).total_seconds() / 3600.0)
    market_key = str(market or "").upper()
    if market_key == "US":
        if age_hours <= US_CLOSED_BUT_FRESH_HOURS:
            return "closed_but_fresh"
        if age_hours <= US_PREVIOUS_COMPLETE_HOURS:
            return "previous_complete"
        return "closed_stale"
    if market_key == "TAIFEX":
        if age_hours <= TAIFEX_CLOSED_BUT_FRESH_HOURS:
            return "closed_but_fresh"
        return "closed_stale"
    return "previous_complete"


def make_factor_freshness(
    *,
    factor: str,
    market: str,
    freshness: str,
    confidence: str = "low",
    usable: bool | None = None,
    data_quality: str | None = None,
    reason: str | None = None,
    data_time: datetime | None = None,
    data_trade_date: str | None = None,
    published_at: datetime | None = None,
    calendar_context_note: str | None = None,
) -> FactorFreshness:
    inferred_usable = freshness not in UNUSABLE_FRESHNESS if usable is None else bool(usable)
    return FactorFreshness(
        factor=factor,
        market=market,
        data_time=data_time,
        data_trade_date=data_trade_date,
        published_at=published_at,
        freshness=freshness,
        confidence=confidence,
        usable=inferred_usable,
        data_quality=data_quality or freshness,
        reason=reason,
        calendar_context_note=calendar_context_note,
    )
