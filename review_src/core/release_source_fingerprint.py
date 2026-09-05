from __future__ import annotations

"""Runtime-bound hashes for LINE model release evidence.

The API captures this mapping once at import time. Evidence collectors compute
the same mapping from the current workspace before issuing any benchmark POST,
so a stale running process cannot be mislabeled with newer on-disk source.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE_FINGERPRINT_CONTRACT = "line-model-runtime-source-fingerprint-v1"
LINE_MODEL_RUNTIME_SOURCE_FILES = (
    "review_src/line_bot_app.py",
    "review_src/analysis/target_label_contract_v1.py",
    "review_src/analysis/technical_ensemble_v1.py",
    "review_src/core/release_source_fingerprint.py",
    "review_src/core/image_data_quality_v1.py",
    "review_src/core/line_memory_schema.py",
    "review_src/core/line_model_contract.py",
    "review_src/core/line_model_release_config.py",
    "review_src/core/line_model_output_schema.py",
    "review_src/core/line_model_validation.py",
    "review_src/core/news_research_policy.py",
    "review_src/core/public_url.py",
    "review_src/core/single_track_v3_schema.py",
    "review_src/adapter/controlled_news_research.py",
    "review_src/adapter/bot_market_data_client.py",
    "review_src/adapter/qwen_local.py",
    "review_src/api/image_analysis.py",
    "review_src/api/bot_market_data.py",
    "review_src/api/line_model_benchmark.py",
    "review_src/api/line_webhook.py",
    "review_src/repository/line_conversation_repository.py",
    "review_src/repository/single_track_v3_repository.py",
    "review_src/services/canonical_analysis_orchestrator.py",
    "review_src/services/canonical_model_answer_service.py",
    "review_src/services/canonical_model_candidate_service.py",
    "review_src/services/canonical_model_packet_service.py",
    "review_src/services/canonical_model_packet_orchestrator.py",
    "review_src/services/canonical_question_analysis_service.py",
    "review_src/services/chart_image_service.py",
    "review_src/services/conversation_memory_service.py",
    "review_src/services/conversation_projection_v1.py",
    "review_src/services/event_safety_scan_service.py",
    "review_src/services/expert_response_renderer.py",
    "review_src/services/image_input_service.py",
    "review_src/services/line_model_candidate_reply_service.py",
    "review_src/services/line_model_candidate_delivery_service.py",
    "review_src/services/line_canonical_model_service.py",
    "review_src/services/line_model_research_service.py",
    "review_src/services/line_request_planning_service.py",
    "review_src/services/line_model_shadow_service.py",
    "review_src/services/line_model_benchmark_service.py",
    "review_src/services/model_admission_service.py",
    "review_src/services/stock_entity_registry_service.py",
    "review_src/services/line_bot_service.py",
    "review_src/services/line_reply_telemetry_service.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_runtime_source_fingerprint() -> dict[str, Any]:
    source_hashes: dict[str, str | None] = {}
    for relative in LINE_MODEL_RUNTIME_SOURCE_FILES:
        path = PROJECT_ROOT / relative
        source_hashes[relative] = _sha256(path) if path.is_file() else None
    complete = all(isinstance(value, str) and len(value) == 64 for value in source_hashes.values())
    digest = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "contract_version": RUNTIME_SOURCE_FINGERPRINT_CONTRACT,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "complete": complete,
        "source_digest": digest,
        "source_hashes": source_hashes,
    }
