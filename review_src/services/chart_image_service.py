from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from adapter.bot_market_data_client import (
    BotMarketDataClientError,
    fetch_daily_market_data,
    resolve_stock_query,
)
from adapter.qwen_local import qwen_vision_json
from core.image_data_quality_v1 import assess_chart_image_analysis_v1
from core.line_bot_config import env_bool, env_int
from services.image_input_service import VerifiedImageInput, verify_and_sanitize_image
from services.model_admission_service import run_interactive_model


CHART_VISION_SYSTEM_PROMPT = """Extract only facts directly visible in a Taiwan stock technical chart.
Do not calculate or infer exact RSI, MACD, KD, moving-average, price, date, timeframe, stock code, or stock name when its text is not clearly readable.
Observations may describe visible candle, trend, moving-average, volume, or indicator-line structure, but must not predict a certain rise/fall, issue a trade instruction, or provide a target price.
Do not infer red/green meaning without enough chart evidence because app color conventions differ.
If the image is a chat, news article, statement, ordinary text, or not a technical chart, set is_stock_chart=false.
Lower confidence for blur, cropping, or tiny text. Do not output names, accounts, notifications, or other personal information.
"""

IMAGE_ANALYSIS_ARTIFACT_VERSION = "ImageAnalysisArtifactV1"
NON_STOCK_IMAGE_RESPONSE = (
    "這張不是可可靠分析的股票技術圖，我不會據此查行情或推測交易訊號。"
    "若要分析，請上傳含股票名稱、週期、K 線與指標區的完整截圖。"
)


def _remaining_timeout(deadline_monotonic: float | None) -> float | None:
    if deadline_monotonic is None:
        return None
    reply_reserve = env_int("LINE_REPLY_TIMEOUT_SECONDS", 12, minimum=3, maximum=30) + 2
    return deadline_monotonic - time.monotonic() - float(reply_reserve)


def _safe_stock_resolution(resolution: dict[str, Any]) -> dict[str, Any] | None:
    if not resolution.get("ok"):
        return None
    stock = resolution.get("stock") if isinstance(resolution.get("stock"), dict) else {}
    code = str(stock.get("code") or "")
    if not (len(code) == 4 and code.isdigit()):
        return None
    return {
        "code": code,
        "name": str(stock.get("name") or "")[:40],
        "market": str(stock.get("market") or "")[:20],
        "exchange": str(stock.get("exchange") or "")[:20],
    }


def _image_analysis_artifact(
    verified: VerifiedImageInput,
    quality: dict[str, Any],
) -> dict[str, Any]:
    typed_observations = {
        "stock": dict(quality.get("stock") or {}),
        "chart_type": str(quality.get("chart_type") or ""),
        "timeframe": str(quality.get("timeframe") or ""),
        "visible_date_range": str(quality.get("visible_date_range") or ""),
        "indicators": [dict(item) for item in list(quality.get("indicators") or [])],
        "price_values": [dict(item) for item in list(quality.get("price_values") or [])],
        "chart_observations": list(quality.get("chart_observations") or []),
        "uncertainty_reasons": list(quality.get("uncertainty_reasons") or []),
        "estimated": True,
        "can_enter_referee": False,
    }
    seed = {
        "artifact_version": IMAGE_ANALYSIS_ARTIFACT_VERSION,
        "input_digest": verified.digest,
        "classification": {
            "is_stock_chart": quality.get("is_stock_chart"),
            "schema_valid": bool(quality.get("classification_schema_valid")),
            "ready": bool(quality.get("classification_ready")),
            "confidence": quality.get("overall_confidence"),
        },
        "quality_status": str(quality.get("status") or "unavailable"),
        "quality_reason": str(quality.get("reason") or ""),
        "typed_observations": typed_observations,
    }
    artifact_digest = hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "artifact_version": IMAGE_ANALYSIS_ARTIFACT_VERSION,
        "artifact_digest": artifact_digest,
        "classification": seed["classification"],
        "quality_status": seed["quality_status"],
        "typed_observations": typed_observations,
        "input": {
            "format": verified.image_format,
            "mime_type": verified.mime_type,
            "width": verified.width,
            "height": verified.height,
            "pixel_count": verified.pixel_count,
            "metadata_retained": False,
        },
    }


def analyze_chart_image(
    image_bytes: bytes,
    *,
    conversation_context: dict[str, Any] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Extract chart observations and fetch official data for cross-checking.

    No image bytes or full OCR text are persisted or returned.  Model-derived
    numbers are explicitly estimated and never enter the referee.
    """

    verified = verify_and_sanitize_image(image_bytes)
    if not env_bool("QWEN_VISION_ENABLED", False):
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "vision_analysis_not_configured",
            "quality": {"ready": False, "status": "unavailable"},
        }
    timeout = _remaining_timeout(deadline_monotonic)
    if timeout is not None and timeout < 8:
        return {
            "ok": False,
            "status": "source_delayed",
            "reason": "insufficient_reply_budget_for_image_analysis",
            "quality": {"ready": False, "status": "source_delayed"},
        }
    admission = run_interactive_model(
        lambda: qwen_vision_json(
            CHART_VISION_SYSTEM_PROMPT,
            "請逐項讀取這張圖片中的股票技術圖、K 線與技術指標。無法直接看清楚的欄位請留空，不要猜。",
            verified.data,
            timeout_seconds=timeout,
        ),
        category="interactive_chart_vision",
        deadline_monotonic=(time.monotonic() + timeout) if timeout else None,
    )
    raw = admission.value
    quality = assess_chart_image_analysis_v1(raw)
    artifact = _image_analysis_artifact(verified, quality)
    if (
        quality.get("classification_schema_valid")
        and quality.get("classification_ready")
        and quality.get("is_stock_chart") is False
    ):
        return {
            "ok": False,
            "status": "not_stock_chart",
            "reason": "image_is_not_a_stock_chart",
            "quality": quality,
            "image_artifact": artifact,
            "answer_text": NON_STOCK_IMAGE_RESPONSE,
            "can_override_main_status": False,
        }
    if not quality.get("ready"):
        return {
            "ok": False,
            "status": str(quality.get("status") or "unavailable"),
            "reason": quality.get("reason"),
            "quality": quality,
            "image_artifact": artifact,
            "can_override_main_status": False,
        }

    image_stock = quality.get("stock") if isinstance(quality.get("stock"), dict) else {}
    image_code = str(image_stock.get("code") or "")
    image_name = str(image_stock.get("name") or "")
    context_code = str((conversation_context or {}).get("code") or "")
    query = image_code or image_name or context_code
    resolution_source = "image" if image_code or image_name else ("conversation" if context_code else "unavailable")
    stock: dict[str, Any] | None = None
    official_payload: dict[str, Any] | None = None
    if query:
        try:
            stock = _safe_stock_resolution(resolve_stock_query(query))
            if stock:
                official_payload = fetch_daily_market_data(stock["code"])
        except BotMarketDataClientError:
            stock = None
            official_payload = None

    return {
        "ok": True,
        "status": "ok",
        "quality": quality,
        "stock": stock,
        "stock_resolution_source": resolution_source if stock else "unavailable",
        "context_conflict": bool(stock and context_code and stock["code"] != context_code),
        "official_payload": official_payload,
        "image_artifact": artifact,
        "can_override_main_status": False,
    }


def chart_context_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Create a bounded, non-image conversation record for later follow-ups."""

    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    stock = result.get("stock") if isinstance(result.get("stock"), dict) else {}
    return {
        "code": str(stock.get("code") or ""),
        "stock_name": str(stock.get("name") or "")[:40],
        "timeframe": str(quality.get("timeframe") or "")[:30],
        "visible_date_range": str(quality.get("visible_date_range") or "")[:50],
        "image_quality": str(quality.get("image_quality") or "")[:12],
        "overall_confidence": quality.get("overall_confidence"),
        "indicators": [dict(item) for item in list(quality.get("indicators") or [])[:8]],
        "chart_observations": [str(item)[:160] for item in list(quality.get("chart_observations") or [])[:4]],
        "status": str(result.get("status") or "unavailable"),
        "can_override_main_status": False,
    }
