from __future__ import annotations

"""Bounded official Federal Reserve monetary-policy RSS retrieval."""

import hashlib
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from core.config import HEADERS
from core.news_research_policy import (
    OFFICIAL_RESEARCH_POLICY_VERSION,
    public_news_source_rights,
)


TPE = ZoneInfo("Asia/Taipei")
FEDERAL_RESERVE_MONETARY_POLICY_RSS_URL = (
    "https://www.federalreserve.gov/feeds/press_monetary.xml"
)
FEDERAL_RESERVE_SOURCE_ID = "FEDERAL_RESERVE_MONETARY_POLICY_RSS"
FEDERAL_RESERVE_RETRIEVAL_ADAPTER_VERSION = "single-track-v3-fed-monetary-rss-v1"
MAX_RESPONSE_BYTES = 1_000_000
MAX_EVENTS_PER_RUN = 8


def _target_code(values: list[str] | tuple[str, ...]) -> str:
    codes = sorted({str(value or "").strip() for value in values})
    if len(codes) != 1 or not re.fullmatch(r"\d{4}", codes[0]):
        raise ValueError("Federal Reserve retrieval requires exactly one four-digit entity ref")
    return codes[0]


def _clock_value(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(TPE)
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an offset-aware datetime")
    return value.astimezone(TPE)


def _text(value: Any, maximum: int = 300) -> str:
    clean = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return re.sub(r"\s+", " ", clean).strip()[:maximum]


def _child(node: ElementTree.Element, name: str) -> str:
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1].lower() == name.lower():
            return "".join(child.itertext()).strip()
    return ""


def _timestamp(value: Any, *, retrieved_at: datetime) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    normalized = parsed.astimezone(TPE)
    if normalized > retrieved_at:
        return None
    return normalized.isoformat(timespec="seconds")


def _official_link(value: Any) -> str | None:
    text = str(value or "").strip()
    parsed = urlparse(text)
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or hostname not in {"federalreserve.gov", "www.federalreserve.gov"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return text


def _parse_events(
    content: bytes,
    *,
    stock_code: str,
    retrieved_at: datetime,
) -> list[dict[str, Any]]:
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large")
    # The payload is already bounded to 1 MB, so inspect the complete document.
    # A declaration can legally appear after a long whitespace prefix and must
    # not bypass the entity/DTD rejection boundary.
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("unsafe_xml_rejected")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid_response") from exc
    rights = public_news_source_rights(FEDERAL_RESERVE_SOURCE_ID)
    if not rights or not rights.get("allow_fetch") or not rights.get("allow_model"):
        raise PermissionError("disabled_by_source_policy")
    events: list[dict[str, Any]] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() not in {"item", "entry"}:
            continue
        title = _text(_child(node, "title"))
        source_url = _official_link(_child(node, "link"))
        published_at = _timestamp(
            _child(node, "pubDate") or _child(node, "published") or _child(node, "updated"),
            retrieved_at=retrieved_at,
        )
        if not title or source_url is None or published_at is None:
            continue
        event_key = hashlib.sha256(
            f"{FEDERAL_RESERVE_SOURCE_ID}|{source_url}|{published_at}|{title}".encode(
                "utf-8"
            )
        ).hexdigest()
        events.append(
            {
                "event_key": event_key,
                "event_date": published_at[:10],
                "title": title,
                "publisher": "Board of Governors of the Federal Reserve System",
                "url": source_url,
                "publisher_published_at": published_at,
                "publisher_time_verified": True,
                "index_seen_at": None,
                "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
                "verification_state": "primary_verified",
                "untrusted_text": True,
                "rights": rights,
                "citation_required": True,
                "authority_tier": "canonical_official",
                "quality": "official_primary",
                "entity_refs": [stock_code],
                "can_override_main_status": False,
            }
        )
        if len(events) > MAX_EVENTS_PER_RUN:
            raise ValueError("response_too_large")
    return events


def _failure(exc: Exception) -> tuple[str, str | None]:
    if isinstance(exc, requests.Timeout):
        return "timeout", "source_timeout"
    if isinstance(exc, requests.ConnectionError):
        return "offline", "source_offline"
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None and int(getattr(response, "status_code", 0) or 0) == 429:
            return "rate_limited", "source_rate_limited"
    if isinstance(exc, PermissionError):
        return "disabled_by_source_policy", None
    if isinstance(exc, ValueError) and str(exc) in {
        "invalid_response",
        "response_too_large",
        "unsafe_xml_rejected",
    }:
        return str(exc), None
    return "source_error", None


def build_federal_reserve_retrieval_fetcher(
    entity_refs: list[str] | tuple[str, ...],
    *,
    http_get: Callable[..., Any] = requests.get,
    clock: Callable[[], datetime] | None = None,
    timeout_seconds: float = 8.0,
) -> Callable[[str], dict[str, Any]]:
    stock_code = _target_code(entity_refs)
    request_timeout = max(1.0, min(float(timeout_seconds), 10.0))

    def retrieve(_: str) -> dict[str, Any]:
        retrieved_at = _clock_value(clock)
        try:
            response = http_get(
                FEDERAL_RESERVE_MONETARY_POLICY_RSS_URL,
                headers={**HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"},
                timeout=(request_timeout, request_timeout),
                allow_redirects=False,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 429:
                return _result("rate_limited", [], "source_rate_limited")
            if 300 <= status_code < 400:
                return _result("redirect_rejected", [], None)
            response.raise_for_status()
            events = _parse_events(
                bytes(response.content or b""),
                stock_code=stock_code,
                retrieved_at=retrieved_at,
            )
        except Exception as exc:
            status, timeout_class = _failure(exc)
            return _result(status, [], timeout_class)
        return _result("ok" if events else "no_results", events, None)

    return retrieve


def _result(
    status: str,
    events: list[dict[str, Any]],
    timeout_class: str | None,
) -> dict[str, Any]:
    success = status in {"ok", "no_results"}
    return {
        "ok": success,
        "status": status,
        "events": events,
        "attempts": 1,
        "timeout_class": timeout_class,
        "adapter_version": FEDERAL_RESERVE_RETRIEVAL_ADAPTER_VERSION,
        "source_policy_version": OFFICIAL_RESEARCH_POLICY_VERSION,
        "source_id": FEDERAL_RESERVE_SOURCE_ID,
        "source_attempts": [
            {
                "source_id": FEDERAL_RESERVE_SOURCE_ID,
                "status": status,
                "timeout_class": timeout_class,
                "item_count": len(events),
            }
        ],
        "raw_article_bodies_fetched": 0,
        "raw_body_retention_seconds": 0,
        "canonical_table_writes": 0,
    }
