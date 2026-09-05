"""Preload local Ollama models before requests need a short reply deadline."""

from __future__ import annotations
import os
import time
import requests
from core.line_bot_config import env_bool, env_int, env_text


def warmup():
    base = env_text("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020").rstrip("/")
    models = [
        (
            env_text("QWEN_MODEL_ID", "taiwan-stock-qwen"),
            env_int("QWEN_CONTEXT_TOKENS", 16384, minimum=2048, maximum=131072),
        )
    ]
    if env_bool("QWEN_VISION_ENABLED", False):
        models.append(
            (
                env_text("QWEN_VISION_MODEL_ID", "qwen3-vl:8b-instruct"),
                env_int("QWEN_VISION_CONTEXT_TOKENS", 4096, minimum=2048, maximum=16384),
            )
        )
    results = []
    for model, context in models:
        started = time.monotonic()
        response = requests.post(
            base + "/api/generate",
            json={
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": -1,
                "options": {"num_ctx": context},
            },
            timeout=(5, int(os.getenv("EQUITY_MODEL_LOAD_TIMEOUT_SECONDS", "180"))),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error") or payload.get("done") is not True:
            raise RuntimeError("Model preload did not complete")
        results.append(
            {
                "model": model,
                "context_tokens": context,
                "ready": True,
                "seconds": round(time.monotonic() - started, 2),
            }
        )
    return results
