from __future__ import annotations

from typing import Any
from contextlib import closing
from core.news_radar_schema import ensure_news_radar_schema
from core.external_event_schema import ensure_external_event_schema

from adapter.official_external_events import (
    fetch_authorized_trump_social_feed,
    fetch_configured_licensed_news_feeds,
    fetch_official_monthly_revenue,
    fetch_official_policy_feeds,
)
from adapter.gdelt_news import fetch_gdelt_news_radar
from core.db import db
from repository.external_event_repository import (
    prune_external_market_events,
    upsert_external_market_events,
)
from repository.news_radar_repository import prune_news_radar_events, upsert_news_radar_events


def refresh_external_market_events(*, dry_run: bool = False) -> dict[str, Any]:
    """Fetch outside the request path, then atomically persist auditable events."""

    monthly_revenue = fetch_official_monthly_revenue()
    official_policy = fetch_official_policy_feeds()
    authorized_trump_social = fetch_authorized_trump_social_feed()
    licensed_news = fetch_configured_licensed_news_feeds()
    gdelt_news_radar = fetch_gdelt_news_radar()
    required = (monthly_revenue, official_policy)
    rows = [
        row
        for result in (monthly_revenue, official_policy, authorized_trump_social, licensed_news)
        for row in list(result.get("items") or [])
    ]
    written = 0
    pruned = 0
    radar_rows = list(gdelt_news_radar.get("items") or [])
    radar_written = 0
    radar_pruned = 0
    if not dry_run:
        with closing(db()) as conn, conn:
            ensure_external_event_schema(conn)
            ensure_news_radar_schema(conn)
            if rows:
                written = upsert_external_market_events(conn, rows)
            if radar_rows:
                radar_written = upsert_news_radar_events(conn, radar_rows)
            pruned = prune_external_market_events(conn)
            radar_pruned = prune_news_radar_events(conn)
            conn.execute("PRAGMA optimize")
            conn.commit()
    required_ok = all(bool(result.get("ok")) for result in required)
    return {
        "ok": required_ok,
        "status": "dry_run" if dry_run and required_ok else "ok" if required_ok else "partial",
        "writes_db": bool(not dry_run and (written or radar_written or pruned or radar_pruned)),
        "rows_fetched": len(rows) + len(radar_rows),
        "verified_or_licensed_rows_fetched": len(rows),
        "unverified_radar_rows_fetched": len(radar_rows),
        "rows_written": written,
        "rows_pruned": pruned,
        "monthly_revenue": {key: monthly_revenue.get(key) for key in ("ok", "status", "rows", "sources")},
        "official_policy": {key: official_policy.get(key) for key in ("ok", "status", "rows", "sources")},
        "authorized_trump_social": {
            key: authorized_trump_social.get(key) for key in ("ok", "status", "rows", "sources")
        },
        "licensed_news": {key: licensed_news.get(key) for key in ("ok", "status", "rows", "sources")},
        "gdelt_news_radar": {
            **{key: gdelt_news_radar.get(key) for key in ("ok", "status", "rows", "sources")},
            "rows_written": radar_written,
            "rows_pruned": radar_pruned,
            "can_override_main_status": False,
        },
        "truth_social_direct_scraping": {
            "enabled": False,
            "reason": "provider_terms_prohibit_unauthorized_automated_retrieval",
            "authorized_feed_required": True,
        },
    }
