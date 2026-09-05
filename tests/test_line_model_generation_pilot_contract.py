from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.line_model_output_schema import MODEL_OUTPUT_SCHEMA_VERSION, model_analysis_output_schema
from services import line_model_shadow_service


PILOT_FAILURES = (
    {
        "case_id": "pilot_placeholder_not_in_same_block",
        "observed_text": "截至{{F002}}收盤價{{F001}}。",
        "observed_evidence_ids": ["F001"],
        "observed_reasons": [
            "placeholder_without_matching_evidence",
            "unresolved_placeholder",
            "ungrounded_numeric_or_date_claim",
        ],
        "required_generation_rule": "same_block_placeholder_subset",
    },
    {
        "case_id": "pilot_requested_institutional_scope_omitted",
        "requested_scopes": [
            "fundamentals",
            "institutional",
            "technical",
            "current_news",
        ],
        "observed_reasons": ["requested_scope_not_covered:institutional"],
        "required_generation_rule": "every_effective_scope_covered",
    },
    {
        "case_id": "pilot_unsupported_comparison_and_label_shape",
        "observed_text": (
            "收盤價為{{F001}}，位於布林通道中軌{{F010}}與上軌{{F011}}之間，"
            "且高於短期均線{{F022}}、中期均線{{F023}}及長期均線{{F025}}。"
        ),
        "observed_evidence_ids": ["F001", "F010", "F011", "F022", "F023", "F025"],
        "observed_reasons": [
            "comparison_structure_unverifiable",
            "claim_field_label_unverifiable",
        ],
        "required_generation_rule": "finite_comparison_grammar_only",
    },
)

PILOT_FAILURES_SHA256 = "d085b521cea1eb0d0d44d4334dcbbd2cbe61af1b09b0219c0049b660f0d9e2dd"

PILOT_V3_SOURCE = {
    "artifact": (
        "logs/line_model_shadow/generation_contract_pilot_fix_deploy_20260901_1325/"
        "pilot3.jsonl"
    ),
    "artifact_sha256": "86cc89da5a26127506a5f66322c345781e9251467a80c1bc9db2ec8322176466",
    "attempt_count": 3,
    "generation_schema_version": "model-analysis-generation-shape-v3",
}

PILOT_V3_FAILURES = (
    {
        "case_id": "v3_valuation_limitation_without_typed_missing_evidence",
        "scenario": "fundamental_chip_technical_news",
        "source_line_sha256": "a53dec484fd90d52fa4fc4549eb0d30081258d268442080708d633a3da7e6e7c",
        "model_output_sha256": "2b71d7b74f858a4385d133277cb96e6b9ea23380abecdb1676178a8f16fd1c9c",
        "requested_scopes": ["fundamentals", "institutional", "technical", "current_news"],
        "observed_text": "缺少比較基準，無法進行相對估值判斷。",
        "observed_evidence_ids": ["F031", "F032", "F033"],
        "observed_reasons": ["unverifiable_limitation_claim"],
        "required_generation_rule": "valuation_limitation_must_be_exactly_authorized",
    },
    {
        "case_id": "v3_multiple_ma_operands_in_one_comparison_sentence",
        "scenario": "technical_valuation_support_risk",
        "source_line_sha256": "0834afe77c4fe8857bf2823249d50ba01d269d5447cb2a5144b04a6905d3948c",
        "model_output_sha256": "db41c73e678dbaffcdce9d38557dd673f150111211bd66dddf913fbd0b8e68d4",
        "requested_scopes": ["technical", "valuation", "support_resistance", "risk"],
        "observed_text": (
            "收盤價{{F001}}高於短中長期均線{{F024}}、{{F022}}、{{F023}}、{{F025}}，"
            "顯示價格位於多條均線上方，技術面呈現偏多結構。"
        ),
        "observed_evidence_ids": ["F001", "F024", "F022", "F023", "F025"],
        "observed_reasons": ["claim_operand_binding_missing"],
        "required_generation_rule": "one_operand_pair_per_comparison_sentence",
    },
    {
        "case_id": "v3_required_institutional_scope_not_realized",
        "scenario": "chip_night_us_events",
        "source_line_sha256": "375d6a4f2a57b1d21a4ca35732c324e1bd0c879e9513713ca6d98c7cfeefd0c1",
        "model_output_sha256": "95862e7b5e3b119530fd8c553df5c4ffa891ff42a7340e69a39c53d292217f41",
        "requested_scopes": ["institutional", "current_news", "global_market", "night_market"],
        "observed_block_evidence_ids": [
            ["F001", "F002", "F003", "F004", "F005", "F006"],
            ["F031", "F032", "F033"],
            ["F034", "F035", "F036"],
            ["E001", "E002", "E003", "E004"],
        ],
        "observed_reasons": ["requested_scope_not_covered:institutional"],
        "required_generation_rule": "ordered_required_scope_block_slots",
    },
)

PILOT_V3_FAILURES_SHA256 = "a80be52dae851273df4a7ac7dabecd699f6dc92f08447b37402db2e029865e34"


def _cases_sha256() -> str:
    payload = json.dumps(PILOT_FAILURES, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _v3_cases_sha256() -> str:
    payload = json.dumps(
        {"source": PILOT_V3_SOURCE, "cases": PILOT_V3_FAILURES},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_pilot_failure_contract_is_content_addressed() -> None:
    assert _cases_sha256() == PILOT_FAILURES_SHA256


def test_v3_live_pilot_failures_are_content_addressed() -> None:
    assert len(PILOT_V3_FAILURES) == PILOT_V3_SOURCE["attempt_count"] == 3
    assert len({case["case_id"] for case in PILOT_V3_FAILURES}) == 3
    assert _v3_cases_sha256() == PILOT_V3_FAILURES_SHA256


def test_v3_live_pilot_source_artifact_matches_every_frozen_case() -> None:
    source = Path(PILOT_V3_SOURCE["artifact"])
    source_bytes = source.read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == PILOT_V3_SOURCE["artifact_sha256"]

    raw_lines = source.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == len(PILOT_V3_FAILURES)
    for raw_line, frozen_case in zip(raw_lines, PILOT_V3_FAILURES, strict=True):
        row = json.loads(raw_line)
        model_output = str(row["body"]["result"]["model_output"])
        assert row["scenario"] == frozen_case["scenario"]
        assert hashlib.sha256(raw_line.encode("utf-8")).hexdigest() == frozen_case[
            "source_line_sha256"
        ]
        assert hashlib.sha256(model_output.encode("utf-8")).hexdigest() == frozen_case[
            "model_output_sha256"
        ]


def test_v4_generation_contract_only_emits_authorized_valuation_limitation() -> None:
    prompt = line_model_shadow_service.MODEL_ANALYSIS_SYSTEM_PROMPT
    schema = model_analysis_output_schema("comprehensive")
    block_properties = schema["properties"]["explanation_blocks"]["items"]["properties"]

    assert "relative_valuation_claim_allowed=false" in prompt
    assert "limitation_blocks_exact" in prompt
    assert "未列出就不得生成「缺少比較基準" in prompt
    assert "limitation_blocks_exact" in block_properties["text_template"]["description"]


def test_v4_generation_contract_requires_one_operand_pair_per_sentence() -> None:
    prompt = line_model_shadow_service.MODEL_ANALYSIS_SYSTEM_PROMPT
    text_description = model_analysis_output_schema("comprehensive")["properties"][
        "explanation_blocks"
    ]["items"]["properties"]["text_template"]["description"]

    assert "每個比較句嚴格只有一組左右operand" in prompt
    assert "多條均線拆成多句" in prompt
    assert "一組左右operand" in text_description
    assert "多均線拆句" in text_description


def test_v4_generation_guard_assigns_one_ordered_block_slot_per_scope() -> None:
    scopes = ["institutional", "current_news", "global_market", "night_market"]
    guard = line_model_shadow_service._generation_request_guard(scopes)
    expected_slots = json.dumps(
        [[index + 1, scope] for index, scope in enumerate(scopes)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    blocks_description = model_analysis_output_schema("comprehensive")["properties"][
        "explanation_blocks"
    ]["description"]

    assert MODEL_OUTPUT_SCHEMA_VERSION == "model-analysis-generation-shape-v4"
    assert f"REQUIRED_SCOPE_BLOCK_SLOTS={expected_slots}" in guard
    assert "前4塊依序一對一覆蓋" in guard
    assert "每塊evidence_ids至少一個ID取自" in guard
    assert "輸出前逐slot核對" in guard
    assert "REQUIRED_SCOPE_BLOCK_SLOTS" in blocks_description


def test_generation_request_guard_names_every_effective_scope_without_substitution() -> None:
    builder = getattr(line_model_shadow_service, "_generation_request_guard", None)
    assert callable(builder), "generation pipeline needs one dynamic per-request scope checklist"

    scopes = ["fundamentals", "institutional", "technical", "current_news"]
    guard = builder(scopes)

    assert 'REQUIRED_SCOPE_CHECKLIST=["fundamentals","institutional","technical","current_news"]' in guard
    assert "每個scope至少一塊" in guard
    assert "不得用其他scope替代" in guard
    assert "正文不得直接寫數字股票代號" in guard
    assert "price塊只引用一個收盤價fact" in guard
    assert "不得列舉開高低量" in guard
    assert "每個非limitation塊最多兩個placeholder" in guard
    assert "每句最多一個placeholder" in guard
    assert "日期只選單一最新截止日" in guard


def test_generation_prompt_requires_same_block_placeholder_and_finite_comparison_forms() -> None:
    prompt = (
        line_model_shadow_service.MODEL_ANALYSIS_SYSTEM_PROMPT
        + line_model_shadow_service.MODEL_ANALYSIS_FINAL_GUARD
    )

    assert "每句最多一個比較關係" in prompt
    assert "區間改寫成兩句" in prompt
    assert "短中長期均線" in prompt
    assert "短期均線{{" in prompt
    assert "不得使用" in prompt
    assert "每個{{F...}}" in prompt
    assert "同塊evidence_ids" in prompt


def test_generation_schema_describes_scope_and_same_block_binding() -> None:
    schema = model_analysis_output_schema("comprehensive")
    blocks = schema["properties"]["explanation_blocks"]
    block_properties = blocks["items"]["properties"]

    assert "每個request.scopes" in blocks["description"]
    assert "同塊" in block_properties["evidence_ids"]["description"]
    assert "placeholder" in block_properties["evidence_ids"]["description"]
    assert "每句最多一個" in block_properties["text_template"]["description"]
