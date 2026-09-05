from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import run_line_model_phase_d_probes as probes


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status
        self.closed = False

    def json(self):
        return self.body

    def close(self):
        self.closed = True


def test_default_probe_set_never_requests_cold_unload():
    selected = probes._selected_probes(include_cold=False, maintenance_window_confirmed=False)
    assert selected == probes.PROBES
    assert not any("cold" in endpoint for _, endpoint in selected)


def test_runtime_source_preflight_requires_exact_loaded_hashes(monkeypatch):
    current = probes.current_runtime_source_fingerprint()
    monkeypatch.setattr(probes.requests, "get", lambda *a, **k: Response(current))

    passed = probes._runtime_source_preflight(token="secret", base_url="http://localhost")

    assert passed["matches"] is True
    assert passed["reason_codes"] == ["pass"]


def test_runtime_source_preflight_rejects_stale_process(monkeypatch):
    current = probes.current_runtime_source_fingerprint()
    stale = {
        **current,
        "source_digest": "0" * 64,
        "source_hashes": {**current["source_hashes"], "review_src/api/line_webhook.py": "0" * 64},
    }
    monkeypatch.setattr(probes.requests, "get", lambda *a, **k: Response(stale))

    rejected = probes._runtime_source_preflight(token="secret", base_url="http://localhost")

    assert rejected["matches"] is False
    assert rejected["reason_codes"] == [
        "runtime_source_digest_mismatch",
        "runtime_source_hashes_mismatch",
    ]


def test_main_refuses_stale_runtime_before_health_or_probe_post(monkeypatch, tmp_path, capsys):
    output = tmp_path / "probe.json"
    monkeypatch.setattr(sys, "argv", ["probes", "--output", str(output)])
    monkeypatch.setattr(probes, "dotenv_values", lambda _: {"BOT_MARKET_DATA_TOKEN": "x" * 32})
    monkeypatch.setattr(
        probes,
        "_runtime_source_preflight",
        lambda **_kwargs: {
            "matches": False,
            "reason_codes": ["runtime_source_digest_mismatch"],
        },
    )
    monkeypatch.setattr(
        probes.phase_d,
        "_service_health",
        lambda: pytest.fail("health must not run after source mismatch"),
    )
    monkeypatch.setattr(
        probes.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("probe POST must not run"),
    )

    assert probes.main() == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["reason_code"] == "runtime_source_fingerprint_mismatch"
    assert refusal["model_probe_posts"] == 0
    assert not output.exists()


def test_cold_probe_requires_explicit_maintenance_confirmation():
    with pytest.raises(ValueError, match="maintenance-window-confirmed"):
        probes._selected_probes(include_cold=True, maintenance_window_confirmed=False)
    assert probes._selected_probes(include_cold=True, maintenance_window_confirmed=True)[0] == probes.COLD_PROBE


@pytest.mark.parametrize("capability", [
    {},
    {"benchmark_contract": "line-model-live-cold-load-v1", "enabled": True},
    {"benchmark_contract": "line-model-maintenance-cold-load-v2", "enabled": False},
])
def test_old_or_disabled_server_never_receives_cold_post(monkeypatch, tmp_path, capability):
    monkeypatch.setattr(probes.requests, "get", lambda *a, **k: Response(capability))
    monkeypatch.setattr(probes.requests, "post", lambda *a, **k: pytest.fail("must not unload"))
    path = tmp_path / "journal.jsonl"
    with path.open("x", encoding="utf-8") as journal:
        result = probes._collect_probes((probes.COLD_PROBE,), token="secret", base_url="http://localhost", journal=journal)
    assert result["cold_load"]["reason_code"] == "cold_probe_not_enabled_or_unsupported"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["state"] for row in rows] == ["started", "finished"]


def test_network_failure_is_journalled_and_aborts_remaining_probes(monkeypatch, tmp_path):
    calls = []
    def post(*args, **kwargs):
        calls.append(args)
        raise probes.requests.Timeout("token=private-secret")
    monkeypatch.setattr(probes.requests, "post", post)
    path = tmp_path / "journal.jsonl"
    with path.open("x", encoding="utf-8") as journal:
        result = probes._collect_probes(probes.PROBES, token="secret", base_url="http://localhost", journal=journal)
    assert len(calls) == 1
    assert result[probes.PROBES[0][0]]["transport_error_class"] == "Timeout"
    assert result[probes.PROBES[1][0]]["skip_reason"] == "previous_probe_failed"
    text = path.read_text()
    assert "private-secret" not in text
    assert [json.loads(line)["state"] for line in text.splitlines()] == ["started", "finished", "skipped"]


def test_cold_client_uses_server_timeout_and_preserves_structured_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(probes.requests, "get", lambda *a, **k: Response({
        "benchmark_contract": "line-model-maintenance-cold-load-v2", "enabled": True,
        "maintenance_only": True, "exclusive_admission": True, "load_timeout_seconds": 1260,
    }))
    captured = []
    def post(*args, **kwargs):
        captured.append(kwargs)
        return Response({"probe_status": "fail", "failure_reason_code": "model_load_timeout", "recovery_required": True})
    monkeypatch.setattr(probes.requests, "post", post)
    with (tmp_path / "journal.jsonl").open("x", encoding="utf-8") as journal:
        result = probes._collect_probes((probes.COLD_PROBE, *probes.PROBES), token="secret", base_url="http://localhost", journal=journal)
    assert len(captured) == 1
    assert captured[0]["timeout"] == (5, 1350)
    assert captured[0]["json"]["maintenance_window_confirmed"] is True
    assert not probes._probe_passed(result["cold_load"], name="cold_load")
    assert result[probes.PROBES[0][0]]["skip_reason"] == "previous_probe_failed"


def test_main_keeps_artifact_when_post_health_check_fails(monkeypatch, tmp_path):
    output = tmp_path / "probe.json"
    monkeypatch.setattr(sys, "argv", ["probes", "--output", str(output)])
    monkeypatch.setenv("BOT_MARKET_DATA_TOKEN", "before")
    monkeypatch.setattr(probes, "dotenv_values", lambda _: {"BOT_MARKET_DATA_TOKEN": "x" * 32})
    monkeypatch.setattr(
        probes,
        "_runtime_source_preflight",
        lambda **_kwargs: {"matches": True, "reason_codes": ["pass"]},
    )
    calls = []
    def health():
        calls.append(1)
        if len(calls) > 1:
            raise probes.requests.Timeout("private health endpoint")
        return {"line": {"ready": True}}
    monkeypatch.setattr(probes.phase_d, "_service_health", health)
    monkeypatch.setattr(probes.phase_d, "_environment_snapshot", lambda _: ({}, {}, {}))
    monkeypatch.setattr(probes.phase_d, "_health_sampler", lambda *args: None)
    monkeypatch.setattr(probes.phase_d, "_health_evidence_summary", lambda _: {"all_services_ready": True})
    monkeypatch.setattr(probes.requests, "post", lambda *a, **k: Response({"probe_status": "pass"}))
    assert probes.main() == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["health_after"]["error_class"] == "Timeout"
    assert artifact["requested_probe_set_complete"] is False
    assert artifact["cold_probe_requested"] is False
    assert artifact["probe_set_complete"] is False
    assert "private health endpoint" not in output.read_text(encoding="utf-8")
    assert (tmp_path / "probe_attempts.jsonl").is_file()


@pytest.mark.parametrize("name", ["cold_load", "memory_compaction_contention", "vision_stable_reply_contention"])
def test_http_200_alone_never_passes_a_probe(name):
    assert not probes._probe_passed({"http_status": 200, "body": {}}, name=name)
