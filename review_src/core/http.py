from __future__ import annotations

import time
from typing import Any

import requests

from core.config import HEADERS, mask_secret_text, safe_error


def request_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 3,
    retry_wait: float = 5,
    timeout: float = 25,
) -> Any:
    last = None
    for i in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers or HEADERS, timeout=timeout)
            if resp.status_code >= 400:
                body = resp.text.strip()[:240]
                raise RuntimeError(f"HTTP {resp.status_code}: {mask_secret_text(body)}")
            text = resp.text.strip()
            if not text:
                raise RuntimeError("empty response")
            return resp.json()
        except Exception as exc:
            last = exc
            if i < retries - 1:
                time.sleep(retry_wait)
    raise RuntimeError(safe_error(last))
