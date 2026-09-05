from __future__ import annotations

"""Generation shape only; evidence/financial validity stays in the validator.

Keep this schema free of facts, IDs and claims. It is included in the prompt
before compaction so its entire text is charged to the token preflight. A
runner accepting the schema is not proof that it enforces every keyword.
"""

from typing import Any


MODEL_OUTPUT_SCHEMA_VERSION = "model-analysis-generation-shape-v4"


def model_analysis_output_schema(depth: str) -> dict[str, Any]:
    """Return a fresh bounded JSON schema without changing model-analysis-v2."""

    block = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "block_type": {"type": "string", "enum": ["fact", "inference", "scenario", "limitation"]},
            "evidence_ids": {
                "type": "array", "items": {"type": "string"},
                "maxItems": 8, "uniqueItems": True,
                "description": "同塊ID；包含全部placeholder。",
            },
            "text_template": {
                "type": "string", "minLength": 1, "maxLength": 200,
                "description": (
                    "同塊placeholder；每句最多一個比較／一組左右operand；"
                    "多均線拆句；limitation_blocks_exact逐字。"
                ),
            },
            "uncertainty": {"type": "string", "enum": ["low", "medium", "high"]},
            "conditions": {
                "type": "array", "items": {"type": "string", "maxLength": 160},
                "maxItems": 4, "uniqueItems": True,
            },
        },
        "required": ["block_type", "evidence_ids", "text_template", "uncertainty", "conditions"],
    }
    properties = {
        "contract_version": {"type": "string", "enum": ["model-analysis-v2"]},
        "explanation_blocks": {
            "type": "array", "items": block,
            "minItems": 4 if depth == "comprehensive" else 3,
            "maxItems": 5 if depth == "comprehensive" else 4,
            "description": "REQUIRED_SCOPE_BLOCK_SLOTS覆蓋每個request.scopes。",
        },
        "missing_data": {
            "type": "array", "items": {"type": "string", "maxLength": 160},
            "maxItems": 2, "uniqueItems": True,
        },
        "used_event_ids": {
            "type": "array", "items": {"type": "string"}, "maxItems": 8, "uniqueItems": True,
        },
        "research_limitations": {
            "type": "array", "items": {"type": "string", "maxLength": 240},
            "maxItems": 2, "uniqueItems": True,
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": properties, "required": list(properties),
    }
