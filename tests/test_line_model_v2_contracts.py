from __future__ import annotations

import json
import hashlib
import socket
import sqlite3
import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.qwen_local import QwenChatResult  # noqa: E402
from core.line_model_validation import (  # noqa: E402
    deterministic_limitation_placeholder_repair,
    validate_model_analysis_v2,
)
from services import line_model_shadow_service  # noqa: E402
from core.line_model_contract import conservative_prompt_token_estimate  # noqa: E402
from core.line_model_output_schema import model_analysis_output_schema  # noqa: E402


def _facts_with_missing_fundamentals() -> dict[str, object]:
    display = {
        "trade_date": "2026-08-28",
        "official_ohlcv": {
            "available": True,
            "official_trusted": True,
            "close": "2420",
            "date": "2026-08-28",
        },
        "valuation": {
            "available": False,
            "status": "unavailable",
            "availability_reason": "not_published_yet",
        },
        "referee": {
            "decision_ready": True,
            "main_status": "中性",
            "main_reasons": ["現有訊號多空交錯"],
            "can_be_overridden_by_model": False,
        },
    }
    return {**display, "display": display, "referee": display["referee"]}


def _completion(payload: dict[str, object]) -> QwenChatResult:
    return QwenChatResult(
        text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        finish_reason="stop",
        prompt_tokens=100,
        completion_tokens=50,
        model="taiwan-stock-qwen",
    )


@pytest.fixture
def offline_v2_contract(monkeypatch):
    """Keep real packet/validation code, but forbid external I/O in these contracts."""
    monkeypatch.setenv("LINE_MODEL_RESEARCH_ROLLOUT", "off")
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("QWEN_CONTEXT_TOKENS", "16384")
    monkeypatch.setenv("QWEN_MAX_OUTPUT_TOKENS", "900")
    monkeypatch.delenv("LINE_MODEL_V2_PROFILE", raising=False)
    blocked_calls = []

    def forbidden_io(*_args, **_kwargs):
        blocked_calls.append("unexpected_network_or_database_call")
        raise AssertionError("offline V2 contracts must not access network or database")

    monkeypatch.setattr(socket.socket, "connect", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_io)
    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    monkeypatch.setattr(sqlite3, "connect", forbidden_io)
    yield blocked_calls
    assert blocked_calls == []


def _packet_fact(packet, domain, field):
    matches = [
        fact for fact in packet["facts"]
        if fact["domain"] == domain and fact["field"] == field
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _grounded_missing_data_output(packet):
    """Resolve IDs from the actual compacted model input, never from fixture ordering."""
    absent = _packet_fact(packet, "fundamentals", "availability")
    close = _packet_fact(packet, "official_ohlcv", "close")
    assert absent["value"] is None and absent["quality"] == "unavailable"
    assert absent["use_scope"] == ["limitation"]
    assert close["value"] == "2420" and close["quality"] == "ok"
    assert "numeric_claim" in close["use_scope"]
    return {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "limitation",
                "text_template": "目前缺少可核對的基本面資料，只能說明判讀限制。",
                "evidence_ids": [absent["fact_id"]],
                "uncertainty": "high",
                "conditions": [],
            },
            {
                "block_type": "fact",
                "text_template": "收盤價為{{" + close["fact_id"] + "}}。",
                "evidence_ids": [close["fact_id"]],
                "uncertainty": "low",
                "conditions": [],
            },
            {
                "block_type": "inference",
                "text_template": "單一收盤價格只能描述當次價格，不能證明獲利是否改善。",
                "evidence_ids": [close["fact_id"]],
                "uncertainty": "high",
                "conditions": [],
            },
        ],
        "missing_data": ["fundamentals"],
        "used_event_ids": [],
        "research_limitations": [],
    }


def _run_offline_v2_contract(monkeypatch, tmp_path, case_id, output_factory):
    calls = []

    def model_boundary(system_prompt, user_prompt, **kwargs):
        fragment = user_prompt.split("MODEL_FACT_PACKET_V2：", 1)[1]
        packet, end = json.JSONDecoder().raw_decode(fragment)
        expected_guard = (
            line_model_shadow_service.MODEL_ANALYSIS_FINAL_GUARD
            + line_model_shadow_service._generation_request_guard(packet["request"]["scopes"])
        )
        assert fragment[end:].strip() == expected_guard
        output = output_factory(packet)
        calls.append({"system_prompt": system_prompt, "packet": packet, "output": output})
        assert kwargs["response_schema"] == model_analysis_output_schema("focused")
        return _completion(output)

    monkeypatch.setattr(line_model_shadow_service, "qwen_chat_detailed", model_boundary)
    evidence_path = tmp_path / (case_id + ".jsonl")
    result = line_model_shadow_service.execute_line_model_shadow(
        {
            "request_id": case_id,
            "question": "台積電基本面分析",
            "model_facts": _facts_with_missing_fundamentals(),
            "focus": "fundamentals",
        },
        force=True,
        evidence_path=evidence_path,
    )
    ledger = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
    # -s evidence runs retain the actual synthetic model output and both validation outcomes.
    print("OFFLINE_CONTRACT_EVIDENCE " + json.dumps(
        {"case_id": case_id, "synthetic": True, "calls": calls, "result": result, "ledger": ledger},
        ensure_ascii=True,
    ))
    assert len(calls) == 1
    assert calls[0]["packet"] == result["compacted_packet"]
    assert calls[0]["output"] == json.loads(result["model_output"])
    assert result["compacted_packet"]["contract_version"] == "model-fact-packet-v2"
    assert result["preflight"]["ready"] is True
    assert result["classification_method"] == "rule_based"
    assert result["scopes"] == ["fundamentals"]
    assert result["research_summary"]["status"] == "disabled"
    assert result["candidate_model_called"] is True
    assert result["candidate_can_replace_reply"] is False
    assert len(ledger) == 1
    for field in ("validator_result", "validator_reason_codes", "raw_validator_reason_codes"):
        assert ledger[0][field] == result[field]
    return result


def _assert_rejected_without_rendering(result, reason):
    assert result["raw_validator_result"] == "reject"
    assert reason in result["raw_validator_reason_codes"]
    assert result["validator_result"] == "reject"
    assert reason in result["validator_reason_codes"]
    assert result["rendered_blocks"] == []


@pytest.mark.parametrize("depth,minimum,maximum", [("focused", 3, 4), ("comprehensive", 4, 5)])
def test_generation_schema_bounds_without_embedding_market_facts(depth, minimum, maximum):
    schema = model_analysis_output_schema(depth)
    blocks = schema["properties"]["explanation_blocks"]
    assert (blocks["minItems"], blocks["maxItems"]) == (minimum, maximum)
    assert blocks["items"]["properties"]["evidence_ids"]["maxItems"] == 8
    assert blocks["items"]["properties"]["text_template"]["maxLength"] == 200
    assert blocks["items"]["properties"]["conditions"]["uniqueItems"] is True
    assert schema["properties"]["missing_data"]["maxItems"] == 2
    assert schema["properties"]["missing_data"]["uniqueItems"] is True
    assert schema["properties"]["research_limitations"]["maxItems"] == 2
    assert schema["properties"]["research_limitations"]["uniqueItems"] is True
    assert schema["properties"]["used_event_ids"]["maxItems"] == 8
    assert schema["additionalProperties"] is False
    assert blocks["items"]["additionalProperties"] is False
    blocks["maxItems"] = 99
    assert model_analysis_output_schema(depth)["properties"]["explanation_blocks"]["maxItems"] == maximum


def test_generation_schema_and_guard_are_charged_to_real_pipeline_preflight(monkeypatch, tmp_path):
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("QWEN_CONTEXT_TOKENS", "16384")
    captured = {}

    def completion(system, user, **kwargs):
        captured.update(system=system, user=user, **kwargs)
        return _completion({})  # Deliberately invalid: transport success is not validation success.

    monkeypatch.setattr(line_model_shadow_service, "qwen_chat_detailed", completion)
    result = line_model_shadow_service.execute_line_model_shadow(
        {"request_id": "schema-budget", "question": "台積電基本面分析",
         "model_facts": _facts_with_missing_fundamentals(), "focus": "fundamentals"},
        force=True, evidence_path=tmp_path / "schema.jsonl",
    )
    schema_text = captured["system"].split("\nOUTPUT_JSON_SCHEMA：", 1)[1]
    assert json.loads(schema_text) == captured["response_schema"]
    prompt = (
        f'{captured["system"]}\n{line_model_shadow_service.MODEL_ANALYSIS_FINAL_GUARD}'
        f'{line_model_shadow_service._generation_request_guard(result["scopes"])}'
    )
    assert result["generation_prompt_sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert result["generation_schema_prompt_tokens"] > 0
    # Preflight concatenates guard before packet; final prompt puts it after.
    # The estimator is character-additive (rounding tolerance: one token).
    actual_estimate = conservative_prompt_token_estimate(captured["system"], captured["user"])
    assert abs(result["estimated_prompt_token_count"] - actual_estimate) <= 1
    assert result["estimated_prompt_token_count"] <= result["effective_prompt_budget"]
    assert result["validator_result"] == "reject"
    assert result["candidate_can_replace_reply"] is False
    assert result["compacted_packet"]["render_contract"]["output_rules"]["version"] == (
        "model-analysis-output-rules-v1"
    )
    assert "render_contract.output_rules" in captured["system"]


@pytest.mark.parametrize("claim", ["本益比為99倍", "2026-08-30數據缺少", "缺少二十日均量", "缺少{{F003}}"])
def test_missing_data_field_cannot_smuggle_unbound_numbers_through_pipeline(monkeypatch, tmp_path, claim):
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setattr(line_model_shadow_service, "qwen_chat_detailed", lambda *_args, **_kwargs: _completion({
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [{"block_type": "limitation", "text_template": "基本面資料不足。",
                                "evidence_ids": ["F003"], "uncertainty": "high", "conditions": []}],
        "missing_data": [claim], "used_event_ids": [], "research_limitations": [],
    }))
    result = line_model_shadow_service.execute_line_model_shadow(
        {"request_id": "missing-data-number", "question": "台積電基本面分析",
         "model_facts": _facts_with_missing_fundamentals(), "focus": "fundamentals"},
        force=True, evidence_path=tmp_path / "reject.jsonl",
    )
    assert result["validator_result"] == "reject"
    assert "ungrounded_numeric_or_date_claim" in result["validator_reason_codes"]
    assert result["rendered_blocks"] == []


@pytest.mark.parametrize("field", ["text_template", "conditions", "missing_data", "research_limitations"])
@pytest.mark.parametrize(
    "claim,expected_pass",
    [
        ("缺少2026年Q2的EPS數據", False),
        ("缺少Q1的EPS數據", False),
        ("缺少Q2的EPS數據", False),
        ("缺少q2的EPS數據", False),
        ("缺少Q3的EPS數據", False),
        ("缺少Q4的EPS數據", False),
        ("缺少Ｑ２的EPS數據", False),
        ("缺少第二季的EPS數據", False),
        ("缺少二〇二六 年的EPS數據", False),
        ("缺少八 月的EPS數據", False),
        ("缺少EPS9元的資料", False),
        ("目前缺少可核對的基本面資料，只能說明判讀限制。", True),
    ],
)
def test_unbound_period_and_value_labels_cannot_bypass_pipeline(
    monkeypatch, tmp_path, field, claim, expected_pass,
):
    """Raw text is checked on every surface, before and after bounded repair."""

    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    block = {
        "block_type": "limitation", "text_template": "基本面資料不足。",
        "evidence_ids": ["F003"], "uncertainty": "high", "conditions": [],
    }
    output = {
        "contract_version": "model-analysis-v2", "explanation_blocks": [block],
        "missing_data": [], "used_event_ids": [], "research_limitations": [],
    }
    if field == "text_template":
        block[field] = claim
    elif field == "conditions":
        block[field] = [claim]
    else:
        output[field] = [claim]
    calls = []

    def completion(*_args, **_kwargs):
        calls.append(True)
        return _completion(output)

    monkeypatch.setattr(line_model_shadow_service, "qwen_chat_detailed", completion)
    result = line_model_shadow_service.execute_line_model_shadow(
        {"request_id": "period-boundary", "question": "台積電基本面分析",
         "model_facts": _facts_with_missing_fundamentals(), "focus": "fundamentals"},
        force=True, evidence_path=tmp_path / "period-boundary.jsonl",
    )
    assert calls == [True]
    assert result["compacted_packet"]["contract_version"] == "model-fact-packet-v2"
    assert result["validator_result"] == ("pass" if expected_pass else "reject")
    assert result["candidate_can_replace_reply"] is False
    if expected_pass:
        assert result["rendered_blocks"]
    else:
        assert "ungrounded_numeric_or_date_claim" in result["validator_reason_codes"]
        assert result["rendered_blocks"] == []


@pytest.mark.parametrize("quality,expected_pass", [("ok", True), ("unavailable", False)])
def test_backend_bound_date_still_requires_eligible_fact(quality, expected_pass):
    packet = {
        "request": {"depth": "focused"},
        "facts": [{
            "fact_id": "F101", "field": "trade_date", "value": "2026-08-28",
            "unit": "", "period": "daily", "as_of": "2026-08-28",
            "authority_tier": "canonical_db", "quality": quality,
            "use_scope": ["date_claim"] if expected_pass else ["limitation"],
        }],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [{
            "block_type": "fact", "text_template": "資料日期為{{F101}}。",
            "evidence_ids": ["F101"], "uncertainty": "low", "conditions": [],
        }],
        "missing_data": [], "used_event_ids": [], "research_limitations": [],
    }
    result = validate_model_analysis_v2(output, packet)
    assert result.passed is expected_pass
    if expected_pass:
        assert result.rendered_blocks == ("資料日期為2026-08-28。",)
    else:
        assert "ineligible_numeric_placeholder" in result.reason_codes
        assert result.rendered_blocks == ()


def test_contract_a_new_packet_pipeline_allows_grounded_missing_data_explanation(
    monkeypatch,
    tmp_path: Path,
    offline_v2_contract,
) -> None:
    """Contract A: missing DB facts may be explained, never fabricated."""

    result = _run_offline_v2_contract(
        monkeypatch, tmp_path, "contract-a-new-pipeline", _grounded_missing_data_output,
    )
    assert result["raw_validator_result"] == "pass"
    assert result["raw_validator_reason_codes"] == ["pass"]
    assert result["validator_result"] == "pass"
    assert result["validator_reason_codes"] == ["pass"]
    assert result["rendered_blocks"] == [
        "目前缺少可核對的基本面資料，只能說明判讀限制。",
        "收盤價為2420 元。",
        "單一收盤價格只能描述當次價格，不能證明獲利是否改善。",
    ]


def test_contract_b_new_packet_pipeline_rejects_unbound_number_with_existing_evidence_id(
    monkeypatch,
    tmp_path: Path,
    offline_v2_contract,
) -> None:
    """Contract B: an evidence ID alone never authorizes a model-created number."""

    def forged_output(packet):
        output = _grounded_missing_data_output(packet)
        output["explanation_blocks"][0]["text_template"] = "缺少基本面資料，但每股盈餘是 88.88 元。"
        return output

    result = _run_offline_v2_contract(
        monkeypatch, tmp_path, "contract-b-new-pipeline", forged_output,
    )
    _assert_rejected_without_rendering(result, "ungrounded_numeric_or_date_claim")


@pytest.mark.parametrize("case_id,expected_reason", [
    ("raw-number-without-id", "ungrounded_numeric_or_date_claim"),
    ("raw-number-with-eligible-id", "ungrounded_numeric_or_date_claim"),
    ("placeholder-without-listed-id", "placeholder_without_matching_evidence"),
])
def test_numeric_binding_requires_both_typed_placeholder_and_matching_evidence_in_pipeline(
    monkeypatch, tmp_path, offline_v2_contract, case_id, expected_reason,
):
    def invalid_binding(packet):
        output = _grounded_missing_data_output(packet)
        block = output["explanation_blocks"][1]
        if case_id != "placeholder-without-listed-id":
            block["text_template"] = "收盤價為2420元。"
        if case_id != "raw-number-with-eligible-id":
            block["evidence_ids"] = []
        return output

    result = _run_offline_v2_contract(monkeypatch, tmp_path, case_id, invalid_binding)
    _assert_rejected_without_rendering(result, expected_reason)


def test_contract_c_new_packet_pipeline_skips_model_when_preflight_rejects(
    monkeypatch,
    tmp_path: Path,
    offline_v2_contract,
) -> None:
    """Contract C: context admission failure never calls the candidate model."""

    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("QWEN_CONTEXT_TOKENS", "4096")
    monkeypatch.setattr(
        line_model_shadow_service,
        "qwen_chat_detailed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate model must not run after token preflight rejection")
        ),
    )

    result = line_model_shadow_service.execute_line_model_shadow(
        {
            "request_id": "contract-c-new-pipeline",
            "question": "台積電基本面完整分析" + ("請非常詳細說明" * 3000),
            "model_facts": _facts_with_missing_fundamentals(),
            "focus": "fundamentals",
        },
        force=True,
        evidence_path=tmp_path / "contract-c.jsonl",
    )

    print("OFFLINE_CONTRACT_EVIDENCE " + json.dumps(
        {"case_id": "contract-c-new-pipeline", "synthetic": True, "result": result},
        ensure_ascii=True,
    ))
    assert result["candidate_model_called"] is False
    assert result["finish_reason"] == "preflight_rejected"
    assert result["validator_reason_codes"] == [
        "estimated_prompt_exceeds_effective_budget"
    ]


def test_deterministic_repair_generalizes_only_cited_indicator_period_labels() -> None:
    packet = {
        "request": {"depth": "focused", "scopes": ["technical"]},
        "facts": [
            {
                "fact_id": "F101",
                "field": "rsi.rsi14",
                "value": 55,
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
                "block_type": "fact",
                "text_template": "RSI14為{{F101}}。",
                "evidence_ids": ["F101"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)

    assert repaired is not None
    assert repaired["explanation_blocks"][0]["text_template"] == "相對強弱指標為{{F101}}。"
    assert "generalized_rsi_label" in codes


def test_deterministic_repair_does_not_generalize_uncited_indicator_or_raw_value() -> None:
    packet = {"request": {"depth": "focused"}, "facts": [], "events": []}
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": "RSI14為55。",
                "evidence_ids": [],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)

    assert repaired is not None
    assert repaired["explanation_blocks"][0]["text_template"] == "RSI14為55。"
    assert codes == ()


def test_deterministic_repair_removes_duplicate_unit_only_for_cited_renderable_fact() -> None:
    packet = {
        "request": {"depth": "focused"},
        "facts": [
            {
                "fact_id": "F101",
                "field": "close",
                "value": 2410,
                "unit": "TWD",
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
                "text_template": "收盤為{{F101}}元。",
                "evidence_ids": ["F101"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)

    assert repaired is not None
    assert repaired["explanation_blocks"][0]["text_template"] == "收盤為{{F101}}。"
    assert "removed_duplicate_render_unit" in codes
