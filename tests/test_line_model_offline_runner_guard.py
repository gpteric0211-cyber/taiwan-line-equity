from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_line_model_shadow_evidence.py"
SPEC = importlib.util.spec_from_file_location("run_line_model_shadow_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_legacy_runner_cannot_bypass_live_stack_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_port_accepting", lambda _port: False)

    with pytest.raises(RuntimeError, match="permanently disabled"):
        runner._enforce_nonproduction_runner_boundary(
            allow_live_stack=True,
            nonproduction_offline_ack=True,
        )


def test_legacy_runner_refuses_when_production_ports_are_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_port_accepting", lambda port: port == 8021)

    with pytest.raises(RuntimeError, match="do not stop the production LINE stack"):
        runner._enforce_nonproduction_runner_boundary(
            allow_live_stack=False,
            nonproduction_offline_ack=True,
        )


def test_legacy_runner_requires_nonproduction_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_port_accepting", lambda _port: False)
    monkeypatch.delenv("APP_ENV", raising=False)

    with pytest.raises(RuntimeError, match="nonproduction-offline-ack"):
        runner._enforce_nonproduction_runner_boundary(
            allow_live_stack=False,
            nonproduction_offline_ack=False,
        )


def test_legacy_runner_can_only_enter_isolated_nonproduction_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_port_accepting", lambda _port: False)
    monkeypatch.setenv("APP_ENV", "development")

    runner._enforce_nonproduction_runner_boundary(
        allow_live_stack=False,
        nonproduction_offline_ack=True,
    )
