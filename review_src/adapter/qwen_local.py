from __future__ import annotations

import base64
import ipaddress
import json
import math
import re
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any
from urllib.parse import urlparse

import requests

from core.line_bot_config import env_bool, env_float, env_int, env_text


class QwenClientError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str = "qwen_request_failed") -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class QwenChatResult:
    text: str
    finish_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str


def qwen_model_resident(model_id: str | None = None, *, timeout_seconds: float = 1.0) -> bool:
    """Return whether the configured text model is already resident in Ollama."""

    configured = str(model_id or env_text("QWEN_MODEL_ID", "taiwan-stock-qwen"))
    requested = configured.removesuffix(":latest")
    base_url = _base_url().removesuffix("/v1")
    try:
        response = requests.get(f"{base_url}/api/ps", timeout=max(0.1, timeout_seconds))
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError):
        return False
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "")
        if name == configured or name.removesuffix(":latest") == requested:
            return True
    return False


def qwen_unload_text_model(*, timeout_seconds: float = 10.0) -> bool:
    """Unload only the configured text model for an acknowledged cold-load benchmark."""

    base_url = _native_text_base_url()
    model_id = env_text("QWEN_MODEL_ID", "taiwan-stock-qwen")
    try:
        response = requests.post(
            f"{base_url}/api/generate",
            json={"model": model_id, "keep_alive": 0},
            timeout=max(1.0, min(float(timeout_seconds), 30.0)),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise QwenClientError(
            "configured text model could not be unloaded for cold benchmark",
            reason_code="model_unload_failed",
        ) from exc
    return not qwen_model_resident(model_id, timeout_seconds=2.0)


def qwen_preload_text_model(*, timeout_seconds: float) -> dict[str, Any]:
    """Maintenance-only native preload; does not extend normal inference timeouts.

    The caller must hold the shared GPU admission slot and obtain maintenance
    approval. An empty native request loads the model without generating text.
    """

    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise QwenClientError("invalid model load timeout", reason_code="invalid_load_timeout") from exc
    if not math.isfinite(timeout) or not 30 <= timeout <= 3600:
        raise QwenClientError("invalid model load timeout", reason_code="invalid_load_timeout")
    response = None
    try:
        response = requests.post(
            f"{_native_text_base_url()}/api/generate",
            json={
                "model": env_text("QWEN_MODEL_ID", "taiwan-stock-qwen"),
                "stream": False,
                "keep_alive": _ollama_keep_alive("QWEN_KEEP_ALIVE"),
            },
            timeout=(5.0, timeout),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("done") is not True or payload.get("error"):
            raise QwenClientError("model preload did not complete", reason_code="model_load_incomplete")
        metrics: dict[str, Any] = {}
        for field in ("load_duration", "total_duration"):
            value = payload.get(field)
            metrics[f"{field}_ms"] = (
                round(value / 1_000_000, 3)
                if type(value) is int and value >= 0 else None
            )
        return metrics
    except requests.Timeout as exc:
        raise QwenClientError("model preload timed out", reason_code="model_load_timeout") from exc
    except requests.RequestException as exc:
        raise QwenClientError("model preload request failed", reason_code="model_load_request_failed") from exc
    except ValueError as exc:
        raise QwenClientError("invalid model preload response", reason_code="model_load_invalid_json") from exc
    finally:
        if response is not None:
            response.close()


MIN_USEFUL_TIMEOUT_SECONDS = 3.0
MAX_BACKGROUND_TIMEOUT_SECONDS = 120.0
DEFAULT_LOCAL_API_KEY = "local-only"
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)


def _base_url() -> str:
    value = env_text("QWEN_BASE_URL", "http://127.0.0.1:8020/v1").rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise QwenClientError("QWEN_BASE_URL is invalid")
    return value


def _vision_base_url() -> str:
    configured = env_text("QWEN_VISION_BASE_URL")
    value = configured.rstrip("/") if configured else _base_url().removesuffix("/v1")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise QwenClientError("QWEN_VISION_BASE_URL is invalid")
    return value


def _native_text_base_url() -> str:
    configured = env_text("QWEN_NATIVE_BASE_URL")
    value = configured.rstrip("/") if configured else _base_url().removesuffix("/v1")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise QwenClientError("QWEN_NATIVE_BASE_URL is invalid")
    if not _is_loopback_base_url(value):
        raise QwenClientError("native Ollama text mode is restricted to loopback")
    return value


def _is_loopback_base_url(base_url: str) -> bool:
    host = str(urlparse(base_url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _api_key(base_url: str) -> str:
    api_key = env_text("QWEN_API_KEY", DEFAULT_LOCAL_API_KEY)
    if not _is_loopback_base_url(base_url) and api_key == DEFAULT_LOCAL_API_KEY:
        raise QwenClientError(
            "QWEN_API_KEY must be configured when QWEN_BASE_URL is not loopback"
        )
    return api_key


def _request_timeout(
    timeout_seconds: float | None,
    *,
    allow_background_timeout: bool = False,
) -> float:
    configured_timeout = float(
        env_int("QWEN_TIMEOUT_SECONDS", 40, minimum=5, maximum=55)
    )
    if timeout_seconds is None:
        return configured_timeout
    try:
        requested_timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise QwenClientError("Qwen timeout budget is invalid") from exc
    if (
        not math.isfinite(requested_timeout)
        or requested_timeout < MIN_USEFUL_TIMEOUT_SECONDS
    ):
        raise QwenClientError(
            "Qwen timeout budget is too small to call the model: "
            f"{requested_timeout:.1f}s"
        )
    if allow_background_timeout:
        return min(requested_timeout, MAX_BACKGROUND_TIMEOUT_SECONDS)
    return min(requested_timeout, configured_timeout)


def _ollama_keep_alive(name: str, default: str = "-1") -> str | int:
    """Normalize Ollama's sentinel values; bare numeric strings need units."""

    value = env_text(name, default)
    if value in {"-1", "0"}:
        return int(value)
    return value


def _final_answer_from_message(message: Any) -> str:
    if not isinstance(message, dict):
        raise QwenClientError("local Qwen returned an invalid message object")
    content = message.get("content")
    reasoning_content = message.get("reasoning_content")
    if content is not None and not isinstance(content, str):
        raise QwenClientError("local Qwen returned non-text final content")
    text = str(content or "").strip()
    text = _THINK_BLOCK_RE.sub("", text).strip()
    if _THINK_OPEN_RE.search(text):
        raise QwenClientError(
            "local Qwen returned unfinished reasoning without a final answer"
        )
    closing_tags = list(_THINK_CLOSE_RE.finditer(text))
    if closing_tags:
        text = text[closing_tags[-1].end():].strip()
    if text:
        return text
    if str(reasoning_content or "").strip():
        raise QwenClientError(
            "local Qwen returned reasoning_content without final content"
        )
    raise QwenClientError("local Qwen returned an empty final answer")


def qwen_chat(
    system_prompt: str,
    user_prompt: str,
    *,
    timeout_seconds: float | None = None,
    allow_background_timeout: bool = False,
    max_output_tokens: int | None = None,
    model_id: str | None = None,
    cancellation_event: Event | None = None,
) -> str:
    return qwen_chat_detailed(
        system_prompt,
        user_prompt,
        timeout_seconds=timeout_seconds,
        allow_background_timeout=allow_background_timeout,
        max_output_tokens=max_output_tokens,
        model_id=model_id,
        cancellation_event=cancellation_event,
    ).text


def _text_http_error_reason(exc: requests.HTTPError, *, structured: bool) -> str:
    status = getattr(exc.response, "status_code", "unknown")
    if status != 400:
        return f"model_http_{status}"
    if not structured:
        return "context_http_400"  # Preserve the existing plain-chat classification.
    # A schema can itself cause HTTP 400. Never report every new format error
    # as context overflow or retry without the schema. Do not log response text.
    detail = str(getattr(exc.response, "text", ""))[:4096].lower()
    if "context" in detail and any(word in detail for word in ("exceed", "overflow", "too long")):
        return "context_http_400"
    return "model_http_400"


def qwen_chat_detailed(
    system_prompt: str,
    user_prompt: str,
    *,
    timeout_seconds: float | None = None,
    allow_background_timeout: bool = False,
    max_output_tokens: int | None = None,
    model_id: str | None = None,
    cancellation_event: Event | None = None,
    response_schema: dict[str, Any] | None = None,
    temperature: float | None = None,
    seed: int | None = None,
) -> QwenChatResult:
    timeout = _request_timeout(
        timeout_seconds,
        allow_background_timeout=allow_background_timeout,
    )
    base_url = _base_url()
    output_tokens = (
        env_int("QWEN_MAX_OUTPUT_TOKENS", 900, minimum=128, maximum=2000)
        if max_output_tokens is None
        else max(128, min(int(max_output_tokens), 2000))
    )
    api_mode = env_text("QWEN_TEXT_API_MODE", "openai").lower()
    if api_mode not in {"openai", "native"}:
        raise QwenClientError("QWEN_TEXT_API_MODE must be openai or native")
    effective_temperature = (
        env_float("QWEN_TEMPERATURE", 0.2, minimum=0.0, maximum=1.0)
        if temperature is None
        else max(0.0, min(float(temperature), 1.0))
    )
    effective_seed = None if seed is None else int(seed)
    if api_mode == "native":
        return _native_text_chat_detailed(
            system_prompt,
            user_prompt,
            timeout=timeout,
            output_tokens=output_tokens,
            model_id=str(model_id or env_text("QWEN_MODEL_ID", "taiwan-stock-qwen")),
            cancellation_event=cancellation_event,
            response_schema=response_schema,
            temperature=effective_temperature,
            seed=effective_seed,
        )
    body: dict[str, Any] = {
        "model": str(model_id or env_text("QWEN_MODEL_ID", "taiwan-stock-qwen")),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": effective_temperature,
        "top_p": env_float("QWEN_TOP_P", 0.8, minimum=0.1, maximum=1.0),
        "max_tokens": output_tokens,
        "stream": cancellation_event is not None,
        # Verified for the bundled Ollama OpenAI-compatible endpoint. Revalidate this
        # field before switching to another inference engine such as vLLM or SGLang.
        "reasoning_effort": "low" if env_bool("QWEN_ENABLE_THINKING", False) else "none",
    }
    if effective_seed is not None:
        body["seed"] = effective_seed
    if response_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "model_analysis", "strict": True, "schema": response_schema},
        }
    api_key = _api_key(base_url)
    try:
        request_kwargs: dict[str, Any] = {
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            "json": body,
            "timeout": timeout,
        }
        if cancellation_event is not None:
            request_kwargs["stream"] = True
        response = requests.post(
            f"{base_url}/chat/completions",
            **request_kwargs,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise QwenClientError(
            f"local Qwen request timed out after {timeout:.1f}s",
            reason_code="model_timeout",
        ) from exc
    except requests.ConnectionError as exc:
        raise QwenClientError(
            "local Qwen connection failed",
            reason_code="model_connection_failed",
        ) from exc
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", "unknown")
        reason_code = _text_http_error_reason(exc, structured=response_schema is not None)
        raise QwenClientError(
            f"local Qwen returned HTTP {status}",
            reason_code=reason_code,
        ) from exc
    except requests.RequestException as exc:
        raise QwenClientError(
            "local Qwen request failed",
            reason_code="model_request_failed",
        ) from exc
    if cancellation_event is not None:
        return _streaming_chat_result(
            response,
            model_id=str(body["model"]),
            cancellation_event=cancellation_event,
        )
    try:
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise QwenClientError("local Qwen returned invalid JSON") from exc
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise QwenClientError("local Qwen returned an invalid response schema") from exc
    usage = payload.get("usage") if isinstance(payload, dict) else {}
    choice = payload["choices"][0]
    return QwenChatResult(
        text=_final_answer_from_message(message),
        finish_reason=str(choice.get("finish_reason") or "unknown"),
        prompt_tokens=(
            int(usage["prompt_tokens"])
            if isinstance(usage, dict) and usage.get("prompt_tokens") is not None
            else None
        ),
        completion_tokens=(
            int(usage["completion_tokens"])
            if isinstance(usage, dict) and usage.get("completion_tokens") is not None
            else None
        ),
        model=str(payload.get("model") or body["model"]),
    )


def _native_text_chat_detailed(
    system_prompt: str,
    user_prompt: str,
    *,
    timeout: float,
    output_tokens: int,
    model_id: str,
    cancellation_event: Event | None = None,
    response_schema: dict[str, Any] | None = None,
    temperature: float | None = None,
    seed: int | None = None,
) -> QwenChatResult:
    """Use Ollama's native text API so `think=false` is an enforced setting."""

    body: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": cancellation_event is not None,
        "think": "low" if env_bool("QWEN_ENABLE_THINKING", False) else False,
        "keep_alive": _ollama_keep_alive("QWEN_KEEP_ALIVE"),
        "options": {
            "temperature": (
                env_float("QWEN_TEMPERATURE", 0.2, minimum=0.0, maximum=1.0)
                if temperature is None
                else float(temperature)
            ),
            "top_p": env_float("QWEN_TOP_P", 0.8, minimum=0.1, maximum=1.0),
            "num_predict": output_tokens,
            "num_batch": env_int("QWEN_NUM_BATCH", 1024, minimum=128, maximum=2048),
        },
    }
    if seed is not None:
        body["options"]["seed"] = int(seed)
    if response_schema is not None:
        body["format"] = response_schema
    try:
        response = requests.post(
            f"{_native_text_base_url()}/api/chat",
            json=body,
            timeout=timeout,
            stream=cancellation_event is not None,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise QwenClientError(
            f"local Qwen request timed out after {timeout:.1f}s",
            reason_code="model_timeout",
        ) from exc
    except requests.ConnectionError as exc:
        raise QwenClientError(
            "local Qwen connection failed",
            reason_code="model_connection_failed",
        ) from exc
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", "unknown")
        reason_code = _text_http_error_reason(exc, structured=response_schema is not None)
        raise QwenClientError(
            f"local Qwen returned HTTP {status}",
            reason_code=reason_code,
        ) from exc
    except requests.RequestException as exc:
        raise QwenClientError(
            "local Qwen request failed",
            reason_code="model_request_failed",
        ) from exc
    if cancellation_event is not None:
        return _streaming_native_chat_result(
            response,
            model_id=model_id,
            cancellation_event=cancellation_event,
        )
    try:
        payload = response.json()
        message = payload["message"]
    except (ValueError, TypeError, KeyError) as exc:
        raise QwenClientError("local Qwen returned an invalid native response") from exc
    if not isinstance(message, dict):
        raise QwenClientError("local Qwen returned an invalid native message")
    return QwenChatResult(
        text=_final_answer_from_message(
            {
                "content": message.get("content"),
                "reasoning_content": message.get("thinking"),
            }
        ),
        finish_reason=str(payload.get("done_reason") or "unknown"),
        prompt_tokens=(
            int(payload["prompt_eval_count"])
            if payload.get("prompt_eval_count") is not None
            else None
        ),
        completion_tokens=(
            int(payload["eval_count"])
            if payload.get("eval_count") is not None
            else None
        ),
        model=str(payload.get("model") or model_id),
    )


def _cancellation_watcher(
    response: requests.Response,
    cancellation_event: Event,
    watcher_stop: Event,
) -> None:
    """Close an in-flight local stream as soon as interactive work requests it."""

    while not watcher_stop.wait(0.02):
        if not cancellation_event.is_set():
            continue
        try:
            response.close()
        except requests.RequestException:
            pass
        return


def _streaming_native_chat_result(
    response: requests.Response,
    *,
    model_id: str,
    cancellation_event: Event,
) -> QwenChatResult:
    """Consume Ollama NDJSON while allowing cancellation during prompt evaluation."""

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = "unknown"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    returned_model = model_id
    watcher_stop = Event()
    watcher = Thread(
        target=_cancellation_watcher,
        args=(response, cancellation_event, watcher_stop),
        name="qwen-native-cancellation",
        daemon=True,
    )
    watcher.start()
    try:
        for raw_line in response.iter_lines(decode_unicode=False):
            if cancellation_event.is_set():
                raise QwenClientError(
                    "shadow model request preempted by interactive LINE work",
                    reason_code="shadow_preempted_by_interactive",
                )
            if not raw_line:
                continue
            try:
                payload = json.loads(
                    raw_line.decode("utf-8", errors="strict")
                    if isinstance(raw_line, bytes)
                    else str(raw_line)
                )
            except (UnicodeDecodeError, ValueError, TypeError) as exc:
                raise QwenClientError(
                    "local Qwen returned invalid native streaming JSON",
                    reason_code="model_invalid_stream",
                ) from exc
            if not isinstance(payload, dict):
                raise QwenClientError(
                    "local Qwen returned a non-object native streaming event",
                    reason_code="model_invalid_stream",
                )
            returned_model = str(payload.get("model") or returned_model)
            message = payload.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                thinking = message.get("thinking")
                if isinstance(content, str):
                    content_parts.append(content)
                if isinstance(thinking, str):
                    reasoning_parts.append(thinking)
            if payload.get("prompt_eval_count") is not None:
                prompt_tokens = int(payload["prompt_eval_count"])
            if payload.get("eval_count") is not None:
                completion_tokens = int(payload["eval_count"])
            if payload.get("done") is True:
                finish_reason = str(payload.get("done_reason") or "stop")
                break
    except requests.RequestException as exc:
        if cancellation_event.is_set():
            raise QwenClientError(
                "shadow model request preempted by interactive LINE work",
                reason_code="shadow_preempted_by_interactive",
            ) from exc
        raise QwenClientError(
            "local Qwen native stream failed",
            reason_code="model_request_failed",
        ) from exc
    finally:
        watcher_stop.set()
        try:
            response.close()
        except requests.RequestException:
            pass
        watcher.join(timeout=0.2)
    if cancellation_event.is_set():
        raise QwenClientError(
            "shadow model request preempted by interactive LINE work",
            reason_code="shadow_preempted_by_interactive",
        )
    return QwenChatResult(
        text=_final_answer_from_message(
            {
                "content": "".join(content_parts),
                "reasoning_content": "".join(reasoning_parts),
            }
        ),
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=returned_model,
    )


def _streaming_chat_result(
    response: requests.Response,
    *,
    model_id: str,
    cancellation_event: Event,
) -> QwenChatResult:
    """Consume an OpenAI-compatible SSE stream and close it on preemption."""

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = "unknown"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    returned_model = model_id
    try:
        for payload in _iter_streaming_json_events(response, cancellation_event):
            returned_model = str(payload.get("model") or returned_model)
            usage = payload.get("usage") if isinstance(payload, dict) else None
            if isinstance(usage, dict):
                if usage.get("prompt_tokens") is not None:
                    prompt_tokens = int(usage["prompt_tokens"])
                if usage.get("completion_tokens") is not None:
                    completion_tokens = int(usage["completion_tokens"])
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0] if isinstance(choices[0], dict) else {}
            delta = choice.get("delta") if isinstance(choice, dict) else {}
            if not isinstance(delta, dict):
                delta = {}
            content = delta.get("content")
            reasoning = delta.get("reasoning_content")
            if isinstance(content, str):
                content_parts.append(content)
            if isinstance(reasoning, str):
                reasoning_parts.append(reasoning)
            if choice.get("finish_reason") is not None:
                finish_reason = str(choice["finish_reason"])
        if cancellation_event.is_set():
            raise QwenClientError(
                "shadow model request preempted by interactive LINE work",
                reason_code="shadow_preempted_by_interactive",
            )
    finally:
        response.close()
    message = {
        "content": "".join(content_parts),
        "reasoning_content": "".join(reasoning_parts),
    }
    return QwenChatResult(
        text=_final_answer_from_message(message),
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=returned_model,
    )


def _iter_streaming_json_events(
    response: requests.Response,
    cancellation_event: Event,
):
    """Yield JSON SSE events, repairing Ollama's literal-newline frame split."""

    pending_segments: list[str] = []

    def parse_pending() -> dict[str, Any] | None:
        if not pending_segments:
            return None
        candidate = "\\n".join(pending_segments)
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            raise QwenClientError(
                "local Qwen returned a non-object streaming event",
                reason_code="model_invalid_stream",
            )
        pending_segments.clear()
        return payload

    # Always split raw bytes first. requests may otherwise choose ISO-8859-1 for
    # text/event-stream without a charset; Unicode splitlines can then treat a
    # UTF-8 continuation byte as a line boundary and corrupt Chinese JSON.
    for raw_line in response.iter_lines(decode_unicode=False):
        if cancellation_event.is_set():
            raise QwenClientError(
                "shadow model request preempted by interactive LINE work",
                reason_code="shadow_preempted_by_interactive",
            )
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else str(raw_line)
        ).rstrip("\r")
        if not line:
            payload = parse_pending()
            if payload is not None:
                yield payload
            continue
        stripped = line.strip()
        if stripped.startswith(":"):
            continue
        if stripped.startswith(("event:", "id:", "retry:")) and not pending_segments:
            continue
        segment = stripped[5:].strip() if stripped.startswith("data:") else line
        if segment == "[DONE]" and not pending_segments:
            break
        pending_segments.append(segment)
        payload = parse_pending()
        if payload is not None:
            yield payload
    if cancellation_event.is_set():
        raise QwenClientError(
            "shadow model request preempted by interactive LINE work",
            reason_code="shadow_preempted_by_interactive",
        )
    if pending_segments:
        raise QwenClientError(
            "local Qwen returned incomplete streaming JSON",
            reason_code="model_invalid_stream",
        )


def qwen_vision_json(
    system_prompt: str,
    user_prompt: str,
    image_bytes: bytes,
    *,
    timeout_seconds: float | None = None,
    response_template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return structured observations from a local Ollama vision model."""

    if not image_bytes:
        raise QwenClientError("vision input image is empty")
    configured_timeout = env_int("QWEN_VISION_TIMEOUT_SECONDS", 35, minimum=8, maximum=50)
    timeout = min(float(timeout_seconds), configured_timeout) if timeout_seconds is not None else float(configured_timeout)
    if not math.isfinite(timeout) or timeout < 8:
        raise QwenClientError("vision timeout budget is too small")
    base_url = _vision_base_url()
    response_template = response_template or {
        "is_stock_chart": True,
        "chart_type": "",
        "image_quality": "medium",
        "overall_confidence": 0.0,
        "stock": {"code": "", "name": "", "confidence": 0.0},
        "timeframe": "",
        "visible_date_range": "",
        "indicators": [
            {
                "name": "RSI",
                "period": "14",
                "value": None,
                "signal": "",
                "visible": True,
                "confidence": 0.0,
            }
        ],
        "price_values": [],
        "chart_observations": [],
        "uncertainty_reasons": [],
    }
    json_contract = (
        "Return only one JSON object, without Markdown, using exactly this shape and data types: "
        f"{json.dumps(response_template, ensure_ascii=False, separators=(',', ':'))}. "
        "image_quality must be high, medium, low, or unusable. Confidence fields must be numbers "
        "from 0 to 1. Use an empty string for unreadable text, null for unreadable numeric values, "
        "and an empty array when no reliable item is visible."
    )
    body: dict[str, Any] = {
        "model": env_text("QWEN_VISION_MODEL_ID", "qwen3-vl:8b-instruct"),
        "messages": [
            {
                "role": "user",
                # This Ollama template can emit thinking-only output for image
                # requests with a separate system role.  The same instruction in
                # the image-bearing user turn yields parseable final content.
                "content": f"{system_prompt}\n\n{user_prompt}\n\n{json_contract}",
                "images": [base64.b64encode(image_bytes).decode("ascii")],
            },
        ],
        "stream": False,
        "keep_alive": _ollama_keep_alive("QWEN_VISION_KEEP_ALIVE"),
        "options": {
            "temperature": env_float("QWEN_VISION_TEMPERATURE", 0.0, minimum=0.0, maximum=0.5),
            "num_predict": env_int("QWEN_VISION_MAX_OUTPUT_TOKENS", 1200, minimum=256, maximum=2000),
            "num_ctx": env_int("QWEN_VISION_CONTEXT_TOKENS", 4096, minimum=2048, maximum=16384),
        },
    }
    api_key = _api_key(base_url)
    try:
        response = requests.post(
            f"{base_url}/api/chat",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout as exc:
        raise QwenClientError(f"local vision model timed out after {timeout:.1f}s") from exc
    except requests.ConnectionError as exc:
        raise QwenClientError("local vision model connection failed") from exc
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", "unknown")
        raise QwenClientError(f"local vision model returned HTTP {status}") from exc
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise QwenClientError("local vision model request failed") from exc
    message = payload.get("message") if isinstance(payload, dict) else None
    try:
        text = _final_answer_from_message(message)
    except QwenClientError as exc:
        if isinstance(message, dict) and not str(message.get("content") or "").strip():
            thinking_chars = len(str(message.get("thinking") or ""))
            done_reason = str(payload.get("done_reason") or "unknown")
            raise QwenClientError(
                "local vision model returned no final content "
                f"(done_reason={done_reason}, thinking_chars={thinking_chars})"
            ) from exc
        raise
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise QwenClientError("local vision model returned invalid structured JSON") from exc
    if not isinstance(result, dict):
        raise QwenClientError("local vision model returned a non-object analysis")
    return result
