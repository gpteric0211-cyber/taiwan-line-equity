from __future__ import annotations

"""Metadata-only Single-Track V3 boundary for official MOPS disclosures.

This module adapts the existing two-market MOPS snapshot into the retrieval
worker protocol.  It deliberately carries only the bounded disclosure subject,
official timestamp, entity identity, and source attribution.  The MOPS
explanation/body is never copied into the research lane.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from adapter.official_company_events import (
    LISTED_EVENTS_URL,
    OTC_EVENTS_URL,
    fetch_official_company_events,
)
from core.news_research_policy import (
    OFFICIAL_RESEARCH_POLICY_VERSION,
    public_news_source_rights,
)


TPE = ZoneInfo("Asia/Taipei")
OFFICIAL_COMPANY_RETRIEVAL_ADAPTER_VERSION = "single-track-v3-mops-metadata-v1"
OFFICIAL_COMPANY_RETRIEVAL_SOURCE_ID = "official_company_mops_daily"
MAX_OFFICIAL_EVENTS_PER_RUN = 12
_MARKET_SOURCE = {
    "listed": {
        "attempt_source_id": "mops_listed_disclosures",
        "publisher": "臺灣證券交易所公開資訊觀測站",
        "rights_source_id": "TWSE_MOPS_DAILY_EVENT",
        "url": LISTED_EVENTS_URL,
    },
    "otc": {
        "attempt_source_id": "mops_otc_disclosures",
        "publisher": "證券櫃檯買賣中心公開資訊觀測站",
        "rights_source_id": "TPEX_MOPS_DAILY_EVENT",
        "url": OTC_EVENTS_URL,
    },
}


def _target_codes(values: Sequence[str]) -> tuple[str, ...]:
    codes = sorted({str(value or "").strip() for value in values})
    if not codes or len(codes) > 32 or any(not re.fullmatch(r"\d{4}", code) for code in codes):
        raise ValueError("official MOPS retrieval requires 1..32 four-digit entity refs")
    return tuple(codes)


def _clock_value(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(TPE)
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an offset-aware datetime")
    return value.astimezone(TPE)


def _official_timestamp(row: Mapping[str, Any]) -> str | None:
    disclosed_date = str(row.get("disclosed_date") or "").strip()
    disclosed_time = str(row.get("disclosed_time") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", disclosed_date):
        return None
    if not re.fullmatch(r"\d{6}", disclosed_time):
        return None
    try:
        value = datetime.strptime(
            f"{disclosed_date}{disclosed_time}", "%Y-%m-%d%H%M%S"
        ).replace(tzinfo=TPE)
    except ValueError:
        return None
    return value.isoformat(timespec="seconds")


def _event(row: Mapping[str, Any], *, retrieved_at: str) -> dict[str, Any] | None:
    market = str(row.get("market") or "").strip()
    source = _MARKET_SOURCE.get(market)
    if source is None:
        return None
    code = str(row.get("code") or "").strip()
    title = re.sub(r"\s+", " ", str(row.get("subject") or "")).strip()[:300]
    event_key = str(row.get("event_key") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", event_key) or not title:
        return None
    rights = public_news_source_rights(source["rights_source_id"])
    if not rights or not rights.get("allow_fetch") or not rights.get("allow_model"):
        return None
    official_time = _official_timestamp(row)
    return {
        "event_key": event_key,
        "event_date": str(row.get("disclosed_date") or ""),
        "title": title,
        "publisher": source["publisher"],
        "url": source["url"],
        "publisher_published_at": official_time,
        "publisher_time_verified": official_time is not None,
        "index_seen_at": None,
        "retrieved_at": retrieved_at,
        "verification_state": "primary_verified",
        "untrusted_text": True,
        "rights": rights,
        "citation_required": True,
        "authority_tier": "canonical_official",
        "quality": "official_primary",
        "entity_refs": [code],
        "can_override_main_status": False,
    }


def _source_attempts(
    sources: Any,
    *,
    item_count_by_market: Mapping[str, int],
) -> list[dict[str, Any]]:
    by_market = {
        str(item.get("market") or "").strip(): item
        for item in sources
        if isinstance(item, Mapping)
    } if isinstance(sources, list) else {}
    attempts: list[dict[str, Any]] = []
    for market, source in _MARKET_SOURCE.items():
        raw = by_market.get(market)
        if raw is None:
            attempts.append(
                {
                    "source_id": source["attempt_source_id"],
                    "status": "invalid_response",
                    "timeout_class": None,
                    "item_count": 0,
                }
            )
            continue
        explicit_status = str(raw.get("status") or "").strip().lower()
        if explicit_status:
            status = explicit_status
        elif raw.get("ok"):
            status = "ok" if item_count_by_market.get(market) else "no_results"
        else:
            status = "source_error"
        attempts.append(
            {
                "source_id": source["attempt_source_id"],
                "status": status,
                "timeout_class": raw.get("timeout_class"),
                "item_count": int(item_count_by_market.get(market, 0)),
            }
        )
    return attempts


def build_official_company_retrieval_fetcher(
    entity_refs: Sequence[str],
    *,
    fetcher: Callable[[], Mapping[str, Any]] = fetch_official_company_events,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[str], dict[str, Any]]:
    """Return a once-per-run worker fetcher bound to official stock identities."""

    codes = set(_target_codes(entity_refs))
    if not callable(fetcher):
        raise TypeError("fetcher must be callable")

    def retrieve(_: str) -> dict[str, Any]:
        retrieved_at = _clock_value(clock).isoformat(timespec="seconds")
        fetched = fetcher()
        if not isinstance(fetched, Mapping):
            fetched = {}
        rows = fetched.get("items")
        selected = [
            dict(row)
            for row in rows
            if isinstance(row, Mapping) and str(row.get("code") or "").strip() in codes
        ] if isinstance(rows, list) else []
        normalized_pairs = [
            (row, normalized)
            for row in selected
            if (normalized := _event(row, retrieved_at=retrieved_at)) is not None
        ]
        events = [event for _, event in normalized_pairs]
        count_by_market = {
            market: sum(1 for row, _ in normalized_pairs if row.get("market") == market)
            for market in _MARKET_SOURCE
        }
        attempts = _source_attempts(
            fetched.get("sources"), item_count_by_market=count_by_market
        )
        if len(events) > MAX_OFFICIAL_EVENTS_PER_RUN:
            events = []
            attempts = [
                {
                    "source_id": item["source_id"],
                    "status": "response_too_large",
                    "timeout_class": None,
                    "item_count": 0,
                }
                for item in attempts
            ]
        successful = [item for item in attempts if item["status"] in {"ok", "no_results"}]
        failed = [item for item in attempts if item["status"] not in {"ok", "no_results"}]
        if failed and successful:
            status = "partial"
        elif failed:
            status = str(failed[0]["status"])
        else:
            status = "ok" if events else "no_results"
        return {
            "ok": not failed,
            "status": status,
            "events": events,
            "attempts": len(attempts),
            "timeout_class": failed[0].get("timeout_class") if failed else None,
            "adapter_version": OFFICIAL_COMPANY_RETRIEVAL_ADAPTER_VERSION,
            "source_policy_version": OFFICIAL_RESEARCH_POLICY_VERSION,
            "source_id": OFFICIAL_COMPANY_RETRIEVAL_SOURCE_ID,
            "source_attempts": attempts,
            "raw_article_bodies_fetched": 0,
            "raw_body_retention_seconds": 0,
            "canonical_table_writes": 0,
        }

    return retrieve
