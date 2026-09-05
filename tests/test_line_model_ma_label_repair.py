from __future__ import annotations

import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_model_validation import (  # noqa: E402
    deterministic_limitation_placeholder_repair,
    validate_model_analysis_v2,
)


def test_deterministic_repair_generalizes_short_ma_only_for_cited_ma_field() -> None:
    packet = {
        "request": {"depth": "focused", "scopes": ["technical"]},
        "facts": [
            {
                "fact_id": "F001",
                "domain": "official_ohlcv",
                "field": "close",
                "value": "2410",
                "unit": "TWD",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
            {
                "fact_id": "F002",
                "domain": "technical",
                "field": "moving_averages.ma5",
                "value": "2395",
                "unit": "TWD",
                "authority_tier": "canonical_db",
                "quality": "ok",
                "use_scope": ["explanation", "numeric_claim"],
            },
        ],
        "events": [],
    }
    output = {
        "contract_version": "model-analysis-v2",
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": "收盤價為{{F001}}，短均線為{{F002}}。",
                "evidence_ids": ["F001", "F002"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "missing_data": [],
        "used_event_ids": [],
        "research_limitations": [],
    }

    raw = validate_model_analysis_v2(output, packet)
    repaired, codes = deterministic_limitation_placeholder_repair(output, packet)

    assert raw.passed is False
    assert raw.reason_codes == ("claim_field_label_unverifiable",)
    assert repaired is not None
    assert "短均線" not in repaired["explanation_blocks"][0]["text_template"]
    assert "均線為{{F002}}" in repaired["explanation_blocks"][0]["text_template"]
    assert "generalized_ma_label" in codes
    assert validate_model_analysis_v2(repaired, packet).passed is True
