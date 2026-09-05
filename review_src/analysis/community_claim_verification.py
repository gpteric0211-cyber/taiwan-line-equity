from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any


COMMUNITY_CLAIM_VERIFICATION_VERSION = "community-claim-verification-v1"

OPINION_TERMS = ("會漲", "會跌", "目標價", "必買", "必賣", "噴出", "崩盤", "抄底", "明牌")
FACT_TERMS = ("營收", "法說", "財測", "公告", "政策", "關稅", "訂單", "處分", "裁罰", "停工")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _percent_claims(text: str) -> list[float]:
    values: list[float] = []
    for raw in re.findall(r"(?<!\d)([-+]?\d+(?:\.\d+)?)\s*%", text):
        value = _number(raw)
        if value is not None:
            values.append(value)
    return values[:8]


def _fact_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z]{3,}", text.lower()))
    return {token for token in tokens if token not in {"今天", "昨日", "公司", "股票", "表示", "目前"}}


def _price_reaction(quote: dict[str, Any]) -> dict[str, Any]:
    price = _number(quote.get("price"))
    previous_close = _number(quote.get("previous_close"))
    high = _number(quote.get("high"))
    low = _number(quote.get("low"))
    if price is None or previous_close is None or previous_close <= 0:
        return {"available": False, "status": "unavailable"}
    change_pct = (price - previous_close) / previous_close * 100
    range_position = (price - low) / (high - low) * 100 if high is not None and low is not None and high > low else None
    return {
        "available": True,
        "status": "ok",
        "as_of": quote.get("as_of"),
        "price": price,
        "previous_close": previous_close,
        "open": _number(quote.get("open")),
        "high": high,
        "low": low,
        "change_pct": round(change_pct, 4),
        "range_position_pct": round(range_position, 2) if range_position is not None else None,
        "reaction": "strong_positive" if change_pct >= 3 else "strong_negative" if change_pct <= -3 else "not_extreme",
        "causality_proven": False,
    }


def verify_community_claims(
    *,
    posts: list[dict[str, Any]],
    official_events: list[dict[str, Any]],
    quote: dict[str, Any] | None = None,
    reference_date: str,
) -> dict[str, Any]:
    """Verify each same-day community claim without treating repetition as proof."""

    official = [
        dict(event)
        for event in official_events
        if str(event.get("source_quality") or "").lower() == "official"
        and str(event.get("quality_status") or "ok").lower() == "ok"
    ]
    normalized_posts = [
        dict(post)
        for post in posts[:100]
        if str(post.get("published_at") or "")[:10] == reference_date
    ]
    fingerprints: dict[str, int] = {}
    for post in normalized_posts:
        text = re.sub(r"\s+", " ", f"{post.get('title') or ''} {post.get('content') or ''}").strip()
        fingerprint = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())[:500]
        fingerprints[fingerprint] = fingerprints.get(fingerprint, 0) + 1

    results: list[dict[str, Any]] = []
    for post in normalized_posts:
        text = re.sub(r"\s+", " ", f"{post.get('title') or ''} {post.get('content') or ''}").strip()
        fingerprint = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())[:500]
        opinion = any(term in text for term in OPINION_TERMS)
        factual = any(term in text for term in FACT_TERMS)
        claims = _percent_claims(text)
        tokens = _fact_tokens(text)
        matches: list[dict[str, Any]] = []
        metric_verified = False
        metric_contradicted = False
        post_time = _timestamp(post.get("published_at"))
        for event in official:
            event_text = f"{event.get('title') or event.get('subject') or ''} {event.get('summary_excerpt') or event.get('explanation_excerpt') or ''}"
            overlap = sorted(tokens & _fact_tokens(event_text))
            metrics = dict(event.get("metrics") or {})
            official_percents = [
                value for value in (
                    _number(metrics.get("month_over_month_pct")),
                    _number(metrics.get("year_over_year_pct")),
                    _number(metrics.get("cumulative_year_over_year_pct")),
                ) if value is not None
            ]
            if claims and official_percents and "營收" in text:
                metric_verified = any(abs(claim - value) <= 0.2 for claim in claims for value in official_percents)
                metric_contradicted = not metric_verified
            if len(overlap) >= 2 or metric_verified or metric_contradicted:
                official_time = _timestamp(event.get("published_at"))
                matches.append(
                    {
                        "event_date": event.get("event_date") or event.get("disclosed_date"),
                        "publisher": event.get("publisher") or "官方揭露",
                        "title": event.get("title") or event.get("subject"),
                        "url": event.get("url") or event.get("source_url"),
                        "matched_terms": overlap[:8],
                        "metric_verified": metric_verified,
                        "metric_contradicted": metric_contradicted,
                        "official_was_available_before_post": bool(
                            official_time and post_time and official_time <= post_time
                        ),
                    }
                )
        if metric_contradicted:
            status, reliability, reference_value = "contradicted", 0.05, 0.1
        elif metric_verified:
            status, reliability, reference_value = "verified", 0.95, 0.85
        elif matches:
            status, reliability, reference_value = "partially_verified", 0.75, 0.55
        elif factual:
            status, reliability, reference_value = "unverified", 0.2, 0.15
        else:
            status, reliability, reference_value = "opinion", 0.1, 0.05
        if opinion and status in {"verified", "partially_verified"}:
            status = "facts_verified_prediction_unverified"
            reference_value = min(reference_value, 0.5)
        results.append(
            {
                "post_id": post.get("post_id"),
                "published_at": post.get("published_at"),
                "url": post.get("url"),
                "claim_excerpt": text[:500],
                "status": status,
                "reliability_score": reliability,
                "reference_value_score": reference_value,
                "official_matches": matches[:3],
                "community_repeat_count": fingerprints.get(fingerprint, 1),
                "community_repetition_counts_as_independent_confirmation": False,
                "can_override_main_status": False,
            }
        )
    return {
        "status": "ok" if results else "unavailable",
        "reference_date": reference_date,
        "post_count": len(results),
        "results": results,
        "price_reaction": _price_reaction(dict(quote or {})),
        "quality_contract": {
            "same_day_posts_only": True,
            "official_primary_source_required_for_fact_verification": True,
            "community_repetition_is_not_independent_proof": True,
            "predictions_cannot_be_verified_as_facts": True,
            "quote_cannot_prove_news_causality": True,
        },
        "version": COMMUNITY_CLAIM_VERIFICATION_VERSION,
        "can_override_main_status": False,
    }
