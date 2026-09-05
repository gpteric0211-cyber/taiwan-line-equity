from __future__ import annotations

import sys
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services import line_model_warmup_service as warmup_service  # noqa: E402
from adapter.qwen_local import QwenClientError  # noqa: E402


def test_cold_model_schedules_one_admission_visible_warmup(monkeypatch) -> None:
    future: Future[Any] = Future()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(warmup_service, "_FUTURE", None)
    monkeypatch.setattr(warmup_service, "qwen_model_resident", lambda **_kwargs: False)
    monkeypatch.setenv("LINE_MODEL_COLD_WARMUP_P95_MS", "70000")
    monkeypatch.delenv("LINE_MODEL_STARTUP_LOAD_TIMEOUT_SECONDS", raising=False)

    def fake_submit(callable_: Any, **kwargs: Any) -> Future[Any]:
        captured["callable"] = callable_
        captured.update(kwargs)
        return future

    monkeypatch.setattr(warmup_service, "submit_maintenance_model", fake_submit)

    first = warmup_service.ensure_background_text_model_warmup()
    second = warmup_service.ensure_background_text_model_warmup()

    assert first == {
        "status": "scheduled",
        "scheduled": True,
        "predicted_duration_ms": 210000,
    }
    assert second == {"status": "warming", "scheduled": False}
    assert captured["category"] == "maintenance_startup_text_warmup"
    assert captured["predicted_duration_ms"] == 210000
    assert "cancellable_callable" not in captured


def test_resident_model_does_not_schedule_warmup(monkeypatch) -> None:
    monkeypatch.setattr(warmup_service, "qwen_model_resident", lambda **_kwargs: True)
    monkeypatch.setattr(
        warmup_service,
        "submit_maintenance_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not submit")),
    )

    assert warmup_service.ensure_background_text_model_warmup() == {
        "status": "resident",
        "scheduled": False,
    }


def _capture_cold_job(monkeypatch, *, resident_after=True, load_error=None, chat_error=None):
    """Real warmup callable, fake I/O and submission; never touch the operator's GPU."""
    calls = []
    captured = {}
    residency = iter([False, resident_after])
    monkeypatch.setattr(warmup_service, "_FUTURE", None)
    monkeypatch.setattr(warmup_service, "qwen_model_resident", lambda **_kw: next(residency))
    monkeypatch.setenv("LINE_MODEL_COLD_WARMUP_P95_MS", "70000")

    def load(**kwargs):
        calls.append(("load", kwargs))
        if load_error:
            raise load_error
        return {"load_duration_ms": 125000, "total_duration_ms": 125001}

    def chat(*args, **kwargs):
        calls.append(("ready", kwargs))
        if chat_error:
            raise chat_error
        return "READY"

    def submit(callable_, **kwargs):
        captured.update(callable=callable_, **kwargs)
        return Future()

    # raising=False permits the red phase to run against the old implementation.
    monkeypatch.setattr(warmup_service, "qwen_preload_text_model", load, raising=False)
    monkeypatch.setattr(warmup_service, "qwen_chat", chat)
    monkeypatch.setattr(warmup_service, "submit_maintenance_model", submit)
    return calls, captured


@pytest.mark.parametrize("configured, expected", [(None, 180), ("240", 240), ("999", 240), ("1", 30), ("invalid", 180)])
def test_admitted_cold_load_precedes_bounded_ready(monkeypatch, configured, expected):
    calls, captured = _capture_cold_job(monkeypatch)
    if configured is None:
        monkeypatch.delenv("LINE_MODEL_STARTUP_LOAD_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("LINE_MODEL_STARTUP_LOAD_TIMEOUT_SECONDS", configured)
    warmup_service.ensure_background_text_model_warmup()
    assert calls == []  # No preload or generation before the admission slot executes.
    assert captured["category"] == "maintenance_startup_text_warmup"
    assert captured["predicted_duration_ms"] == max(70000, (expected + 30) * 1000)
    assert captured["callable"]() == "READY"
    assert [name for name, _ in calls] == ["load", "ready"]
    assert calls[0][1] == {"timeout_seconds": expected}
    assert calls[1][1]["timeout_seconds"] == 20
    assert calls[1][1]["max_output_tokens"] == 128
    assert calls[1][1].get("allow_background_timeout", False) is False


def test_preload_error_propagates_without_ready_or_retry(monkeypatch):
    error = QwenClientError("synthetic timeout", reason_code="model_load_timeout")
    calls, captured = _capture_cold_job(monkeypatch, load_error=error)
    warmup_service.ensure_background_text_model_warmup()
    with pytest.raises(QwenClientError) as caught:
        captured["callable"]()
    assert caught.value is error
    assert caught.value.reason_code == "model_load_timeout"
    assert [name for name, _ in calls] == ["load"]


def test_preload_without_residency_cannot_start_ready(monkeypatch):
    calls, captured = _capture_cold_job(monkeypatch, resident_after=False)
    warmup_service.ensure_background_text_model_warmup()
    with pytest.raises(QwenClientError) as caught:
        captured["callable"]()
    assert caught.value.reason_code == "model_not_resident_after_load"
    assert [name for name, _ in calls] == ["load"]


def test_ready_error_propagates_without_reloading(monkeypatch):
    error = QwenClientError("synthetic READY failure", reason_code="qwen_timeout")
    calls, captured = _capture_cold_job(monkeypatch, chat_error=error)
    warmup_service.ensure_background_text_model_warmup()
    with pytest.raises(QwenClientError) as caught:
        captured["callable"]()
    assert caught.value is error
    assert [name for name, _ in calls] == ["load", "ready"]


def test_failed_warmup_future_is_visible(monkeypatch):
    future = Future()
    future.set_exception(QwenClientError("synthetic timeout", reason_code="model_load_timeout"))
    monkeypatch.setattr(warmup_service, "_FUTURE", future)
    monkeypatch.setattr(warmup_service, "qwen_model_resident", lambda **_kw: False)
    assert warmup_service.background_text_model_warmup_status() == {"status": "failed", "resident": False}
