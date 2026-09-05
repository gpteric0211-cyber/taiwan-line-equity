from __future__ import annotations

import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_event_source_plan import (  # noqa: E402
    EVENT_QUERY_PLAN_VERSION,
    EVENT_SOURCE_PLAN_VERSION,
    REQUIRED_EVENT_SCAN_SCOPES,
    SOURCE_DESCRIPTORS,
    build_bounded_event_radar_queries,
    evaluate_event_source_coverage,
    merge_event_source_statuses,
    source_statuses_from_retrieval_attempts,
)
from services.event_safety_scan_service import REQUIRED_SCAN_SCOPES  # noqa: E402


def _complete_sources() -> dict[str, str]:
    return {
        str(item["source_key"]): "no_results"
        for item in SOURCE_DESCRIPTORS
    }


def test_source_plan_is_the_single_scan_scope_contract() -> None:
    assert EVENT_SOURCE_PLAN_VERSION == "SingleTrackV3EventSourcePlanV3"
    assert REQUIRED_SCAN_SCOPES is REQUIRED_EVENT_SCAN_SCOPES
    assert set(REQUIRED_EVENT_SCAN_SCOPES) == {
        "official_company",
        "taiwan_policy",
        "us_policy_geopolitics",
        "company_industry_news",
        "related_overseas_price_reaction",
        "us_market_taiwan_night",
        "dilution_valuation_risk",
    }


def test_complete_source_plan_allows_high_confidence_without_inventing_events() -> None:
    result = evaluate_event_source_coverage(_complete_sources())

    assert result["complete"] is True
    assert result["high_confidence_allowed"] is True
    assert result["incomplete_scopes"] == []
    assert set(result["scope_statuses"].values()) == {"ok"}


def test_radar_success_does_not_cover_missing_official_or_market_scopes() -> None:
    result = evaluate_event_source_coverage(
        {"controlled_news_metadata": "no_results"}
    )

    assert result["scope_statuses"]["company_industry_news"] == "ok"
    assert result["scope_statuses"]["official_company"] == "missing"
    assert result["scope_statuses"]["us_policy_geopolitics"] == "missing"
    assert result["scope_statuses"]["related_overseas_price_reaction"] == "missing"
    assert result["complete"] is False
    assert result["high_confidence_allowed"] is False


def test_one_missing_us_official_source_keeps_geopolitical_scope_incomplete() -> None:
    sources = _complete_sources()
    sources.pop("us_export_control_official")
    sources["us_government_policy_official"] = "ok"
    sources["us_federal_reserve_official"] = "ok"
    sources["us_sanctions_official"] = "ok"
    sources["geopolitical_official_or_licensed"] = "ok"

    result = evaluate_event_source_coverage(sources)

    assert result["scope_statuses"]["us_policy_geopolitics"] == "missing"
    assert result["incomplete_scopes"] == ["us_policy_geopolitics"]


def test_timeout_is_preserved_as_scope_failure_class() -> None:
    sources = _complete_sources()
    sources["mops_listed_disclosures"] = "timeout"

    result = evaluate_event_source_coverage(sources)

    assert result["scope_statuses"]["official_company"] == "timeout"
    assert result["scope_statuses"]["dilution_valuation_risk"] == "timeout"


def test_query_plan_uses_only_structured_entity_terms_and_is_bounded() -> None:
    plan = build_bounded_event_radar_queries(
        {
            "stock_code": "2454",
            "registry_version": "StockEntityRegistryV1",
            "official_names": ["聯發科技股份有限公司", "MediaTek Inc."],
            "audited_aliases": ["聯發科", "MediaTek"],
            "industry_terms": ["semiconductor", "IC design"],
            "related_symbols": ["SOXX", "QCOM"],
        }
    )

    assert plan["query_plan_version"] == EVENT_QUERY_PLAN_VERSION
    assert plan["stock_code"] == "2454"
    assert plan["query_count"] == 4
    assert "聯發科技股份有限公司" in plan["policy_relevance_terms"]
    assert "IC design" in plan["policy_relevance_terms"]
    assert all(len(query) <= 512 for query in plan["queries"])
    assert all("2454" in query or "SOXX" in query for query in plan["queries"])
    assert plan["discovery_only"] is True
    assert plan["can_verify_event"] is False
    assert plan["can_override_referee"] is False


def test_query_plan_rejects_missing_official_identity_instead_of_model_guessing() -> None:
    with pytest.raises(ValueError, match="official company name"):
        build_bounded_event_radar_queries(
            {
                "stock_code": "2454",
                "registry_version": "StockEntityRegistryV1",
                "official_names": [],
            }
        )


def test_durable_attempts_map_to_logical_sources_without_hiding_fallback_failure() -> None:
    statuses = source_statuses_from_retrieval_attempts(
        [
            {
                "source_id": "mops_listed_disclosures",
                "outcome": "success",
                "source_status": "ok",
                "failure_class": "none",
            },
            {
                "source_id": "MOEA_NEWS",
                "outcome": "no_results",
                "source_status": "no_results",
                "failure_class": "none",
            },
            {
                "source_id": "mops_otc_disclosures",
                "outcome": "no_results",
                "source_status": "no_results",
                "failure_class": "none",
            },
            {
                "source_id": "GDELT_DOC_INDEX",
                "outcome": "failed",
                "source_status": "timeout",
                "failure_class": "timeout",
            },
            {
                "source_id": "GOOGLE_NEWS_RSS_INDEX",
                "outcome": "success",
                "source_status": "ok",
                "failure_class": "none",
            },
        ]
    )

    assert statuses == {
        "controlled_news_metadata": "timeout",
        "mops_listed_disclosures": "ok",
        "mops_otc_disclosures": "no_results",
        "taiwan_government_policy_official": "no_results",
    }
    coverage = evaluate_event_source_coverage(statuses)
    assert coverage["scope_statuses"]["official_company"] == "ok"
    assert coverage["scope_statuses"]["company_industry_news"] == "timeout"
    assert coverage["scope_statuses"]["taiwan_policy"] == "ok"
    assert coverage["complete"] is False

    with pytest.raises(ValueError, match="four-digit official code"):
        build_bounded_event_radar_queries(
            {
                "stock_code": "MediaTek",
                "registry_version": "StockEntityRegistryV1",
                "official_names": ["聯發科技股份有限公司"],
            }
        )


def test_us_official_attempts_cover_policy_and_sanctions_but_not_export_control() -> None:
    statuses = source_statuses_from_retrieval_attempts(
        [
            {
                "source_id": "US_TREASURY_PRESS_RELEASE_INDEX",
                "outcome": "success",
                "source_status": "ok",
                "failure_class": "none",
            },
            {
                "source_id": "OFAC_RECENT_ACTIONS_INDEX",
                "outcome": "no_results",
                "source_status": "no_results",
                "failure_class": "none",
            },
            {
                "source_id": "FEDERAL_RESERVE_MONETARY_POLICY_RSS",
                "outcome": "success",
                "source_status": "ok",
                "failure_class": "none",
            },
        ]
    )

    assert statuses == {
        "us_federal_reserve_official": "ok",
        "us_government_policy_official": "ok",
        "us_sanctions_official": "no_results",
    }
    coverage = evaluate_event_source_coverage(statuses)
    evidence = coverage["scope_evidence"]["us_policy_geopolitics"]
    assert evidence["source_statuses"]["us_export_control_official"] == "missing"
    assert evidence["source_statuses"]["geopolitical_official_or_licensed"] == "missing"
    assert coverage["scope_statuses"]["us_policy_geopolitics"] == "missing"
    assert coverage["complete"] is False


def test_snapshot_receipt_statuses_merge_without_hiding_stale_or_unavailable() -> None:
    merged = merge_event_source_statuses(
        {"mops_listed_disclosures": "ok"},
        {
            "related_overseas_price_snapshot": "ok",
            "us_market_snapshot": "ok",
            "taifex_night_snapshot": "stale",
            "dilution_valuation_snapshot": "unavailable",
        },
        {"taifex_night_snapshot": "ok"},
    )

    assert merged["taifex_night_snapshot"] == "stale"
    assert merged["dilution_valuation_snapshot"] == "unavailable"
    coverage = evaluate_event_source_coverage(merged)
    assert coverage["scope_statuses"]["us_market_taiwan_night"] == "stale"
    assert coverage["scope_statuses"]["dilution_valuation_risk"] == "unavailable"
    assert coverage["complete"] is False
