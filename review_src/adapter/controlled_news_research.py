from __future__ import annotations

"""Bounded metadata-only news research for the post-reply model shadow lane."""

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass

import requests

from core.config import HEADERS
from core.news_research_policy import public_news_source_rights
from core.public_url import normalize_public_https_url


GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
CONTROLLED_NEWS_ADAPTER_VERSION = "controlled-news-metadata-v2"
TPE = ZoneInfo("Asia/Taipei")
MAX_RESPONSE_BYTES = 1_000_000
_DEFAULT_HTTP_GET = requests.get


def _clean_text(value: Any, maximum: int) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def _timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            return parsed.astimezone(TPE).isoformat(timespec="seconds")
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(TPE).isoformat(timespec="seconds")


def _rss_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(TPE).isoformat(timespec="seconds")


def _fingerprint(*values: Any) -> str:
    normalized = "\x1f".join(str(value or "").strip().lower() for value in values)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_article(
    article: dict[str, Any],
    *,
    retrieved_at: str,
) -> dict[str, Any] | None:
    title = _clean_text(article.get("title"), 160)
    source_url = normalize_public_https_url(article.get("url"))
    index_seen_at = _timestamp(article.get("seendate"))
    if not title or not source_url or not index_seen_at:
        return None
    publisher_published_at = _timestamp(
        article.get("published_at") or article.get("publishedat")
    )
    hostname = str(urlparse(source_url).hostname or "").lower().removeprefix("www.")
    publisher = _clean_text(article.get("domain") or hostname, 120) or hostname
    rights = public_news_source_rights("GDELT_DOC_INDEX")
    if not rights or not rights.get("allow_model"):
        return None
    return {
        "event_key": _fingerprint("GDELT_DOC_INDEX", source_url, index_seen_at, title),
        "event_date": index_seen_at[:10],
        "title": title,
        "publisher": publisher,
        "url": source_url,
        "publisher_published_at": publisher_published_at,
        "index_seen_at": index_seen_at,
        "retrieved_at": retrieved_at,
        "verification_state": "unverified",
        "untrusted_text": True,
        "rights": rights,
        "citation_required": True,
        "authority_tier": "news_radar",
        "quality": "unverified",
        "can_override_main_status": False,
    }


def _fetch_gdelt_news_metadata(
    query: str,
    *,
    max_records: int = 8,
    timespan: str = "3d",
    timeout_seconds: float = 6.0,
    max_attempts: int = 1,
    retry_backoff_seconds: float = 0.25,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    """Fetch GDELT title/domain/link metadata; never fetch publisher article bodies."""

    rights = public_news_source_rights("GDELT_DOC_INDEX")
    if not rights or not rights.get("allow_fetch"):
        return {
            "ok": False,
            "status": "disabled_by_source_policy",
            "events": [],
            "timeout_class": None,
            "canonical_table_writes": 0,
        }
    bounded_query = re.sub(r"\s+", " ", str(query or "")).strip()[:512]
    if not bounded_query:
        return {
            "ok": False,
            "status": "empty_query",
            "events": [],
            "timeout_class": None,
            "canonical_table_writes": 0,
        }
    bounded_timespan = str(timespan or "3d").strip().lower()
    if not re.fullmatch(r"(?:[1-9]|[12]\d|30)d", bounded_timespan):
        bounded_timespan = "3d"
    params = {
        "query": bounded_query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max(1, min(int(max_records), 12)),
        "timespan": bounded_timespan,
        "sort": "datedesc",
    }
    attempts = max(1, min(int(max_attempts), 2))
    request_timeout = max(1.0, min(float(timeout_seconds), 10.0))
    last_status = "source_delayed"
    timeout_class: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = http_get(
                GDELT_DOC_API_URL,
                params=params,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=(request_timeout, request_timeout),
                allow_redirects=False,
            )
            status_code = int(response.status_code)
            if status_code == 429:
                return {
                    "ok": False,
                    "status": "rate_limited",
                    "events": [],
                    "attempts": attempt,
                    "timeout_class": None,
                    "canonical_table_writes": 0,
                }
            if 300 <= status_code < 400:
                return {
                    "ok": False,
                    "status": "redirect_rejected",
                    "events": [],
                    "attempts": attempt,
                    "timeout_class": None,
                    "canonical_table_writes": 0,
                }
            response.raise_for_status()
            content = bytes(response.content or b"")
            if len(content) > MAX_RESPONSE_BYTES:
                return {
                    "ok": False,
                    "status": "response_too_large",
                    "events": [],
                    "attempts": attempt,
                    "timeout_class": None,
                    "canonical_table_writes": 0,
                }
            payload = json.loads(content.decode("utf-8"))
            articles = payload.get("articles") if isinstance(payload, dict) else None
            if not isinstance(articles, list):
                return {
                    "ok": False,
                    "status": "invalid_response",
                    "events": [],
                    "attempts": attempt,
                    "timeout_class": None,
                    "canonical_table_writes": 0,
                }
            retrieved_at = datetime.now(TPE).isoformat(timespec="seconds")
            events: list[dict[str, Any]] = []
            seen: set[str] = set()
            for article in articles:
                if not isinstance(article, dict):
                    continue
                normalized = _normalize_article(article, retrieved_at=retrieved_at)
                if not normalized:
                    continue
                fingerprint = str(normalized["event_key"])
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                events.append(normalized)
                if len(events) >= params["maxrecords"]:
                    break
            return {
                "ok": True,
                "status": "ok" if events else "no_results",
                "events": events,
                "attempts": attempt,
                "timeout_class": None,
                "adapter_version": CONTROLLED_NEWS_ADAPTER_VERSION,
                "source_policy_version": rights.get("policy_version"),
                "raw_article_bodies_fetched": 0,
                "raw_body_retention_seconds": 0,
                "canonical_table_writes": 0,
            }
        except requests.Timeout:
            last_status = "timeout"
            timeout_class = "source_timeout"
        except requests.ConnectionError:
            last_status = "offline"
            timeout_class = "source_offline"
        except (requests.RequestException, UnicodeDecodeError, ValueError, TypeError):
            last_status = "source_delayed"
            timeout_class = "source_error"
        if attempt < attempts:
            time.sleep(max(0.0, min(float(retry_backoff_seconds), 1.0)) * attempt)
    return {
        "ok": False,
        "status": last_status,
        "events": [],
        "attempts": attempts,
        "timeout_class": timeout_class,
        "adapter_version": CONTROLLED_NEWS_ADAPTER_VERSION,
        "raw_article_bodies_fetched": 0,
        "raw_body_retention_seconds": 0,
        "canonical_table_writes": 0,
    }


def _normalize_google_rss_item(
    item: ET.Element,
    *,
    retrieved_at: str,
) -> dict[str, Any] | None:
    title = _clean_text(item.findtext("title"), 160)
    source_url = normalize_public_https_url(item.findtext("link"))
    index_seen_at = _rss_timestamp(item.findtext("pubDate"))
    source = item.find("source")
    publisher = _clean_text(source.text if source is not None else "Google News", 120)
    if not title or not source_url or not index_seen_at or not publisher:
        return None
    rights = public_news_source_rights("GOOGLE_NEWS_RSS_INDEX")
    if not rights or not rights.get("allow_model"):
        return None
    return {
        "event_key": _fingerprint(
            "GOOGLE_NEWS_RSS_INDEX",
            source_url,
            index_seen_at,
            title,
        ),
        "event_date": index_seen_at[:10],
        "title": title,
        "publisher": publisher,
        "url": source_url,
        # RSS pubDate is treated as aggregator index time, not publisher time.
        "publisher_published_at": None,
        "index_seen_at": index_seen_at,
        "retrieved_at": retrieved_at,
        "verification_state": "unverified",
        "untrusted_text": True,
        "rights": rights,
        "citation_required": True,
        "authority_tier": "news_radar",
        "quality": "unverified",
        "can_override_main_status": False,
    }


def _fetch_google_news_rss_metadata(
    query: str,
    *,
    max_records: int,
    timeout_seconds: float,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    rights = public_news_source_rights("GOOGLE_NEWS_RSS_INDEX")
    if not rights or not rights.get("allow_fetch"):
        return {
            "ok": False,
            "status": "disabled_by_source_policy",
            "events": [],
            "timeout_class": None,
            "canonical_table_writes": 0,
        }
    try:
        response = http_get(
            GOOGLE_NEWS_RSS_URL,
            params={
                "q": query,
                "hl": "zh-TW",
                "gl": "TW",
                "ceid": "TW:zh-Hant",
            },
            headers={**HEADERS, "Accept": "application/rss+xml, application/xml"},
            timeout=(timeout_seconds, timeout_seconds),
            allow_redirects=False,
        )
        status_code = int(response.status_code)
        if status_code == 429:
            raise requests.HTTPError("rate limited", response=response)
        if 300 <= status_code < 400:
            return {
                "ok": False,
                "status": "redirect_rejected",
                "events": [],
                "timeout_class": None,
                "canonical_table_writes": 0,
            }
        response.raise_for_status()
        content = bytes(response.content or b"")
        if len(content) > MAX_RESPONSE_BYTES:
            return {
                "ok": False,
                "status": "response_too_large",
                "events": [],
                "timeout_class": None,
                "canonical_table_writes": 0,
            }
        lowered = content[:4096].lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            return {
                "ok": False,
                "status": "unsafe_xml_rejected",
                "events": [],
                "timeout_class": None,
                "canonical_table_writes": 0,
            }
        root = ET.fromstring(content)
        retrieved_at = datetime.now(TPE).isoformat(timespec="seconds")
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in root.findall("./channel/item"):
            normalized = _normalize_google_rss_item(item, retrieved_at=retrieved_at)
            if not normalized:
                continue
            fingerprint = str(normalized["event_key"])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            events.append(normalized)
            if len(events) >= max_records:
                break
        return {
            "ok": True,
            "status": "ok" if events else "no_results",
            "events": events,
            "attempts": 1,
            "timeout_class": None,
            "adapter_version": CONTROLLED_NEWS_ADAPTER_VERSION,
            "source_policy_version": rights.get("policy_version"),
            "source_id": "GOOGLE_NEWS_RSS_INDEX",
            "raw_article_bodies_fetched": 0,
            "raw_body_retention_seconds": 0,
            "canonical_table_writes": 0,
        }
    except requests.Timeout:
        status, timeout_class = "timeout", "source_timeout"
    except requests.ConnectionError:
        status, timeout_class = "offline", "source_offline"
    except requests.HTTPError as exc:
        status = "rate_limited" if getattr(exc.response, "status_code", None) == 429 else "source_delayed"
        timeout_class = "source_rate_limited" if status == "rate_limited" else "source_error"
    except (requests.RequestException, ET.ParseError, UnicodeDecodeError, ValueError, TypeError):
        status, timeout_class = "source_delayed", "source_error"
    return {
        "ok": False,
        "status": status,
        "events": [],
        "attempts": 1,
        "timeout_class": timeout_class,
        "adapter_version": CONTROLLED_NEWS_ADAPTER_VERSION,
        "source_id": "GOOGLE_NEWS_RSS_INDEX",
        "raw_article_bodies_fetched": 0,
        "raw_body_retention_seconds": 0,
        "canonical_table_writes": 0,
    }


def fetch_controlled_news_metadata(
    query: str,
    *,
    max_records: int = 8,
    timespan: str = "3d",
    timeout_seconds: float = 6.0,
    max_attempts: int = 1,
    retry_backoff_seconds: float = 0.25,
    http_get: Callable[..., Any] = _DEFAULT_HTTP_GET,
    enable_experimental_fallback: bool | None = None,
) -> dict[str, Any]:
    """Fetch bounded index metadata; publisher article bodies are never fetched."""

    total_timeout = max(2.0, min(float(timeout_seconds), 10.0))
    fallback_enabled = (
        http_get is _DEFAULT_HTTP_GET
        if enable_experimental_fallback is None
        else bool(enable_experimental_fallback)
    )
    primary_timeout = total_timeout if not fallback_enabled else max(2.0, total_timeout * 0.6)
    primary = _fetch_gdelt_news_metadata(
        query,
        max_records=max_records,
        timespan=timespan,
        timeout_seconds=primary_timeout,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        http_get=http_get,
    )
    primary["source_id"] = "GDELT_DOC_INDEX"
    primary["source_attempts"] = [
        {
            "source_id": "GDELT_DOC_INDEX",
            "status": str(primary.get("status") or "unknown"),
            "timeout_class": primary.get("timeout_class"),
        }
    ]
    if primary.get("ok") and primary.get("events"):
        return primary
    if not fallback_enabled:
        return primary

    fallback_timeout = max(2.0, total_timeout - primary_timeout)
    fallback = _fetch_google_news_rss_metadata(
        query,
        max_records=max(1, min(int(max_records), 12)),
        timeout_seconds=fallback_timeout,
        http_get=http_get,
    )
    source_attempts = [
        *primary["source_attempts"],
        {
            "source_id": "GOOGLE_NEWS_RSS_INDEX",
            "status": str(fallback.get("status") or "unknown"),
            "timeout_class": fallback.get("timeout_class"),
        },
    ]
    fallback["source_attempts"] = source_attempts
    fallback["fallback_from"] = "GDELT_DOC_INDEX"
    return fallback
