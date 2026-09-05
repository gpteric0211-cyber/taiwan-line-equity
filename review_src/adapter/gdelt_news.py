from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from analysis.external_event_impact import classify_text_event
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


GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_NEWS_RADAR_VERSION = "gdelt-news-radar-v1"
DEFAULT_GDELT_QUERY = (
    "(Taiwan OR TSMC OR Foxconn OR MediaTek OR UMC OR Quanta OR Pegatron OR Wistron) "
    "(semiconductor OR chip OR server OR tariff OR trade OR sanction OR export)"
)
TPE = ZoneInfo("Asia/Taipei")

COMPANY_ALIASES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("tsmc", "taiwan semiconductor manufacturing"), ("台積電", "2330")),
    (("foxconn", "hon hai"), ("鴻海", "2317")),
    (("mediatek",), ("聯發科", "2454")),
    (("umc", "united microelectronics"), ("聯電", "2303")),
    (("quanta",), ("廣達", "2382")),
    (("pegatron",), ("和碩", "4938")),
    (("wistron",), ("緯創", "3231")),
)


def _text(value: Any, limit: int = 500) -> str:
    clean = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return re.sub(r"\s+", " ", clean).strip()[:limit]


def _fingerprint(*values: Any) -> str:
    normalized = re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        "",
        "".join(str(value or "") for value in values).lower(),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
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


def _public_article_url(value: Any) -> str | None:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return text


def _normalize_article(article: dict[str, Any], query: str) -> dict[str, Any] | None:
    title = _text(article.get("title"), 500)
    url = _public_article_url(article.get("url"))
    published_at = _timestamp(article.get("seendate"))
    if not title or not url or not published_at:
        return None
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    publisher = _text(article.get("domain") or hostname, 120) or hostname
    impact = classify_text_event(title)
    lowered = title.lower()
    aliases: list[str] = []
    for triggers, terms in COMPANY_ALIASES:
        if any(trigger in lowered for trigger in triggers):
            aliases.extend(terms)
    affected_terms = list(dict.fromkeys([*impact["affected_terms"], *aliases]))
    reference_value = 0.35 if affected_terms else 0.2
    return {
        "event_key": _fingerprint("GDELT_DOC_INDEX", url, published_at, title),
        "event_date": published_at[:10],
        "published_at": published_at,
        "source_id": "GDELT_DOC_INDEX",
        "publisher": publisher,
        "source_url": url,
        "title": title,
        "affected_terms": affected_terms,
        "metrics": {
            "language": _text(article.get("language"), 40),
            "source_country": _text(article.get("sourcecountry"), 80),
            "index_provider": "GDELT",
            "query": query,
            "potential_direction": impact["direction"],
        },
        "quality_status": "ok",
        "source_quality": "supplemental",
        "verification_status": "unverified",
        "license_class": "gdelt_open_metadata_citation_required",
        "analysis_version": GDELT_NEWS_RADAR_VERSION,
        "content_fingerprint": _fingerprint(title, url),
        "reliability_score": 0.45,
        "reference_value_score": reference_value,
        "can_override_main_status": False,
    }


def fetch_gdelt_news_radar(
    timeout: float = 25,
    max_records: int = 50,
    *,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    """Fetch title/URL metadata only; publisher content is not copied or trusted as fact."""

    if str(os.getenv("GDELT_NEWS_ENABLED", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": True, "status": "disabled", "items": [], "rows": 0, "sources": []}
    query = str(os.getenv("GDELT_NEWS_QUERY") or DEFAULT_GDELT_QUERY).strip()[:1000]
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max(1, min(int(max_records), 75)),
        "timespan": "3d",
        "sort": "datedesc",
    }
    attempts = max(1, min(int(max_attempts), 3))
    request_timeout = max(float(timeout), 10.0)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                GDELT_DOC_API_URL,
                params=params,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=(request_timeout, request_timeout),
            )
            if response.status_code == 429:
                return {
                    "ok": False,
                    "status": "source_delayed",
                    "items": [],
                    "rows": 0,
                    "sources": [{
                        "source": "GDELT_DOC_INDEX",
                        "ok": False,
                        "rows": 0,
                        "reason": "rate_limited",
                        "attempts": attempt,
                    }],
                }
            response.raise_for_status()
            payload = json.loads(response.text)
            articles = payload.get("articles") if isinstance(payload, dict) else []
            rows = [
                item
                for item in (
                    _normalize_article(article, query)
                    for article in articles or []
                    if isinstance(article, dict)
                )
                if item
            ]
            return {
                "ok": True,
                "status": "ok",
                "items": rows,
                "rows": len(rows),
                "sources": [{
                    "source": "GDELT_DOC_INDEX",
                    "ok": True,
                    "rows": len(rows),
                    "url": GDELT_DOC_API_URL,
                    "attempts": attempt,
                }],
            }
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(max(float(retry_backoff_seconds), 0.0) * attempt)
                continue
            break
        except Exception as exc:
            last_error = exc
            break
    return {
        "ok": False,
        "status": "source_delayed",
        "items": [],
        "rows": 0,
        "sources": [{
            "source": "GDELT_DOC_INDEX",
            "ok": False,
            "rows": 0,
            "attempts": attempts,
            "error": safe_error(last_error or RuntimeError("GDELT request failed")),
        }],
    }
