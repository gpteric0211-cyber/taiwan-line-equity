from __future__ import annotations

import subprocess

import requests

from scripts import freeze_single_track_v3_stage8 as freeze


def test_runtime_snapshot_records_connection_failure_as_release_evidence(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise requests.ConnectionError("runtime unavailable")

    monkeypatch.setattr(freeze.requests, "get", unavailable)
    monkeypatch.setattr(
        freeze.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout=(
                "NVIDIA-SMI 610.88 Driver Version: 610.88 "
                "CUDA Version: 13.3\n"
            ),
            stderr="",
        ),
    )

    snapshot = freeze._runtime_snapshot()

    assert snapshot["available"] is False
    assert snapshot["api_available"] is False
    assert snapshot["model_resident"] is False
    assert snapshot["probe_status"] == "unavailable"
    assert snapshot["probe_error_class"] == "ConnectionError"
    assert snapshot["driver_version"] == "610.88"
    assert snapshot["cuda_version"] == "13.3"
    assert snapshot["model_digest"] == ""
