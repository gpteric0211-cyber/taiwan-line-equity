from __future__ import annotations

"""Controlled, noncanonical news retrieval for post-reply model shadow work."""

import copy
import hashlib
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from adapter.controlled_news_research import fetch_controlled_news_metadata
from core.line_bot_config import env_int, env_text


LINE_MODEL_RESEARCH_VERSION = "line-model-controlled-research-v1"
RESEARCH_ROLLOUT_VALUES = {"off", "shadow"}
_CACHE_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

_TOPIC_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("美伊", "伊朗", "iran", "荷莫茲", "hormuz"), ("Iran", "Hormuz", "Middle East")),
    (("以色列", "israel", "加薩", "gaza"), ("Israel", "Gaza", "Middle East")),
    (("台海", "中國軍演", "兩岸"), ("Taiwan Strait", "China military")),
    (("關稅", "貿易戰", "tariff"), ("tariff", "trade war", "Taiwan")),
    (("制裁", "出口管制", "禁運"), ("sanction", "export control", "Taiwan")),
    (("石油", "原油", "油價", "能源"), ("oil", "energy", "Middle East")),
)


def line_model_research_rollout() -> str:
    value = env_text("LINE_MODEL_RESEARCH_ROLLOUT", "off").strip().lower()
    return value if value in RESEARCH_ROLLOUT_VALUES else "off"


def _bounded_public_term(value: Any, *, maximum: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    clean = "".join(character for character in text if character.isalnum() or character in " .&-_" )
    return clean.strip()[:maximum]


def _or_group(terms: list[str] | tuple[str, ...]) -> str:
    bounded = list(dict.fromkeys(_bounded_public_term(term) for term in terms))
    bounded = [term for term in bounded if term]
    return "(" + " OR ".join(f'"{term}"' for term in bounded) + ")" if bounded else ""


def _matched_public_topics(question: str) -> list[str]:
    normalized = str(question or "").casefold()
    values: list[str] = []
    for triggers, query_terms in _TOPIC_RULES:
        if any(trigger.casefold() in normalized for trigger in triggers):
            values.extend(query_terms)
    return list(dict.fromkeys(values))


def build_controlled_research_queries(
    *,
    question: str,
    model_facts: dict[str, Any],
    requested_scopes: list[str] | tuple[str, ...],
) -> list[str]:
    """Build at most two GDELT queries from public DB entities and allowlisted topics."""

    scopes = {str(item) for item in requested_scopes}
    if not scopes.intersection({"current_news", "geopolitics"}):
        return []
    stock = model_facts.get("stock") if isinstance(model_facts.get("stock"), dict) else {}
    code = _bounded_public_term(model_facts.get("code") or stock.get("code"), maximum=12)
    name = _bounded_public_term(stock.get("name"), maximum=40)
    queries: list[str] = []
    if "current_news" in scopes:
        entity = _or_group([name, code])
        queries.append(entity or _or_group(["台股", "Taiwan stock market", "Taiwan economy"]))
    if "geopolitics" in scopes:
        topics = _matched_public_topics(question)
        topic_group = _or_group(topics or ["geopolitics", "Taiwan", "trade", "sanction"])
        market_group = _or_group(["Taiwan", "台股", "semiconductor"])
        queries.append(f"{topic_group} {market_group}".strip())
    return [query[:512] for query in dict.fromkeys(queries) if query][:2]


def _cache_key(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _cached_fetch(
    query: str,
    *,
    fetcher: Callable[..., dict[str, Any]],
    now_monotonic: float,
) -> tuple[dict[str, Any], str]:
    key = _cache_key(query)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > now_monotonic:
            _CACHE.move_to_end(key)
            return copy.deepcopy(cached[1]), "hit"
        if cached:
            _CACHE.pop(key, None)
    result = fetcher(
        query,
        max_records=env_int("LINE_MODEL_RESEARCH_MAX_EVENTS_PER_QUERY", 4, minimum=1, maximum=6),
        timespan="3d",
        timeout_seconds=float(
            env_int("LINE_MODEL_RESEARCH_TIMEOUT_SECONDS", 6, minimum=2, maximum=10)
        ),
        max_attempts=1,
    )
    positive = bool(result.get("ok") and result.get("events"))
    ttl = env_int(
        "NEWS_SEARCH_POSITIVE_TTL_SECONDS" if positive else "NEWS_SEARCH_NEGATIVE_TTL_SECONDS",
        600 if positive else 120,
        minimum=10,
        maximum=86_400,
    )
    maximum_entries = env_int("LINE_MODEL_RESEARCH_CACHE_MAX_ENTRIES", 256, minimum=8, maximum=2048)
    with _CACHE_LOCK:
        _CACHE[key] = (now_monotonic + ttl, copy.deepcopy(result))
        _CACHE.move_to_end(key)
        while len(_CACHE) > maximum_entries:
            _CACHE.popitem(last=False)
    return result, "miss"


def _merge_events(
    model_facts: dict[str, Any],
    controlled_events: list[dict[str, Any]],
) -> dict[str, Any]:
    enriched = copy.deepcopy(model_facts)
    display = enriched.get("display")
    target = display if isinstance(display, dict) else enriched
    context = target.get("news_radar_context")
    context = dict(context) if isinstance(context, dict) else {}
    existing = [dict(item) for item in context.get("events") or [] if isinstance(item, dict)]
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in [*controlled_events, *existing]:
        identity = str(event.get("event_key") or event.get("url") or event.get("title") or "")
        fingerprint = hashlib.sha256(identity.strip().lower().encode("utf-8")).hexdigest()
        if not identity or fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged.append(event)
        if len(merged) >= 8:
            break
    if merged:
        context.update(
            {
                "available": True,
                "status": "unverified",
                "events": merged,
                "requires_primary_source_verification": True,
                "ready_for_referee": False,
                "can_override_main_status": False,
            }
        )
        target["news_radar_context"] = context
    return enriched


def enrich_shadow_model_facts_with_research(
    *,
    question: str,
    model_facts: dict[str, Any],
    requested_scopes: list[str] | tuple[str, ...],
    fetcher: Callable[..., dict[str, Any]] = fetch_controlled_news_metadata,
) -> dict[str, Any]:
    """Return detached facts plus deidentified retrieval metadata; write no DB rows."""

    started = time.monotonic()
    rollout = line_model_research_rollout()
    queries = build_controlled_research_queries(
        question=question,
        model_facts=model_facts,
        requested_scopes=requested_scopes,
    )
    if rollout != "shadow" or not queries:
        return {
            "model_facts": copy.deepcopy(model_facts),
            "summary": {
                "research_version": LINE_MODEL_RESEARCH_VERSION,
                "rollout": rollout,
                "status": "disabled" if rollout != "shadow" else "not_requested",
                "retrieval_mode": "cache_only",
                "cache_state": "not_used",
                "query_count": 0,
                "query_hashes": [],
                "event_count": 0,
                "verification_counts": {},
                "timeout_classes": [],
                "canonical_table_writes": 0,
                "raw_article_bodies_fetched": 0,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        }

    controlled_events: list[dict[str, Any]] = []
    cache_states: list[str] = []
    statuses: list[str] = []
    timeout_classes: list[str] = []
    raw_bodies = 0
    for query in queries:
        result, cache_state = _cached_fetch(
            query,
            fetcher=fetcher,
            now_monotonic=time.monotonic(),
        )
        cache_states.append(cache_state)
        statuses.append(str(result.get("status") or "unknown"))
        timeout_class = str(result.get("timeout_class") or "")
        if timeout_class:
            timeout_classes.append(timeout_class)
        raw_bodies += int(result.get("raw_article_bodies_fetched") or 0)
        controlled_events.extend(
            dict(item) for item in result.get("events") or [] if isinstance(item, dict)
        )
    merged_facts = _merge_events(model_facts, controlled_events)
    verification_counts: dict[str, int] = {}
    for event in controlled_events:
        state = str(event.get("verification_state") or "unverified")
        verification_counts[state] = verification_counts.get(state, 0) + 1
    cache_state = (
        "hit" if set(cache_states) == {"hit"}
        else "miss" if set(cache_states) == {"miss"}
        else "mixed"
    )
    return {
        "model_facts": merged_facts,
        "summary": {
            "research_version": LINE_MODEL_RESEARCH_VERSION,
            "rollout": rollout,
            "status": "ok" if controlled_events else ";".join(dict.fromkeys(statuses)),
            "retrieval_mode": "bounded_live" if "miss" in cache_states else "cache_only",
            "cache_state": cache_state,
            "query_count": len(queries),
            "query_hashes": [_cache_key(query) for query in queries],
            "event_count": len(controlled_events),
            "verification_counts": dict(sorted(verification_counts.items())),
            "timeout_classes": list(dict.fromkeys(timeout_classes)),
            "canonical_table_writes": 0,
            "raw_article_bodies_fetched": raw_bodies,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    }


def clear_line_model_research_cache() -> int:
    """Clear the process-local research cache and return the removed entry count."""

    with _CACHE_LOCK:
        removed = len(_CACHE)
        _CACHE.clear()
    return removed


def clear_line_model_research_cache_for_tests() -> None:
    clear_line_model_research_cache()
