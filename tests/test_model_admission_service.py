from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services import model_admission_service  # noqa: E402


def test_interactive_overtakes_queued_shadow_during_idle_grace(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "150")
    controller = model_admission_service._AdmissionController()
    order: list[str] = []

    shadow = controller.submit(
        lambda: order.append("shadow"),
        priority=10,
        category="shadow_candidate",
        deadline_monotonic=None,
    )
    time.sleep(0.02)
    interactive = controller.submit(
        lambda: order.append("interactive"),
        priority=0,
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() + 1,
    )

    interactive.result(timeout=2)
    shadow.result(timeout=2)

    assert order == ["interactive", "shadow"]
    assert controller.snapshot()["running_task_preemptible"] is False
    assert controller.snapshot()["shadow_preemption_supported"] is True


def test_contiguous_shadow_batch_pays_idle_grace_once(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "120")
    controller = model_admission_service._AdmissionController()
    started: list[float] = []
    submitted = time.monotonic()

    futures = [
        controller.submit(
            lambda: started.append(time.monotonic()),
            priority=10,
            category="shadow_candidate",
            deadline_monotonic=None,
        )
        for _ in range(3)
    ]
    for future in futures:
        future.result(timeout=2)

    assert len(started) == 3
    assert started[0] - submitted >= 0.08
    assert started[-1] - started[0] < 0.08


def test_expired_interactive_deadline_is_rejected() -> None:
    controller = model_admission_service._AdmissionController()
    future = controller.submit(
        lambda: None,
        priority=0,
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() - 1,
    )

    with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
        future.result(timeout=1)

    assert captured.value.reason_code == "deadline_admission_rejected"


def test_predicted_deadline_miss_is_rejected_before_model_execution(monkeypatch) -> None:
    controller = model_admission_service._AdmissionController()
    executed = False

    def work() -> None:
        nonlocal executed
        executed = True

    future = controller.submit(
        work,
        priority=0,
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() + 0.2,
        predicted_duration_ms=500,
    )

    with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
        future.result(timeout=1)

    assert captured.value.reason_code == "predicted_deadline_admission_rejected"
    assert executed is False
    assert controller.snapshot()["predicted_deadline_rejected"] == 1


def test_predicted_queue_backlog_rejects_only_requests_that_cannot_finish(monkeypatch) -> None:
    controller = model_admission_service._AdmissionController()
    blocker = threading.Event()
    started = threading.Event()

    first = controller.submit(
        lambda: (started.set(), blocker.wait(1)),
        priority=0,
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() + 2,
        predicted_duration_ms=600,
    )
    assert started.wait(timeout=1)
    second = controller.submit(
        lambda: "second",
        priority=0,
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() + 0.7,
        predicted_duration_ms=600,
    )

    with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
        second.result(timeout=1)
    blocker.set()
    first.result(timeout=1)

    assert captured.value.reason_code == "predicted_deadline_admission_rejected"


def test_predicted_rejection_does_not_cancel_background_work(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    controller = model_admission_service._AdmissionController()
    started = threading.Event()
    release = threading.Event()
    cancellation = threading.Event()

    background = controller.submit(
        lambda: release.wait(1),
        priority=10,
        category="shadow_candidate",
        deadline_monotonic=None,
        on_start=lambda _wait: started.set(),
        cancel_event=cancellation,
        preemptible=True,
    )
    assert started.wait(timeout=1)

    rejected = controller.submit(
        lambda: "must-not-run",
        priority=0,
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() + 0.2,
        predicted_duration_ms=500,
    )

    with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
        rejected.result(timeout=1)
    assert captured.value.reason_code == "predicted_deadline_admission_rejected"
    assert cancellation.is_set() is False
    assert controller.snapshot()["preemption_requests"] == 0
    release.set()
    background.result(timeout=1)


def test_running_shadow_is_preempted_when_interactive_arrives(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    controller = model_admission_service._AdmissionController()
    cancellation = threading.Event()
    started = threading.Event()

    def shadow_work() -> None:
        started.set()
        while not cancellation.wait(0.01):
            pass
        raise model_admission_service.ModelAdmissionError(
            "yield to interactive",
            reason_code="shadow_preempted_by_interactive",
        )

    shadow = controller.submit(
        shadow_work,
        priority=10,
        category="shadow_candidate",
        deadline_monotonic=None,
        cancel_event=cancellation,
        preemptible=True,
    )
    assert started.wait(timeout=1)
    submitted = time.monotonic()
    interactive = controller.submit(
        lambda: "interactive-complete",
        priority=0,
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() + 1,
    )

    assert interactive.result(timeout=1).value == "interactive-complete"
    interactive_wait_ms = int((time.monotonic() - submitted) * 1000)
    with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
        shadow.result(timeout=1)

    snapshot = controller.snapshot()
    assert captured.value.reason_code == "shadow_preempted_by_interactive"
    assert interactive_wait_ms < 250
    assert snapshot["preemption_requests"] == 1
    assert snapshot["preemptions_completed"] == 1


def test_running_maintenance_is_preempted_when_interactive_arrives(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("LINE_MODEL_PREEMPTION_P95_MS", "50")
    controller = model_admission_service._AdmissionController()
    monkeypatch.setattr(model_admission_service, "_CONTROLLER", controller)
    started = threading.Event()

    def maintenance_work(cancellation_event: threading.Event) -> None:
        started.set()
        while not cancellation_event.wait(0.01):
            pass
        raise RuntimeError("cancelled stream")

    maintenance = model_admission_service.submit_maintenance_model(
        lambda: None,
        category="maintenance_conversation_compaction",
        cancellable_callable=maintenance_work,
    )
    assert started.wait(timeout=1)
    interactive = model_admission_service.run_interactive_model(
        lambda: "interactive-complete",
        category="interactive_stock_analysis",
        deadline_monotonic=time.monotonic() + 1,
        predicted_duration_ms=100,
    )

    assert interactive.value == "interactive-complete"
    with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
        maintenance.result(timeout=1)

    assert captured.value.reason_code == "maintenance_preempted_by_interactive"
    assert controller.snapshot()["preemptions_completed"] == 1
    assert controller.snapshot()["maintenance_preemption_supported"] is True


def test_nonpreemptible_cold_warmup_causes_early_interactive_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    controller = model_admission_service._AdmissionController()
    monkeypatch.setattr(model_admission_service, "_CONTROLLER", controller)
    started = threading.Event()
    release = threading.Event()

    def cold_warmup() -> str:
        started.set()
        assert release.wait(timeout=2)
        return "READY"

    maintenance = model_admission_service.submit_maintenance_model(
        cold_warmup,
        category="maintenance_startup_text_warmup",
        predicted_duration_ms=70_000,
    )
    assert started.wait(timeout=1)
    try:
        with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
            model_admission_service.run_interactive_model(
                lambda: "must-not-run",
                category="interactive_stock_analysis",
                deadline_monotonic=time.monotonic() + 1,
                predicted_duration_ms=100,
            )
        assert captured.value.reason_code == "predicted_deadline_admission_rejected"
        snapshot = controller.snapshot()
        assert snapshot["active_category"] == "maintenance_startup_text_warmup"
        assert snapshot["running_task_preemptible"] is False
        assert snapshot["preemption_requests"] == 0
    finally:
        release.set()
        assert maintenance.result(timeout=1).value == "READY"


def test_vision_uses_separate_predicted_duration(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_INTERACTIVE_P95_MS", "15000")
    monkeypatch.setenv("LINE_MODEL_VISION_P95_MS", "35000")

    assert (
        model_admission_service._default_interactive_predicted_duration_ms(
            "interactive_stock_analysis"
        )
        == 15_000
    )
    assert (
        model_admission_service._default_interactive_predicted_duration_ms(
            "interactive_chart_vision"
        )
        == 35_000
    )
    assert (
        model_admission_service._default_interactive_predicted_duration_ms(
            "interactive_benchmark_vision"
        )
        == 35_000
    )


def test_active_nonpreemptible_vision_causes_early_stock_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("LINE_MODEL_VISION_P95_MS", "35000")
    controller = model_admission_service._AdmissionController()
    monkeypatch.setattr(model_admission_service, "_CONTROLLER", controller)
    started = threading.Event()
    release = threading.Event()

    def vision_work() -> str:
        started.set()
        assert release.wait(timeout=2)
        return "VISION_READY"

    vision_future = controller.submit(
        vision_work,
        priority=0,
        category="interactive_benchmark_vision",
        deadline_monotonic=time.monotonic() + 60,
        predicted_duration_ms=(
            model_admission_service._default_interactive_predicted_duration_ms(
                "interactive_benchmark_vision"
            )
        ),
    )
    assert started.wait(timeout=1)
    try:
        with pytest.raises(model_admission_service.ModelAdmissionError) as captured:
            model_admission_service.run_interactive_model(
                lambda: "must-not-run",
                category="interactive_stock_analysis",
                deadline_monotonic=time.monotonic() + 31,
            )
        assert captured.value.reason_code == "predicted_deadline_admission_rejected"
        snapshot = controller.snapshot()
        assert snapshot["active_category"] == "interactive_benchmark_vision"
        assert snapshot["active_predicted_duration_ms"] == 35_000
        assert snapshot["active_remaining_estimate_ms"] > 30_000
    finally:
        release.set()
        assert vision_future.result(timeout=1).value == "VISION_READY"
