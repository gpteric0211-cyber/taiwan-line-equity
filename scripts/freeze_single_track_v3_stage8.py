from __future__ import annotations

"""Produce a sanitized, fail-closed Stage 8 freeze manifest on stdout."""

import argparse
import hashlib
import json
import py_compile
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_bot_config import env_int, env_text, load_line_bot_env  # noqa: E402
from core.release_source_fingerprint import (  # noqa: E402
    LINE_MODEL_RUNTIME_SOURCE_FILES,
    RUNTIME_SOURCE_FINGERPRINT_CONTRACT,
    current_runtime_source_fingerprint,
)
from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    SINGLE_TRACK_V3_TABLES,
    ensure_single_track_v3_schema,
    rollback_single_track_v3_schema,
)
from evaluation.expert_response_quality_review_kit import (  # noqa: E402
    EVALUATION_VERSION,
    evaluate_review_kit,
)


TPE = ZoneInfo("Asia/Taipei")
EXPECTED_DRIVER = "595.71"
EXPECTED_CUDA = "13.3"
FREEZE_CONTRACT = "SingleTrackV3Stage8FreezeV1"
SCHEDULER_AUDIT_CONTRACT = "SingleTrackV3SchedulerAuditV1"
SCHEDULER_AUDIT_PATH = PROJECT_ROOT / "docs" / "STAGE8_SCHEDULER_AUDIT.json"
REQUIRED_SCHEDULER_SLOTS = (
    "evening_1800",
    "evening_2100",
    "preopen_0600",
    "preopen_final_scan",
    "high_signal_sentinel_15m",
)
ADDITIONAL_FREEZE_FILES = (
    ".gitignore",
    "scripts/freeze_single_track_v3_stage8.py",
    "scripts/collect_stage8_canonical_candidate_matrix.py",
    "scripts/diagnose_stage8_canonical_candidate.py",
    "scripts/run_line_model_candidate_preview_audit.py",
    "scripts/run_line_model_live_acceptance.py",
    "scripts/run_line_model_phase_d.py",
    "scripts/run_line_model_phase_d_probes.py",
    "scripts/run_line_stable_admission_load.py",
    "scripts/run_expert_response_quality_review.py",
    "scripts/run_single_track_v3_scheduler.py",
    "scripts/install_single_track_v3_scheduler.ps1",
    "scripts/start_line_bot_stack.py",
    "review_src/evaluation/statistical_release_gate_v1.py",
    "review_src/evaluation/expert_response_quality_corpus.py",
    "review_src/evaluation/expert_response_quality_gate_v1.py",
    "review_src/evaluation/expert_response_quality_review_kit.py",
    "review_src/adapter/official_calendar_revision.py",
    "review_src/adapter/official_company_events.py",
    "review_src/adapter/official_external_events.py",
    "review_src/adapter/single_track_v3_federal_reserve_retrieval.py",
    "review_src/adapter/single_track_v3_official_retrieval.py",
    "review_src/adapter/single_track_v3_taiwan_policy_retrieval.py",
    "review_src/adapter/single_track_v3_us_policy_retrieval.py",
    "review_src/core/news_research_policy.py",
    "review_src/core/single_track_v3_event_source_plan.py",
    "review_src/repository/single_track_v3_assessment_repository.py",
    "review_src/repository/single_track_v3_artifact_production_repository.py",
    "review_src/repository/single_track_v3_artifact_repository.py",
    "review_src/repository/single_track_v3_calendar_repository.py",
    "review_src/repository/single_track_v3_content_assessment_state_repository.py",
    "review_src/repository/single_track_v3_manifest_support.py",
    "review_src/repository/single_track_v3_outcome_evaluation_manifest_repository.py",
    "review_src/repository/single_track_v3_prediction_manifest_repository.py",
    "review_src/repository/single_track_v3_research_repository.py",
    "review_src/repository/single_track_v3_reconciliation_repository.py",
    "review_src/repository/single_track_v3_retrieval_repository.py",
    "review_src/repository/single_track_v3_scheduler_repository.py",
    "review_src/repository/single_track_v3_target_assessment_state_repository.py",
    "review_src/task/single_track_v3_scheduler.py",
    "review_src/task/single_track_v3_calendar_materializer.py",
    "review_src/task/single_track_v3_retrieval_worker.py",
    "review_src/task/single_track_v3_event_retrieval_plan.py",
    "review_src/task/single_track_v3_event_reconciler.py",
    "review_src/requirements.txt",
    "docs/EXPERT_RESPONSE_QUALITY_CORPUS_V1.md",
    "docs/SINGLE_TRACK_V3_CONTRACTS.md",
    "docs/STAGE8_CANONICAL_MATRIX.json",
    "docs/STAGE8_RUNTIME_EVIDENCE_AUDIT.md",
    "docs/STAGE8_SCHEDULER_AUDIT.json",
)
FREEZE_FILES = tuple(dict.fromkeys((*LINE_MODEL_RUNTIME_SOURCE_FILES, *ADDITIONAL_FREEZE_FILES)))
HUMAN_QUALITY_EVIDENCE_FILES = {
    "public_review": "logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/reviewer/blind_review.json",
    "ratings": "logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/reviewer/ratings.completed.json",
    "private_role_key": "logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/coordinator_private/role_key.json",
    "stable_outputs": "logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/coordinator_private/stable_outputs.json",
    "candidate_outputs": "logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/coordinator_private/candidate_outputs.json",
    "evaluation": "logs/line_model_shadow/single_track_v3_stage7/expert_response_quality_v1/evaluation.json",
}


def _evaluate_scheduler_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    reason_codes: list[str] = []
    if payload.get("contract_version") != SCHEDULER_AUDIT_CONTRACT:
        reason_codes.append("stage8_single_track_v3_scheduler_audit_invalid")

    observed_slots = tuple(payload.get("required_slots") or ())
    if set(observed_slots) != set(REQUIRED_SCHEDULER_SLOTS):
        reason_codes.append("stage3_single_track_v3_scheduler_slots_incomplete")

    source = payload.get("source_implementation") or {}
    if not (
        source.get("runner_present") is True
        and source.get("installer_present") is True
        and source.get("required_slot_markers_present") is True
    ):
        reason_codes.append("stage3_single_track_v3_scheduler_source_missing")

    if source.get("retrieval_worker_present") is not True:
        reason_codes.append("stage3_single_track_v3_retrieval_worker_missing")

    windows_tasks = payload.get("windows_tasks") or {}
    if not (
        windows_tasks.get("query_status") == "ok"
        and windows_tasks.get("required_tasks_installed") is True
        and windows_tasks.get("required_tasks_enabled") is True
    ):
        reason_codes.append("stage8_single_track_v3_scheduler_tasks_not_ready")

    restart_probe = payload.get("restart_wake_catch_up") or {}
    if not (
        restart_probe.get("status") == "verified"
        and restart_probe.get("verified") is True
    ):
        reason_codes.append("stage8_single_track_v3_restart_wake_catch_up_unverified")

    if payload.get("gate_passed") is not True:
        reason_codes.append("stage8_single_track_v3_scheduler_gate_not_passed")

    evaluated = dict(payload)
    evaluated["evidence_file"] = str(
        SCHEDULER_AUDIT_PATH.relative_to(PROJECT_ROOT)
    ).replace("\\", "/")
    evaluated["passed"] = not reason_codes
    evaluated["reason_codes"] = list(dict.fromkeys(reason_codes))
    return evaluated


def _scheduler_evidence() -> dict[str, Any]:
    evidence_file = str(SCHEDULER_AUDIT_PATH.relative_to(PROJECT_ROOT)).replace(
        "\\", "/"
    )
    if not SCHEDULER_AUDIT_PATH.is_file():
        return {
            "contract_version": SCHEDULER_AUDIT_CONTRACT,
            "evidence_file": evidence_file,
            "status": "unavailable",
            "passed": False,
            "reason_codes": ["stage8_single_track_v3_scheduler_audit_unavailable"],
        }
    try:
        payload = json.loads(SCHEDULER_AUDIT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "contract_version": SCHEDULER_AUDIT_CONTRACT,
            "evidence_file": evidence_file,
            "status": "invalid",
            "passed": False,
            "reason_codes": ["stage8_single_track_v3_scheduler_audit_invalid"],
        }
    if not isinstance(payload, dict):
        return {
            "contract_version": SCHEDULER_AUDIT_CONTRACT,
            "evidence_file": evidence_file,
            "status": "invalid",
            "passed": False,
            "reason_codes": ["stage8_single_track_v3_scheduler_audit_invalid"],
        }
    return _evaluate_scheduler_evidence(payload)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _human_quality_evidence() -> dict[str, Any]:
    paths = {
        key: PROJECT_ROOT / relative
        for key, relative in HUMAN_QUALITY_EVIDENCE_FILES.items()
    }
    missing = [
        HUMAN_QUALITY_EVIDENCE_FILES[key]
        for key, path in paths.items()
        if not path.is_file()
    ]
    if missing:
        return {
            "status": "unavailable",
            "passed": False,
            "missing_files": missing,
            "reason_codes": ["independent_human_quality_evidence_incomplete"],
        }
    try:
        documents = {
            key: json.loads(path.read_text(encoding="utf-8"))
            for key, path in paths.items()
        }
        if any(not isinstance(document, dict) for document in documents.values()):
            raise ValueError("quality evidence document must be an object")
        recomputed = evaluate_review_kit(
            documents["public_review"],
            documents["private_role_key"],
            documents["ratings"],
            documents["stable_outputs"],
            documents["candidate_outputs"],
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid_human_evidence",
            "passed": False,
            "error_class": type(exc).__name__,
            "reason_codes": ["independent_human_quality_evidence_invalid"],
        }
    evaluation_matches = documents["evaluation"] == recomputed
    passed = (
        evaluation_matches
        and recomputed.get("evaluation_version") == EVALUATION_VERSION
        and recomputed.get("status") == "pass"
        and recomputed.get("passed") is True
        and recomputed.get("integrity_errors") == []
        and recomputed.get("case_count") == 100
        and recomputed.get("human_rating_count") == 100
    )
    reason_codes: list[str] = []
    if not evaluation_matches:
        reason_codes.append("stored_quality_evaluation_does_not_match_recomputation")
    if not passed and not reason_codes:
        reason_codes.extend(
            str(reason)
            for reason in recomputed.get("reason_codes") or ["human_quality_gate_not_passed"]
        )
    return {
        "status": "pass" if passed else str(recomputed.get("status") or "invalid_human_evidence"),
        "passed": passed,
        "evaluation_matches_recomputation": evaluation_matches,
        "case_count": recomputed.get("case_count"),
        "human_rating_count": recomputed.get("human_rating_count"),
        "overall_candidate_preference": recomputed.get("overall_candidate_preference"),
        "category_candidate_preference": recomputed.get("category_candidate_preference"),
        "candidate_dimension_medians": recomputed.get("candidate_dimension_medians"),
        "critical_factual_or_entity_errors": recomputed.get(
            "critical_factual_or_entity_errors"
        ),
        "reason_codes": reason_codes or ["pass"],
        "file_sha256": {
            key: _sha256(path)
            for key, path in paths.items()
        },
    }


def _protected_hash_check() -> dict[str, Any]:
    baseline_path = PROJECT_ROOT / "docs" / "LINE_MODEL_SPEC_V2_BASELINE.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    rows = {
        relative: {
            "expected": expected,
            "actual": _sha256(PROJECT_ROOT / relative),
        }
        for relative, expected in baseline["protected_files"].items()
    }
    mismatches = [relative for relative, row in rows.items() if row["expected"] != row["actual"]]
    return {
        "baseline_file": "docs/LINE_MODEL_SPEC_V2_BASELINE.json",
        "matched": len(rows) - len(mismatches),
        "total": len(rows),
        "mismatches": mismatches,
        "passed": not mismatches,
    }


def _migration_rollback_drill() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="single-track-v3-stage8-") as directory:
        database = Path(directory) / "drill.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            ensure_single_track_v3_schema(connection)
            connection.commit()
            tables_after_migration = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            prediction_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(analysis_target_prediction)"
                ).fetchall()
            }
            expected_prediction_columns = {
                "event_cluster_id",
                "event_type",
                "large_safety_slice",
                "synthetic",
            }
            migration_ready = set(SINGLE_TRACK_V3_TABLES) <= tables_after_migration and (
                expected_prediction_columns <= prediction_columns
            )
            rollback_single_track_v3_schema(connection, allow_destructive=True)
            connection.commit()
            tables_after_rollback = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            leftovers = sorted(set(SINGLE_TRACK_V3_TABLES).intersection(tables_after_rollback))
        finally:
            connection.close()
    return {
        "database_scope": "temporary_sqlite_only",
        "schema_version": SINGLE_TRACK_V3_SCHEMA_VERSION,
        "migration_ready": migration_ready,
        "rollback_leftover_tables": leftovers,
        "temporary_database_destroyed": True,
        "production_database_writes": 0,
        "passed": migration_ready and not leftovers,
    }


def _compile_freeze_files() -> dict[str, Any]:
    python_files = [relative for relative in FREEZE_FILES if relative.endswith(".py")]
    failures = []
    for relative in python_files:
        try:
            py_compile.compile(str(PROJECT_ROOT / relative), doraise=True)
        except py_compile.PyCompileError:
            failures.append(relative)
    return {
        "python_file_count": len(python_files),
        "failures": failures,
        "passed": not failures,
    }


def _canonical_candidate_matrix() -> dict[str, Any]:
    evidence_path = PROJECT_ROOT / "docs" / "STAGE8_CANONICAL_MATRIX.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    expected_digest = str(evidence.pop("evidence_sha256", ""))
    actual_digest = _canonical_digest(evidence)
    collector = str(evidence.get("collector") or "")
    collector_path = PROJECT_ROOT / collector
    collector_digest = _sha256(collector_path) if collector_path.is_file() else ""
    profiles = evidence.get("profiles") if isinstance(evidence.get("profiles"), dict) else {}
    profile_summary = {
        name: {
            "actual_execution_count": int(row.get("actual_execution_count") or 0),
            "validator_passed": int(row.get("validator_passed") or 0),
            "validator_rejected": int(row.get("validator_rejected") or 0),
            "latency_gate_passed": bool(row.get("latency_gate_passed")),
            "queue_plus_execution_p95_ms": [
                int(level.get("queue_plus_execution_p95_ms") or 0)
                for level in row.get("levels") or []
            ],
        }
        for name, row in profiles.items()
        if isinstance(row, dict)
    }
    integrity_passed = (
        bool(expected_digest)
        and expected_digest == actual_digest
        and bool(collector_digest)
        and collector_digest == str(evidence.get("collector_sha256") or "")
    )
    return {
        "contract": str(evidence.get("evidence_contract") or ""),
        "captured_at": str(evidence.get("captured_at") or ""),
        "evidence_file": "docs/STAGE8_CANONICAL_MATRIX.json",
        "evidence_sha256": expected_digest,
        "integrity_passed": integrity_passed,
        "actual_execution_count": int(evidence.get("actual_execution_count") or 0),
        "synthetic_execution_count": int(evidence.get("synthetic_execution_count") or 0),
        "validator_passed": int(evidence.get("validator_passed") or 0),
        "validator_rejected": int(evidence.get("validator_rejected") or 0),
        "phase_d_qualified": bool(evidence.get("phase_d_qualified")),
        "phase_d_blockers": list(evidence.get("phase_d_blockers") or []),
        "profiles": profile_summary,
    }


def _full_test_suite() -> dict[str, Any]:
    environment = dict(__import__("os").environ)
    environment["PYTHONPATH"] = "review_src"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    combined = f"{completed.stdout}\n{completed.stderr}"
    matched = re.search(r"(\d+) passed in ([0-9.]+)s", combined)
    failed_match = re.search(r"(\d+) failed", combined)
    error_match = re.search(r"(\d+) errors?", combined)
    failure_lines = [
        line.strip()
        for line in combined.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    ]
    return {
        "command": "python -m pytest tests -q",
        "exit_code": completed.returncode,
        "passed_count": int(matched.group(1)) if matched else None,
        "failed_count": int(failed_match.group(1)) if failed_match else 0,
        "error_count": int(error_match.group(1)) if error_match else 0,
        "failure_lines": failure_lines[:50],
        "duration_seconds": float(matched.group(2)) if matched else None,
        "passed": completed.returncode == 0 and matched is not None,
    }


def _runtime_snapshot() -> dict[str, Any]:
    base_url = env_text(
        "QWEN_NATIVE_BASE_URL",
        env_text("QWEN_BASE_URL", "http://127.0.0.1:8020"),
    ).rstrip("/")
    configured_model = env_text("QWEN_MODEL_ID", "taiwan-stock-qwen")
    snapshot: dict[str, Any] = {
        "available": False,
        "api_available": False,
        "model_resident": False,
        "probe_status": "unavailable",
        "probe_error_class": None,
        "runner": "Ollama",
        "runner_version": "",
        "model_id": configured_model,
        "model_digest": "",
        "quantization": "",
        "context_length": 0,
        "cpu_offload_bytes": None,
        "driver_version": "unverified",
        "cuda_version": "unverified",
        "hardware_probe_error_class": None,
    }
    nvidia_command = env_text("NVIDIA_SMI_COMMAND", "nvidia-smi")
    try:
        nvidia = subprocess.run(
            [nvidia_command],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
        version_match = re.search(
            r"NVIDIA-SMI\s+([^\s]+).*?CUDA(?: UMD)? Version:\s*([^\s]+)",
            nvidia,
        )
        snapshot["driver_version"] = version_match.group(1) if version_match else "unverified"
        snapshot["cuda_version"] = version_match.group(2) if version_match else "unverified"
    except (OSError, subprocess.SubprocessError) as exc:
        snapshot["hardware_probe_error_class"] = type(exc).__name__
    try:
        version_response = requests.get(f"{base_url}/api/version", timeout=5)
        version_response.raise_for_status()
        process_response = requests.get(f"{base_url}/api/ps", timeout=10)
        process_response.raise_for_status()
        version_payload = version_response.json()
        process_payload = process_response.json()
    except (requests.RequestException, ValueError, TypeError) as exc:
        snapshot["probe_error_class"] = type(exc).__name__
        return snapshot
    snapshot["api_available"] = True
    snapshot["runner_version"] = str((version_payload or {}).get("version") or "")
    models = list((process_payload or {}).get("models") or [])
    model = next(
        (
            row
            for row in models
            if str(row.get("name") or row.get("model") or "").removesuffix(":latest")
            == configured_model.removesuffix(":latest")
        ),
        {},
    )
    if not model:
        snapshot["probe_status"] = "configured_model_not_resident"
        return snapshot
    size = int(model.get("size") or 0)
    size_vram = int(model.get("size_vram") or 0)
    snapshot.update({
        "available": True,
        "model_resident": True,
        "probe_status": "ok",
        "model_id": str(model.get("name") or model.get("model") or configured_model),
        "model_digest": str(model.get("digest") or ""),
        "quantization": str((model.get("details") or {}).get("quantization_level") or ""),
        "context_length": int(model.get("context_length") or 0),
        "cpu_offload_bytes": max(0, size - size_vram),
    })
    return snapshot


def _line_runtime_source_snapshot() -> dict[str, Any]:
    current = current_runtime_source_fingerprint()
    port = env_int("LINE_GATEWAY_PORT", 8021, minimum=1, maximum=65_535)
    benchmark_url = f"http://127.0.0.1:{port}/internal/line-model-benchmark/source-fingerprint"
    health_url = f"http://127.0.0.1:{port}/healthz"
    token = env_text("BOT_MARKET_DATA_TOKEN")
    remote: dict[str, Any] = {}
    fingerprint_status: int | None = None
    fingerprint_error: str | None = None
    try:
        response = requests.get(
            benchmark_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        fingerprint_status = int(response.status_code)
        if response.status_code == 200:
            value = response.json()
            remote = value if isinstance(value, dict) else {}
    except (requests.RequestException, ValueError) as exc:
        fingerprint_error = type(exc).__name__
    source_matches = (
        fingerprint_status == 200
        and remote.get("contract_version") == RUNTIME_SOURCE_FINGERPRINT_CONTRACT
        and remote.get("complete") is True
        and current.get("complete") is True
        and remote.get("source_digest") == current.get("source_digest")
        and remote.get("source_hashes") == current.get("source_hashes")
    )

    line_ready = False
    health_status: int | None = None
    health_error: str | None = None
    degraded_checks: list[str] = []
    try:
        health_response = requests.get(health_url, timeout=5)
        health_status = int(health_response.status_code)
        health = health_response.json() if health_response.content else {}
        line_ready = (
            health_response.status_code == 200
            and isinstance(health, dict)
            and health.get("ready") is True
        )
        if isinstance(health, dict) and not line_ready:
            degraded_checks = sorted(
                key
                for key in (
                    "line_channel_secret_configured",
                    "line_access_token_configured",
                    "market_api_token_configured",
                    "qwen_base_url_configured",
                    "qwen_vision_configured",
                    "signature_verification_enabled",
                    "read_only_asserted",
                    "trading_disabled",
                    "decision_ready_required",
                    "model_recalculation_disabled",
                    "conversation_memory_ready",
                )
                if health.get(key) is not True
            )
    except (requests.RequestException, ValueError) as exc:
        health_error = type(exc).__name__
    return {
        "contract_version": RUNTIME_SOURCE_FINGERPRINT_CONTRACT,
        "current_source_digest": current.get("source_digest"),
        "remote_source_digest": remote.get("source_digest"),
        "remote_loaded_at": remote.get("captured_at"),
        "fingerprint_http_status": fingerprint_status,
        "fingerprint_error_class": fingerprint_error,
        "source_matches_current_workspace": source_matches,
        "health_http_status": health_status,
        "health_error_class": health_error,
        "line_ready": line_ready,
        "degraded_checks": degraded_checks,
    }


def main(output_path: Path | None = None) -> int:
    missing_freeze_files = [relative for relative in FREEZE_FILES if not (PROJECT_ROOT / relative).is_file()]
    if missing_freeze_files:
        raise SystemExit(f"freeze files missing: {missing_freeze_files}")
    frozen_hashes = {relative: _sha256(PROJECT_ROOT / relative) for relative in FREEZE_FILES}
    compile_result = _compile_freeze_files()
    migration_result = _migration_rollback_drill()
    tests = _full_test_suite()
    protected = _protected_hash_check()
    load_line_bot_env()
    runtime = _runtime_snapshot()
    line_runtime = _line_runtime_source_snapshot()
    candidate_matrix = _canonical_candidate_matrix()
    human_quality = _human_quality_evidence()
    durable_scheduler = _scheduler_evidence()
    pre_phase_a_blockers = [
        "stage7_cold_vision_memory_contention_evidence_incomplete",
        "stage8_security_diff_scan_incomplete",
        "pre_phase_a_production_schema_not_migrated",
    ]
    if not durable_scheduler["passed"]:
        pre_phase_a_blockers.extend(durable_scheduler["reason_codes"])
    future_release_phase_blockers = [
        "phase_d_driver_version_mismatch",
        "phase_d_five_trading_days_not_met",
        "phase_d_100_executions_per_profile_not_met",
        "phase_d_production_paired_outcomes_unavailable",
        "phase_d_statistical_gate_insufficient_power",
    ]
    if not human_quality["passed"]:
        pre_phase_a_blockers.append("stage7_independent_human_quality_ratings_unavailable")
    if not runtime["available"]:
        pre_phase_a_blockers.append("runtime_model_probe_unavailable")
    if not line_runtime["source_matches_current_workspace"]:
        pre_phase_a_blockers.append("running_line_source_does_not_match_frozen_workspace")
    if not line_runtime["line_ready"]:
        pre_phase_a_blockers.append("running_line_service_not_ready")
    if not candidate_matrix["integrity_passed"]:
        pre_phase_a_blockers.append("canonical_candidate_matrix_integrity_failed")
    if candidate_matrix["validator_rejected"]:
        pre_phase_a_blockers.append("actual_candidate_validator_rejections")
    if not candidate_matrix["profiles"] or any(
        not row["latency_gate_passed"] for row in candidate_matrix["profiles"].values()
    ):
        pre_phase_a_blockers.append("stage7_concurrency_latency_diagnostic_failed")
    if not compile_result["passed"]:
        pre_phase_a_blockers.append("freeze_python_compile_failed")
    if not migration_result["passed"]:
        pre_phase_a_blockers.append("migration_rollback_drill_failed")
    if not tests["passed"]:
        pre_phase_a_blockers.append("full_test_suite_failed")
    if not protected["passed"]:
        pre_phase_a_blockers.append("protected_hash_mismatch")
    if runtime["driver_version"] == EXPECTED_DRIVER:
        future_release_phase_blockers.remove("phase_d_driver_version_mismatch")
    if runtime["cuda_version"] != EXPECTED_CUDA:
        future_release_phase_blockers.append("phase_d_cuda_version_mismatch")
    pre_phase_a_blockers = list(dict.fromkeys(pre_phase_a_blockers))
    future_release_phase_blockers = list(dict.fromkeys(future_release_phase_blockers))
    phase_a_authorized = not pre_phase_a_blockers
    freeze_digest = _canonical_digest(frozen_hashes)
    manifest = {
        "freeze_contract": FREEZE_CONTRACT,
        "captured_at": datetime.now(TPE).isoformat(timespec="seconds"),
        "freeze_digest": freeze_digest,
        "frozen_file_count": len(frozen_hashes),
        "frozen_files": frozen_hashes,
        "runtime": runtime,
        "line_runtime": line_runtime,
        "expected_runtime": {
            "driver_version": EXPECTED_DRIVER,
            "cuda_version": EXPECTED_CUDA,
        },
        "python_compile": compile_result,
        "full_test_suite": tests,
        "protected_analysis": protected,
        "migration_rollback_drill": migration_result,
        "canonical_candidate_matrix": candidate_matrix,
        "expert_response_quality": human_quality,
        "durable_scheduler": durable_scheduler,
        "privacy_security_review": {
            "status": "incomplete",
            "codex_security_scan_started": False,
            "runtime_outputs_now_ignored": True,
            "reason": (
                "initial scan identity creation failed on an unignored runtime lock; "
                "runtime outputs are now excluded, but no completed formal scan exists"
            ),
            "raw_image_or_full_ocr_persistence_gate": "covered_by_passing_regression_but_not_a_completed_security_scan",
        },
        "stage7_release_state": {
            "statistical_normal": "remain_shadow_insufficient_power",
            "statistical_material_event": "remain_shadow_insufficient_power",
            "quality": human_quality["status"],
            "candidate_delivery": "shadow_only",
            "release_bound_items_pending": True,
        },
        "rollout": {
            "observed": env_text("LINE_MODEL_V2_ROLLOUT", "off"),
            "changed_by_stage8": False,
            "historical_state_does_not_count_as_phase_a": True,
        },
        "gate_order": {
            "pre_phase_a": "Stages 0-8 must be complete before Phase A starts",
            "future_release_phases": "Phase D evidence is collected after Phase A shadow and cannot be a Phase A prerequisite",
        },
        "release_ready": phase_a_authorized,
        "phase_a_authorized": phase_a_authorized,
        "pre_phase_a_blockers": pre_phase_a_blockers,
        "future_release_phase_blockers": future_release_phase_blockers,
        "release_phases": {
            "A": (
                "authorized_not_started" if phase_a_authorized
                else "not_started_stage8_prerequisite_failed"
            ),
            "B": "not_started_phase_a_not_completed",
            "C": "not_started_phase_b_not_completed",
            "D": "not_started_phase_c_not_completed",
            "E": "not_started_phase_d_not_completed",
        },
        "release_blockers": [*pre_phase_a_blockers, *future_release_phase_blockers],
    }
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        print(rendered, end="")
    else:
        destination = output_path if output_path.is_absolute() else PROJECT_ROOT / output_path
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as handle:
                handle.write(rendered)
                handle.flush()
                temporary_path = Path(handle.name)
            temporary_path.replace(destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "freeze_digest": freeze_digest,
                    "release_ready": phase_a_authorized,
                    "full_test_suite_passed": tests["passed"],
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Atomically replace a manifest path relative to the project root.",
    )
    cli_args = parser.parse_args()
    raise SystemExit(main(cli_args.output))
