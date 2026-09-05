from __future__ import annotations

"""Single-process deadline-aware admission for work sharing the local GPU.

Queued interactive LINE work outranks shadow and maintenance work. Ollama calls
already executing cannot be safely preempted, so shadow jobs receive an idle
grace window before they may start and this limitation is exposed in metrics.
"""

import heapq
import itertools
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from core.line_bot_config import env_int


T = TypeVar("T")


class ModelAdmissionError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ModelExecution(Generic[T]):
    value: T
    queue_wait_ms: int
    execution_ms: int
    category: str


@dataclass(order=True)
class _QueuedTask:
    priority: int
    deadline_sort: float
    sequence: int
    category: str = field(compare=False)
    submitted_at: float = field(compare=False)
    deadline_monotonic: float | None = field(compare=False)
    callable: Callable[[], Any] = field(compare=False)
    on_start: Callable[[int], None] | None = field(compare=False)
    cancel_event: threading.Event = field(compare=False)
    preemptible: bool = field(compare=False)
    predicted_duration_ms: int = field(compare=False)
    future: Future[ModelExecution[Any]] = field(compare=False)


class _AdmissionController:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._queue: list[_QueuedTask] = []
        self._sequence = itertools.count()
        self._active_category = ""
        self._completed = 0
        self._deadline_rejected = 0
        self._predicted_deadline_rejected = 0
        self._queue_rejected = 0
        self._preemption_requests = 0
        self._preemptions_completed = 0
        self._active_cancel_event: threading.Event | None = None
        self._active_preemptible = False
        self._active_started_at = 0.0
        self._active_predicted_duration_ms = 0
        self._worker = threading.Thread(
            target=self._run,
            name="line-model-admission",
            daemon=True,
        )
        self._worker.start()

    def submit(
        self,
        callable_: Callable[[], T],
        *,
        priority: int,
        category: str,
        deadline_monotonic: float | None,
        on_start: Callable[[int], None] | None = None,
        cancel_event: threading.Event | None = None,
        preemptible: bool = False,
        predicted_duration_ms: int = 0,
    ) -> Future[ModelExecution[T]]:
        # Reject every own-worker submission before queueing or scheduler effects.
        # Covers on_start/Future callbacks without relying on active-category lifetime.
        if threading.current_thread() is self._worker:
            raise ModelAdmissionError(
                "model admission reentry is not allowed",
                reason_code="admission_reentry_rejected",
            )
        future: Future[ModelExecution[T]] = Future()
        now = time.monotonic()
        if deadline_monotonic is not None and deadline_monotonic <= now:
            future.set_exception(
                ModelAdmissionError(
                    "model deadline already expired",
                    reason_code="deadline_admission_rejected",
                )
            )
            return future
        with self._condition:
            predicted_ms = max(0, int(predicted_duration_ms))
            if priority == 0 and deadline_monotonic is not None and predicted_ms > 0:
                active_remaining_ms = 0
                if self._active_category:
                    if self._active_preemptible:
                        active_remaining_ms = env_int(
                            "LINE_MODEL_PREEMPTION_P95_MS",
                            2_500,
                            minimum=100,
                            maximum=30_000,
                        )
                    elif self._active_predicted_duration_ms > 0:
                        elapsed_ms = int(max(0.0, now - self._active_started_at) * 1000)
                        active_remaining_ms = max(
                            0,
                            self._active_predicted_duration_ms - elapsed_ms,
                        )
                queued_interactive_ms = sum(
                    max(0, task.predicted_duration_ms)
                    for task in self._queue
                    if task.priority == 0
                )
                predicted_finish = now + (
                    active_remaining_ms + queued_interactive_ms + predicted_ms
                ) / 1000.0
                if predicted_finish >= deadline_monotonic:
                    self._predicted_deadline_rejected += 1
                    future.set_exception(
                        ModelAdmissionError(
                            "model task is predicted to miss its reply deadline",
                            reason_code="predicted_deadline_admission_rejected",
                        )
                    )
                    return future
            if priority == 0 and self._active_preemptible and self._active_cancel_event is not None:
                if not self._active_cancel_event.is_set():
                    self._active_cancel_event.set()
                    self._preemption_requests += 1
            maximum = env_int("LINE_MODEL_GPU_QUEUE_MAX", 64, minimum=1, maximum=512)
            if len(self._queue) >= maximum:
                self._queue_rejected += 1
                future.set_exception(
                    ModelAdmissionError(
                        "model queue is full",
                        reason_code="gpu_queue_full",
                    )
                )
                return future
            heapq.heappush(
                self._queue,
                _QueuedTask(
                    priority=int(priority),
                    deadline_sort=deadline_monotonic if deadline_monotonic is not None else float("inf"),
                    sequence=next(self._sequence),
                    category=str(category),
                    submitted_at=now,
                    deadline_monotonic=deadline_monotonic,
                    callable=callable_,
                    on_start=on_start,
                    cancel_event=cancel_event or threading.Event(),
                    preemptible=bool(preemptible),
                    predicted_duration_ms=predicted_ms,
                    future=future,
                ),
            )
            self._condition.notify_all()
        return future

    def _run(self) -> None:
        background_batch_active = False
        while True:
            with self._condition:
                while not self._queue:
                    background_batch_active = False
                    self._condition.wait()
                task = self._queue[0]
                if task.priority >= 10 and not background_batch_active:
                    grace = env_int(
                        "LINE_MODEL_SHADOW_IDLE_GRACE_MS",
                        2_000,
                        minimum=0,
                        maximum=30_000,
                    ) / 1000.0
                    if grace > 0:
                        self._condition.wait(timeout=grace)
                        if not self._queue:
                            continue
                        task = self._queue[0]
                task = heapq.heappop(self._queue)
                background_batch_active = task.priority >= 10
                self._active_category = task.category
                self._active_cancel_event = task.cancel_event
                self._active_preemptible = task.preemptible
                self._active_started_at = time.monotonic()
                self._active_predicted_duration_ms = task.predicted_duration_ms
            now = time.monotonic()
            if task.deadline_monotonic is not None and now >= task.deadline_monotonic:
                self._deadline_rejected += 1
                task.future.set_exception(
                    ModelAdmissionError(
                        "model task would start after its deadline",
                        reason_code="deadline_admission_rejected",
                    )
                )
                with self._condition:
                    self._active_category = ""
                    self._active_cancel_event = None
                    self._active_preemptible = False
                    self._active_started_at = 0.0
                    self._active_predicted_duration_ms = 0
                continue
            started = time.monotonic()
            queue_wait_ms = int((started - task.submitted_at) * 1000)
            try:
                if task.on_start is not None:
                    task.on_start(queue_wait_ms)
                value = task.callable()
            except BaseException as exc:
                if (
                    isinstance(exc, ModelAdmissionError)
                    and exc.reason_code.endswith("_preempted_by_interactive")
                ):
                    self._preemptions_completed += 1
                task.future.set_exception(exc)
            else:
                task.future.set_result(
                    ModelExecution(
                        value=value,
                        queue_wait_ms=queue_wait_ms,
                        execution_ms=int((time.monotonic() - started) * 1000),
                        category=task.category,
                    )
                )
            finally:
                with self._condition:
                    self._active_category = ""
                    self._active_cancel_event = None
                    self._active_preemptible = False
                    self._active_started_at = 0.0
                    self._active_predicted_duration_ms = 0
                    self._completed += 1
                    self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            queued_by_category: dict[str, int] = {}
            for task in self._queue:
                queued_by_category[task.category] = queued_by_category.get(task.category, 0) + 1
            active_elapsed_ms = (
                int(max(0.0, time.monotonic() - self._active_started_at) * 1000)
                if self._active_category and self._active_started_at > 0
                else 0
            )
            active_remaining_estimate_ms = (
                max(0, self._active_predicted_duration_ms - active_elapsed_ms)
                if self._active_category and self._active_predicted_duration_ms > 0
                else 0
            )
            return {
                "queue_depth": len(self._queue),
                "queued_by_category": dict(sorted(queued_by_category.items())),
                "active_category": self._active_category or None,
                "active_predicted_duration_ms": self._active_predicted_duration_ms,
                "active_elapsed_ms": active_elapsed_ms,
                "active_remaining_estimate_ms": active_remaining_estimate_ms,
                "completed_tasks": self._completed,
                "deadline_rejected": self._deadline_rejected,
                "predicted_deadline_rejected": self._predicted_deadline_rejected,
                "queue_rejected": self._queue_rejected,
                "preemption_requests": self._preemption_requests,
                "preemptions_completed": self._preemptions_completed,
                "interactive_priority": 0,
                "shadow_priority": 10,
                "maintenance_priority": 20,
                "running_task_preemptible": self._active_preemptible,
                "shadow_preemption_supported": True,
                "maintenance_preemption_supported": True,
                "shadow_idle_grace_ms": env_int(
                    "LINE_MODEL_SHADOW_IDLE_GRACE_MS",
                    2_000,
                    minimum=0,
                    maximum=30_000,
                ),
                "scope": "single_process",
            }


_CONTROLLER = _AdmissionController()


def _default_interactive_predicted_duration_ms(category: str) -> int:
    if category in {"interactive_chart_vision", "interactive_benchmark_vision"}:
        return env_int(
            "LINE_MODEL_VISION_P95_MS",
            35_000,
            minimum=1_000,
            maximum=120_000,
        )
    if category in {
        "interactive_conversation_router",
        "interactive_general_investment",
        "interactive_market_brief",
        "interactive_stock_analysis",
    }:
        return env_int(
            "LINE_MODEL_INTERACTIVE_P95_MS",
            15_000,
            minimum=500,
            maximum=120_000,
        )
    return 0


def run_interactive_model(
    callable_: Callable[[], T],
    *,
    category: str,
    deadline_monotonic: float | None = None,
    predicted_duration_ms: int | None = None,
) -> ModelExecution[T]:
    if predicted_duration_ms is None:
        predicted_duration_ms = _default_interactive_predicted_duration_ms(category)
    future = _CONTROLLER.submit(
        callable_,
        priority=0,
        category=category,
        deadline_monotonic=deadline_monotonic,
        predicted_duration_ms=predicted_duration_ms,
    )
    wait_timeout = None
    if deadline_monotonic is not None:
        wait_timeout = max(0.1, deadline_monotonic - time.monotonic() + 1.0)
    return future.result(timeout=wait_timeout)


def submit_shadow_model(
    callable_: Callable[[], T],
    *,
    category: str = "shadow_candidate",
    on_start: Callable[[int], None] | None = None,
    cancellable_callable: Callable[[threading.Event], T] | None = None,
) -> Future[ModelExecution[T]]:
    cancel_event = threading.Event()
    admitted_callable = (
        (lambda: cancellable_callable(cancel_event))
        if cancellable_callable is not None
        else callable_
    )
    return _CONTROLLER.submit(
        admitted_callable,
        priority=10,
        category=category,
        deadline_monotonic=None,
        on_start=on_start,
        cancel_event=cancel_event,
        preemptible=cancellable_callable is not None,
    )


def run_shadow_model(
    callable_: Callable[[], T],
    *,
    on_start: Callable[[int], None] | None = None,
    cancellable_callable: Callable[[threading.Event], T] | None = None,
) -> ModelExecution[T]:
    return submit_shadow_model(
        callable_,
        on_start=on_start,
        cancellable_callable=cancellable_callable,
    ).result()


def submit_maintenance_model(
    callable_: Callable[[], T],
    *,
    category: str,
    cancellable_callable: Callable[[threading.Event], T] | None = None,
    predicted_duration_ms: int = 0,
) -> Future[ModelExecution[T]]:
    cancel_event = threading.Event()

    def admitted_callable() -> T:
        if cancellable_callable is None:
            return callable_()
        try:
            return cancellable_callable(cancel_event)
        except BaseException as exc:
            if cancel_event.is_set() and not isinstance(exc, ModelAdmissionError):
                raise ModelAdmissionError(
                    "maintenance model work yielded to interactive LINE work",
                    reason_code="maintenance_preempted_by_interactive",
                ) from exc
            raise

    return _CONTROLLER.submit(
        admitted_callable,
        priority=20,
        category=category,
        deadline_monotonic=None,
        cancel_event=cancel_event,
        preemptible=cancellable_callable is not None,
        predicted_duration_ms=predicted_duration_ms,
    )


def run_maintenance_model(
    callable_: Callable[[], T],
    *,
    category: str,
    cancellable_callable: Callable[[threading.Event], T] | None = None,
    predicted_duration_ms: int = 0,
) -> ModelExecution[T]:
    return submit_maintenance_model(
        callable_,
        category=category,
        cancellable_callable=cancellable_callable,
        predicted_duration_ms=predicted_duration_ms,
    ).result()


def model_admission_snapshot() -> dict[str, Any]:
    return _CONTROLLER.snapshot()
