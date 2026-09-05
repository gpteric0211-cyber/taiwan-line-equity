from __future__ import annotations

"""Deadline-safe background residency for the local text model.

Cold model loading is not assumed to be preemptible. It is registered as a
long-running maintenance task so interactive LINE requests can reject model
admission early and immediately use the existing DB-grounded fallback instead
of waiting behind a 20+ GB model load.
"""

import threading
from concurrent.futures import Future
from typing import Any

from adapter.qwen_local import QwenClientError, qwen_chat, qwen_model_resident, qwen_preload_text_model
from core.line_bot_config import env_int
from services.model_admission_service import ModelExecution, submit_maintenance_model


_LOCK = threading.Lock()
_FUTURE: Future[ModelExecution[str]] | None = None


def _load_and_probe_text_model(load_timeout_seconds: int) -> str:
    """Execute inside the existing maintenance slot, never the reply budget.

    Cold loading is not ordinary chat inference. Keep the native load timeout
    separate so the adapter's 120-second background-chat cap cannot abort it.
    Neither load nor READY failure retries or unloads a model here.
    """
    qwen_preload_text_model(timeout_seconds=load_timeout_seconds)
    if not qwen_model_resident(timeout_seconds=2.0):
        raise QwenClientError(
            "text model is not resident after startup preload",
            reason_code="model_not_resident_after_load",
        )
    return qwen_chat(
        "你是本機文字模型載入探針，只回覆 READY。",
        "READY",
        timeout_seconds=20,
        max_output_tokens=128,
    )


def ensure_background_text_model_warmup() -> dict[str, Any]:
    """Ensure one non-blocking, admission-visible text warmup is queued."""

    global _FUTURE
    if qwen_model_resident(timeout_seconds=0.5):
        return {"status": "resident", "scheduled": False}
    with _LOCK:
        if _FUTURE is not None and not _FUTURE.done():
            return {"status": "warming", "scheduled": False}
        load_timeout = env_int(
            "LINE_MODEL_STARTUP_LOAD_TIMEOUT_SECONDS", 180, minimum=30, maximum=240
        )
        predicted_ms = env_int(
            "LINE_MODEL_COLD_WARMUP_P95_MS",
            70_000,
            minimum=10_000,
            maximum=180_000,
        )
        # Conservative occupancy, not a measured p95: preload + READY +
        # transport/residency reserve. Do not under-admit behind a 70s estimate
        # when the non-preemptible load is allowed to take longer.
        predicted_ms = max(predicted_ms, (load_timeout + 20 + 10) * 1000)
        _FUTURE = submit_maintenance_model(
            lambda: _load_and_probe_text_model(load_timeout),
            category="maintenance_startup_text_warmup",
            predicted_duration_ms=predicted_ms,
        )
        return {
            "status": "scheduled",
            "scheduled": True,
            "predicted_duration_ms": predicted_ms,
        }


def background_text_model_warmup_status() -> dict[str, Any]:
    """Return non-sensitive warmup state for health and startup diagnostics."""

    resident = qwen_model_resident(timeout_seconds=0.5)
    with _LOCK:
        future = _FUTURE
        if resident:
            state = "resident"
        elif future is None:
            state = "not_scheduled"
        elif not future.done():
            state = "warming"
        else:
            try:
                future.result()
            except BaseException:
                state = "failed"
            else:
                state = "completed_not_resident"
    return {"status": state, "resident": resident}
