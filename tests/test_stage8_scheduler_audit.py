from __future__ import annotations

from scripts import freeze_single_track_v3_stage8 as freeze


def _complete_payload() -> dict:
    return {
        "contract_version": freeze.SCHEDULER_AUDIT_CONTRACT,
        "required_slots": list(freeze.REQUIRED_SCHEDULER_SLOTS),
        "source_implementation": {
            "runner_present": True,
            "installer_present": True,
            "required_slot_markers_present": True,
            "retrieval_worker_present": True,
        },
        "windows_tasks": {
            "query_status": "ok",
            "required_tasks_installed": True,
            "required_tasks_enabled": True,
        },
        "restart_wake_catch_up": {
            "status": "verified",
            "verified": True,
        },
        "gate_passed": True,
    }


def test_scheduler_audit_requires_source_tasks_and_restart_evidence() -> None:
    payload = _complete_payload()
    payload["source_implementation"]["runner_present"] = False
    payload["source_implementation"]["retrieval_worker_present"] = False
    payload["windows_tasks"]["required_tasks_enabled"] = False
    payload["restart_wake_catch_up"] = {
        "status": "unavailable",
        "verified": False,
    }
    payload["gate_passed"] = False

    result = freeze._evaluate_scheduler_evidence(payload)

    assert result["passed"] is False
    assert result["reason_codes"] == [
        "stage3_single_track_v3_scheduler_source_missing",
        "stage3_single_track_v3_retrieval_worker_missing",
        "stage8_single_track_v3_scheduler_tasks_not_ready",
        "stage8_single_track_v3_restart_wake_catch_up_unverified",
        "stage8_single_track_v3_scheduler_gate_not_passed",
    ]


def test_scheduler_audit_passes_only_when_every_required_gate_is_present() -> None:
    result = freeze._evaluate_scheduler_evidence(_complete_payload())

    assert result["passed"] is True
    assert result["reason_codes"] == []
