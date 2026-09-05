from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import date
from typing import Any

import requests

from core.config import HEADERS, safe_error

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


LISTED_EVENTS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
OTC_EVENTS_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"
ATTENTION_TERMS = (
    "停止交易", "恢復交易", "重大災害", "火災", "資安事件", "訴訟",
    "違約", "處分", "裁罰", "終止", "減資", "破產", "重整", "下修",
)


def _roc_date(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 7:
        return None
    try:
        parsed = date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7]))
    except ValueError:
        return None
    return parsed.isoformat()


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _disclosed_time(value: Any) -> str:
    """Return the MOPS disclosure time as validated zero-padded HHMMSS."""

    digits = re.sub(r"\D", "", str(value or ""))
    if not digits or len(digits) > 6:
        return ""
    padded = digits.zfill(6)
    hour, minute, second = (int(padded[0:2]), int(padded[2:4]), int(padded[4:6]))
    return padded if hour < 24 and minute < 60 and second < 60 else ""


def _event_key(row: dict[str, Any]) -> str:
    text = "|".join(
        str(row.get(key) or "")
        for key in ("market", "code", "disclosed_date", "disclosed_time", "subject")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(row: dict[str, Any], market: str) -> dict[str, Any] | None:
    row = {str(key).strip(): value for key, value in row.items()}
    code = _first(row, "公司代號", "SecuritiesCompanyCode")
    if not re.fullmatch(r"\d{4}", code):
        return None
    subject = _first(row, "主旨")
    disclosed_date = _roc_date(_first(row, "發言日期")) or _roc_date(_first(row, "出表日期", "Date"))
    if not subject or not disclosed_date:
        return None
    raw_disclosed_time = _first(row, "發言時間")
    normalized = {
        "disclosed_date": disclosed_date,
        "disclosed_time": _disclosed_time(raw_disclosed_time),
        "fact_date": _roc_date(_first(row, "事實發生日")),
        "code": code,
        "company_name": _first(row, "公司名稱", "CompanyName"),
        "subject": subject[:1000],
        "explanation": _first(row, "說明")[:6000],
        "article_code": _first(row, "符合條款"),
        "market": market,
        "attention_level": "attention" if any(term in subject for term in ATTENTION_TERMS) else "normal",
        "source": "TWSE_MOPS_DAILY_EVENT" if market == "listed" else "TPEX_MOPS_DAILY_EVENT",
        "source_quality": "official",
    }
    # Keep the identity compatible with already persisted rows while storing a
    # canonical six-digit time for chronology and session classification.
    normalized["event_key"] = _event_key(
        {**normalized, "disclosed_time": raw_disclosed_time}
    )
    return normalized


def _transport_failure(exc: Exception) -> tuple[str, str | None]:
    """Map transport exceptions to the retrieval worker's bounded failure classes."""

    if isinstance(exc, requests.Timeout):
        return "timeout", "source_timeout"
    if isinstance(exc, requests.ConnectionError):
        return "offline", "source_offline"
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None and int(getattr(response, "status_code", 0) or 0) == 429:
            return "rate_limited", "source_rate_limited"
    return "source_error", None


def fetch_official_company_events(
    timeout: float = 20,
    *,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    getter = http_get or requests.get
    items: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    for market, url in (("listed", LISTED_EVENTS_URL), ("otc", OTC_EVENTS_URL)):
        try:
            response = getter(
                url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            payload = json.loads(response.text)
            raw_rows = payload if isinstance(payload, list) else []
            parsed = [item for item in (_normalize(row, market) for row in raw_rows if isinstance(row, dict)) if item]
            items.extend(parsed)
            source_results.append(
                {
                    "market": market,
                    "ok": True,
                    "status": "ok" if parsed else "no_results",
                    "timeout_class": None,
                    "rows": len(parsed),
                    "url": url,
                }
            )
        except Exception as exc:
            status, timeout_class = _transport_failure(exc)
            source_results.append(
                {
                    "market": market,
                    "ok": False,
                    "status": status,
                    "timeout_class": timeout_class,
                    "rows": 0,
                    "url": url,
                    "error": safe_error(exc),
                }
            )
    return {
        "ok": all(item.get("ok") for item in source_results),
        "status": "ok" if all(item.get("ok") for item in source_results) else "partial",
        "items": items,
        "rows": len(items),
        "sources": source_results,
    }
