from __future__ import annotations

from typing import Any


PCHOME_SOURCE = "PCHOME"


def fetch_pchome_price_volume_detail(code: str, trade_date: str | None = None) -> dict[str, Any]:
    """Skeleton for future PChome supplemental price-volume detail.

    This phase intentionally performs no HTTP request.  PChome may require
    session/cookie/Cloudflare handling and must be implemented in a separate
    explicit phase after legal/operational review.
    """
    return {
        "ok": False,
        "source": PCHOME_SOURCE,
        "source_quality": "UNAVAILABLE",
        "code": str(code or "").strip(),
        "trade_date": trade_date,
        "rows": [],
        "reason": "PChome supplemental source is skeleton-only in this phase.",
        "http_request_performed": False,
    }


def fetch_pchome_major_traders(code: str, trade_date: str | None = None) -> dict[str, Any]:
    """Skeleton for future PChome major trader / broker-like data."""
    return {
        "ok": False,
        "source": PCHOME_SOURCE,
        "source_quality": "UNAVAILABLE",
        "code": str(code or "").strip(),
        "trade_date": trade_date,
        "rows": [],
        "reason": "PChome major trader source is skeleton-only in this phase.",
        "http_request_performed": False,
    }
