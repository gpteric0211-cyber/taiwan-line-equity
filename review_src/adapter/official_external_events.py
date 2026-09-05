from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from analysis.external_event_impact import (
    EXTERNAL_EVENT_ANALYSIS_VERSION,
    classify_monthly_revenue,
    classify_text_event,
    score_event_reference_quality,
)
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


TWSE_MONTHLY_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_MONTHLY_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

OFFICIAL_RSS_SOURCES = (
    {
        "source_id": "WHITE_HOUSE_NEWS",
        "publisher": "The White House",
        "url": "https://www.whitehouse.gov/news/feed/",
        "source_class": "official_foreign_government",
    },
    {
        "source_id": "EXECUTIVE_YUAN_NEWS",
        "publisher": "行政院",
        "url": "https://www.ey.gov.tw/RSS_Content.aspx?ModuleType=3",
        "source_class": "official_taiwan_government",
    },
    {
        "source_id": "EXECUTIVE_YUAN_MINISTRY_NEWS",
        "publisher": "行政院部會新聞",
        "url": "https://www.ey.gov.tw/RSS_Content.aspx?ModuleType=4",
        "source_class": "official_taiwan_government",
    },
    {
        "source_id": "MOEA_NEWS",
        "publisher": "經濟部",
        "url": "https://www.moea.gov.tw/MNS/populace/news/NewsRSSdetail.aspx?Kind=1",
        "source_class": "official_taiwan_government",
    },
)
TAIWAN_POLICY_RSS_SOURCE_IDS = (
    "EXECUTIVE_YUAN_NEWS",
    "EXECUTIVE_YUAN_MINISTRY_NEWS",
    "MOEA_NEWS",
)


def _text(value: Any, limit: int = 2000) -> str:
    clean = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return re.sub(r"\s+", " ", clean).strip()[:limit]


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _roc_date(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 7:
        return None
    try:
        return date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7])).isoformat()
    except ValueError:
        return None


def _roc_month(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 5:
        return None
    year = int(digits[:3]) + 1911
    month = int(digits[3:5])
    return f"{year:04d}-{month:02d}" if 1 <= month <= 12 else None


def _event_key(*values: Any) -> str:
    return hashlib.sha256("|".join(str(value or "") for value in values).encode("utf-8")).hexdigest()


def _content_fingerprint(title: Any, summary: Any) -> str:
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", f"{title or ''}{summary or ''}".lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _published_at(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def _normalize_revenue(row: dict[str, Any], market: str, source_url: str) -> dict[str, Any] | None:
    row = {str(key).strip(): value for key, value in row.items()}
    code = _first(row, "公司代號", "SecuritiesCompanyCode")
    event_date = _roc_date(_first(row, "出表日期"))
    period = _roc_month(_first(row, "資料年月"))
    if not re.fullmatch(r"\d{4}", code) or not event_date or not period:
        return None
    mom = _first(row, "營業收入-上月比較增減(%)")
    yoy = _first(row, "營業收入-去年同月增減(%)")
    cumulative = _first(row, "累計營業收入-前期比較增減(%)")
    impact = classify_monthly_revenue(
        month_over_month_pct=mom,
        year_over_year_pct=yoy,
        cumulative_year_over_year_pct=cumulative,
    )
    company_name = _first(row, "公司名稱", "CompanyName")
    metrics = {
        **impact["metrics"],
        "period": period,
        "current_month_revenue_thousand_twd": _first(row, "營業收入-當月營收"),
        "current_year_cumulative_revenue_thousand_twd": _first(row, "累計營業收入-當月累計營收"),
    }
    title = f"{company_name or code} {period} 月營收"
    quality_scores = score_event_reference_quality(
        source_quality="official",
        source_class="official_company_filing",
        event_type="monthly_revenue",
        has_exact_timestamp=False,
        direction=impact["direction"],
        has_structured_metrics=True,
    )
    return {
        "event_key": _event_key("monthly_revenue", market, code, period),
        "event_date": event_date,
        "published_at": None,
        "code": code,
        "source_id": "TWSE_MONTHLY_REVENUE" if market == "listed" else "TPEX_MONTHLY_REVENUE",
        "publisher": "臺灣證券交易所" if market == "listed" else "證券櫃檯買賣中心",
        "source_url": source_url,
        "source_class": "official_company_filing",
        "event_type": "monthly_revenue",
        "title": title,
        "summary_excerpt": impact["reason"],
        "direction": impact["direction"],
        "confidence": impact["confidence"],
        "time_horizon": impact["time_horizon"],
        "affected_terms": [],
        "metrics": metrics,
        "quality_status": "ok" if impact["confidence"] != "unavailable" else "unavailable",
        "source_quality": "official",
        "license_class": "taiwan_government_open_data_v1",
        "analysis_version": impact["analysis_version"],
        "content_fingerprint": _content_fingerprint(title, json.dumps(metrics, ensure_ascii=False, sort_keys=True)),
        **quality_scores,
        "can_override_main_status": False,
    }


def fetch_official_monthly_revenue(timeout: float = 25) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for market, url in (("listed", TWSE_MONTHLY_REVENUE_URL), ("otc", TPEX_MONTHLY_REVENUE_URL)):
        try:
            response = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            payload = json.loads(response.text)
            rows = payload if isinstance(payload, list) else []
            parsed = [item for item in (_normalize_revenue(row, market, url) for row in rows if isinstance(row, dict)) if item]
            items.extend(parsed)
            results.append({"source": market, "ok": True, "rows": len(parsed), "url": url})
        except Exception as exc:
            results.append({"source": market, "ok": False, "rows": 0, "url": url, "error": safe_error(exc)})
    return {
        "ok": all(row["ok"] for row in results),
        "status": "ok" if all(row["ok"] for row in results) else "partial",
        "items": items,
        "rows": len(items),
        "sources": results,
    }


def _xml_child_text(node: ElementTree.Element, local_name: str) -> str:
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1].lower() == local_name.lower():
            return "".join(child.itertext()).strip()
    return ""


def _rss_link(node: ElementTree.Element) -> str:
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        return str(child.attrib.get("href") or child.text or "").strip()
    return ""


def _parse_feed(response_content: bytes, source: dict[str, str], *, limit: int) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(response_content)
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    items: list[dict[str, Any]] = []
    for node in entries[: max(1, min(limit, 50))]:
        title = _text(_xml_child_text(node, "title"), 500)
        link = _rss_link(node) or source["url"]
        summary = _text(
            _xml_child_text(node, "description")
            or _xml_child_text(node, "summary")
            or _xml_child_text(node, "content"),
            1200,
        )
        published = _published_at(
            _xml_child_text(node, "pubDate")
            or _xml_child_text(node, "published")
            or _xml_child_text(node, "updated")
        )
        if not title or not published:
            continue
        event_date = published[:10]
        # Keep long generic article bodies from creating weak, accidental stock mappings.
        # The title and opening paragraph carry the event's actual market subject.
        impact = classify_text_event(title, summary[:350])
        event_type = "government_policy" if "government" in source["source_class"] else "official_news"
        quality_scores = score_event_reference_quality(
            source_quality="official",
            source_class=source["source_class"],
            event_type=event_type,
            has_exact_timestamp=True,
            direction=impact["direction"],
            affected_terms=impact["affected_terms"],
        )
        items.append(
            {
                "event_key": _event_key(source["source_id"], link, published, title),
                "event_date": event_date,
                "published_at": published,
                "code": None,
                "source_id": source["source_id"],
                "publisher": source["publisher"],
                "source_url": link,
                "source_class": source["source_class"],
                "event_type": event_type,
                "title": title,
                "summary_excerpt": summary,
                "direction": impact["direction"],
                "confidence": impact["confidence"],
                "time_horizon": impact["time_horizon"],
                "affected_terms": impact["affected_terms"],
                "metrics": {"evidence_terms": impact["evidence_terms"], "matched_topics": impact["matched_topics"]},
                "quality_status": "ok",
                "source_quality": "official",
                "license_class": "official_rss",
                "analysis_version": impact["analysis_version"],
                "content_fingerprint": _content_fingerprint(title, summary),
                **quality_scores,
                "can_override_main_status": False,
            }
        )
    return items


def _transport_failure(exc: Exception) -> tuple[str, str | None]:
    if isinstance(exc, requests.Timeout):
        return "timeout", "source_timeout"
    if isinstance(exc, requests.ConnectionError):
        return "offline", "source_offline"
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None and int(getattr(response, "status_code", 0) or 0) == 429:
            return "rate_limited", "source_rate_limited"
    return "source_error", None


def fetch_official_policy_feeds(
    timeout: float = 15,
    per_source_limit: int = 20,
    *,
    source_ids: Sequence[str] | None = None,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    configured = {source["source_id"]: source for source in OFFICIAL_RSS_SOURCES}
    requested = list(source_ids) if source_ids is not None else list(configured)
    if not requested or len(requested) > len(configured) or len(set(requested)) != len(requested):
        raise ValueError("official policy source_ids are empty, duplicated, or excessive")
    unknown = [source_id for source_id in requested if source_id not in configured]
    if unknown:
        raise ValueError(f"unknown official policy source_ids: {unknown}")
    selected_sources = [configured[source_id] for source_id in requested]
    getter = http_get or requests.get
    items: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for source in selected_sources:
        try:
            response = getter(
                source["url"],
                headers={**HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"},
                timeout=timeout,
            )
            response.raise_for_status()
            parsed = _parse_feed(response.content, source, limit=per_source_limit)
            items.extend(parsed)
            results.append(
                {
                    "source": source["source_id"],
                    "ok": True,
                    "status": "ok" if parsed else "no_results",
                    "timeout_class": None,
                    "rows": len(parsed),
                    "url": source["url"],
                }
            )
        except Exception as exc:
            status, timeout_class = _transport_failure(exc)
            results.append(
                {
                    "source": source["source_id"],
                    "ok": False,
                    "status": status,
                    "timeout_class": timeout_class,
                    "rows": 0,
                    "url": source["url"],
                    "error": safe_error(exc),
                }
            )
    return {
        "ok": all(row["ok"] for row in results),
        "status": "ok" if all(row["ok"] for row in results) else "partial",
        "items": items,
        "rows": len(items),
        "sources": results,
    }


def _public_https_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local") or hostname.endswith(".internal"):
        return False
    try:
        return not ipaddress.ip_address(hostname).is_private
    except ValueError:
        return True


def fetch_authorized_trump_social_feed(timeout: float = 15) -> dict[str, Any]:
    """Use only an operator-provided licensed feed; direct Truth Social scraping is forbidden."""

    url = str(os.getenv("TRUMP_SOCIAL_AUTHORIZED_FEED_URL") or "").strip()
    license_reference = str(os.getenv("TRUMP_SOCIAL_LICENSE_REFERENCE") or "").strip()
    publisher = str(os.getenv("TRUMP_SOCIAL_SOURCE_NAME") or "Authorized Trump social provider").strip()
    if not url or not license_reference:
        return {
            "ok": True,
            "status": "disabled",
            "items": [],
            "rows": 0,
            "sources": [{"source": "TRUMP_SOCIAL_AUTHORIZED", "ok": True, "rows": 0, "reason": "authorized_feed_not_configured"}],
        }
    hostname = (urlparse(url).hostname or "").lower()
    if not _public_https_url(url) or hostname.endswith("truthsocial.com"):
        return {
            "ok": False,
            "status": "unavailable",
            "items": [],
            "rows": 0,
            "sources": [{"source": "TRUMP_SOCIAL_AUTHORIZED", "ok": False, "rows": 0, "reason": "direct_or_unsafe_feed_not_allowed"}],
        }
    source = {
        "source_id": "TRUMP_SOCIAL_AUTHORIZED",
        "publisher": publisher,
        "url": url,
        "source_class": "authorized_social",
    }
    try:
        response = requests.get(url, headers={**HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"}, timeout=timeout)
        response.raise_for_status()
        rows = _parse_feed(response.content, source, limit=20)
        for row in rows:
            quality_scores = score_event_reference_quality(
                source_quality="licensed",
                source_class="authorized_social",
                event_type="authorized_social",
                has_exact_timestamp=bool(row.get("published_at")),
                direction=str(row.get("direction") or "unknown"),
                affected_terms=list(row.get("affected_terms") or []),
            )
            row.update({
                "source_quality": "licensed",
                "license_class": f"licensed:{license_reference}",
                "event_type": "authorized_social",
                **quality_scores,
            })
        return {"ok": True, "status": "ok", "items": rows, "rows": len(rows), "sources": [{"source": source["source_id"], "ok": True, "rows": len(rows), "url": url}]}
    except Exception as exc:
        return {"ok": False, "status": "unavailable", "items": [], "rows": 0, "sources": [{"source": source["source_id"], "ok": False, "rows": 0, "url": url, "error": safe_error(exc)}]}


def fetch_configured_licensed_news_feeds(timeout: float = 15) -> dict[str, Any]:
    """Fetch operator-approved RSS feeds only when a license reference is recorded."""

    raw = str(os.getenv("LICENSED_NEWS_FEEDS_JSON") or "").strip()
    if not raw:
        return {
            "ok": True,
            "status": "disabled",
            "items": [],
            "rows": 0,
            "sources": [{"source": "LICENSED_NEWS", "ok": True, "rows": 0, "reason": "licensed_feeds_not_configured"}],
        }
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "status": "unavailable", "items": [], "rows": 0, "sources": [{"source": "LICENSED_NEWS", "ok": False, "rows": 0, "reason": f"invalid_json:{safe_error(exc)}"}]}
    if not isinstance(configured, list):
        return {"ok": False, "status": "unavailable", "items": [], "rows": 0, "sources": [{"source": "LICENSED_NEWS", "ok": False, "rows": 0, "reason": "configuration_must_be_a_list"}]}
    items: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for index, item in enumerate(configured[:10], start=1):
        source = dict(item) if isinstance(item, dict) else {}
        source_id = re.sub(r"[^A-Z0-9_]+", "_", str(source.get("id") or f"LICENSED_NEWS_{index}").upper())[:80]
        publisher = str(source.get("name") or "").strip()
        url = str(source.get("url") or "").strip()
        license_reference = str(source.get("license_reference") or "").strip()
        hostname = (urlparse(url).hostname or "").lower()
        if not publisher or not license_reference or not _public_https_url(url) or hostname.endswith("truthsocial.com"):
            results.append({"source": source_id, "ok": False, "rows": 0, "reason": "publisher_https_url_and_license_reference_required"})
            continue
        feed_source = {"source_id": source_id, "publisher": publisher, "url": url, "source_class": "licensed_news"}
        try:
            response = requests.get(url, headers={**HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"}, timeout=timeout)
            response.raise_for_status()
            rows = _parse_feed(response.content, feed_source, limit=20)
            for row in rows:
                quality_scores = score_event_reference_quality(
                    source_quality="licensed",
                    source_class="licensed_news",
                    event_type="licensed_news",
                    has_exact_timestamp=bool(row.get("published_at")),
                    direction=str(row.get("direction") or "unknown"),
                    affected_terms=list(row.get("affected_terms") or []),
                )
                row.update({
                    "source_quality": "licensed",
                    "license_class": f"licensed:{license_reference}",
                    "event_type": "licensed_news",
                    **quality_scores,
                })
            items.extend(rows)
            results.append({"source": source_id, "ok": True, "rows": len(rows), "url": url})
        except Exception as exc:
            results.append({"source": source_id, "ok": False, "rows": 0, "url": url, "error": safe_error(exc)})
    return {
        "ok": all(row.get("ok") for row in results) if results else False,
        "status": "ok" if results and all(row.get("ok") for row in results) else "partial",
        "items": items,
        "rows": len(items),
        "sources": results,
    }
