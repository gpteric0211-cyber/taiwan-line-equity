from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.data_quality import assess_chart_image_analysis  # noqa: E402
from core.image_data_quality_v1 import assess_chart_image_analysis_v1  # noqa: E402
from services import chart_image_service  # noqa: E402
from services.image_input_service import ImageInputError  # noqa: E402


def _image_bytes(image_format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), color=(240, 240, 240)).save(output, format=image_format)
    return output.getvalue()


def _vision_result(*, code: str = "2330", stock_confidence: float = 0.95) -> dict[str, object]:
    return {
        "is_stock_chart": True,
        "chart_type": "K線圖",
        "image_quality": "high",
        "overall_confidence": 0.9,
        "stock": {"code": code, "name": "台積電", "confidence": stock_confidence},
        "timeframe": "日K",
        "visible_date_range": "2026-08-01 至 2026-08-26",
        "indicators": [
            {
                "name": "RSI",
                "period": "14",
                "value": 42.6,
                "signal": "線條回升",
                "visible": True,
                "confidence": 0.9,
            },
            {
                "name": "MACD",
                "period": "",
                "value": None,
                "signal": "柱狀體可見",
                "visible": True,
                "confidence": 0.8,
            },
        ],
        "price_values": [],
        "chart_observations": ["近期低點略為墊高"],
        "uncertainty_reasons": ["無法只靠配色確認紅綠意義"],
    }


def test_chart_quality_marks_values_estimated_and_blocks_referee() -> None:
    result = assess_chart_image_analysis(_vision_result())

    assert result["ready"] is True
    assert result["status"] == "estimated"
    assert result["stock"]["code"] == "2330"
    assert result["indicators"][0]["value"] == 42.6
    assert result["can_enter_referee"] is False


def test_chart_quality_rejects_low_confidence_hallucinated_values() -> None:
    raw = _vision_result(stock_confidence=0.2)
    raw["overall_confidence"] = 0.4
    raw["image_quality"] = "low"
    result = assess_chart_image_analysis_v1(raw)

    assert result["ready"] is False
    assert result["status"] == "unavailable"
    assert result["stock"]["code"] is None
    assert "image_quality_is_too_low" in result["reasons"]
    assert "chart_confidence_is_too_low" in result["reasons"]


def test_chart_service_prefers_visible_image_stock_and_crosschecks_official_data(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    monkeypatch.setattr(
        chart_image_service,
        "qwen_vision_json",
        lambda *_args, **_kwargs: _vision_result(),
    )
    calls: list[tuple[str, str]] = []

    def fake_resolve(query: str) -> dict[str, object]:
        calls.append(("resolve", query))
        return {
            "ok": True,
            "stock": {"code": "2330", "name": "台積電", "market": "listed", "exchange": "TWSE"},
        }

    def fake_daily(code: str) -> dict[str, object]:
        calls.append(("daily", code))
        return {"status": "ok", "code": code, "trade_date": "2026-08-26"}

    monkeypatch.setattr(chart_image_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(chart_image_service, "fetch_daily_market_data", fake_daily)
    result = chart_image_service.analyze_chart_image(
        _image_bytes(),
        conversation_context={"code": "2317", "stock_name": "鴻海"},
    )

    assert result["ok"] is True
    assert result["stock_resolution_source"] == "image"
    assert result["context_conflict"] is True
    assert result["official_payload"]["code"] == "2330"
    assert result["can_override_main_status"] is False
    assert calls == [("resolve", "2330"), ("daily", "2330")]


def test_chart_service_uses_conversation_stock_only_when_image_has_no_identity(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    raw = _vision_result(code="")
    raw["stock"] = {"code": "", "name": "", "confidence": 0.1}
    monkeypatch.setattr(chart_image_service, "qwen_vision_json", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        chart_image_service,
        "resolve_stock_query",
        lambda query: {
            "ok": query == "2317",
            "stock": {"code": "2317", "name": "鴻海", "market": "listed", "exchange": "TWSE"},
        },
    )
    monkeypatch.setattr(
        chart_image_service,
        "fetch_daily_market_data",
        lambda code: {"status": "ok", "code": code},
    )

    result = chart_image_service.analyze_chart_image(
        _image_bytes(),
        conversation_context={"code": "2317", "stock_name": "鴻海"},
    )

    assert result["stock_resolution_source"] == "conversation"
    assert result["stock"]["code"] == "2317"
    assert result["context_conflict"] is False


def test_non_chart_never_calls_market_api(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    raw = _vision_result()
    raw["is_stock_chart"] = False
    monkeypatch.setattr(chart_image_service, "qwen_vision_json", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        chart_image_service,
        "resolve_stock_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    result = chart_image_service.analyze_chart_image(_image_bytes())

    assert result["ok"] is False
    assert result["status"] == "not_stock_chart"
    assert result["image_artifact"]["classification"]["is_stock_chart"] is False
    assert "不會據此查行情" in result["answer_text"]
    assert len(result["answer_text"]) < 100


def test_non_chart_classification_with_low_quality_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    raw = _vision_result()
    raw["is_stock_chart"] = False
    raw["image_quality"] = "low"
    monkeypatch.setattr(chart_image_service, "qwen_vision_json", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        chart_image_service,
        "resolve_stock_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    result = chart_image_service.analyze_chart_image(_image_bytes())

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["quality"]["classification_ready"] is False


@pytest.mark.parametrize("value", [None, 1, 0, "true", [], {}])
def test_chart_quality_requires_is_stock_chart_to_be_an_exact_boolean(value) -> None:
    raw = _vision_result()
    raw["is_stock_chart"] = value

    result = assess_chart_image_analysis_v1(raw)

    assert result["ready"] is False
    assert result["classification_schema_valid"] is False
    assert "is_stock_chart_must_be_a_required_boolean" in result["reasons"]


def test_bad_image_fails_before_vision_model(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    monkeypatch.setattr(
        chart_image_service,
        "qwen_vision_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not call model")),
    )

    with pytest.raises(ImageInputError, match="image_decode_failed"):
        chart_image_service.analyze_chart_image(b"\x89PNG\r\n\x1a\ntruncated")


def test_equal_image_inputs_have_equal_typed_artifacts(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    raw = _vision_result(code="")
    raw["stock"] = {"code": "", "name": "", "confidence": 0.0}
    monkeypatch.setattr(chart_image_service, "qwen_vision_json", lambda *_args, **_kwargs: raw)
    image = _image_bytes("JPEG")

    web_result = chart_image_service.analyze_chart_image(image)
    line_result = chart_image_service.analyze_chart_image(image)

    assert web_result["quality"] == line_result["quality"]
    assert web_result["image_artifact"]["classification"] == line_result["image_artifact"]["classification"]
    assert web_result["image_artifact"]["typed_observations"] == line_result["image_artifact"]["typed_observations"]
    assert web_result["image_artifact"]["artifact_digest"] == line_result["image_artifact"]["artifact_digest"]


def test_conversation_summary_never_contains_image_bytes_or_artifact_digest(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    raw = _vision_result(code="")
    raw["stock"] = {"code": "", "name": "", "confidence": 0.0}
    monkeypatch.setattr(chart_image_service, "qwen_vision_json", lambda *_args, **_kwargs: raw)

    result = chart_image_service.analyze_chart_image(_image_bytes())
    summary = chart_image_service.chart_context_summary(result)

    assert all(not isinstance(value, bytes) for value in summary.values())
    assert "image_artifact" not in summary
    assert "artifact_digest" not in summary
