from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_model_contract import (  # noqa: E402
    CONTEXT_PROFILES,
    ContextProfile,
    MODEL_FACT_PACKET_VERSION,
    TOKEN_ESTIMATOR_VERSION,
    build_model_fact_packet_v2,
    compact_packet_to_token_budget,
    conservative_prompt_token_estimate,
    preflight_prompt_tokens,
    select_context_profile,
)
from core.line_model_validation import (  # noqa: E402
    deterministic_limitation_placeholder_repair,
    validate_model_analysis_v2,
)
from adapter.qwen_local import QwenChatResult  # noqa: E402
from services import line_model_shadow_service  # noqa: E402
from services.line_model_shadow_service import (  # noqa: E402
    line_model_shadow_readiness,
    observe_line_model_shadow,
)


def _model_facts() -> dict[str, object]:
    display = {
        "status": "ok",
        "code": "2330",
        "stock": {"name": "台積電", "market": "listed"},
        "trade_date": "2026-08-21",
        "official_ohlcv": {
            "available": True,
            "official_trusted": True,
            "open": "2375",
            "high": "2410",
            "low": "2365",
            "close": "2410",
            "volume_shares": "18922480",
            "source": "must-not-escape",
        },
        "technical": {
            "available": True,
            "decision_ready": True,
            "rsi": {"rsi14": "53.37"},
            "moving_averages": {"ma20": "2370", "ma60": "2350"},
        },
        "valuation": {
            "available": False,
            "status": "unavailable",
            "reason": "not published",
        },
        "official_event_context": {
            "available": True,
            "events": [
                {
                    "title": "重大訊息" * 50,
                    "publisher": "公開資訊觀測站",
                    "event_date": "2026-08-21",
                }
            ],
        },
        "referee": {
            "decision_ready": True,
            "main_status": "中性",
            "main_reasons": ["指標多空交錯"],
            "version": "referee-v1",
            "can_be_overridden_by_model": False,
        },
    }
    return {
        **display,
        "display": display,
        "answer_contract": {
            "analysis_depth": "comprehensive",
            "requested_focus": "overview",
        },
        "conversation": {
            "recent_user_questions": ["我的成本是 2310"],
            "line_user_id": "U-private",
        },
        "verified_claims": ["官方收盤：2410"],
        "db_path": "C:/private/market.sqlite3",
    }


def test_16k_profiles_close_the_context_math_and_reject_old_oversize_prompt() -> None:
    profile = CONTEXT_PROFILES["comprehensive-16k-v1"]
    ready = preflight_prompt_tokens(
        11_000,
        profile=profile,
        actual_context=16_384,
        reserved_output_tokens=900,
    )
    oversized = preflight_prompt_tokens(
        19_627,
        profile=profile,
        actual_context=16_384,
        reserved_output_tokens=900,
    )

    assert ready.safety_margin == 2_048
    assert ready.context_prompt_capacity == 13_436
    assert ready.effective_prompt_budget == 11_000
    assert ready.ready is True
    assert oversized.ready is False
    assert oversized.reason == "estimated_prompt_exceeds_effective_budget"


def test_32k_candidate_is_never_selected_implicitly() -> None:
    automatic = select_context_profile("comprehensive", 32_768)
    explicit = select_context_profile(
        "comprehensive",
        32_768,
        requested_profile="comprehensive-32k-candidate-v1",
    )
    unavailable = select_context_profile(
        "comprehensive",
        16_384,
        requested_profile="comprehensive-32k-candidate-v1",
    )

    assert automatic.profile.name == "comprehensive-16k-v1"
    assert explicit.profile.name == "comprehensive-32k-candidate-v1"
    assert explicit.profile.release_state == "candidate"
    assert unavailable.profile.name == "comprehensive-16k-v1"
    assert unavailable.fallback_reason == "candidate_context_not_available"


def test_packet_is_typed_bounded_and_excludes_conversation_and_internal_provenance() -> None:
    facts = _model_facts()
    built = build_model_fact_packet_v2(
        facts,
        focus="overview",
        depth="comprehensive",
        profile=CONTEXT_PROFILES["comprehensive-16k-v1"],
    )
    packet = built.packet
    serialized = json.dumps(packet, ensure_ascii=False)

    assert packet["contract_version"] == MODEL_FACT_PACKET_VERSION
    assert packet["referee"] == {
        "version": "referee-v1",
        "main_status": "中性",
        "reasons": ["指標多空交錯"],
        "decision_ready": True,
        "immutable": True,
    }
    assert len(packet["facts"]) <= 96
    assert len(packet["events"]) <= 8
    assert len(packet["events"][0]["title"]) == 160
    assert packet["events"][0]["verification_state"] == "primary_verified"
    assert packet["events"][0]["untrusted_text"] is True
    assert "我的成本" not in serialized
    assert "U-private" not in serialized
    assert "market.sqlite3" not in serialized
    assert "must-not-escape" not in serialized
    assert "verified_claims" not in serialized
    assert all(item["fact_id"].startswith("F") for item in packet["facts"])
    close = next(item for item in packet["facts"] if item["field"] == "close")
    assert close["value"] == "2410"
    assert close["unit"] == "TWD"
    assert close["currency"] == "TWD"
    assert close["quality"] == "ok"
    assert close["use_scope"] == ["explanation", "numeric_claim"]
    assert close["fact_id"] in packet["render_contract"]["placeholder_eligible_fact_ids"]
    assert packet["events"][0]["event_id"] in packet["render_contract"]["event_evidence_ids"]
    assert packet["render_contract"]["relative_valuation_claim_allowed"] is False


def test_missing_fundamentals_can_only_support_a_limitation() -> None:
    facts = _model_facts()
    facts["display"] = {
        "trade_date": "2026-08-21",
        "referee": facts["referee"],
        "external_event_context": {"available": True, "events": []},
    }
    built = build_model_fact_packet_v2(
        facts,
        focus="fundamentals",
        depth="focused",
        profile=CONTEXT_PROFILES["focused-16k-v1"],
    )

    missing = next(item for item in built.packet["facts"] if item["domain"] == "fundamentals")
    assert missing["quality"] == "unavailable"
    assert missing["availability_reason"] == "source_not_supported"
    assert missing["use_scope"] == ["limitation"]
    assert missing["fact_id"] in built.packet["render_contract"]["limitation_only_fact_ids"]
    assert missing["fact_id"] not in built.packet["render_contract"]["placeholder_eligible_fact_ids"]


def test_render_contract_maps_each_requested_scope_to_bounded_evidence_ids() -> None:
    built = build_model_fact_packet_v2(
        _model_facts(),
        focus="overview",
        depth="comprehensive",
        profile=CONTEXT_PROFILES["comprehensive-16k-v1"],
        requested_scopes=["technical", "fundamentals", "current_news"],
    )

    scope_ids = built.packet["render_contract"]["scope_evidence_ids"]
    fact_ids = {str(item["fact_id"]) for item in built.packet["facts"]}
    event_ids = {str(item["event_id"]) for item in built.packet["events"]}

    assert set(scope_ids) == {"technical", "fundamentals", "current_news"}
    assert scope_ids["technical"]
    assert scope_ids["fundamentals"]
    assert scope_ids["current_news"]
    assert all(len(values) <= 8 for values in scope_ids.values())
    assert set(scope_ids["technical"]).issubset(fact_ids)
    assert set(scope_ids["fundamentals"]).issubset(fact_ids)
    assert set(scope_ids["current_news"]).issubset(fact_ids | event_ids)


def test_deterministic_repair_neutralizes_only_denied_relative_valuation_claim() -> None:
    packet = {
        "request": {"depth": "focused", "scopes": ["valuation"]},
        "facts": [
            {
                "fact_id": "F001",
                "domain": "valuation",
                "field": "pe_ratio",
                "value": "28.05",
                "unit": "ratio",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
            {
                "fact_id": "F002",
                "domain": "valuation",
                "field": "relative_value_assessment",
                "value": "unavailable_without_baseline",
                "authority_tier": "canonical_db",
                "quality": "unavailable",
                "use_scope": ["limitation"],
            },
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "inference",
                "text_template": "本益比為{{F001}}；缺乏比較基準，無法判斷高估、低估或溢價折價。",
                "evidence_ids": ["F001", "F002"],
                "uncertainty": "high",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    raw = validate_model_analysis_v2(output, packet)
    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)
    final = validate_model_analysis_v2(repaired or {}, packet)

    assert raw.passed is False
    assert "relative_valuation_without_baseline" in raw.reason_codes
    assert repaired is not None
    assert repaired["explanation_blocks"][0]["block_type"] == "limitation"
    assert "高估" not in repaired["explanation_blocks"][0]["text_template"]
    assert "溢價" not in repaired["explanation_blocks"][0]["text_template"]
    assert "neutralized_denied_relative_valuation_terms" in codes
    assert "reclassified_limitation_evidence_block" in codes
    assert final.passed is True


def test_deterministic_repair_removes_only_extra_limitation_block() -> None:
    packet = {
        "request": {"depth": "comprehensive", "scopes": ["technical"]},
        "facts": [
            {
                "fact_id": "F001",
                "domain": "technical",
                "field": "trend",
                "value": "偏多",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation"],
            }
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "fact" if index < 5 else "limitation",
                "text_template": f"技術證據說明{chr(0x4E00 + index)}。",
                "evidence_ids": ["F001"],
                "uncertainty": "medium",
                "conditions": [],
            }
            for index in range(6)
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)

    assert repaired is not None
    assert len(repaired["explanation_blocks"]) == 5
    assert "removed_redundant_limitation_block" in codes
    assert validate_model_analysis_v2(repaired, packet).passed is True


def test_deterministic_repair_generalizes_cited_event_id_and_required_day_threshold() -> None:
    packet = {
        "request": {"depth": "focused", "scopes": ["current_news"]},
        "facts": [
            {
                "fact_id": "F027",
                "domain": "institutional_context",
                "field": "required_days",
                "value": "60",
                "unit": "count",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            }
        ],
        "events": [{"event_id": "E001", "verification_state": "unverified"}],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "limitation",
                "text_template": "最近事件E001仍待驗證，資料需累積至60個交易日以上。",
                "evidence_ids": ["E001", "F027"],
                "uncertainty": "high",
                "conditions": ["需累積60個交易日以上"],
            }
        ],
        "missing_data": [],
        "used_event_ids": ["E001"],
        "research_limitations": ["官方資料未達60日"],
    }

    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)

    assert repaired is not None
    serialized = json.dumps(repaired, ensure_ascii=False)
    assert "E001" not in repaired["explanation_blocks"][0]["text_template"]
    assert "60" not in serialized
    assert "generalized_cited_event_id" in codes
    assert "generalized_required_day_threshold" in codes
    # required_days=60 is a threshold, not evidence of insufficient observed days;
    # generalizing the wording does not add missing-data evidence.
    result = validate_model_analysis_v2(repaired, packet)
    assert result.passed is False
    assert result.reason_codes == ("unverifiable_limitation_claim",)
    assert result.rendered_blocks == ()


def test_backend_renderer_removes_nonsemantic_decimal_zero() -> None:
    packet = {
        "request": {"depth": "focused", "scopes": ["price"]},
        "facts": [
            {
                "fact_id": "F001",
                "domain": "official_ohlcv",
                "field": "volume_shares",
                "value": "15025832.0",
                "unit": "shares",
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
                "text_template": "成交量為{{F001}}。",
                "evidence_ids": ["F001"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is True
    assert result.rendered_blocks == ("成交量為15025832 股。",)


def test_event_placeholder_repair_deletes_dangling_grammar_without_inventing_text() -> None:
    packet = {
        "request": {"depth": "focused", "scopes": ["current_news"]},
        "facts": [],
        "events": [
            {"event_id": "E001", "verification_state": "unverified"},
            {"event_id": "E002", "verification_state": "unverified"},
            {"event_id": "E003", "verification_state": "unverified"},
        ],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "limitation",
                "text_template": (
                    "近期事件包括供應鏈會議，分別為{{E001}}與{{E002}}。"
                    "最近一期營收資料為{{E003}}。事件均待驗證。"
                ),
                "evidence_ids": ["E001", "E002", "E003"],
                "uncertainty": "high",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": ["E001", "E002", "E003"],
        "research_limitations": [],
    }

    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)

    assert repaired is not None
    template = repaired["explanation_blocks"][0]["text_template"]
    assert template == "近期事件包括供應鏈會議。事件均待驗證。"
    assert "分別為" not in template
    assert "資料為" not in template
    assert "removed_event_placeholder_dangling_clause" in codes
    assert validate_model_analysis_v2(repaired, packet).passed is True


def test_shadow_observability_calls_candidate_and_is_deidentified(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("QWEN_CONTEXT_TOKENS", "16384")
    monkeypatch.setenv("QWEN_MAX_OUTPUT_TOKENS", "900")
    caplog.set_level(logging.INFO, logger="services.line_model_shadow_service")
    question = "請分析 2330 技術面，我的成本是 2310"
    model_output = json.dumps(
        {
            "contract_version": "model-analysis-v2",
            "explanation_blocks": [
                {
                    "block_type": "fact",
                    "text_template": text,
                    "evidence_ids": ["F006"],
                    "uncertainty": "low",
                    "conditions": [],
                }
                for text in (
                    "官方價格資料可用於說明趨勢位置。",
                    "目前僅沿用資料庫提供的價格事實。",
                    "條件情境仍以既有價格證據為界。",
                    "未提供的資訊不由模型自行補造。",
                )
            ],
            "missing_data": [],
            "used_event_ids": [],
            "research_limitations": [],
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr(
        line_model_shadow_service,
        "qwen_chat_detailed",
        lambda *_args, **_kwargs: QwenChatResult(
            text=model_output,
            finish_reason="stop",
            prompt_tokens=1234,
            completion_tokens=88,
            model="taiwan-stock-qwen",
        ),
    )
    monkeypatch.setattr(
        line_model_shadow_service,
        "_evidence_path",
        lambda: tmp_path / "shadow.jsonl",
    )

    result = observe_line_model_shadow(
        question=question,
        model_facts=_model_facts(),
        system_prompt="只能使用封包資料",
        focus="overview",
    )
    log_text = "\n".join(record.getMessage() for record in caplog.records)

    assert result["enabled"] is True
    assert result["candidate_model_called"] is True
    assert result["candidate_can_replace_reply"] is False
    assert result["preflight"]["estimator"] == TOKEN_ESTIMATOR_VERSION
    assert result["validator_result"] == "pass"
    assert result["prompt_token_count"] == 1234
    assert set(result["stage_timings_ms"]) == {
        "retrieval",
        "research_retrieval",
        "projection",
        "classification",
        "packet_build",
        "generation",
        "validation",
        "render",
    }
    assert result["stage_timings_ms"]["generation"] >= 0
    assert result["stage_timings_ms"]["validation"] >= 0
    assert result["stage_timings_ms"]["render"] >= 0
    assert "請分析 2330" not in log_text
    assert "2310" not in log_text
    assert "台積電" not in log_text
    assert "U-private" not in log_text
    evidence_text = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")
    assert question not in evidence_text
    assert "台積電" not in evidence_text


def test_shadow_is_off_by_default_and_readiness_is_non_authoritative(monkeypatch) -> None:
    monkeypatch.delenv("LINE_MODEL_V2_ROLLOUT", raising=False)

    result = observe_line_model_shadow(
        question="任意問題",
        model_facts=_model_facts(),
        system_prompt="system",
        focus="overview",
    )
    readiness = line_model_shadow_readiness()

    assert result == {"enabled": False, "rollout": "off"}
    assert readiness["shadow_enabled"] is False
    assert readiness["candidate_can_replace_reply"] is False
    assert readiness["candidate_model_called"] is False


def test_charclass_estimator_counts_cjk_and_fixed_template_reserve() -> None:
    assert conservative_prompt_token_estimate("台股", template_overhead=10) == 13


def test_compaction_preserves_event_for_explicit_news_scope() -> None:
    profile = ContextProfile(
        name="test-news-budget",
        prompt_token_cap=4_000,
        facts_total=96,
        facts_per_scope=96,
        events_total=8,
        events_per_scope=8,
        minimum_context=4_096,
        release_state="test",
    )
    packet = {
        "contract_version": "model-fact-packet-v2",
        "request": {"depth": "comprehensive", "scopes": ["technical", "current_news"]},
        "referee": {"immutable": True, "decision_ready": True},
        "facts": [
            {
                "fact_id": f"F{index:03d}",
                "domain": "technical",
                "field": f"detail_{index}",
                "value": "低優先細節" * 80,
                "quality": "ok",
                "use_scope": ["explanation"],
            }
            for index in range(1, 17)
        ],
        "events": [
            {
                "event_id": "E001",
                "event_context": "official_event_context",
                "title": "較新官方事件" * 30,
                "publisher_published_at": "2026-08-28",
                "verification_state": "unverified",
                "untrusted_text": True,
            },
            {
                "event_id": "E002",
                "event_context": "news_radar_context",
                "title": "較舊受控新聞" * 30,
                "publisher_published_at": "2026-08-27",
                "verification_state": "unverified",
                "untrusted_text": True,
            },
        ],
        "coverage": {"included_sections": ["technical", "news_radar_context"]},
    }

    result = compact_packet_to_token_budget(
        packet,
        system_prompt="只使用證據",
        question="技術面與最新新聞完整分析",
        profile=profile,
        actual_context=16_384,
        reserved_output_tokens=900,
    )

    assert result.original_preflight.ready is False
    assert result.compacted_preflight.ready is True
    assert result.packet["events"]
    assert result.packet["events"][-1]["event_id"] == "E002"
    fact_ids = {item["fact_id"] for item in result.packet["facts"]}
    event_ids = {item["event_id"] for item in result.packet["events"]}
    contract = result.packet["render_contract"]
    assert set(contract["placeholder_eligible_fact_ids"]).issubset(fact_ids)
    assert set(contract["limitation_only_fact_ids"]).issubset(fact_ids)
    assert set(contract["event_evidence_ids"]) == event_ids
    for rule in contract["output_rules"]["limitation_blocks_exact"]:
        assert set(rule["evidence_ids"]).issubset(fact_ids | event_ids)


def test_v2_contract_a_missing_data_allows_grounded_limitation() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "facts": [
            {
                "fact_id": "F001",
                "domain": "fundamentals",
                "field": "availability",
                "value": None,
                "authority_tier": "canonical_db",
                "quality": "unavailable",
                "availability_reason": "source_not_supported",
                "use_scope": ["limitation"],
            }
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "limitation",
                "text_template": "目前缺少可核對的基本面欄位，只能說明後續判讀條件。",
                "evidence_ids": ["F001"],
                "uncertainty": "high",
                "conditions": [],
            }
        ],
        "missing_data": ["fundamentals"],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is True


def test_v2_contract_date_placeholder_requires_canonical_date_claim() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "facts": [
            {
                "fact_id": "F002",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "value": "2026-08-28",
                "unit": None,
                "use_scope": ["explanation", "date_claim"],
            }
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": "資料日期為 {{F002}}。",
                "evidence_ids": ["F002"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is True
    assert result.rendered_blocks == ("資料日期為 2026-08-28。",)
    assert result.reason_codes == ("pass",)


def test_deterministic_repair_only_removes_limitation_placeholders() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "facts": [
            {
                "fact_id": "F001",
                "authority_tier": "canonical_db",
                "quality": "unavailable",
                "value": None,
                "use_scope": ["limitation"],
            }
        ],
        "events": [
            {"event_id": "E001", "verification_state": "unverified", "untrusted_text": True}
        ],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "limitation",
                "text_template": "支撐資料 {{F001}} 不可用，事件 {{E001}} 尚未驗證。",
                "evidence_ids": ["F001", "E001"],
                "uncertainty": "high",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": ["E001"],
        "research_limitations": [],
    }

    assert validate_model_analysis_v2(output, packet).passed is False
    repaired, repair_codes = deterministic_limitation_placeholder_repair(output, packet)

    assert repaired is not None
    assert repair_codes == ("removed_absence_placeholder", "removed_event_placeholder")
    assert "{{" not in repaired["explanation_blocks"][0]["text_template"]
    # F001 has no domain/field: model-authored support wording cannot identify
    # an otherwise untyped absence fact as support data.
    result = validate_model_analysis_v2(repaired, packet)
    assert result.passed is False
    assert result.reason_codes == ("unverifiable_limitation_claim",)
    assert result.rendered_blocks == ()


def test_v2_contract_b_rejects_unbound_numeric_claim_even_with_existing_evidence_id() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "facts": [
            {
                "fact_id": "F001",
                "domain": "fundamentals",
                "field": "availability",
                "value": None,
                "authority_tier": "canonical_db",
                "quality": "unavailable",
                "use_scope": ["limitation"],
            }
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "limitation",
                "text_template": "缺少資料，但每股盈餘是 88.88 元。",
                "evidence_ids": ["F001"],
                "uncertainty": "high",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is False
    assert "ungrounded_numeric_or_date_claim" in result.reason_codes
    assert result.ungrounded_claim_count == 1


def test_v2_numeric_placeholder_requires_exact_eligible_fact_binding() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "facts": [
            {
                "fact_id": "F007",
                "domain": "official_ohlcv",
                "field": "close",
                "value": "2410",
                "unit": "TWD",
                "period": "daily",
                "as_of": "2026-08-21",
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
                "text_template": "收盤欄位為 {{F007}}。",
                "evidence_ids": ["F007"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is True
    assert result.rendered_blocks == ("收盤欄位為 2410 元。",)


def test_v2_relative_valuation_claim_requires_canonical_baseline() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "request": {"depth": "focused", "scopes": ["valuation"]},
        "facts": [
            {
                "fact_id": "F001",
                "domain": "valuation",
                "field": "pe_ratio",
                "value": "28.05",
                "unit": "ratio",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
            {
                "fact_id": "F002",
                "domain": "valuation",
                "field": "relative_value_assessment",
                "value": "unavailable_without_peer_or_historical_baseline",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation"],
            },
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "inference",
                "text_template": "本益比{{F001}}代表市場給予較高溢價。",
                "evidence_ids": ["F001", "F002"],
                "uncertainty": "medium",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is False
    assert "relative_valuation_without_baseline" in result.reason_codes


def test_v2_comparison_requires_both_numeric_sides_as_placeholders() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "request": {"depth": "focused", "scopes": ["technical"]},
        "facts": [
            {
                "fact_id": "F001",
                "domain": "technical",
                "field": "rsi",
                "value": "55",
                "unit": "index",
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
                "block_type": "inference",
                "text_template": "動能指標 {{F001}} 高於均量水準。",
                "evidence_ids": ["F001"],
                "uncertainty": "medium",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is False
    assert "comparison_without_two_numeric_placeholders" in result.reason_codes


def test_v2_comprehensive_rejects_uncovered_requested_scope() -> None:
    packet = {
        "contract_version": "model-fact-packet-v2",
        "request": {"depth": "comprehensive", "scopes": ["technical", "current_news"]},
        "facts": [
            {
                "fact_id": "F001",
                "domain": "technical",
                "field": "trend",
                "value": "偏多",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation"],
            }
        ],
        "events": [{"event_id": "E001", "verification_state": "unverified"}],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": text,
                "evidence_ids": ["F001"],
                "uncertainty": "medium",
                "conditions": [],
            }
            for text in ("技術資料可用。", "趨勢僅供解釋。", "情境需要觀察。", "風險仍需留意。")
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    result = validate_model_analysis_v2(output, packet)

    assert result.passed is False
    assert "requested_scope_not_covered:current_news" in result.reason_codes


def test_packet_coverage_separates_partial_from_fully_omitted_sections() -> None:
    profile = ContextProfile(
        name="test-partial-coverage",
        prompt_token_cap=6_000,
        facts_total=4,
        facts_per_scope=1,
        events_total=0,
        events_per_scope=0,
        minimum_context=4_096,
        release_state="test",
    )
    result = build_model_fact_packet_v2(
        _model_facts(),
        focus="technical",
        depth="focused",
        profile=profile,
        requested_scopes=["technical"],
    )
    coverage = result.packet["coverage"]

    assert "technical" in coverage["included_sections"]
    assert "technical" in coverage["partially_omitted_sections"]
    assert "technical" not in coverage["omitted_sections"]


def test_technical_packet_units_distinguish_price_and_share_indicators() -> None:
    model_facts = {
        "trade_date": "2026-08-28",
        "technical": {
            "status": "ok",
            "bollinger": {"upper": "2442.19", "middle": "2389.25"},
            "atr14": "48.66",
            "macd": {"oscillator": "3.67"},
            "volume_ma20": "22143102",
            "obv": "123456789",
            "rsi": {"rsi14": "62.28"},
        },
    }
    result = build_model_fact_packet_v2(
        model_facts,
        focus="technical",
        depth="focused",
        profile=CONTEXT_PROFILES["focused-16k-v1"],
        requested_scopes=["technical"],
    )
    facts = {fact["field"]: fact for fact in result.packet["facts"]}

    assert facts["bollinger.upper"]["unit"] == "TWD"
    assert facts["atr14"]["unit"] == "TWD"
    assert facts["macd.oscillator"]["unit"] == "TWD"
    assert facts["volume_ma20"]["unit"] == "shares"
    assert facts["obv"]["unit"] == "shares"
    assert facts["rsi.rsi14"]["unit"] == "index"


def test_v2_contract_c_preflight_rejection_skips_candidate_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("QWEN_CONTEXT_TOKENS", "4096")
    monkeypatch.setattr(
        line_model_shadow_service,
        "qwen_chat_detailed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate model must not run after preflight rejection")
        ),
    )
    job = {
        "request_id": "contract-c",
        "question": "台積電基本面完整分析" + ("很詳細" * 2000),
        "model_facts": _model_facts(),
        "focus": "fundamentals",
    }

    result = line_model_shadow_service.execute_line_model_shadow(
        job,
        evidence_path=tmp_path / "contract-c.jsonl",
    )

    assert result["candidate_model_called"] is False
    assert result["finish_reason"] == "preflight_rejected"
    assert result["validator_result"] == "reject"
    assert result["validator_reason_codes"] == ["estimated_prompt_exceeds_effective_budget"]


def test_shadow_ledger_marks_prior_process_nonterminal_job_abandoned(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "ledger_version": "line-model-shadow-ledger-v1",
                "event": "started",
                "request_id": "lost-job",
                "process_instance_id": "prior-process",
                "recorded_at_epoch_ms": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    line_model_shadow_service._recover_abandoned_jobs(ledger)
    summary = line_model_shadow_service.shadow_ledger_summary(ledger)
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]

    assert summary["abandoned_jobs"] == 1
    assert summary["restart_resume_supported"] is False
    assert rows[-1]["event"] == "abandoned"
    assert rows[-1]["reason"] == "process_restart_or_unclean_shutdown"


def test_post_reply_submission_writes_deidentified_lifecycle(monkeypatch, tmp_path: Path) -> None:
    from concurrent.futures import Future

    from services.model_admission_service import ModelExecution

    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(line_model_shadow_service, "qwen_model_resident", lambda: True)
    monkeypatch.setattr(
        line_model_shadow_service,
        "_execute_line_model_shadow_admitted",
        lambda *_args, **_kwargs: {
            "candidate_model_called": True,
            "validator_result": "pass",
            "model_latency_ms": 12,
            "total_shadow_duration_ms": 15,
        },
    )

    def immediate_submit(
        callable_,
        *,
        category="shadow_candidate",
        on_start=None,
        cancellable_callable=None,
    ):
        future = Future()
        if on_start:
            on_start(7)
        value = (
            cancellable_callable(threading.Event())
            if cancellable_callable is not None
            else callable_()
        )
        future.set_result(
            ModelExecution(
                value=value,
                queue_wait_ms=7,
                execution_ms=15,
                category=category,
            )
        )
        return future

    monkeypatch.setattr(line_model_shadow_service, "submit_shadow_model", immediate_submit)
    future = line_model_shadow_service.submit_line_model_shadow(
        {
            "request_id": "job-1",
            "question": "台積電 2330 我的成本 2310",
            "model_facts": {"secret": "must-not-persist"},
            "focus": "overview",
        },
        stable_reply_sha256="abc",
        ledger_path=ledger,
    )

    assert future is not None
    assert future.result().value["validator_result"] == "pass"
    text = ledger.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert [row["event"] for row in rows] == [
        "queued",
        "research_started",
        "research_completed",
        "started",
        "completed",
    ]
    assert rows[3]["queue_wait_ms"] == 7
    assert "query" not in rows[2]
    assert "events" not in rows[2]
    assert "台積電" not in text
    assert "2310" not in text
    assert "must-not-persist" not in text


def test_post_reply_research_finishes_before_gpu_admission(monkeypatch, tmp_path: Path) -> None:
    from concurrent.futures import Future

    from services.model_admission_service import ModelExecution

    order: list[str] = []
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(line_model_shadow_service, "qwen_model_resident", lambda: True)

    def prepare(job):
        order.append("research")
        prepared = dict(job)
        prepared["_research_summary"] = {
            "status": "ok",
            "event_count": 1,
            "canonical_table_writes": 0,
        }
        return prepared

    def submit(callable_, *, category="shadow_candidate", on_start=None, cancellable_callable=None):
        order.append("gpu_admission")
        future = Future()
        if on_start:
            on_start(0)
        value = callable_()
        future.set_result(
            ModelExecution(value=value, queue_wait_ms=0, execution_ms=1, category=category)
        )
        return future

    monkeypatch.setattr(line_model_shadow_service, "_prepare_shadow_job_for_execution", prepare)
    monkeypatch.setattr(line_model_shadow_service, "submit_shadow_model", submit)
    monkeypatch.setattr(
        line_model_shadow_service,
        "_execute_line_model_shadow_admitted",
        lambda *_args, **_kwargs: {
            "candidate_model_called": True,
            "validator_result": "pass",
            "model_latency_ms": 1,
            "total_shadow_duration_ms": 1,
        },
    )

    future = line_model_shadow_service.submit_line_model_shadow(
        {"request_id": "order-1", "question": "台積電新聞", "model_facts": {}},
        ledger_path=tmp_path / "order-ledger.jsonl",
    )

    assert future is not None
    assert future.result().value["validator_result"] == "pass"
    assert order == ["research", "gpu_admission"]


def test_post_reply_shadow_is_deferred_while_text_model_is_cold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "cold-ledger.jsonl"
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(line_model_shadow_service, "qwen_model_resident", lambda: False)
    monkeypatch.setattr(
        line_model_shadow_service,
        "ensure_background_text_model_warmup",
        lambda: {"status": "scheduled", "scheduled": True},
    )

    future = line_model_shadow_service.submit_line_model_shadow(
        {"request_id": "cold-job", "question": "技術面", "model_facts": {}},
        ledger_path=ledger,
    )
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]

    assert future is None
    assert [row["event"] for row in rows] == ["queued", "deferred_model_not_resident"]
    assert rows[-1]["reason"] == "cold_shadow_deferred_background_warmup_scheduled"


def test_render_contract_exposes_exact_machine_readable_output_rules() -> None:
    from core import line_model_contract

    packet = {
        "request": {
            "depth": "comprehensive",
            "scopes": ["fundamentals", "valuation", "institutional", "current_news", "night_market"],
            "analysis_cutoff": "2026-08-28",
        },
        "facts": [
            {
                "fact_id": "F001", "domain": "fundamentals", "field": "availability",
                "value": None, "unit": None, "period": "current", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db",
                "quality": "unavailable", "use_scope": ["limitation"],
            },
            {
                "fact_id": "F002", "domain": "valuation", "field": "relative_value_assessment",
                "value": "unavailable_without_peer_or_historical_baseline", "unit": None,
                "period": "daily", "trade_date": "2026-08-28", "as_of": "2026-08-28",
                "authority_tier": "canonical_db", "quality": "ok", "use_scope": ["explanation"],
            },
            {
                "fact_id": "F003", "domain": "institutional_context",
                "field": "canonical_costs.foreign_estimated.required_days", "value": 60,
                "unit": "count", "period": "daily", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db", "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
            {
                "fact_id": "F004", "domain": "institutional_context",
                "field": "canonical_costs.foreign_estimated.sample_days", "value": 18,
                "unit": "count", "period": "daily", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db", "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
            {
                "fact_id": "F005", "domain": "taifex_night_context", "field": "availability",
                "value": None, "unit": None, "period": "current", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db",
                "quality": "source_delayed", "use_scope": ["limitation"],
            },
        ],
        "events": [
            {"event_id": "E001", "event_context": "news_radar_context", "verification_state": "unverified"},
            {"event_id": "E002", "event_context": "news_radar_context", "verification_state": "unverified"},
        ],
    }

    line_model_contract._refresh_render_contract(packet)
    rules = packet["render_contract"]["output_rules"]

    assert rules["version"] == "model-analysis-output-rules-v1"
    assert rules["same_block_placeholders"] is True
    assert rules["comparison_pair_required"] is True
    assert rules["used_event_ids"] == "exactly_cited_event_ids"
    assert rules["unlisted_limitation_text"] == "forbidden"
    assert rules["missing_data_exact"] == [
        "基本面資料不可用", "缺少同業或歷史估值基準", "事件尚未驗證", "資料不可用",
    ]
    assert rules["research_limitations_exact"] == ["本包外資近期增量成本估算樣本不足"]
    assert rules["limitation_blocks_exact"] == [
        {"text_template": "基本面資料不可用", "evidence_ids": ["F001"]},
        {
            "text_template": "缺少同業或歷史估值基準。無法進行相對估值判斷",
            "evidence_ids": ["F002"],
        },
        {"text_template": "資料不可用", "evidence_ids": ["F005"]},
        {"text_template": "事件尚未驗證", "evidence_ids": ["E001", "E002"]},
    ]


def test_render_contract_does_not_offer_cost_limitation_without_valid_pair() -> None:
    from core import line_model_contract

    packet = {
        "request": {
            "depth": "focused", "scopes": ["institutional"], "analysis_cutoff": "2026-08-28",
        },
        "facts": [{
            "fact_id": "F001", "domain": "institutional_context",
            "field": "canonical_costs.foreign_estimated.required_days", "value": 60,
            "unit": "count", "period": "daily", "trade_date": "2026-08-28",
            "as_of": "2026-08-28", "authority_tier": "canonical_db", "quality": "ok",
            "use_scope": ["explanation", "numeric_claim"],
        }],
        "events": [],
    }

    line_model_contract._refresh_render_contract(packet)

    assert packet["render_contract"]["output_rules"]["research_limitations_exact"] == []


def test_render_contract_output_rules_are_accepted_by_current_validator() -> None:
    from core import line_model_contract

    packet = {
        "request": {
            "depth": "focused", "scopes": [], "analysis_cutoff": "2026-08-28",
        },
        "facts": [
            {
                "fact_id": "F001", "domain": "fundamentals", "field": "availability",
                "value": None, "unit": None, "period": "current", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db",
                "quality": "unavailable", "use_scope": ["limitation"],
            },
            {
                "fact_id": "F002", "domain": "valuation", "field": "relative_value_assessment",
                "value": "unavailable_without_peer_or_historical_baseline", "unit": None,
                "period": "daily", "trade_date": "2026-08-28", "as_of": "2026-08-28",
                "authority_tier": "canonical_db", "quality": "ok", "use_scope": ["explanation"],
            },
            {
                "fact_id": "F003", "domain": "institutional_context",
                "field": "canonical_costs.foreign_estimated.required_days", "value": 60,
                "unit": "count", "period": "daily", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db", "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
            {
                "fact_id": "F004", "domain": "institutional_context",
                "field": "canonical_costs.foreign_estimated.sample_days", "value": 18,
                "unit": "count", "period": "daily", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db", "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
            {
                "fact_id": "F005", "domain": "taifex_night_context", "field": "availability",
                "value": None, "unit": None, "period": "current", "trade_date": "2026-08-28",
                "as_of": "2026-08-28", "authority_tier": "canonical_db",
                "quality": "source_delayed", "use_scope": ["limitation"],
            },
        ],
        "events": [
            {
                "event_id": "E001", "event_context": "news_radar_context",
                "verification_state": "unverified",
            },
        ],
    }
    line_model_contract._refresh_render_contract(packet)
    rules = packet["render_contract"]["output_rules"]

    def validation_output(
        item: dict,
        *,
        missing_data: list[str] | None = None,
        research_limitations: list[str] | None = None,
    ) -> dict:
        evidence_ids = list(item["evidence_ids"])
        block = {
            "block_type": "limitation",
            "text_template": item["text_template"],
            "evidence_ids": evidence_ids,
            "uncertainty": "high",
            "conditions": [],
        }
        return {
            "contract_version": "model-analysis-v2",
            "explanation_blocks": [dict(block) for _ in range(3)],
            "missing_data": list(missing_data or []),
            "used_event_ids": [item_id for item_id in evidence_ids if item_id.startswith("E")],
            "research_limitations": list(research_limitations or []),
        }

    for item in rules["limitation_blocks_exact"]:
        result = validate_model_analysis_v2(validation_output(item), packet)
        assert result.reason_codes == ("pass",), item

    seed = rules["limitation_blocks_exact"][0]
    for text in rules["missing_data_exact"]:
        result = validate_model_analysis_v2(
            validation_output(seed, missing_data=[text]),
            packet,
        )
        assert result.reason_codes == ("pass",), text

    for text in rules["research_limitations_exact"]:
        result = validate_model_analysis_v2(
            validation_output(seed, research_limitations=[text]),
            packet,
        )
        assert result.reason_codes == ("pass",), text
