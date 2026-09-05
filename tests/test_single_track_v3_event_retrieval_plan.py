from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_event_source_plan import (  # noqa: E402
    EVENT_QUERY_PLAN_VERSION,
    EVENT_SOURCE_PLAN_VERSION,
)
from task.single_track_v3_event_retrieval_plan import (  # noqa: E402
    build_event_retrieval_execution_plan,
)


TPE = ZoneInfo("Asia/Taipei")


def _entity() -> dict:
    return {
        "stock_code": "2454",
        "registry_version": "StockEntityRegistryV1",
        "official_names": ["聯發科技股份有限公司"],
        "audited_aliases": ["聯發科"],
        "industry_terms": ["IC design"],
        "related_symbols": ["QCOM"],
    }


def test_execution_plan_binds_official_and_radar_without_model_or_network_calls() -> None:
    official_calls = 0
    radar_calls = 0
    policy_calls = 0
    federal_reserve_calls = 0
    us_policy_calls = 0

    def official_fetcher() -> dict:
        nonlocal official_calls
        official_calls += 1
        return {"ok": True, "status": "ok", "items": [], "sources": []}

    def radar_fetcher(_: str) -> dict:
        nonlocal radar_calls
        radar_calls += 1
        return {"ok": True, "status": "no_results", "events": []}

    def policy_fetcher() -> dict:
        nonlocal policy_calls
        policy_calls += 1
        return {"ok": True, "status": "no_results", "items": [], "sources": []}

    def federal_reserve_http_get(*_args, **_kwargs):
        nonlocal federal_reserve_calls
        federal_reserve_calls += 1
        raise AssertionError("execution-plan construction must not perform HTTP")

    def us_policy_http_get(*_args, **_kwargs):
        nonlocal us_policy_calls
        us_policy_calls += 1
        raise AssertionError("execution-plan construction must not perform HTTP")

    plan = build_event_retrieval_execution_plan(
        _entity(),
        official_fetcher=official_fetcher,
        radar_fetcher=radar_fetcher,
        taiwan_policy_fetcher=policy_fetcher,
        federal_reserve_http_get=federal_reserve_http_get,
        us_policy_http_get=us_policy_http_get,
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )

    assert official_calls == 0
    assert radar_calls == 0
    assert policy_calls == 0
    assert federal_reserve_calls == 0
    assert us_policy_calls == 0
    assert plan.entity_refs == ("2454",)
    assert plan.query_plan_version == EVENT_QUERY_PLAN_VERSION
    assert plan.source_plan_version == EVENT_SOURCE_PLAN_VERSION
    assert plan.zero_model_calls == 0
    assert len(plan.queries) == 4
    assert [spec.source_id for spec in plan.source_specs] == [
        "official_company_mops_daily",
        "FEDERAL_RESERVE_MONETARY_POLICY_RSS",
        "us_policy_sanctions_official",
        "taiwan_government_policy_official",
        "controlled_news_metadata",
    ]
    assert [spec.query_mode for spec in plan.source_specs] == [
        "once_per_run",
        "once_per_run",
        "once_per_run",
        "once_per_run",
        "per_query",
    ]
    assert [spec.authority_tier for spec in plan.source_specs] == [
        "canonical_official",
        "canonical_official",
        "canonical_official",
        "canonical_official",
        "news_radar",
    ]
