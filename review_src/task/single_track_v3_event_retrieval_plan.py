from __future__ import annotations

"""Off-by-default execution composition for the currently implemented sources."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from adapter.controlled_news_research import fetch_controlled_news_metadata
from adapter.official_company_events import fetch_official_company_events
from adapter.single_track_v3_official_retrieval import (
    OFFICIAL_COMPANY_RETRIEVAL_SOURCE_ID,
    build_official_company_retrieval_fetcher,
)
from adapter.single_track_v3_federal_reserve_retrieval import (
    FEDERAL_RESERVE_SOURCE_ID,
    build_federal_reserve_retrieval_fetcher,
)
from adapter.single_track_v3_taiwan_policy_retrieval import (
    TAIWAN_POLICY_RETRIEVAL_SOURCE_ID,
    build_taiwan_policy_retrieval_fetcher,
)
from adapter.single_track_v3_us_policy_retrieval import (
    US_POLICY_SANCTIONS_RETRIEVAL_SOURCE_ID,
    build_us_policy_sanctions_retrieval_fetcher,
)
from core.single_track_v3_event_source_plan import (
    EVENT_QUERY_PLAN_VERSION,
    EVENT_SOURCE_PLAN_VERSION,
    build_bounded_event_radar_queries,
)
from task.single_track_v3_retrieval_worker import RetrievalSourceSpec


@dataclass(frozen=True)
class EventRetrievalExecutionPlan:
    """Runtime-only plan; callables are intentionally never persisted as evidence."""

    entity_refs: tuple[str, ...]
    queries: tuple[str, ...]
    query_plan_version: str
    source_plan_version: str
    source_specs: tuple[RetrievalSourceSpec, ...]
    zero_model_calls: int = 0


def build_event_retrieval_execution_plan(
    entity: Mapping[str, Any],
    *,
    official_fetcher: Callable[[], Mapping[str, Any]] = fetch_official_company_events,
    radar_fetcher: Callable[[str], Mapping[str, Any]] = fetch_controlled_news_metadata,
    taiwan_policy_fetcher: Callable[[], Mapping[str, Any]] | None = None,
    federal_reserve_http_get: Callable[..., Any] | None = None,
    us_policy_http_get: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> EventRetrievalExecutionPlan:
    """Bind official MOPS plus discovery-only Radar to one frozen entity/query plan."""

    query_plan = build_bounded_event_radar_queries(entity)
    stock_code = str(query_plan["stock_code"])
    official = build_official_company_retrieval_fetcher(
        [stock_code],
        fetcher=official_fetcher,
        clock=clock,
    )
    taiwan_policy = build_taiwan_policy_retrieval_fetcher(
        [stock_code],
        query_plan["policy_relevance_terms"],
        fetcher=taiwan_policy_fetcher,
        clock=clock,
    )
    federal_reserve_kwargs = {"clock": clock}
    if federal_reserve_http_get is not None:
        federal_reserve_kwargs["http_get"] = federal_reserve_http_get
    federal_reserve = build_federal_reserve_retrieval_fetcher(
        [stock_code],
        **federal_reserve_kwargs,
    )
    us_policy_kwargs = {"clock": clock}
    if us_policy_http_get is not None:
        us_policy_kwargs["http_get"] = us_policy_http_get
    us_policy = build_us_policy_sanctions_retrieval_fetcher(
        [stock_code],
        **us_policy_kwargs,
    )
    return EventRetrievalExecutionPlan(
        entity_refs=(stock_code,),
        queries=tuple(str(value) for value in query_plan["queries"]),
        query_plan_version=EVENT_QUERY_PLAN_VERSION,
        source_plan_version=EVENT_SOURCE_PLAN_VERSION,
        source_specs=(
            RetrievalSourceSpec(
                source_id=OFFICIAL_COMPANY_RETRIEVAL_SOURCE_ID,
                scope_key="official_company",
                fetcher=official,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            ),
            RetrievalSourceSpec(
                source_id=FEDERAL_RESERVE_SOURCE_ID,
                scope_key="us_policy_geopolitics",
                fetcher=federal_reserve,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            ),
            RetrievalSourceSpec(
                source_id=US_POLICY_SANCTIONS_RETRIEVAL_SOURCE_ID,
                scope_key="us_policy_geopolitics",
                fetcher=us_policy,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            ),
            RetrievalSourceSpec(
                source_id=TAIWAN_POLICY_RETRIEVAL_SOURCE_ID,
                scope_key="taiwan_policy",
                fetcher=taiwan_policy,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            ),
            RetrievalSourceSpec(
                source_id="controlled_news_metadata",
                scope_key="company_industry_news",
                fetcher=radar_fetcher,
                query_mode="per_query",
                authority_tier="news_radar",
            ),
        ),
    )
