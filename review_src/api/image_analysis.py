from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from adapter.qwen_local import QwenClientError
from services.chart_image_service import analyze_chart_image
from services.image_input_service import ImageInputError, read_bounded_web_image_upload


router = APIRouter(tags=["analysis-image"])


@router.post("/api/analysis/image")
async def api_analyze_uploaded_image(request: Request) -> dict[str, Any]:
    """Analyze one bounded direct multipart upload; remote image references are unsupported."""

    content_type = str(request.headers.get("content-type") or "")
    if content_type.lower().startswith("application/json"):
        raise HTTPException(status_code=400, detail="remote_image_inputs_are_not_supported")
    try:
        upload = await read_bounded_web_image_upload(content_type, request.stream())
        return analyze_chart_image(
            upload.data,
            conversation_context=upload.conversation_context,
        )
    except ImageInputError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc
    except QwenClientError as exc:
        raise HTTPException(status_code=503, detail="vision_analysis_is_temporarily_unavailable") from exc
