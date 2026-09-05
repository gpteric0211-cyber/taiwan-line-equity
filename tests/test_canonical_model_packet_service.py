from __future__ import annotations

import copy
import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_model_contract import (  # noqa: E402
    ContextProfile,
    compact_packet_to_token_budget,
)
from core.line_model_validation import validate_model_analysis_v2  # noqa: E402
from services.canonical_model_packet_service import (  # noqa: E402
    build_canonical_model_fact_packet_v2,
)


def _artifact() -> dict:
    return {
        "analysis_id": "analysis-1",
        "snapshot_id": "snapshot-1",
        "analysis_cutoff": "2026-09-01T13:45:00+08:00",
        "validity": "partial",
        "canonical_answer_text_hash": "answer-hash",
        "evidence_ids": ["canonical-close-evidence"],
        "event_ids": ["event-source-1"],
        "omissions": [{"scope": "institutional", "reason": "unavailable"}],
        "conflicts": [{"scope": "events", "reason": "source_conflict"}],
        "analysis": {
            "analysis_id": "analysis-1",
            "snapshot_id": "snapshot-1",
            "analysis_cutoff": "2026-09-01T13:45:00+08:00",
            "trade_date": "2026-09-01",
            "profile": "focused",
            "target_entities": [{"code": "2330", "name": "台積電"}],
            "comparison_set": [],
            "conversation_projection": {
                "active_stock": {"code": "2330"},
                "historical_claims": [
                    {"text": "上週看到 999 元", "can_replace_canonical_fact": False}
                ],
            },
            "entity_analyses": [
                {
                    "entity": {"code": "2330", "name": "台積電"},
                    "trade_date": "2026-09-01",
                    "ohlcv": {"close": 1200.0, "volume": 10_000_000},
                    "technical_ensemble": {
                        "status": "ok",
                        "overall_score": 0.35,
                        "family_scores": {"trend_score": 0.4, "momentum_score": 0.3},
                    },
                }
            ],
            "factor_scores": {"event_direction_score": None},
            "event_scan": {"coverage": {"official": "ok"}, "source_policy_version": "policy-v1"},
            "referee": {
                "decision_ready": True,
                "main_status": "觀察",
                "can_be_overridden_by_model": False,
            },
        },
    }


def test_canonical_packet_contains_exact_cutoff_context_image_omissions_and_event_binding() -> None:
    packet = build_canonical_model_fact_packet_v2(
        _artifact(),
        requested_scopes=["price", "technical", "events", "risk"],
        image_typed_observations={
            "timeframe": "日K",
            "indicators": [{"name": "RSI", "value": 42.6}],
        },
        event_records=[
            {
                "event_id": "event-source-1",
                "untrusted_text": "重大訊息",
                "publisher": "MOPS",
                "verification_state": "verified",
                "dedup_cluster": "cluster-1",
                "source_class": "licensed_secondary",
                "source_url": "https://example.com/event-source-1",
                "rights": {
                    "allow_model": True,
                    "allow_display": True,
                    "attribution_required": True,
                    "policy_version": "policy-v1",
                },
            }
        ],
    )

    assert packet["contract_version"] == "model-fact-packet-v2"
    assert packet["referee"]["immutable"] is True
    assert packet["request"]["analysis_cutoff"] == "2026-09-01T13:45:00+08:00"
    assert packet["target_entities"] == [{"code": "2330", "name": "台積電"}]
    assert packet["conversation_projection"]["historical_claims"][0]["can_replace_canonical_fact"] is False
    assert packet["image_typed_observations"]["estimated"] is True
    assert packet["image_typed_observations"]["can_enter_referee"] is False
    assert packet["omissions"][0]["scope"] == "institutional"
    assert packet["conflicts"][0]["scope"] == "events"
    assert packet["events"][0]["source_event_id"] == "event-source-1"
    assert packet["events"][0]["dedup_cluster"] == "cluster-1"
    assert packet["events"][0]["allow_display"] is True
    assert packet["events"][0]["citation_required"] is True
    assert all(fact["fact_id"].startswith("F") for fact in packet["facts"])
    assert any(fact["domain"] == "image_observation" and fact["quality"] == "estimated" for fact in packet["facts"])
    assert all("999" not in str(fact["value"]) for fact in packet["facts"])
    assert len(packet["packet_digest"]) == 64


def test_event_with_unknown_or_non_displayable_rights_becomes_explicit_limitation() -> None:
    packet = build_canonical_model_fact_packet_v2(
        _artifact(),
        requested_scopes=["events"],
        event_records=[
            {
                "event_id": "event-source-1",
                "untrusted_text": "僅 metadata",
                "publisher": "unknown",
                "source_url": "https://example.com/metadata-only",
                "rights": "metadata_only",
            }
        ],
    )

    assert packet["events"] == []
    scope_ids = packet["render_contract"]["scope_evidence_ids"]["events"]
    assert len(scope_ids) == 1
    fallback = next(item for item in packet["facts"] if item["fact_id"] == scope_ids[0])
    assert fallback["domain"] == "events"
    assert fallback["quality"] == "unavailable"
    assert fallback["use_scope"] == ["limitation"]


def test_delivery_channel_does_not_change_packet_or_digest() -> None:
    web = _artifact()
    line = copy.deepcopy(web)
    web["delivery_channel"] = "web"
    line["delivery_channel"] = "line"

    web_packet = build_canonical_model_fact_packet_v2(web)
    line_packet = build_canonical_model_fact_packet_v2(line)

    assert web_packet == line_packet
    assert web_packet["packet_digest"] == line_packet["packet_digest"]


def test_permanent_corporate_action_ratio_reaches_shared_model_packet() -> None:
    artifact = _artifact()
    artifact["analysis"]["entity_analyses"][0]["canonical_sections"] = {
        "recommendation_safety": {
            "available": True,
            "status": "corporate_action_window",
            "trade_date": "2026-09-01",
            "calculated_at": "2026-09-03T10:38:26+08:00",
            "auto_entry_eligible": False,
            "hard_blocked": False,
            "referee_cap": "warning",
            "blocking_reasons": ["公司行動調整期間"],
            "warnings": ["技術價格基準須配合除權調整"],
            "liquidity": {
                "status": "ok",
                "observed_days": 20,
                "required_days": 20,
                "average_turnover_twd": 2_000_000_000,
                "median_turnover_twd": 1_800_000_000,
                "minimum_twd": 30_000_000,
                "normal_twd": 100_000_000,
            },
            "trading_restriction": {
                "status": "ok",
                "active_types": [],
                "labels": [],
            },
            "corporate_action": {
                "status": "active_window",
                "confirmed": True,
                "action_date": "2026-09-02",
                "action_type": "right",
                "adjustment_method": "bonus_share_distribution",
                "stock_distribution_ratio": 1.98279460,
                "ratio_unit": "new_shares_per_existing_share",
                "share_count_factor": 2.98279460,
                "pre_event_price_multiplier": 0.335256071605,
                "verification_status": "official_verified",
                "source_id": "TWSE_TWT48U",
                "directional_weight_eligible": False,
            },
            "company_size": {
                "status": "ok",
                "data_date": "2026-08-31",
                "age_days": 1,
                "paid_in_capital_twd": 2_500_000_000,
                "estimated_market_cap_twd": 1_000_000_000_000,
                "minimum_paid_in_capital_twd": 1_000_000_000,
                "minimum_market_cap_twd": 5_000_000_000,
            },
            "can_override_main_status": False,
            "version": "recommendation-safety-v3",
        }
    }

    packet = build_canonical_model_fact_packet_v2(
        artifact,
        requested_scopes=["risk"],
    )
    by_field = {
        fact["field"]: fact
        for fact in packet["facts"]
        if fact["domain"] == "recommendation_safety"
    }

    ratio = by_field["corporate_action.stock_distribution_ratio"]
    assert ratio["value"] == 1.98279460
    assert ratio["unit"] == "ratio"
    assert "numeric_claim" in ratio["use_scope"]
    assert by_field["corporate_action.action_date"]["value"] == "2026-09-02"
    assert by_field["corporate_action.share_count_factor"]["unit"] == "ratio"
    assert by_field["corporate_action.pre_event_price_multiplier"]["unit"] == "ratio"
    for field in (
        "corporate_action.action_date",
        "corporate_action.action_type",
        "corporate_action.stock_distribution_ratio",
        "corporate_action.ratio_unit",
        "corporate_action.share_count_factor",
        "corporate_action.pre_event_price_multiplier",
    ):
        assert sum(fact["field"] == field for fact in packet["facts"]) == 1

    compacted = compact_packet_to_token_budget(
        packet,
        system_prompt="你是台灣股票研究助理。" * 60,
        question="請說明除權對價格基準的影響。" * 30,
        profile=ContextProfile(
            name="corporate-action-compaction-test",
            prompt_token_cap=2_600,
            facts_total=96,
            facts_per_scope=96,
            events_total=0,
            events_per_scope=0,
            minimum_context=4_096,
            release_state="audit_only",
        ),
        actual_context=4_096,
        reserved_output_tokens=256,
    )
    compacted_fields = {
        fact["field"]
        for fact in compacted.packet["facts"]
        if fact["domain"] == "recommendation_safety"
    }
    assert compacted.removed_fact_ids
    assert {
        "corporate_action.action_date",
        "corporate_action.action_type",
        "corporate_action.stock_distribution_ratio",
        "corporate_action.ratio_unit",
        "corporate_action.share_count_factor",
        "corporate_action.pre_event_price_multiplier",
    } <= compacted_fields


def test_validator_rejects_referee_override_and_unbound_old_conversation_number() -> None:
    packet = build_canonical_model_fact_packet_v2(_artifact())
    override = {
        "contract_version": "model-analysis-v2",
        "main_status": "買進",
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": "資料可用。",
                "evidence_ids": [],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "research_limitations": [],
        "used_event_ids": [],
    }
    historical_number = copy.deepcopy(override)
    historical_number.pop("main_status")
    historical_number["explanation_blocks"][0]["text_template"] = "目前價格是 999 元。"

    assert validate_model_analysis_v2(override, packet).reason_codes == (
        "referee_override_attempt",
    )
    rejected = validate_model_analysis_v2(historical_number, packet)
    assert rejected.passed is False
    assert "ungrounded_numeric_or_date_claim" in rejected.reason_codes


def test_cost_fact_accepts_offset_aware_artifact_cutoff_without_changing_daily_as_of() -> None:
    packet = {
        "request": {
            "depth": "focused",
            "scopes": ["institutional"],
            "analysis_cutoff": "2026-09-01T14:00:00+08:00",
        },
        "facts": [
            {
                "fact_id": "F001",
                "domain": "institutional_context",
                "field": "canonical_costs.foreign_estimated.sample_days",
                "value": 18,
                "unit": "count",
                "currency": None,
                "period": "daily",
                "trade_date": "2026-08-28",
                "as_of": "2026-08-28",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            }
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": "外資估算樣本天數為{{F001}}。",
                "evidence_ids": ["F001"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "research_limitations": [],
        "used_event_ids": [],
    }

    assert validate_model_analysis_v2(output, packet).reason_codes == ("pass",)


def _corporate_action_validation_packet() -> dict:
    common = {
        "domain": "recommendation_safety",
        "unit": "ratio",
        "currency": None,
        "period": "effective_date",
        "trade_date": "2026-09-02",
        "as_of": "2026-09-02",
        "authority_tier": "canonical_db",
        "quality": "ok",
        "use_scope": ["explanation", "numeric_claim"],
    }
    return {
        "request": {
            "depth": "focused",
            "scopes": ["risk"],
            "analysis_cutoff": "2026-09-03T10:38:26+08:00",
        },
        "facts": [
            {
                **common,
                "fact_id": "F901",
                "field": "corporate_action.stock_distribution_ratio",
                "value": 1.98279460,
            },
            {
                **common,
                "fact_id": "F902",
                "field": "corporate_action.share_count_factor",
                "value": 2.98279460,
            },
            {
                **common,
                "fact_id": "F903",
                "field": "corporate_action.pre_event_price_multiplier",
                "value": 0.335256071605,
            },
        ],
        "events": [],
    }


def _corporate_action_validation_output(text_template: str) -> dict:
    return {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": text_template,
                "evidence_ids": ["F901", "F902", "F903"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "research_limitations": [],
        "used_event_ids": [],
    }


def test_validator_accepts_evidence_bound_corporate_action_ratio_labels() -> None:
    result = validate_model_analysis_v2(
        _corporate_action_validation_output(
            "無償配股率為{{F901}}。股數倍率為{{F902}}。價格基準倍率為{{F903}}。"
        ),
        _corporate_action_validation_packet(),
    )

    assert result.reason_codes == ("pass",)
    assert result.rendered_blocks == (
        "無償配股率為1.9827946 倍。股數倍率為2.9827946 倍。"
        "價格基準倍率為0.335256071605 倍。",
    )


def test_validator_rejects_unbound_corporate_action_number_even_when_fact_is_cited() -> None:
    output = _corporate_action_validation_output(
        "無償配股率為1.98279460倍。股數倍率為{{F902}}。價格基準倍率為{{F903}}。"
    )
    result = validate_model_analysis_v2(output, _corporate_action_validation_packet())

    assert result.passed is False
    assert "ungrounded_numeric_or_date_claim" in result.reason_codes
    assert result.rendered_blocks == ()
