from __future__ import annotations

"""Run legacy non-production shadow diagnostics and write reviewable evidence.

Production evidence must use ``run_line_model_live_acceptance.py`` so 8010,
8020, and 8021 remain online and every model call shares the live admission
controller. This runner intentionally cannot bypass that safety boundary.
"""

import argparse
import hashlib
import json
import math
import os
import socket
import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_bot_config import load_line_bot_env, resolve_line_bot_env_file  # noqa: E402
from services import line_bot_service  # noqa: E402
from services.bot_market_data_service import (  # noqa: E402
    build_bot_daily_market_data,
    resolve_bot_stock_query,
)
from services.line_model_shadow_service import execute_line_model_shadow  # noqa: E402


DEFAULT_QUESTIONS = (
    "台積電基本面、籌碼、技術面與最新新聞完整分析",
    "台積電技術面、估值、支撐壓力與風險完整評估",
    "台積電籌碼、夜盤、美股與重大事件完整分析",
)


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _port_accepting(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _enforce_nonproduction_runner_boundary(
    *,
    allow_live_stack: bool,
    nonproduction_offline_ack: bool,
) -> None:
    if allow_live_stack:
        raise RuntimeError(
            "--allow-live-stack is permanently disabled; use "
            "scripts/run_line_model_live_acceptance.py"
        )
    if _port_accepting(8010) or _port_accepting(8021):
        raise RuntimeError(
            "legacy offline evidence is forbidden while 8010 or 8021 is accepting users; "
            "do not stop the production LINE stack—use "
            "scripts/run_line_model_live_acceptance.py"
        )
    if str(os.environ.get("APP_ENV") or "").strip().lower() == "production":
        raise RuntimeError("legacy offline evidence is forbidden in APP_ENV=production")
    if not nonproduction_offline_ack:
        raise RuntimeError("--nonproduction-offline-ack is required")


def _nearest_rank_p95(values: list[int]) -> int:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return 0
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _protected_hash_summary() -> dict[str, Any]:
    manifest_path = PROJECT_ROOT / "docs" / "LINE_MODEL_SPEC_V2_BASELINE.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for relative, expected in dict(manifest.get("protected_files") or {}).items():
        target = PROJECT_ROOT / str(relative)
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else "missing"
        rows.append(
            {
                "path": str(relative),
                "expected_sha256": str(expected),
                "actual_sha256": actual,
                "match": actual == str(expected),
            }
        )
    return {
        "baseline_schema_version": str(manifest.get("schema_version") or ""),
        "matched": sum(row["match"] for row in rows),
        "total": len(rows),
        "all_match": bool(rows) and all(row["match"] for row in rows),
        "files": rows,
    }


def _load_runner_environment() -> None:
    load_line_bot_env()
    values = dotenv_values(resolve_line_bot_env_file())
    required = {
        "BOT_MARKET_DATA_BASE_URL",
        "BOT_MARKET_DATA_TOKEN",
        "BOT_MARKET_DATA_TIMEOUT_SECONDS",
        "QWEN_BASE_URL",
        "QWEN_API_KEY",
        "QWEN_MODEL_ID",
        "QWEN_CONTEXT_TOKENS",
        "QWEN_MAX_OUTPUT_TOKENS",
        "QWEN_TIMEOUT_SECONDS",
        "QWEN_ENABLE_THINKING",
        "QWEN_TEMPERATURE",
        "QWEN_TOP_P",
    }
    for name in required:
        value = values.get(name)
        if value is not None and not str(os.environ.get(name) or "").strip():
            os.environ[name] = str(value)


def _stable_reply_byte_evidence(questions: tuple[str, ...]) -> list[dict[str, Any]]:
    original_rollout = os.environ.get("LINE_MODEL_V2_ROLLOUT")
    original_qwen = os.environ.get("QWEN_ENABLED")
    original_resolver = line_bot_service.resolve_stock_query
    original_fetcher = line_bot_service.fetch_daily_market_data
    rows: list[dict[str, Any]] = []
    try:
        os.environ["QWEN_ENABLED"] = "false"
        line_bot_service.resolve_stock_query = resolve_bot_stock_query
        line_bot_service.fetch_daily_market_data = lambda code, **kwargs: build_bot_daily_market_data(
            str(code),
            analysis_mode=str(kwargs.get("analysis_mode") or "close_batch"),
        )
        for question in questions:
            os.environ["LINE_MODEL_V2_ROLLOUT"] = "off"
            before = line_bot_service.answer_stock_question(question).encode("utf-8")
            os.environ["LINE_MODEL_V2_ROLLOUT"] = "shadow"
            after = line_bot_service.answer_stock_question(question).encode("utf-8")
            rows.append(
                {
                    "question": question,
                    "before_sha256": hashlib.sha256(before).hexdigest(),
                    "after_sha256": hashlib.sha256(after).hexdigest(),
                    "before_bytes": len(before),
                    "after_bytes": len(after),
                    "byte_identical": before == after,
                }
            )
    finally:
        if original_rollout is None:
            os.environ.pop("LINE_MODEL_V2_ROLLOUT", None)
        else:
            os.environ["LINE_MODEL_V2_ROLLOUT"] = original_rollout
        if original_qwen is None:
            os.environ.pop("QWEN_ENABLED", None)
        else:
            os.environ["QWEN_ENABLED"] = original_qwen
        line_bot_service.resolve_stock_query = original_resolver
        line_bot_service.fetch_daily_market_data = original_fetcher
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=20)
    parser.add_argument("--code", default="2330")
    parser.add_argument(
        "--evidence-path",
        default="logs/line_model_shadow/phase_a_shadow_calls.jsonl",
    )
    parser.add_argument(
        "--audit-path",
        default="logs/line_model_shadow/phase_b_composite_audit.json",
    )
    parser.add_argument(
        "--stable-bytes-path",
        default="logs/line_model_shadow/phase_a_stable_reply_bytes.json",
    )
    parser.add_argument(
        "--config-diff-path",
        default="logs/line_model_shadow/phase_a_rollout_config_diff.json",
    )
    parser.add_argument(
        "--summary-path",
        default="logs/line_model_shadow/phase_a_acceptance_summary.json",
    )
    parser.add_argument(
        "--allow-live-stack",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--nonproduction-offline-ack",
        action="store_true",
        help="Acknowledge that this legacy runner is restricted to an isolated non-production stack.",
    )
    args = parser.parse_args()
    if args.calls < len(DEFAULT_QUESTIONS):
        parser.error(f"--calls must be at least {len(DEFAULT_QUESTIONS)}")

    _load_runner_environment()
    _enforce_nonproduction_runner_boundary(
        allow_live_stack=bool(args.allow_live_stack),
        nonproduction_offline_ack=bool(args.nonproduction_offline_ack),
    )
    os.environ["LINE_MODEL_SHADOW_IDLE_GRACE_MS"] = "0"
    os.environ["LINE_MODEL_V2_ROLLOUT"] = "shadow"
    evidence_path = _resolve_path(args.evidence_path)
    audit_path = _resolve_path(args.audit_path)
    stable_path = _resolve_path(args.stable_bytes_path)
    config_path = _resolve_path(args.config_diff_path)
    summary_path = _resolve_path(args.summary_path)
    if evidence_path.exists():
        raise FileExistsError(
            f"evidence path already exists; choose a new --evidence-path: {evidence_path}"
        )

    protected_hashes = _protected_hash_summary()
    _write_json(
        config_path,
        {
            "changed_keys": ["LINE_MODEL_V2_ROLLOUT"],
            "before": {"LINE_MODEL_V2_ROLLOUT": "unset (effective off)"},
            "after": {"LINE_MODEL_V2_ROLLOUT": "shadow"},
            "stock_formula_files_changed": 0,
            "db_schema_files_changed": 0,
            "protected_analysis_hashes": protected_hashes,
        },
    )
    stable_rows = _stable_reply_byte_evidence(DEFAULT_QUESTIONS)
    _write_json(stable_path, {"comparisons": stable_rows})

    audit_by_question: dict[str, dict[str, Any]] = {}
    audit_attempts_by_question: dict[str, dict[str, int]] = {
        question: {"attempt_count": 0, "pass_count": 0, "reject_count": 0}
        for question in DEFAULT_QUESTIONS
    }
    results: list[dict[str, Any]] = []
    for index in range(args.calls):
        question = DEFAULT_QUESTIONS[index % len(DEFAULT_QUESTIONS)]
        payload = dict(
            build_bot_daily_market_data(str(args.code), analysis_mode="close_batch") or {}
        )
        payload["_line_question"] = question
        facts = line_bot_service._compact_daily(payload)
        job = {
            "request_id": uuid.uuid4().hex,
            "question": question,
            "model_facts": facts,
            "focus": "overview",
        }
        result = execute_line_model_shadow(
            job,
            force=True,
            evidence_path=evidence_path,
        )
        results.append(result)
        attempt_counts = audit_attempts_by_question[question]
        attempt_counts["attempt_count"] += 1
        if result["validator_result"] == "pass":
            attempt_counts["pass_count"] += 1
        else:
            attempt_counts["reject_count"] += 1

        existing_audit = audit_by_question.get(question)
        should_select = existing_audit is None or (
            existing_audit["validator_result"] != "pass"
            and result["validator_result"] == "pass"
        )
        if should_select:
            audit_by_question[question] = {
                "question": question,
                "request_id": result["request_id"],
                "requested_plan": result["requested_plan"],
                "execution_plan": result["execution_plan"],
                "raw_packet_summary": result["raw_packet_summary"],
                "raw_packet": result["raw_packet"],
                "compacted_packet": result["compacted_packet"],
                "included_sections": result["compacted_packet_summary"]["included_sections"],
                "partially_omitted_sections": result["compacted_packet_summary"][
                    "partially_omitted_sections"
                ],
                "omitted_sections": result["compacted_packet_summary"]["omitted_sections"],
                "omission_reasons": result["compacted_packet_summary"]["omission_reasons"],
                "packet_token_count": result["packet_token_count"],
                "prompt_token_count": result["prompt_token_count"],
                "preflight": result["preflight"],
                "model_output": result["model_output"],
                "validated_model_output": result["validated_model_output"],
                "explanation_blocks": result["explanation_blocks"],
                "rendered_blocks": result["rendered_blocks"],
                "validator_result": result["validator_result"],
                "validator_reason_codes": result["validator_reason_codes"],
                "raw_validator_result": result["raw_validator_result"],
                "raw_validator_reason_codes": result["raw_validator_reason_codes"],
                "deterministic_repair_applied": result["deterministic_repair_applied"],
                "deterministic_repair_codes": result["deterministic_repair_codes"],
            }
        print(
            f"shadow {index + 1}/{args.calls} request_id={result['request_id']} "
            f"latency_ms={result['model_latency_ms']} validator={result['validator_result']} "
            f"finish={result['finish_reason']}",
            flush=True,
        )

    _write_json(
        audit_path,
        {
            "contract_version": "line-model-composite-audit-v1",
            "execution_environment": {
                "mode": "offline_ollama_only",
                "line_gateway_available_during_calls": False,
                "market_api_available_during_calls": False,
                "vision_residency_asserted": False,
                "concurrent_user_traffic_present": False,
                "valid_for": ["candidate_call_correctness", "context_preflight", "grounding"],
                "not_valid_for": ["release_load_matrix", "live_queueing", "vision_co_residency"],
            },
            "selection_policy": (
                "first validator-pass sample per question; first reject retained only "
                "when no pass exists"
            ),
            "selection_bias_warning": (
                "Selected samples demonstrate attainable output only; attempt/pass/reject "
                "counts and reject rates remain mandatory for quality interpretation."
            ),
            "questions": list(audit_by_question.values()),
            "attempts_by_question": {
                question: {
                    **counts,
                    "reject_rate": (
                        counts["reject_count"] / counts["attempt_count"]
                        if counts["attempt_count"]
                        else 0.0
                    ),
                }
                for question, counts in audit_attempts_by_question.items()
            },
            "all_questions_have_validator_pass_sample": all(
                row.get("validator_result") == "pass"
                for row in audit_by_question.values()
            )
            and len(audit_by_question) == len(DEFAULT_QUESTIONS),
            "shadow_call_count": len(results),
            "validator_pass_count": sum(row["validator_result"] == "pass" for row in results),
            "validator_reject_count": sum(row["validator_result"] != "pass" for row in results),
            "context_http_400_count": sum(
                row.get("error_classification") == "context_http_400" for row in results
            ),
        },
    )
    required_fields = (
        "request_id",
        "packet_token_count",
        "model_latency_ms",
        "finish_reason",
        "validator_result",
        "validator_reason_codes",
    )
    final_reasons: dict[str, int] = {}
    raw_reasons: dict[str, int] = {}
    for row in results:
        for reason in row.get("validator_reason_codes") or []:
            final_reasons[str(reason)] = final_reasons.get(str(reason), 0) + 1
        for reason in row.get("raw_validator_reason_codes") or []:
            raw_reasons[str(reason)] = raw_reasons.get(str(reason), 0) + 1
    _write_json(
        summary_path,
        {
            "contract_version": "phase-a-shadow-acceptance-v1",
            "execution_environment": {
                "mode": "offline_ollama_only",
                "line_gateway_available_during_calls": False,
                "market_api_available_during_calls": False,
                "vision_residency_asserted": False,
                "concurrent_user_traffic_present": False,
                "valid_for_load_matrix": False,
            },
            "requested_calls": int(args.calls),
            "completed_records": len(results),
            "all_required_fields_present": all(
                all(field in row for field in required_fields) for row in results
            ),
            "required_fields": list(required_fields),
            "candidate_model_called_count": sum(
                bool(row.get("candidate_model_called")) for row in results
            ),
            "validator_pass_count": sum(row["validator_result"] == "pass" for row in results),
            "validator_reject_count": sum(row["validator_result"] != "pass" for row in results),
            "validator_reason_counts": dict(sorted(final_reasons.items())),
            "raw_validator_reason_counts": dict(sorted(raw_reasons.items())),
            "deterministic_repair_count": sum(
                bool(row.get("deterministic_repair_applied")) for row in results
            ),
            "context_http_400_count": sum(
                row.get("error_classification") == "context_http_400" for row in results
            ),
            "finish_reason_counts": {
                reason: sum(str(row.get("finish_reason") or "") == reason for row in results)
                for reason in sorted({str(row.get("finish_reason") or "") for row in results})
            },
            "model_latency_p95_ms": _nearest_rank_p95(
                [int(row.get("model_latency_ms") or 0) for row in results]
            ),
            "gpu_queue_wait_p95_ms": _nearest_rank_p95(
                [int(row.get("gpu_queue_wait_ms") or 0) for row in results]
            ),
            "stable_reply_comparisons": len(stable_rows),
            "stable_reply_byte_identical_count": sum(
                bool(row.get("byte_identical")) for row in stable_rows
            ),
            "protected_analysis_hashes": protected_hashes,
            "evidence_path": str(Path(args.evidence_path).as_posix()),
            "audit_path": str(Path(args.audit_path).as_posix()),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
