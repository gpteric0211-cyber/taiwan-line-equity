from __future__ import annotations

"""Metadata-only retrieval boundary for audited Taiwan government RSS feeds."""

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from adapter.official_external_events import (
    OFFICIAL_RSS_SOURCES,
    TAIWAN_POLICY_RSS_SOURCE_IDS,
    fetch_official_policy_feeds,
)
from core.news_research_policy import (
    OFFICIAL_RESEARCH_POLICY_VERSION,
    public_news_source_rights,
)
from core.public_url import normalize_public_https_url


TPE = ZoneInfo("Asia/Taipei")
TAIWAN_POLICY_RETRIEVAL_ADAPTER_VERSION = "single-track-v3-taiwan-policy-metadata-v1"
TAIWAN_POLICY_RETRIEVAL_SOURCE_ID = "taiwan_government_policy_official"
MAX_TAIWAN_POLICY_EVENTS_PER_RUN = 12
_SOURCE_METADATA = {
    str(source["source_id"]): {
        "publisher": str(source["publisher"]),
        "feed_url": str(source["url"]),
    }
    for source in OFFICIAL_RSS_SOURCES
    if source["source_id"] in TAIWAN_POLICY_RSS_SOURCE_IDS
}


def _target_code(values: Sequence[str]) -> str:
    codes = sorted({str(value or "").strip() for value in values})
    if len(codes) != 1 or not re.fullmatch(r"\d{4}", codes[0]):
        raise ValueError("Taiwan policy retrieval requires exactly one four-digit entity ref")
    return codes[0]


def _terms(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = re.sub(r"[\x00-\x1f]+", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if len(text) > 80:
            raise ValueError("Taiwan policy relevance term exceeds 80 characters")
        folded = text.casefold()
        if folded not in result:
            result.append(folded)
    if not result or len(result) > 20:
        raise ValueError("Taiwan policy retrieval requires 1..20 audited relevance terms")
    return tuple(result)


def _clock_value(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(TPE)
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an offset-aware datetime")
    return value.astimezone(TPE)


def _published_at(value: Any, *, retrieved_at: datetime) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    normalized = parsed.astimezone(TPE)
    if normalized > retrieved_at:
        return None
    return normalized.isoformat(timespec="seconds")


def _title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:300]


def _relevant(row: Mapping[str, Any], terms: Sequence[str]) -> bool:
    folded = _title(row.get("title")).casefold()
    return bool(folded and any(term in folded for term in terms))


def _event(
    row: Mapping[str, Any],
    *,
    stock_code: str,
    retrieved_at: datetime,
) -> dict[str, Any] | None:
    source_id = str(row.get("source_id") or "").strip()
    source = _SOURCE_METADATA.get(source_id)
    if source is None:
        return None
    title = _title(row.get("title"))
    event_key = str(row.get("event_key") or "").strip().lower()
    source_url = normalize_public_https_url(row.get("source_url"))
    published_at = _published_at(row.get("published_at"), retrieved_at=retrieved_at)
    rights = public_news_source_rights(source_id)
    if (
        not title
        or not re.fullmatch(r"[0-9a-f]{64}", event_key)
        or source_url is None
        or published_at is None
        or not rights
        or not rights.get("allow_fetch")
        or not rights.get("allow_model")
    ):
        return None
    return {
        "event_key": event_key,
        "event_date": published_at[:10],
        "title": title,
        "publisher": source["publisher"],
        "url": source_url,
        "publisher_published_at": published_at,
        "publisher_time_verified": True,
        "index_seen_at": None,
        "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
        "verification_state": "primary_verified",
        "untrusted_text": True,
        "rights": rights,
        "citation_required": True,
        "authority_tier": "canonical_official",
        "quality": "official_primary",
        "entity_refs": [stock_code],
        "can_override_main_status": False,
    }


def build_taiwan_policy_retrieval_fetcher(
    entity_refs: Sequence[str],
    relevance_terms: Sequence[str],
    *,
    fetcher: Callable[[], Mapping[str, Any]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[str], dict[str, Any]]:
    """Return a once-per-run official-policy fetcher with deterministic relevance filtering."""

    stock_code = _target_code(entity_refs)
    audited_terms = _terms(relevance_terms)
    source_fetcher = fetcher
    if source_fetcher is None:
        source_fetcher = lambda: fetch_official_policy_feeds(
            per_source_limit=4,
            source_ids=TAIWAN_POLICY_RSS_SOURCE_IDS,
        )
    if not callable(source_fetcher):
        raise TypeError("fetcher must be callable")

    def retrieve(_: str) -> dict[str, Any]:
        retrieved_at = _clock_value(clock)
        fetched = source_fetcher()
        if not isinstance(fetched, Mapping):
            fetched = {}
        rows = fetched.get("items")
        candidates = [
            dict(row)
            for row in rows
            if isinstance(row, Mapping)
            and str(row.get("source_id") or "") in _SOURCE_METADATA
            and _relevant(row, audited_terms)
        ] if isinstance(rows, list) else []
        normalized_pairs = [
            (row, event)
            for row in candidates
            if (event := _event(row, stock_code=stock_code, retrieved_at=retrieved_at)) is not None
        ]
        events = [event for _, event in normalized_pairs]
        raw_count = {
            source_id: sum(1 for row in candidates if row.get("source_id") == source_id)
            for source_id in _SOURCE_METADATA
        }
        valid_count = {
            source_id: sum(1 for row, _ in normalized_pairs if row.get("source_id") == source_id)
            for source_id in _SOURCE_METADATA
        }
        sources = fetched.get("sources")
        by_source = {
            str(item.get("source") or "").strip(): item
            for item in sources
            if isinstance(item, Mapping)
        } if isinstance(sources, list) else {}
        attempts: list[dict[str, Any]] = []
        for source_id in TAIWAN_POLICY_RSS_SOURCE_IDS:
            raw = by_source.get(source_id)
            if raw is None:
                status = "invalid_response"
                timeout_class = None
            elif not raw.get("ok"):
                status = str(raw.get("status") or "source_error").strip().lower()
                timeout_class = raw.get("timeout_class")
            elif raw_count[source_id] != valid_count[source_id]:
                status = "invalid_response"
                timeout_class = None
            else:
                status = "ok" if valid_count[source_id] else "no_results"
                timeout_class = None
            attempts.append(
                {
                    "source_id": source_id,
                    "status": status,
                    "timeout_class": timeout_class,
                    "item_count": valid_count[source_id],
                }
            )
        if len(events) > MAX_TAIWAN_POLICY_EVENTS_PER_RUN:
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
        status = (
            "partial"
            if failed and successful
            else str(failed[0]["status"])
            if failed
            else "ok"
            if events
            else "no_results"
        )
        return {
            "ok": not failed,
            "status": status,
            "events": events,
            "attempts": len(attempts),
            "timeout_class": failed[0].get("timeout_class") if failed else None,
            "adapter_version": TAIWAN_POLICY_RETRIEVAL_ADAPTER_VERSION,
            "source_policy_version": OFFICIAL_RESEARCH_POLICY_VERSION,
            "source_id": TAIWAN_POLICY_RETRIEVAL_SOURCE_ID,
            "source_attempts": attempts,
            "raw_article_bodies_fetched": 0,
            "raw_body_retention_seconds": 0,
            "canonical_table_writes": 0,
        }

    return retrieve
