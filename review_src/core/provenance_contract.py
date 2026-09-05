from __future__ import annotations

"""Deterministic availability rules for point-in-time market data."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence


UTC = timezone.utc
MATURITY_ORDER = {"intraday_partial": 10, "provisional": 20, "final": 30, "revised": 40}
ACCEPTED_QUALITY = {"validated", "official", "ok", "high"}


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("availability timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class AvailabilityPolicy:
    required_maturity_stage: str = "final"
    safety_delay_seconds: int = 120
    fallback_policy: str = "degrade"

    def __post_init__(self) -> None:
        if self.required_maturity_stage not in MATURITY_ORDER:
            raise ValueError("unknown required maturity stage")
        if self.safety_delay_seconds < 0:
            raise ValueError("safety delay cannot be negative")
        if self.fallback_policy not in {"degrade", "unavailable", "previous_version"}:
            raise ValueError("unknown fallback policy")


@dataclass(frozen=True)
class DatasetAvailabilityContract:
    event_semantics: str
    policy: AvailabilityPolicy
    schema_version: str


DATASET_AVAILABILITY_CONTRACTS = {
    "daily_ohlcv": DatasetAvailabilityContract(
        event_semantics="completed regular-session OHLCV for the named trading date; event_at is regular-session close",
        policy=AvailabilityPolicy(safety_delay_seconds=120, fallback_policy="degrade"),
        schema_version="daily-ohlcv-v1",
    ),
    "institution_daily": DatasetAvailabilityContract(
        event_semantics="full-day aggregated institutional activity for the named trading date, not an intraday event stream",
        policy=AvailabilityPolicy(safety_delay_seconds=300, fallback_policy="degrade"),
        schema_version="institution-daily-v1",
    ),
    "margin_daily": DatasetAvailabilityContract(
        event_semantics="post-settlement margin balances attributed to the named trading date",
        policy=AvailabilityPolicy(safety_delay_seconds=300, fallback_policy="degrade"),
        schema_version="margin-daily-v1",
    ),
    "lending_daily": DatasetAvailabilityContract(
        event_semantics="post-settlement securities-lending balances attributed to the named trading date",
        policy=AvailabilityPolicy(safety_delay_seconds=300, fallback_policy="degrade"),
        schema_version="lending-daily-v1",
    ),
    "broker_branch_daily": DatasetAvailabilityContract(
        event_semantics="completed broker-branch aggregation for the named trading date; never treated as intraday-available",
        policy=AvailabilityPolicy(safety_delay_seconds=600, fallback_policy="degrade"),
        schema_version="broker-branch-daily-v1",
    ),
    "holding_distribution_weekly": DatasetAvailabilityContract(
        event_semantics="weekly settled holding snapshot for the stated inventory date",
        policy=AvailabilityPolicy(safety_delay_seconds=600, fallback_policy="degrade"),
        schema_version="holding-distribution-weekly-v1",
    ),
    "price_volume_distribution": DatasetAvailabilityContract(
        event_semantics="completed regular-session price-volume distribution for one stock and trading date",
        policy=AvailabilityPolicy(safety_delay_seconds=300, fallback_policy="degrade"),
        schema_version="price-volume-distribution-v1",
    ),
    "official_company_event": DatasetAvailabilityContract(
        event_semantics="company disclosure event at the backend-observed disclosure timestamp",
        policy=AvailabilityPolicy(safety_delay_seconds=0, fallback_policy="degrade"),
        schema_version="official-company-event-v1",
    ),
    "stock_universe_membership": DatasetAvailabilityContract(
        event_semantics=(
            "official TWSE or TPEx ordinary-share membership snapshot effective "
            "on the stated source date and first usable only after backend observation"
        ),
        policy=AvailabilityPolicy(safety_delay_seconds=0, fallback_policy="previous_version"),
        schema_version="StockUniverseContractV1",
    ),
}


def compute_usable_from(
    *,
    first_seen_at: datetime,
    validation_passed_at: datetime,
    quality_status: str,
    maturity_stage: str,
    schema_valid: bool,
    policy: AvailabilityPolicy,
) -> datetime | None:
    """Compute the earliest safe use time from system-observed timestamps.

    A provider-declared publication time is deliberately not an input. It may
    be retained as provenance metadata, but it cannot make data usable before
    this system actually saw and validated the payload.
    """

    if not schema_valid or str(quality_status).strip().lower() not in ACCEPTED_QUALITY:
        return None
    if maturity_stage not in MATURITY_ORDER:
        return None
    if MATURITY_ORDER[maturity_stage] < MATURITY_ORDER[policy.required_maturity_stage]:
        return None
    observed = max(as_utc(first_seen_at), as_utc(validation_passed_at))
    return observed + timedelta(seconds=policy.safety_delay_seconds)


def compute_derived_usable_from(
    input_usable_from: Iterable[datetime | None],
    *,
    computed_at: datetime,
    validation_passed_at: datetime,
    safety_delay_seconds: int = 0,
) -> datetime | None:
    inputs = list(input_usable_from)
    if not inputs or any(value is None for value in inputs):
        return None
    return max(
        *(as_utc(value) for value in inputs if value is not None),
        as_utc(computed_at),
        as_utc(validation_passed_at),
    ) + timedelta(seconds=max(0, int(safety_delay_seconds)))


def select_point_in_time_version(
    versions: Sequence[Mapping[str, Any]],
    *,
    decision_at: datetime,
) -> Mapping[str, Any] | None:
    """Select only the newest revision that was usable at decision time."""

    cutoff = as_utc(decision_at)
    eligible: list[tuple[datetime, int, Mapping[str, Any]]] = []
    for version in versions:
        raw = version.get("usable_from")
        if isinstance(raw, datetime):
            usable = as_utc(raw)
        else:
            try:
                usable = as_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
            except (TypeError, ValueError):
                continue
        if usable <= cutoff:
            eligible.append((usable, int(version.get("revision_no") or 0), version))
    return max(eligible, key=lambda item: (item[0], item[1]))[2] if eligible else None
