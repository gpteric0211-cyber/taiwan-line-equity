from __future__ import annotations

"""Durable metadata-only retrieval worker for queued Single-Track V3 runs.

The worker owns transaction boundaries on its dedicated SQLite connection.  Network
adapters run outside database transactions.  Terminal persistence is one atomic
savepoint containing source receipts, noncanonical research metadata, retention
pruning, the run CAS, the outbox event, the immutable worker receipt, and lease
release.
"""

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from adapter.controlled_news_research import fetch_controlled_news_metadata
from repository.single_track_v3_research_repository import (
    prune_expired_research_content,
    record_research_news_item,
    research_news_item,
)
from repository.single_track_v3_retrieval_repository import (
    link_research_run_item,
    record_retrieval_source_attempt,
    retrieval_worker_receipt,
    seal_retrieval_worker_receipt,
)
from repository.single_track_v3_scheduler_repository import (
    acquire_scheduler_lease,
    enqueue_scheduler_outbox,
    news_retrieval_run,
    record_scheduler_heartbeat,
    release_scheduler_lease,
    transition_news_retrieval_run,
)


TPE = ZoneInfo("Asia/Taipei")
RETRIEVAL_WORKER_CONTRACT_VERSION = "SingleTrackV3RetrievalWorkerV2"
REQUIRED_RETRIEVAL_TABLES = {
    "news_retrieval_run",
    "research_news_item",
    "single_track_v3_job_lease",
    "single_track_v3_worker_heartbeat",
    "single_track_v3_outbox",
    "single_track_v3_retrieval_source_attempt",
    "single_track_v3_research_run_item",
    "single_track_v3_retrieval_worker_receipt",
}
_SUCCESS_STATUSES = {"ok", "no_results"}
_INVALID_RESPONSE_STATUSES = {
    "empty_query",
    "invalid_response",
    "redirect_rejected",
    "response_too_large",
    "unsafe_xml_rejected",
}
_MAX_QUERIES = 24
_MAX_SOURCES = 8
_MAX_EVENTS_PER_RESULT = 12
_MAX_NEWS_ITEMS_PER_RUN = 512
_RESEARCH_SOURCE_CLASSES = {
    "canonical_official",
    "canonical_normalized_supplemental",
    "licensed_secondary",
    "news_radar",
}


@dataclass(frozen=True)
class RetrievalSourceSpec:
    """Bind one adapter execution mode to one required event-scan scope."""

    source_id: str
    scope_key: str
    fetcher: Callable[[str], Mapping[str, Any]]
    query_mode: str = "per_query"
    authority_tier: str = "news_radar"


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
        raise ValueError("retrieval worker payload must be finite JSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(TPE)


def _clock_value(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(TPE)
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an offset-aware datetime")
    return value.astimezone(TPE)


def _iso(value: datetime) -> str:
    return value.astimezone(TPE).isoformat(timespec="seconds")


def _normalize_queries(values: list[str] | tuple[str, ...]) -> list[str]:
    queries: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            continue
        if len(text) > 512:
            raise ValueError("retrieval query exceeds 512 characters")
        if text not in queries:
            queries.append(text)
    if not queries:
        raise ValueError("at least one bounded retrieval query is required")
    if len(queries) > _MAX_QUERIES:
        raise ValueError(f"retrieval queries exceed the {_MAX_QUERIES}-query limit")
    return queries


def _normalize_entity_refs(values: list[str] | tuple[str, ...]) -> list[str]:
    refs = sorted({str(value or "").strip() for value in values if str(value or "").strip()})
    if len(refs) > 32 or any(len(value) > 128 for value in refs):
        raise ValueError("entity_refs exceed their bounded contract")
    return refs


def retrieval_worker_schema_ready(conn: sqlite3.Connection) -> bool:
    """Check the worker schema without creating tables or writing state."""

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    return REQUIRED_RETRIEVAL_TABLES <= tables


def _failure_class(status: str, timeout_class: Any) -> str:
    normalized_status = str(status or "source_error").strip().lower()
    normalized_timeout = str(timeout_class or "").strip().lower()
    if normalized_status == "timeout" or normalized_timeout == "source_timeout":
        return "timeout"
    if normalized_status == "rate_limited" or normalized_timeout == "source_rate_limited":
        return "rate_limited"
    if normalized_status == "offline" or normalized_timeout == "source_offline":
        return "offline"
    if normalized_status == "disabled_by_source_policy":
        return "policy_disabled"
    if normalized_status in _INVALID_RESPONSE_STATUSES:
        return "invalid_response"
    return "source_error"


def _safe_status(value: Any) -> str:
    status = re.sub(r"[^a-z0-9_:-]+", "_", str(value or "source_error").strip().lower())
    return (status or "source_error")[:64]


def _attempt(
    *,
    run_id: str,
    query_digest: str,
    source_id: str,
    scope_key: str,
    ordinal: int,
    source_status: str,
    timeout_class: Any,
    item_count: int,
    adapter_version: str,
    source_policy_version: str,
    started_at: str,
    completed_at: str,
    force_failure_class: str | None = None,
) -> dict[str, Any]:
    status = _safe_status(source_status)
    failure = (
        force_failure_class
        if force_failure_class is not None
        else ("none" if status in _SUCCESS_STATUSES else _failure_class(status, timeout_class))
    )
    outcome = "failed" if failure != "none" else ("no_results" if status == "no_results" else "success")
    identity = {
        "attempt_ordinal": int(ordinal),
        "query_digest": query_digest,
        "run_id": run_id,
        "scope_key": scope_key,
        "source_id": source_id,
    }
    result_payload = {
        **identity,
        "adapter_version": adapter_version,
        "completed_at": completed_at,
        "failure_class": failure,
        "item_count": int(item_count),
        "outcome": outcome,
        "source_policy_version": source_policy_version,
        "source_status": status,
        "started_at": started_at,
    }
    return {
        "attempt_id": f"retrieval-attempt:{_digest(identity)}",
        **result_payload,
        "result_digest": _digest(result_payload),
        "raw_article_bodies_fetched": 0,
        "raw_body_retention_seconds": 0,
        "canonical_table_writes": 0,
        "created_at": completed_at,
    }


def _normalize_source_result(
    *,
    run_id: str,
    query_digest: str,
    configured_source_id: str,
    scope_key: str,
    authority_tier: str,
    result: Any,
    started_at: str,
    completed_at: str,
    run_source_policy_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(result, Mapping):
        attempt = _attempt(
            run_id=run_id,
            query_digest=query_digest,
            source_id=configured_source_id,
            scope_key=scope_key,
            ordinal=1,
            source_status="invalid_response",
            timeout_class=None,
            item_count=0,
            adapter_version="unavailable",
            source_policy_version=run_source_policy_version,
            started_at=started_at,
            completed_at=completed_at,
            force_failure_class="invalid_response",
        )
        return [attempt], []

    payload = dict(result)
    adapter_version = str(payload.get("adapter_version") or "unavailable")[:128]
    source_policy_version = str(
        payload.get("source_policy_version") or run_source_policy_version
    )[:128]
    result_source_id = str(payload.get("source_id") or configured_source_id).strip()[:128]
    raw_count = int(payload.get("raw_article_bodies_fetched") or 0)
    raw_retention = int(payload.get("raw_body_retention_seconds") or 0)
    canonical_writes = int(payload.get("canonical_table_writes") or 0)
    events = payload.get("events")
    valid_shape = isinstance(events, list) and len(events) <= _MAX_EVENTS_PER_RESULT
    valid_zero_retention = raw_count == 0 and raw_retention == 0 and canonical_writes == 0
    status = _safe_status(payload.get("status"))
    ok_contract = (
        (status in _SUCCESS_STATUSES and bool(payload.get("ok")))
        or (status == "partial" and not bool(payload.get("ok")))
        or (status not in {*_SUCCESS_STATUSES, "partial"} and not bool(payload.get("ok")))
    )
    if not valid_shape or not valid_zero_retention or not ok_contract:
        attempt = _attempt(
            run_id=run_id,
            query_digest=query_digest,
            source_id=result_source_id or configured_source_id,
            scope_key=scope_key,
            ordinal=1,
            source_status="invalid_response",
            timeout_class=None,
            item_count=0,
            adapter_version=adapter_version,
            source_policy_version=source_policy_version,
            started_at=started_at,
            completed_at=completed_at,
            force_failure_class="invalid_response",
        )
        return [attempt], []

    source_attempts = payload.get("source_attempts")
    if not isinstance(source_attempts, list) or not source_attempts:
        source_attempts = [
            {
                "source_id": result_source_id,
                "status": status,
                "timeout_class": payload.get("timeout_class"),
            }
        ]
    ordinal_by_source: dict[str, int] = {}
    attempts: list[dict[str, Any]] = []
    for raw_attempt in source_attempts[:16]:
        attempt_source = (
            str(raw_attempt.get("source_id") or result_source_id).strip()[:128]
            if isinstance(raw_attempt, Mapping)
            else result_source_id
        ) or configured_source_id
        ordinal_by_source[attempt_source] = ordinal_by_source.get(attempt_source, 0) + 1
        attempt_status = (
            raw_attempt.get("status") if isinstance(raw_attempt, Mapping) else "invalid_response"
        )
        attempt_timeout = (
            raw_attempt.get("timeout_class") if isinstance(raw_attempt, Mapping) else None
        )
        attempts.append(
            _attempt(
                run_id=run_id,
                query_digest=query_digest,
                source_id=attempt_source,
                scope_key=scope_key,
                ordinal=ordinal_by_source[attempt_source],
                source_status=str(attempt_status or "source_error"),
                timeout_class=attempt_timeout,
                item_count=(
                    int(raw_attempt.get("item_count") or 0)
                    if isinstance(raw_attempt, Mapping) and raw_attempt.get("item_count") is not None
                    else len(events)
                    if attempt_source == result_source_id and status in {"ok", "partial"}
                    else 0
                ),
                adapter_version=adapter_version,
                source_policy_version=source_policy_version,
                started_at=started_at,
                completed_at=completed_at,
            )
        )
    candidates: list[dict[str, Any]] = []
    if status in {"ok", "partial"}:
        for index, event in enumerate(events):
            if isinstance(event, Mapping):
                candidates.append(
                    {
                        "event": dict(event),
                        "query_digest": query_digest,
                        "source_id": result_source_id,
                        "adapter_version": adapter_version,
                        "source_policy_version": source_policy_version,
                        "authority_tier": authority_tier,
                        "attempt_started_at": started_at,
                        "attempt_completed_at": completed_at,
                        "event_index": index,
                    }
                )
            else:
                attempts.append(
                    _attempt(
                        run_id=run_id,
                        query_digest=query_digest,
                        source_id=f"{result_source_id}:NORMALIZATION",
                        scope_key=scope_key,
                        ordinal=index + 1,
                        source_status="invalid_event_metadata",
                        timeout_class=None,
                        item_count=0,
                        adapter_version=adapter_version,
                        source_policy_version=source_policy_version,
                        started_at=started_at,
                        completed_at=completed_at,
                        force_failure_class="invalid_response",
                    )
                )
    return attempts, candidates


def _normalize_event_candidate(
    candidate: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    entity_refs: list[str],
) -> dict[str, Any]:
    event = dict(candidate["event"])
    title = re.sub(r"\s+", " ", str(event.get("title") or "")).strip()[:300]
    publisher = re.sub(r"\s+", " ", str(event.get("publisher") or "")).strip()[:160]
    source_url = str(event.get("url") or "").strip()
    if not title or not publisher or not source_url:
        raise ValueError("event metadata is missing title, publisher, or URL")
    parsed_url = urlsplit(source_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise ValueError("event source URL must be an absolute HTTPS URL")
    if event.get("untrusted_text") is not True:
        raise ValueError("event metadata must remain untrusted")
    if event.get("can_override_main_status") not in (None, False):
        raise ValueError("news radar metadata cannot override the referee")
    retrieved = _parse_timestamp(
        event.get("retrieved_at") or candidate["attempt_completed_at"],
        "event.retrieved_at",
    )
    completed = _parse_timestamp(candidate["attempt_completed_at"], "attempt_completed_at")
    if retrieved > completed + timedelta(seconds=1):
        raise ValueError("event retrieved_at cannot be in the future")
    index_seen = None
    if event.get("index_seen_at"):
        parsed_index = _parse_timestamp(event["index_seen_at"], "event.index_seen_at")
        if parsed_index <= retrieved:
            index_seen = parsed_index
    source_class = str(candidate.get("authority_tier") or "").strip()
    if source_class not in _RESEARCH_SOURCE_CLASSES:
        raise ValueError("retrieval source authority tier is invalid")
    claimed_source_class = str(
        event.get("source_class") or event.get("authority_tier") or source_class
    ).strip()
    if claimed_source_class != source_class:
        raise ValueError("event authority claim does not match its source spec")
    raw_verification = str(event.get("verification_state") or "unverified").strip()
    if source_class in {"canonical_official", "canonical_normalized_supplemental"}:
        if raw_verification not in {"verified", "primary_verified"}:
            raise ValueError("canonical source event lacks primary verification")
        verification_state = "primary_verified"
    elif source_class == "licensed_secondary":
        verification_state = (
            "secondary_corroborated"
            if raw_verification in {"verified", "secondary_corroborated"}
            else "unverified"
        )
    else:
        verification_state = "unverified"
    event_entity_refs = event.get("entity_refs")
    item_entity_refs = entity_refs
    if event_entity_refs is not None:
        if not isinstance(event_entity_refs, (list, tuple)):
            raise ValueError("event entity_refs must be a list")
        item_entity_refs = _normalize_entity_refs(event_entity_refs)
        if not item_entity_refs or not set(item_entity_refs) <= set(entity_refs):
            raise ValueError("event entity_refs are outside the run identity")
    if source_class in {"canonical_official", "canonical_normalized_supplemental"} and not item_entity_refs:
        raise ValueError("canonical source event requires an official entity reference")
    publisher_time_verified = event.get("publisher_time_verified") is True
    publisher_published = None
    if publisher_time_verified and event.get("publisher_published_at"):
        parsed_publisher = _parse_timestamp(
            event["publisher_published_at"], "event.publisher_published_at"
        )
        if parsed_publisher <= retrieved:
            publisher_published = parsed_publisher
    publisher_time_verified = publisher_published is not None
    publisher_published_date = None
    if event.get("event_date") is not None:
        try:
            declared_date = date.fromisoformat(str(event["event_date"]).strip())
        except ValueError as exc:
            raise ValueError("event.event_date must be an ISO-8601 date") from exc
        if declared_date > retrieved.date():
            raise ValueError("event.event_date cannot be in the future")
        publisher_published_date = declared_date.isoformat()
    if publisher_published is not None:
        exact_date = publisher_published.astimezone(TPE).date().isoformat()
        if publisher_published_date is not None and publisher_published_date != exact_date:
            raise ValueError("event date conflicts with exact publisher timestamp")
        publisher_published_date = exact_date
    available = min(
        [value for value in (index_seen, publisher_published, retrieved) if value is not None]
    )
    rights_source = event.get("rights")
    if not isinstance(rights_source, Mapping):
        raise ValueError("event rights are missing")
    rights_input = dict(rights_source)
    source_id = str(rights_input.get("source_id") or candidate["source_id"]).strip()[:128]
    if not source_id:
        raise ValueError("event source_id is missing")
    for field in ("allow_fetch", "allow_model", "allow_display", "allow_store_excerpt"):
        if not isinstance(rights_input.get(field), bool):
            raise ValueError(f"event rights {field} must be boolean")
    if not rights_input["allow_fetch"] or not rights_input["allow_model"]:
        raise ValueError("event source policy does not allow retrieval/model use")
    metadata_days = rights_input.get("metadata_retention_days", 0)
    if isinstance(metadata_days, bool) or not isinstance(metadata_days, int):
        raise ValueError("metadata_retention_days must be an integer")
    content_retention_days = min(7, max(0, metadata_days))
    source_rights = {
        **rights_input,
        "content_retention_days": content_retention_days,
        "attribution": f"{publisher} via {source_id}",
    }
    normalized_title = re.sub(r"[^\w]+", " ", title.casefold(), flags=re.UNICODE).strip()
    if not normalized_title:
        raise ValueError("event title has no usable deduplication tokens")
    content_hash = _digest(
        {
            "index_seen_at": _iso(index_seen) if index_seen else None,
            "publisher": publisher.casefold(),
            "publisher_published_at": _iso(publisher_published) if publisher_published else None,
            "publisher_published_date": publisher_published_date,
            "source_id": source_id,
            "source_url": source_url,
            "title": title,
        }
    )
    event_key = str(event.get("event_key") or "").strip().lower()
    event_fingerprint = (
        event_key
        if len(event_key) == 64 and all(character in "0123456789abcdef" for character in event_key)
        else _digest({"content_hash": content_hash, "source_id": source_id})
    )
    # A title alone is not a safe event identity: generic MOPS subjects can be
    # identical across issuers.  Bind the official entity set before any
    # reconciliation can cluster or promote the metadata.
    dedup_digest = _digest(
        {
            "entity_refs": item_entity_refs,
            "normalized_title": normalized_title,
        }
    )
    news_item_id = f"research-news:{_digest({'content_hash': content_hash, 'source_id': source_id})}"
    return {
        "news_item_id": news_item_id,
        "source_id": source_id,
        "source_class": source_class,
        "publisher": publisher,
        "source_url": source_url,
        "publisher_published_at": _iso(publisher_published) if publisher_published else None,
        "publisher_published_date": publisher_published_date,
        "publisher_time_verified": publisher_time_verified,
        "index_seen_at": _iso(index_seen) if index_seen else None,
        "first_retrieved_at": _iso(retrieved),
        "last_retrieved_at": _iso(retrieved),
        "available_at": _iso(available),
        "effective_tw_trade_date": str(run["target_trade_date"]),
        "verification_state": verification_state,
        "title": title,
        "key_points": [],
        "short_excerpt": "",
        "content_hash": content_hash,
        "event_fingerprint": event_fingerprint,
        "dedup_cluster": f"research-dedup:{dedup_digest}",
        "entity_refs": item_entity_refs,
        "event_cluster_id": None,
        "event_revision_id": None,
        "source_rights": source_rights,
        "source_policy_version": str(
            rights_input.get("policy_version")
            or candidate.get("source_policy_version")
            or run["source_policy_version"]
        ),
        "retention_class": (
            "canonical_disclosure"
            if source_class in {"canonical_official", "canonical_normalized_supplemental"}
            else "hot_news"
        ),
        "content_expires_at": _iso(retrieved + timedelta(days=content_retention_days)),
        "content_pruned_at": None,
        "untrusted_text": True,
        "raw_body_retained": False,
        "first_run_id": str(run["run_id"]),
        "last_run_id": str(run["run_id"]),
        "created_at": _iso(retrieved),
        "updated_at": _iso(retrieved),
    }


def _merge_existing_news_item(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    *,
    run_id: str,
    linked_at: str,
) -> tuple[dict[str, Any], str]:
    existing = research_news_item(conn, item["news_item_id"])
    if existing is None:
        item["updated_at"] = linked_at
        return item, "discovered"
    existing_last = _parse_timestamp(existing["last_retrieved_at"], "last_retrieved_at")
    incoming_last = _parse_timestamp(item["last_retrieved_at"], "last_retrieved_at")
    existing_available = _parse_timestamp(existing["available_at"], "available_at")
    incoming_available = _parse_timestamp(item["available_at"], "available_at")
    item.update(
        first_retrieved_at=existing["first_retrieved_at"],
        last_retrieved_at=_iso(max(existing_last, incoming_last)),
        available_at=_iso(min(existing_available, incoming_available)),
        content_expires_at=existing["content_expires_at"],
        content_pruned_at=existing.get("content_pruned_at"),
        first_run_id=existing["first_run_id"],
        last_run_id=run_id,
        created_at=existing["created_at"],
        updated_at=linked_at,
        event_cluster_id=existing.get("event_cluster_id"),
        event_revision_id=existing.get("event_revision_id"),
    )
    if existing.get("content_pruned_at"):
        item.update(title="", key_points=[], short_excerpt="", verification_state="expired")
    return item, "unchanged"


def _coverage(attempts: list[Mapping[str, Any]]) -> dict[str, Any]:
    def summarize(key_field: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for attempt in attempts:
            key = str(attempt[key_field])
            summary = result.setdefault(
                key,
                {
                    "attempted": 0,
                    "failed": 0,
                    "item_count": 0,
                    "no_results": 0,
                    "successful": 0,
                    "failure_classes": [],
                },
            )
            summary["attempted"] += 1
            summary["item_count"] += int(attempt["item_count"])
            if attempt["outcome"] == "failed":
                summary["failed"] += 1
                failure = str(attempt["failure_class"])
                if failure not in summary["failure_classes"]:
                    summary["failure_classes"].append(failure)
            else:
                summary["successful"] += 1
                if attempt["outcome"] == "no_results":
                    summary["no_results"] += 1
        for summary in result.values():
            summary["failure_classes"].sort()
        return {key: result[key] for key in sorted(result)}

    return {
        "by_scope": summarize("scope_key"),
        "by_source": summarize("source_id"),
    }


def run_retrieval_worker(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    queries: list[str] | tuple[str, ...],
    entity_refs: list[str] | tuple[str, ...] = (),
    source_fetchers: Mapping[str, Callable[[str], Mapping[str, Any]]] | None = None,
    source_specs: list[RetrievalSourceSpec] | tuple[RetrievalSourceSpec, ...] | None = None,
    query_plan_version: str = "AdHocRetrievalQueryPlanV1",
    source_plan_version: str = "AdHocRetrievalSourcePlanV1",
    owner_id: str = "single-track-v3-retrieval-worker",
    clock: Callable[[], datetime] | None = None,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Execute one queued/running run with durable restart and terminal semantics."""

    if not retrieval_worker_schema_ready(conn):
        raise RuntimeError("Single-Track retrieval worker schema is not initialized")
    normalized_queries = _normalize_queries(queries)
    normalized_entities = _normalize_entity_refs(entity_refs)
    input_query_digests = sorted({_digest({"query": value}) for value in normalized_queries})
    query_plan = str(query_plan_version or "").strip()
    source_plan = str(source_plan_version or "").strip()
    if not query_plan or len(query_plan) > 128:
        raise ValueError("query_plan_version is required and must not exceed 128 characters")
    if not source_plan or len(source_plan) > 128:
        raise ValueError("source_plan_version is required and must not exceed 128 characters")
    if source_specs is not None and source_fetchers is not None:
        raise ValueError("source_specs and source_fetchers are mutually exclusive")
    if source_specs is not None:
        specs = list(source_specs)
    elif source_fetchers is not None:
        specs = [
            RetrievalSourceSpec(
                source_id=str(source_id),
                scope_key=str(source_id),
                fetcher=fetcher,
            )
            for source_id, fetcher in dict(source_fetchers).items()
        ]
    else:
        specs = [
            RetrievalSourceSpec(
                source_id="CONTROLLED_NEWS_METADATA",
                scope_key="company_industry_news",
                fetcher=fetch_controlled_news_metadata,
            )
        ]
    if not specs or len(specs) > _MAX_SOURCES:
        raise ValueError(f"retrieval sources must contain between 1 and {_MAX_SOURCES} specs")
    identities: set[tuple[str, str]] = set()
    for spec in specs:
        if not isinstance(spec, RetrievalSourceSpec):
            raise TypeError("source_specs must contain RetrievalSourceSpec values")
        source_id = str(spec.source_id or "").strip()
        scope_key = str(spec.scope_key or "").strip()
        if not source_id or not scope_key or not callable(spec.fetcher):
            raise ValueError("retrieval source spec requires source_id, scope_key, and fetcher")
        if spec.query_mode not in {"per_query", "once_per_run"}:
            raise ValueError("retrieval source query_mode is invalid")
        if spec.authority_tier not in _RESEARCH_SOURCE_CLASSES:
            raise ValueError("retrieval source authority_tier is invalid")
        identity = (source_id, scope_key)
        if identity in identities:
            raise ValueError("retrieval source spec identity must be unique")
        identities.add(identity)
    lease_duration = int(lease_seconds)
    if lease_duration < 30 or lease_duration > 3600:
        raise ValueError("lease_seconds must be between 30 and 3600")
    owner = str(owner_id or "").strip()
    if not owner or len(owner) > 128:
        raise ValueError("owner_id is required and must not exceed 128 characters")

    replay = retrieval_worker_receipt(conn, run_id)
    if replay is not None:
        replay_identity = {
            "entity_refs": replay.get("entity_refs") or [],
            "query_digests": replay.get("query_digests") or [],
            "query_plan_version": replay.get("query_plan_version"),
            "source_plan_version": replay.get("source_plan_version"),
        }
        requested_identity = {
            "entity_refs": normalized_entities,
            "query_digests": input_query_digests,
            "query_plan_version": query_plan,
            "source_plan_version": source_plan,
        }
        if replay_identity != requested_identity:
            raise ValueError("retrieval replay identity conflicts with its terminal receipt")
        return {
            "contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
            "run_id": str(run_id),
            "status": replay["terminal_status"],
            "receipt": replay,
            "replayed": True,
            "adapter_calls": 0,
            "zero_model_calls": 0,
            "canonical_table_writes": 0,
        }
    run = news_retrieval_run(conn, run_id)
    if run is None:
        raise ValueError("retrieval run does not exist")
    if run["status"] not in {"queued", "running"}:
        return {
            "contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
            "run_id": str(run_id),
            "status": str(run["status"]),
            "reason_code": "terminal_run_without_worker_receipt",
            "replayed": False,
            "adapter_calls": 0,
            "zero_model_calls": 0,
            "canonical_table_writes": 0,
        }

    acquired = _clock_value(clock)
    acquired_text = _iso(acquired)
    lease_key = f"single-track-v3:retrieval:{run_id}"
    lease_token = uuid.uuid4().hex
    worker_digest = _digest(
        {
            "contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
            "owner_id": owner,
            "sources": [
                {
                    "authority_tier": spec.authority_tier,
                    "query_mode": spec.query_mode,
                    "scope_key": spec.scope_key,
                    "source_id": spec.source_id,
                }
                for spec in sorted(
                    specs,
                    key=lambda item: (item.scope_key, item.source_id, item.query_mode),
                )
            ],
        }
    )
    worker_id = f"{owner}:{_digest({'run_id': str(run_id)})[:12]}"
    savepoint = "single_track_retrieval_claim"
    conn.execute(f'SAVEPOINT "{savepoint}"')
    try:
        lease_acquired = acquire_scheduler_lease(
            conn,
            lease_key=lease_key,
            run_id=str(run_id),
            owner_id=owner,
            lease_token=lease_token,
            acquired_at=acquired_text,
            expires_at=_iso(acquired + timedelta(seconds=lease_duration)),
            ensure_schema=False,
        )
        if not lease_acquired:
            conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
            conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
            conn.commit()
            return {
                "contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
                "run_id": str(run_id),
                "status": "lease_not_acquired",
                "replayed": False,
                "adapter_calls": 0,
                "zero_model_calls": 0,
                "canonical_table_writes": 0,
            }
        claimed_run = news_retrieval_run(conn, str(run_id))
        if claimed_run is None:
            raise RuntimeError("retrieval run disappeared after lease acquisition")
        if claimed_run["status"] not in {"queued", "running"}:
            release_scheduler_lease(
                conn,
                lease_key=lease_key,
                lease_token=lease_token,
                released_at=acquired_text,
                ensure_schema=False,
            )
            conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
            conn.commit()
            return {
                "contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
                "run_id": str(run_id),
                "status": str(claimed_run["status"]),
                "reason_code": "run_became_terminal_before_adapter_execution",
                "replayed": False,
                "adapter_calls": 0,
                "zero_model_calls": 0,
                "canonical_table_writes": 0,
            }
        run = claimed_run
        if run["status"] == "queued" and not transition_news_retrieval_run(
            conn,
            str(run_id),
            expected_status="queued",
            new_status="running",
            started_at=acquired_text,
            updated_at=acquired_text,
            ensure_schema=False,
        ):
            raise RuntimeError("retrieval run claim lost its queued compare-and-swap")
    except Exception:
        if conn.in_transaction:
            conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
            conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        conn.rollback()
        raise
    conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
    conn.commit()

    attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    adapter_calls = 0
    last_heartbeat: datetime | None = None
    try:
        for spec in sorted(specs, key=lambda item: (item.scope_key, item.source_id)):
            configured_source_id = str(spec.source_id)
            scope_key = str(spec.scope_key)
            execution_queries = normalized_queries if spec.query_mode == "per_query" else [""]
            for query in execution_queries:
                query_digest = (
                    _digest({"query": query})
                    if spec.query_mode == "per_query"
                    else _digest(
                        {
                            "query_mode": "once_per_run",
                            "scope_key": scope_key,
                            "source_id": configured_source_id,
                        }
                    )
                )
                started = _clock_value(clock)
                try:
                    raw_result: Any = spec.fetcher(query)
                except Exception:
                    raw_result = {
                        "ok": False,
                        "status": "source_error",
                        "events": [],
                        "source_id": configured_source_id,
                        "timeout_class": "source_error",
                        "adapter_version": "exception-boundary-v1",
                        "source_policy_version": run["source_policy_version"],
                        "raw_article_bodies_fetched": 0,
                        "raw_body_retention_seconds": 0,
                        "canonical_table_writes": 0,
                    }
                adapter_calls += 1
                completed = _clock_value(clock)
                normalized_attempts, normalized_candidates = _normalize_source_result(
                    run_id=str(run_id),
                    query_digest=query_digest,
                    configured_source_id=str(configured_source_id),
                    scope_key=scope_key,
                    authority_tier=spec.authority_tier,
                    result=raw_result,
                    started_at=_iso(started),
                    completed_at=_iso(completed),
                    run_source_policy_version=str(run["source_policy_version"]),
                )
                attempts.extend(normalized_attempts)
                for candidate in normalized_candidates:
                    try:
                        candidate["news_item"] = _normalize_event_candidate(
                            candidate,
                            run=run,
                            entity_refs=normalized_entities,
                        )
                        candidates.append(candidate)
                    except (TypeError, ValueError):
                        attempts.append(
                            _attempt(
                                run_id=str(run_id),
                                query_digest=str(candidate["query_digest"]),
                                source_id=f"{candidate['source_id']}:NORMALIZATION",
                                scope_key=scope_key,
                                ordinal=int(candidate["event_index"]) + 1,
                                source_status="invalid_event_metadata",
                                timeout_class=None,
                                item_count=0,
                                adapter_version=str(candidate["adapter_version"]),
                                source_policy_version=str(candidate["source_policy_version"]),
                                started_at=str(candidate["attempt_started_at"]),
                                completed_at=str(candidate["attempt_completed_at"]),
                                force_failure_class="invalid_response",
                            )
                        )
                heartbeat = _clock_value(clock)
                if last_heartbeat is not None and heartbeat <= last_heartbeat:
                    heartbeat = last_heartbeat + timedelta(microseconds=1)
                last_heartbeat = heartbeat
                if not record_scheduler_heartbeat(
                    conn,
                    worker_id=worker_id,
                    lease_key=lease_key,
                    lease_token=lease_token,
                    run_id=str(run_id),
                    worker_identity_digest=worker_digest,
                    state="running",
                    heartbeat_at=heartbeat.isoformat(timespec="microseconds"),
                    expires_at=_iso(heartbeat + timedelta(seconds=lease_duration)),
                    metadata={
                        "adapter_calls": adapter_calls,
                        "query_digests": sorted({_digest({"query": value}) for value in normalized_queries}),
                        "source_ids": sorted(str(value.source_id) for value in specs),
                        "scope_keys": sorted(str(value.scope_key) for value in specs),
                    },
                    ensure_schema=False,
                ):
                    raise RuntimeError("retrieval worker lease expired during adapter execution")
                conn.commit()

        if len(candidates) > _MAX_NEWS_ITEMS_PER_RUN:
            raise ValueError("retrieval result exceeds the per-run news item limit")
        completed = _clock_value(clock)
        completed_text = _iso(completed)
        coverage = _coverage(attempts)
        failures = [
            {
                "attempt_id": attempt["attempt_id"],
                "failure_class": attempt["failure_class"],
                "query_digest": attempt["query_digest"],
                "scope_key": attempt["scope_key"],
                "source_id": attempt["source_id"],
                "source_status": attempt["source_status"],
            }
            for attempt in attempts
            if attempt["outcome"] == "failed"
        ]
        success_count = sum(attempt["outcome"] != "failed" for attempt in attempts)
        if completed > _parse_timestamp(run["cutoff_at"], "run.cutoff_at"):
            terminal_status = "late"
            late_reason = "retrieval_completed_after_run_cutoff"
        elif not failures:
            terminal_status = "success"
            late_reason = None
        elif success_count:
            terminal_status = "partial"
            late_reason = None
        else:
            terminal_status = "failed"
            late_reason = None

        terminal_savepoint = "single_track_retrieval_terminal"
        conn.execute(f'SAVEPOINT "{terminal_savepoint}"')
        try:
            for attempt in attempts:
                record_retrieval_source_attempt(conn, attempt, ensure_schema=False)
            saved_items: dict[str, tuple[dict[str, Any], str]] = {}
            for candidate in candidates:
                item = dict(candidate["news_item"])
                item, action = _merge_existing_news_item(
                    conn,
                    item,
                    run_id=str(run_id),
                    linked_at=completed_text,
                )
                saved = record_research_news_item(conn, item, ensure_schema=False)
                saved_items[str(saved["news_item_id"])] = (saved, action)
            for news_item_id, (_, action) in sorted(saved_items.items()):
                link_research_run_item(
                    conn,
                    run_id=str(run_id),
                    news_item_id=news_item_id,
                    event_action=action,
                    linked_at=completed_text,
                    ensure_schema=False,
                )
            pruned_count = prune_expired_research_content(
                conn,
                pruned_at=completed_text,
                ensure_schema=False,
            )
            attempt_ids = sorted({str(item["attempt_id"]) for item in attempts})
            news_item_ids = sorted(saved_items)
            result_payload = {
                "contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
                "completed_at": completed_text,
                "entity_refs": normalized_entities,
                "news_item_ids": news_item_ids,
                "pruned_item_count": pruned_count,
                "run_id": str(run_id),
                "query_digests": input_query_digests,
                "query_plan_version": query_plan,
                "source_plan_version": source_plan,
                "source_attempt_ids": attempt_ids,
                "source_coverage": coverage,
                "source_failures": failures,
                "terminal_status": terminal_status,
                "zero_model_calls": 0,
            }
            result_digest = _digest(result_payload)
            if not transition_news_retrieval_run(
                conn,
                str(run_id),
                expected_status="running",
                new_status=terminal_status,
                completed_at=completed_text,
                source_coverage=coverage,
                source_failures=failures,
                late_reason=late_reason,
                updated_at=completed_text,
                ensure_schema=False,
            ):
                raise RuntimeError("retrieval terminal compare-and-swap was lost")
            outbox_identity = {
                "event_type": "NEWS_RETRIEVAL_RUN_TERMINAL",
                "result_digest": result_digest,
                "run_id": str(run_id),
            }
            outbox_id = f"outbox:{_digest(outbox_identity)}"
            enqueue_scheduler_outbox(
                conn,
                {
                    "outbox_id": outbox_id,
                    "run_id": str(run_id),
                    "event_type": "NEWS_RETRIEVAL_RUN_TERMINAL",
                    "aggregate_key": str(run_id),
                    "dedupe_key": f"NEWS_RETRIEVAL_RUN_TERMINAL:{run_id}",
                    "payload": {**result_payload, "result_digest": result_digest},
                    "status": "pending",
                    "available_at": completed_text,
                    "claimed_at": None,
                    "delivered_at": None,
                    "attempt_count": 0,
                    "last_error_code": None,
                    "created_at": completed_text,
                    "updated_at": completed_text,
                },
                ensure_schema=False,
            )
            receipt = seal_retrieval_worker_receipt(
                conn,
                {
                    "receipt_id": f"retrieval-receipt:{_digest({'result_digest': result_digest, 'run_id': str(run_id)})}",
                    "run_id": str(run_id),
                    "terminal_status": terminal_status,
                    "source_attempt_ids": attempt_ids,
                    "news_item_ids": news_item_ids,
                    "pruned_item_count": pruned_count,
                    "terminal_outbox_id": outbox_id,
                    "worker_contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
                    "entity_refs": normalized_entities,
                    "query_digests": input_query_digests,
                    "query_plan_version": query_plan,
                    "source_plan_version": source_plan,
                    "result_digest": result_digest,
                    "zero_model_calls": 0,
                    "raw_article_bodies_retained": 0,
                    "canonical_table_writes": 0,
                    "completed_at": completed_text,
                    "created_at": completed_text,
                },
                ensure_schema=False,
            )
            if not release_scheduler_lease(
                conn,
                lease_key=lease_key,
                lease_token=lease_token,
                released_at=completed_text,
                ensure_schema=False,
            ):
                raise RuntimeError("retrieval lease release failed before terminal commit")
        except Exception:
            conn.execute(f'ROLLBACK TO SAVEPOINT "{terminal_savepoint}"')
            conn.execute(f'RELEASE SAVEPOINT "{terminal_savepoint}"')
            raise
        conn.execute(f'RELEASE SAVEPOINT "{terminal_savepoint}"')
        conn.commit()
        return {
            "contract_version": RETRIEVAL_WORKER_CONTRACT_VERSION,
            "run_id": str(run_id),
            "status": terminal_status,
            "receipt": receipt,
            "replayed": False,
            "adapter_calls": adapter_calls,
            "zero_model_calls": 0,
            "canonical_table_writes": 0,
        }
    except Exception:
        conn.rollback()
        released = _clock_value(clock)
        try:
            release_scheduler_lease(
                conn,
                lease_key=lease_key,
                lease_token=lease_token,
                released_at=_iso(released),
                ensure_schema=False,
            )
            conn.commit()
        except sqlite3.DatabaseError:
            conn.rollback()
        raise
