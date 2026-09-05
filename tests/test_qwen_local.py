from __future__ import annotations

import sys
import json
import base64
import threading
from pathlib import Path
from typing import Any

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter import qwen_local  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload

    def close(self) -> None:
        return None


def _response_message(message: dict[str, Any]) -> _FakeResponse:
    return _FakeResponse({"choices": [{"message": message}]})


def _local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QWEN_BASE_URL", "http://127.0.0.1:8020/v1")
    monkeypatch.setenv("QWEN_API_KEY", "local-only")
    monkeypatch.setenv("QWEN_TIMEOUT_SECONDS", "40")
    monkeypatch.setenv("QWEN_TEXT_API_MODE", "openai")


def test_qwen_chat_forwards_cancellation_event(monkeypatch: pytest.MonkeyPatch) -> None:
    cancellation = threading.Event()
    captured: dict[str, Any] = {}

    def fake_detailed(*_args: Any, **kwargs: Any) -> qwen_local.QwenChatResult:
        captured.update(kwargs)
        return qwen_local.QwenChatResult("READY", "stop", 1, 1, "model")

    monkeypatch.setattr(qwen_local, "qwen_chat_detailed", fake_detailed)

    assert qwen_local.qwen_chat("system", "user", cancellation_event=cancellation) == "READY"
    assert captured["cancellation_event"] is cancellation


@pytest.mark.parametrize("api_mode", ["native", "openai"])
@pytest.mark.parametrize("with_schema", [False, True])
def test_text_output_schema_is_opt_in_without_changing_plain_chat(monkeypatch, api_mode, with_schema):
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_TEXT_API_MODE", api_mode)
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    captured = {}
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        if api_mode == "native":
            return _FakeResponse({"message": {"content": '{"ok":true}'}, "done_reason": "stop"})
        return _response_message({"content": '{"ok":true}'})

    monkeypatch.setattr(qwen_local.requests, "post", post)
    result = qwen_local.qwen_chat_detailed("system", "user", response_schema=schema if with_schema else None)
    assert result.text == '{"ok":true}'
    body = captured["json"]
    if not with_schema:
        assert "format" not in body and "response_format" not in body
    elif api_mode == "native":
        assert body["format"] == schema
        assert "response_format" not in body
    else:
        assert body["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "model_analysis", "strict": True, "schema": schema},
        }


def test_native_schema_stream_remains_preemptible(monkeypatch):
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_TEXT_API_MODE", "native")
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    cancellation = threading.Event()
    captured = {}

    class Stream(_FakeResponse):
        closed = False

        def iter_lines(self, **kwargs):
            yield b'{"message":{"content":"{"},"done":false}'
            cancellation.set()
            yield b'{"message":{"content":"}"},"done":true}'

        def close(self):
            self.closed = True

    response = Stream({})

    def post(_url, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(qwen_local.requests, "post", post)
    with pytest.raises(qwen_local.QwenClientError) as error:
        qwen_local.qwen_chat_detailed(
            "system", "user", response_schema={"type": "object"}, cancellation_event=cancellation,
        )
    assert error.value.reason_code == "shadow_preempted_by_interactive"
    assert captured["stream"] is True
    assert captured["json"]["format"] == {"type": "object"}
    assert response.closed is True


@pytest.mark.parametrize("api_mode", ["native", "openai"])
@pytest.mark.parametrize("detail,reason", [
    ("invalid JSON schema", "model_http_400"),
    ("input exceeds context length", "context_http_400"),
])
def test_schema_http_failure_is_not_retried_or_mislabeled(monkeypatch, api_mode, detail, reason):
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_TEXT_API_MODE", api_mode)
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    calls = []
    response = qwen_local.requests.Response()
    response.status_code = 400
    response._content = detail.encode("utf-8")

    def post(*args, **kwargs):
        calls.append(kwargs)
        raise qwen_local.requests.HTTPError(response=response)

    monkeypatch.setattr(qwen_local.requests, "post", post)
    with pytest.raises(qwen_local.QwenClientError) as error:
        qwen_local.qwen_chat_detailed("system", "user", response_schema={"type": "object"})
    assert error.value.reason_code == reason
    assert len(calls) == 1
    assert detail not in str(error.value)


def test_qwen_unload_text_model_uses_fixed_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    monkeypatch.setenv("QWEN_MODEL_ID", "taiwan-stock-qwen")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return _FakeResponse({"done": True})

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)
    monkeypatch.setattr(qwen_local, "qwen_model_resident", lambda *_args, **_kwargs: False)

    assert qwen_local.qwen_unload_text_model() is True
    assert captured == {
        "url": "http://127.0.0.1:8020/api/generate",
        "json": {"model": "taiwan-stock-qwen", "keep_alive": 0},
    }


def test_native_preload_has_separate_timeout_without_prompt(monkeypatch):
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    monkeypatch.setenv("QWEN_MODEL_ID", "taiwan-stock-qwen")
    monkeypatch.setenv("QWEN_KEEP_ALIVE", "-1")
    captured = {}
    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _FakeResponse({"done": True, "load_duration": 125000000000, "total_duration": 125001000000})
    monkeypatch.setattr(qwen_local.requests, "post", post)
    result = qwen_local.qwen_preload_text_model(timeout_seconds=1260)
    assert captured["timeout"] == (5.0, 1260.0)
    assert captured["json"] == {"model": "taiwan-stock-qwen", "stream": False, "keep_alive": -1}
    assert result["load_duration_ms"] == 125000
    assert qwen_local._request_timeout(1260, allow_background_timeout=True) == 120
    assert qwen_local._request_timeout(1260) == 40


@pytest.mark.parametrize("timeout", [0, 29, 3601, float("nan"), "invalid"])
def test_native_preload_rejects_invalid_timeout_without_network(monkeypatch, timeout):
    monkeypatch.setattr(qwen_local.requests, "post", lambda *a, **k: pytest.fail("must not call"))
    with pytest.raises(qwen_local.QwenClientError) as caught:
        qwen_local.qwen_preload_text_model(timeout_seconds=timeout)
    assert caught.value.reason_code == "invalid_load_timeout"


def test_native_preload_timeout_is_classified(monkeypatch):
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    def post(*args, **kwargs):
        raise qwen_local.requests.Timeout("sensitive transport data")
    monkeypatch.setattr(qwen_local.requests, "post", post)
    with pytest.raises(qwen_local.QwenClientError) as caught:
        qwen_local.qwen_preload_text_model(timeout_seconds=1260)
    assert caught.value.reason_code == "model_load_timeout"
    assert "sensitive" not in str(caught.value)


def test_qwen_chat_removes_complete_reasoning_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _local_env(monkeypatch)
    monkeypatch.setattr(
        qwen_local.requests,
        "post",
        lambda *_args, **_kwargs: _response_message(
            {"content": "<think>internal draft</think>正式答案"}
        ),
    )

    assert qwen_local.qwen_chat("system", "user") == "正式答案"


def test_native_text_mode_enforces_thinking_off_and_maps_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_TEXT_API_MODE", "native")
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    monkeypatch.setenv("QWEN_ENABLE_THINKING", "false")
    monkeypatch.setenv("QWEN_NUM_BATCH", "1024")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["timeout"] = kwargs["timeout"]
        return _FakeResponse(
            {
                "model": "taiwan-stock-qwen:latest",
                "done_reason": "stop",
                "prompt_eval_count": 321,
                "eval_count": 45,
                "message": {"content": "正式答案", "thinking": ""},
            }
        )

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)

    result = qwen_local.qwen_chat_detailed("system", "user", max_output_tokens=128)

    assert result.text == "正式答案"
    assert result.prompt_tokens == 321
    assert result.completion_tokens == 45
    assert result.finish_reason == "stop"
    assert captured["url"] == "http://127.0.0.1:8020/api/chat"
    assert captured["json"]["think"] is False
    assert captured["json"]["options"]["num_predict"] == 128
    assert captured["json"]["options"]["num_batch"] == 1024
    assert captured["json"]["keep_alive"] == -1


def test_native_text_mode_honors_deterministic_evaluation_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_TEXT_API_MODE", "native")
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    captured: dict[str, Any] = {}

    def fake_post(_url: str, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs["json"]
        return _FakeResponse(
            {
                "model": "taiwan-stock-qwen:latest",
                "done_reason": "stop",
                "message": {"content": "正式答案", "thinking": ""},
            }
        )

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)

    qwen_local.qwen_chat_detailed(
        "system",
        "user",
        temperature=0.0,
        seed=20260901,
    )

    assert captured["json"]["options"]["temperature"] == 0.0
    assert captured["json"]["options"]["seed"] == 20260901


def test_native_mode_uses_cancellable_native_stream_with_thinking_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_TEXT_API_MODE", "native")
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    monkeypatch.setenv("QWEN_ENABLE_THINKING", "false")
    cancellation = threading.Event()
    captured: dict[str, Any] = {}

    class StreamingResponse(_FakeResponse):
        def iter_lines(self, **_kwargs: Any):
            yield b'{"model":"taiwan-stock-qwen:latest","message":{"content":"READY","thinking":""},"done":false}'
            yield b'{"model":"taiwan-stock-qwen:latest","message":{"content":"","thinking":""},"done":true,"done_reason":"stop","prompt_eval_count":2,"eval_count":1}'

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["stream"] = kwargs["stream"]
        return StreamingResponse({})

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)

    result = qwen_local.qwen_chat_detailed(
        "system",
        "user",
        cancellation_event=cancellation,
    )

    assert result.text == "READY"
    assert captured["url"] == "http://127.0.0.1:8020/api/chat"
    assert captured["json"]["stream"] is True
    assert captured["json"]["think"] is False
    assert captured["stream"] is True
    assert result.prompt_tokens == 2
    assert result.completion_tokens == 1


def test_native_stream_watcher_closes_blocked_response_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    monkeypatch.setenv("QWEN_TEXT_API_MODE", "native")
    monkeypatch.setenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    cancellation = threading.Event()
    response_closed = threading.Event()

    class BlockingStreamingResponse(_FakeResponse):
        def iter_lines(self, **_kwargs: Any):
            assert response_closed.wait(timeout=1)
            return
            yield b""  # pragma: no cover - keeps this a generator

        def close(self) -> None:
            response_closed.set()

    monkeypatch.setattr(
        qwen_local.requests,
        "post",
        lambda *_args, **_kwargs: BlockingStreamingResponse({}),
    )
    timer = threading.Timer(0.05, cancellation.set)
    timer.start()
    try:
        with pytest.raises(qwen_local.QwenClientError) as captured:
            qwen_local.qwen_chat_detailed(
                "system",
                "user",
                cancellation_event=cancellation,
            )
    finally:
        timer.cancel()

    assert captured.value.reason_code == "shadow_preempted_by_interactive"
    assert response_closed.is_set() is True


def test_qwen_model_resident_matches_latest_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    _local_env(monkeypatch)
    monkeypatch.setattr(
        qwen_local.requests,
        "get",
        lambda *_args, **_kwargs: _FakeResponse(
            {"models": [{"name": "taiwan-stock-qwen:latest"}]}
        ),
    )

    assert qwen_local.qwen_model_resident("taiwan-stock-qwen") is True


def test_qwen_chat_keeps_only_content_after_orphan_closing_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    monkeypatch.setattr(
        qwen_local.requests,
        "post",
        lambda *_args, **_kwargs: _response_message(
            {"content": "internal draft</think>正式答案"}
        ),
    )

    assert qwen_local.qwen_chat("system", "user") == "正式答案"


def test_qwen_chat_discards_unfinished_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    _local_env(monkeypatch)
    monkeypatch.setattr(
        qwen_local.requests,
        "post",
        lambda *_args, **_kwargs: _response_message(
            {"content": "<think>unfinished internal draft"}
        ),
    )

    with pytest.raises(qwen_local.QwenClientError, match="unfinished reasoning"):
        qwen_local.qwen_chat("system", "user")


def test_qwen_chat_rejects_too_small_timeout_without_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    called = False

    def fake_post(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        nonlocal called
        called = True
        return _response_message({"content": "should not be called"})

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)

    with pytest.raises(qwen_local.QwenClientError, match="too small"):
        qwen_local.qwen_chat("system", "user", timeout_seconds=1.5)
    assert called is False


def test_background_qwen_timeout_can_exceed_interactive_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    captured: list[tuple[float, int, str]] = []

    def fake_post(*_args: Any, **kwargs: Any) -> _FakeResponse:
        captured.append(
            (
                float(kwargs["timeout"]),
                int(kwargs["json"]["max_tokens"]),
                str(kwargs["json"]["model"]),
            )
        )
        return _response_message({"content": "正式答案"})

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)

    assert qwen_local.qwen_chat("system", "user", timeout_seconds=60) == "正式答案"
    assert qwen_local.qwen_chat(
        "system",
        "user",
        timeout_seconds=60,
        allow_background_timeout=True,
        max_output_tokens=320,
        model_id="qwen3-vl:8b-instruct",
    ) == "正式答案"
    assert captured == [
        (40.0, 900, "taiwan-stock-qwen"),
        (60.0, 320, "qwen3-vl:8b-instruct"),
    ]


def test_qwen_chat_never_uses_reasoning_content_as_final_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    monkeypatch.setattr(
        qwen_local.requests,
        "post",
        lambda *_args, **_kwargs: _response_message(
            {"content": "", "reasoning_content": "未經驗證的推理草稿"}
        ),
    )

    with pytest.raises(qwen_local.QwenClientError, match="reasoning_content"):
        qwen_local.qwen_chat("system", "user")


def test_streaming_shadow_request_closes_and_raises_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)
    cancellation = threading.Event()

    class StreamingResponse(_FakeResponse):
        def __init__(self) -> None:
            super().__init__({})
            self.closed = False

        def iter_lines(self, **_kwargs: Any):
            yield 'data: {"model":"local","choices":[{"delta":{"content":"部分"}}]}'
            cancellation.set()
            yield 'data: {"choices":[{"delta":{"content":"不應完成"}}]}'

        def close(self) -> None:
            self.closed = True

    response = StreamingResponse()
    monkeypatch.setattr(qwen_local.requests, "post", lambda *_args, **_kwargs: response)

    with pytest.raises(qwen_local.QwenClientError) as captured:
        qwen_local.qwen_chat_detailed(
            "system",
            "user",
            cancellation_event=cancellation,
        )

    assert captured.value.reason_code == "shadow_preempted_by_interactive"
    assert response.closed is True


def test_streaming_shadow_reassembles_ollama_literal_newline_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)

    class StreamingResponse(_FakeResponse):
        def iter_lines(self, **_kwargs: Any):
            yield b'data: {"model":"local","choices":[{"delta":{"content":"first'
            yield b'second"},"finish_reason":null}]}'
            yield b''
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2}}'
            yield b''
            yield b'data: [DONE]'

    monkeypatch.setattr(qwen_local.requests, "post", lambda *_args, **_kwargs: StreamingResponse({}))

    result = qwen_local.qwen_chat_detailed(
        "system",
        "user",
        cancellation_event=threading.Event(),
    )

    assert result.text == "first\nsecond"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 2


def test_streaming_shadow_decodes_utf8_before_unicode_line_splitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)

    class StreamingResponse(_FakeResponse):
        def iter_lines(self, **kwargs: Any):
            assert kwargs["decode_unicode"] is False
            payload = {
                "model": "local",
                "choices": [
                    {"delta": {"content": "截至今日，資料完整。"}, "finish_reason": "stop"}
                ],
            }
            yield ("data: " + json.dumps(payload, ensure_ascii=False)).encode("utf-8")
            yield b""
            yield b"data: [DONE]"

    monkeypatch.setattr(qwen_local.requests, "post", lambda *_args, **_kwargs: StreamingResponse({}))

    result = qwen_local.qwen_chat_detailed(
        "system",
        "user",
        cancellation_event=threading.Event(),
    )

    assert result.text == "截至今日，資料完整。"


def test_streaming_shadow_rejects_incomplete_event_at_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local_env(monkeypatch)

    class StreamingResponse(_FakeResponse):
        def iter_lines(self, **_kwargs: Any):
            yield b'data: {"choices":[{"delta":{"content":"unfinished'

    monkeypatch.setattr(qwen_local.requests, "post", lambda *_args, **_kwargs: StreamingResponse({}))

    with pytest.raises(qwen_local.QwenClientError) as captured:
        qwen_local.qwen_chat_detailed(
            "system",
            "user",
            cancellation_event=threading.Event(),
        )

    assert captured.value.reason_code == "model_invalid_stream"


def test_qwen_chat_requires_real_key_for_non_loopback_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_TEXT_API_MODE", "openai")
    monkeypatch.setenv("QWEN_BASE_URL", "https://model.example.test/v1")
    monkeypatch.setenv("QWEN_API_KEY", "local-only")
    called = False

    def fake_post(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        nonlocal called
        called = True
        return _response_message({"content": "should not be called"})

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)

    with pytest.raises(qwen_local.QwenClientError, match="must be configured"):
        qwen_local.qwen_chat("system", "user")
    assert called is False


def test_qwen_vision_uses_native_ollama_chat_and_strict_json_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QWEN_VISION_BASE_URL", "http://127.0.0.1:8020")
    monkeypatch.setenv("QWEN_VISION_MODEL_ID", "qwen3-vl:8b-instruct")
    captured: dict[str, Any] = {}
    analysis = {
        "is_stock_chart": True,
        "chart_type": "K線圖",
        "image_quality": "high",
        "overall_confidence": 0.9,
        "stock": {"code": "2330", "name": "台積電", "confidence": 0.95},
        "timeframe": "日K",
        "visible_date_range": "",
        "indicators": [],
        "price_values": [],
        "chart_observations": ["均線可見"],
        "uncertainty_reasons": [],
    }

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["timeout"] = kwargs["timeout"]
        return _FakeResponse({"message": {"content": json.dumps(analysis, ensure_ascii=False)}})

    monkeypatch.setattr(qwen_local.requests, "post", fake_post)
    result = qwen_local.qwen_vision_json("system", "user", b"test-image", timeout_seconds=20)

    assert result == analysis
    assert captured["url"] == "http://127.0.0.1:8020/api/chat"
    request_body = captured["json"]
    assert request_body["model"] == "qwen3-vl:8b-instruct"
    assert "think" not in request_body
    assert request_body["keep_alive"] == -1
    assert len(request_body["messages"]) == 1
    assert "Return only one JSON object" in request_body["messages"][0]["content"]
    assert "format" not in request_body
    assert base64.b64decode(request_body["messages"][0]["images"][0]) == b"test-image"
