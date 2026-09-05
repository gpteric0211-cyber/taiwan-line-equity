from __future__ import annotations

"""Bounded metadata-only Treasury, OFAC, and BIS official-index retrieval."""

import hashlib
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

from core.config import HEADERS
from core.news_research_policy import (
    OFFICIAL_RESEARCH_POLICY_VERSION,
    public_news_source_rights,
)


TPE = ZoneInfo("Asia/Taipei")
US_TREASURY_PRESS_RELEASES_URL = "https://home.treasury.gov/news/press-releases/"
OFAC_RECENT_ACTIONS_URL = "https://ofac.treasury.gov/recent-actions"
BIS_PRESS_RELEASES_URL = "https://www.bis.gov/news-updates/all-press-releases"
US_TREASURY_SOURCE_ID = "US_TREASURY_PRESS_RELEASE_INDEX"
OFAC_SOURCE_ID = "OFAC_RECENT_ACTIONS_INDEX"
BIS_SOURCE_ID = "BIS_PRESS_RELEASE_INDEX"
US_POLICY_SANCTIONS_RETRIEVAL_SOURCE_ID = "us_policy_sanctions_official"
US_POLICY_SANCTIONS_ADAPTER_VERSION = "single-track-v3-us-policy-sanctions-index-v1"
MAX_RESPONSE_BYTES = 2_000_000
MAX_EVENTS_PER_SOURCE = 10

_DATE_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\b",
    re.IGNORECASE,
)
_OFAC_PATH_PATTERN = re.compile(r"^/recent-actions/(\d{4})(\d{2})(\d{2})/?$")


def _target_code(values: Sequence[str]) -> str:
    codes = sorted({str(value or "").strip() for value in values})
    if len(codes) != 1 or not re.fullmatch(r"\d{4}", codes[0]):
        raise ValueError("U.S. official retrieval requires exactly one four-digit entity ref")
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


def _date_text(value: str) -> str | None:
    match = _DATE_PATTERN.search(value)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group(0), "%B %d, %Y").date()
    except ValueError:
        return None
    return parsed.isoformat()


def _not_future(value: str, retrieved_at: datetime) -> bool:
    try:
        return date.fromisoformat(value) <= retrieved_at.date()
    except ValueError:
        return False


class _OfficialIndexParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.current_date: str | None = None
        self.anchor_href: str | None = None
        self.anchor_text: list[str] = []
        self.rows: list[tuple[str, str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self.anchor_href is not None:
            return
        href = dict(attrs).get("href")
        if href:
            self.anchor_href = urljoin(self.base_url, href)
            self.anchor_text = []

    def handle_data(self, data: str) -> None:
        observed_date = _date_text(data)
        if observed_date is not None:
            self.current_date = observed_date
        if self.anchor_href is not None:
            self.anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self.anchor_href is None:
            return
        self.rows.append(
            (self.anchor_href, _text(" ".join(self.anchor_text)), self.current_date)
        )
        self.anchor_href = None
        self.anchor_text = []


def _official_url(value: str, source_id: str) -> tuple[str, str | None] | None:
    parsed = urlparse(value)
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    if source_id == US_TREASURY_SOURCE_ID:
        if hostname != "home.treasury.gov":
            return None
        if not re.fullmatch(r"/news/press-releases/[^/?#]+/?", parsed.path):
            return None
        return value, None
    if source_id == OFAC_SOURCE_ID:
        if hostname != "ofac.treasury.gov":
            return None
        match = _OFAC_PATH_PATTERN.fullmatch(parsed.path)
        if match is None:
            return None
        try:
            event_date = date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            return None
        return value, event_date
    if source_id == BIS_SOURCE_ID:
        if hostname != "www.bis.gov":
            return None
        if not re.fullmatch(r"/press-release/[^/?#]+/?", parsed.path):
            return None
        return value, None
    return None


def _parse_index(
    content: bytes,
    *,
    base_url: str,
    source_id: str,
    publisher: str,
    stock_code: str,
    retrieved_at: datetime,
) -> tuple[list[dict[str, Any]], int]:
    if not content or len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large" if content else "invalid_response")
    try:
        decoded = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_response") from exc
    parser = _OfficialIndexParser(base_url)
    try:
        parser.feed(decoded)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise ValueError("invalid_response") from exc
    rights = public_news_source_rights(source_id)
    if not rights or not rights.get("allow_fetch") or not rights.get("allow_model"):
        raise PermissionError("disabled_by_source_policy")
    events: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    eligible_links = 0
    for raw_url, title, preceding_date in parser.rows:
        normalized = _official_url(raw_url, source_id)
        if normalized is None:
            continue
        eligible_links += 1
        source_url, path_date = normalized
        event_date = path_date or preceding_date
        if (
            not title
            or not event_date
            or not _not_future(event_date, retrieved_at)
            or source_url in seen_urls
        ):
            continue
        seen_urls.add(source_url)
        event_key = hashlib.sha256(
            f"{source_id}|{source_url}|{event_date}|{title}".encode("utf-8")
        ).hexdigest()
        events.append(
            {
                "event_key": event_key,
                "event_date": event_date,
                "title": title,
                "publisher": publisher,
                "url": source_url,
                "publisher_published_at": None,
                "publisher_time_verified": False,
                "index_seen_at": None,
                "retrieved_at": retrieved_at.isoformat(timespec="seconds"),
                "verification_state": "primary_verified",
                "untrusted_text": True,
                "rights": rights,
                "citation_required": True,
                "authority_tier": "canonical_official",
                "quality": "official_primary_date_only",
                "entity_refs": [stock_code],
                "can_override_main_status": False,
            }
        )
        if len(events) >= MAX_EVENTS_PER_SOURCE:
            break
    return events, eligible_links


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
    }:
        return str(exc), None
    return "source_error", None


def build_us_policy_sanctions_retrieval_fetcher(
    entity_refs: Sequence[str],
    *,
    http_get: Callable[..., Any] = requests.get,
    clock: Callable[[], datetime] | None = None,
    timeout_seconds: float = 8.0,
) -> Callable[[str], dict[str, Any]]:
    stock_code = _target_code(entity_refs)
    request_timeout = max(1.0, min(float(timeout_seconds), 10.0))
    sources = (
        (
            US_TREASURY_SOURCE_ID,
            US_TREASURY_PRESS_RELEASES_URL,
            "U.S. Department of the Treasury",
        ),
        (OFAC_SOURCE_ID, OFAC_RECENT_ACTIONS_URL, "Office of Foreign Assets Control"),
        (
            BIS_SOURCE_ID,
            BIS_PRESS_RELEASES_URL,
            "U.S. Bureau of Industry and Security",
        ),
    )

    def retrieve(_: str) -> dict[str, Any]:
        retrieved_at = _clock_value(clock)
        events: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        for source_id, source_url, publisher in sources:
            status = "source_error"
            timeout_class = None
            source_events: list[dict[str, Any]] = []
            try:
                response = http_get(
                    source_url,
                    headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"},
                    timeout=(request_timeout, request_timeout),
                    allow_redirects=False,
                )
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code == 429:
                    status, timeout_class = "rate_limited", "source_rate_limited"
                elif 300 <= status_code < 400:
                    status = "redirect_rejected"
                else:
                    response.raise_for_status()
                    source_events, eligible_links = _parse_index(
                        bytes(response.content or b""),
                        base_url=source_url,
                        source_id=source_id,
                        publisher=publisher,
                        stock_code=stock_code,
                        retrieved_at=retrieved_at,
                    )
                    status = (
                        "ok"
                        if source_events
                        else "invalid_response"
                        if eligible_links
                        else "invalid_response"
                    )
            except Exception as exc:
                status, timeout_class = _failure(exc)
            events.extend(source_events)
            attempts.append(
                {
                    "source_id": source_id,
                    "status": status,
                    "timeout_class": timeout_class,
                    "item_count": len(source_events),
                }
            )
        successful = [item for item in attempts if item["status"] in {"ok", "no_results"}]
        failed = [item for item in attempts if item["status"] not in {"ok", "no_results"}]
        status = (
            "partial"
            if failed and successful
            else str(failed[0]["status"])
            if failed
            else "ok"
            if events
            else "no_results"
        )
        return {
            "ok": not failed,
            "status": status,
            "events": events,
            "attempts": len(attempts),
            "timeout_class": failed[0].get("timeout_class") if failed else None,
            "adapter_version": US_POLICY_SANCTIONS_ADAPTER_VERSION,
            "source_policy_version": OFFICIAL_RESEARCH_POLICY_VERSION,
            "source_id": US_POLICY_SANCTIONS_RETRIEVAL_SOURCE_ID,
            "source_attempts": attempts,
            "raw_article_bodies_fetched": 0,
            "raw_body_retention_seconds": 0,
            "canonical_table_writes": 0,
        }

    return retrieve
