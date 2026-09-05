from __future__ import annotations

import json
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from adapter.line_messaging import verify_line_signature
from core.line_bot_config import env_bool, env_text
from services.line_bot_service import handle_line_event


router = APIRouter(tags=["line-webhook"])


@router.post("/line/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    ingress_monotonic = time.monotonic()
    channel_secret = env_text("LINE_CHANNEL_SECRET")
    if not channel_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LINE webhook is not configured",
        )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024 * 1024:
            raise HTTPException(status_code=413, detail="webhook payload is too large")
    raw_body = bytes(body)
    signature = request.headers.get("x-line-signature", "")
    if env_bool("LINE_VERIFY_SIGNATURE", True) and not verify_line_signature(
        raw_body,
        signature,
        channel_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid LINE signature",
        )
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid webhook payload",
        ) from exc
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="events must be a list",
        )
    if len(events) > 20:
        raise HTTPException(status_code=413, detail="too many webhook events")
    for event in events:
        if isinstance(event, dict):
            background_tasks.add_task(
                handle_line_event,
                event,
                webhook_ingress_monotonic=ingress_monotonic,
            )
    return {"status": "accepted"}
