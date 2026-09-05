from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from core.config import HEADERS, safe_error


class RequestCapExceeded(RuntimeError):
    def __init__(self, *, request_count: int, max_requests: int, attempted_url: str, purpose: str | None = None):
        self.request_count = request_count
        self.max_requests = max_requests
        self.attempted_url = attempted_url
        self.purpose = purpose
        label = f" for {purpose}" if purpose else ""
        super().__init__(
            f"request_cap_exceeded{label}: request_count={request_count}, "
            f"max_requests={max_requests}, attempted_url={attempted_url}"
        )


@dataclass
class RequestBudget:
    max_requests: int = 10
    request_count: int = 0
    request_cap_exceeded: bool = False
    request_log: list[dict[str, Any]] = field(default_factory=list)

    def consume(self, url: str, *, purpose: str | None = None) -> None:
        if self.request_count >= self.max_requests:
            self.request_cap_exceeded = True
            self.request_log.append(
                {
                    "url": url,
                    "purpose": purpose,
                    "allowed": False,
                    "request_count": self.request_count,
                    "max_requests": self.max_requests,
                }
            )
            raise RequestCapExceeded(
                request_count=self.request_count,
                max_requests=self.max_requests,
                attempted_url=url,
                purpose=purpose,
            )
        self.request_count += 1
        self.request_log.append(
            {
                "url": url,
                "purpose": purpose,
                "allowed": True,
                "request_count": self.request_count,
                "max_requests": self.max_requests,
            }
        )

    def summary(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "max_requests": self.max_requests,
            "request_cap_exceeded": self.request_cap_exceeded,
        }


def budget_summary(budget: RequestBudget | None, *, default_max_requests: int = 10) -> dict[str, Any]:
    if budget is None:
        return {
            "request_count": 0,
            "max_requests": default_max_requests,
            "request_cap_exceeded": False,
        }
    return budget.summary()


def budgeted_get_json(
    url: str,
    *,
    budget: RequestBudget,
    purpose: str | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 1,
    retry_wait: float = 0,
    timeout: float = 25,
    http_get: Callable[..., Any] | None = None,
) -> Any:
    get = http_get or requests.get
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        budget.consume(url, purpose=purpose)
        try:
            resp = get(url, params=params, headers=headers or HEADERS, timeout=timeout)
            if getattr(resp, "status_code", 0) >= 400:
                body = str(getattr(resp, "text", "") or "").strip()[:240]
                raise RuntimeError(f"HTTP {getattr(resp, 'status_code', '')}: {body}")
            if hasattr(resp, "json"):
                return resp.json()
            raise RuntimeError("response object has no json()")
        except RequestCapExceeded:
            raise
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(retry_wait)
    raise RuntimeError(safe_error(last))


def budgeted_get_response(
    url: str,
    *,
    budget: RequestBudget,
    purpose: str | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 1,
    retry_wait: float = 0,
    timeout: float = 25,
    http_get: Callable[..., Any] | None = None,
) -> Any:
    get = http_get or requests.get
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        budget.consume(url, purpose=purpose)
        try:
            return get(url, params=params, headers=headers or HEADERS, timeout=timeout)
        except RequestCapExceeded:
            raise
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(retry_wait)
    raise RuntimeError(safe_error(last))
