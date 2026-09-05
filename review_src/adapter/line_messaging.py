from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from urllib import error as urlerror
from urllib import request as urlrequest

from core.line_bot_config import env_int, env_text


LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message/{message_id}/content"
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class LineMessagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class LineImageContent:
    data: bytes
    mime_type: str


def _verified_image_mime(data: bytes, header_value: str = "") -> str | None:
    """Return a supported MIME type from file signatures, not the HTTP header."""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def get_line_image_content(
    message_id: str,
    *,
    content_provider_type: str = "line",
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> LineImageContent:
    """Download one LINE-hosted image into bounded memory without persisting it."""

    clean_id = str(message_id or "").strip()
    if str(content_provider_type or "").lower() != "line":
        raise LineMessagingError("external image providers are not supported")
    access_token = env_text("LINE_CHANNEL_ACCESS_TOKEN")
    if not access_token:
        raise LineMessagingError("LINE content credentials are not configured")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", clean_id):
        raise LineMessagingError("LINE image message ID is invalid")
    byte_limit = max(1024, min(int(max_bytes), DEFAULT_MAX_IMAGE_BYTES))
    content_request = urlrequest.Request(
        LINE_CONTENT_URL.format(message_id=clean_id),
        headers={"Authorization": f"Bearer {access_token}", "Accept": "image/*"},
        method="GET",
    )
    timeout = env_int("LINE_CONTENT_TIMEOUT_SECONDS", 12, minimum=3, maximum=25)
    try:
        with urlrequest.urlopen(content_request, timeout=timeout) as response:
            declared_length = str(response.headers.get("Content-Length") or "").strip()
            if declared_length.isdigit() and int(declared_length) > byte_limit:
                raise LineMessagingError("LINE image is larger than the configured limit")
            data = response.read(byte_limit + 1)
            if len(data) > byte_limit:
                raise LineMessagingError("LINE image is larger than the configured limit")
            header_type = str(response.headers.get("Content-Type") or "")
    except LineMessagingError:
        raise
    except urlerror.HTTPError as exc:
        raise LineMessagingError(f"LINE content API returned HTTP {exc.code}") from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise LineMessagingError("LINE content API request failed") from exc
    mime_type = _verified_image_mime(data, header_type)
    if not mime_type:
        raise LineMessagingError("LINE content is not a supported PNG, JPEG, or WebP image")
    return LineImageContent(data=data, mime_type=mime_type)


def verify_line_signature(raw_body: bytes, signature: str, channel_secret: str) -> bool:
    if not raw_body or not signature or not channel_secret:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature.strip())


def reply_text(reply_token: str, text: str) -> None:
    access_token = env_text("LINE_CHANNEL_ACCESS_TOKEN")
    if not access_token or not reply_token:
        raise LineMessagingError("LINE reply credentials are not configured")
    safe_text = str(text or "").strip()[:5000]
    if not safe_text:
        safe_text = "目前無法產生回覆，請稍後再試。"
    timeout = env_int("LINE_REPLY_TIMEOUT_SECONDS", 12, minimum=3, maximum=30)
    payload = json.dumps(
        {"replyToken": reply_token, "messages": [{"type": "text", "text": safe_text}]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    line_request = urlrequest.Request(
        LINE_REPLY_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(line_request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            if status >= 400:
                raise LineMessagingError(f"LINE reply API returned HTTP {status}")
    except urlerror.HTTPError as exc:
        raise LineMessagingError(f"LINE reply API returned HTTP {exc.code}") from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise LineMessagingError("LINE reply API request failed") from exc
