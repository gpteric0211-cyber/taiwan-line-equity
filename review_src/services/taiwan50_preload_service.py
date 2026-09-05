from __future__ import annotations

from contextlib import closing
from typing import Any

from adapter.twse import refresh_twse_stock_day_codes
from core.db import db
from core.market_session import recent_market_date_for_eod
from repository.history_repository import history_date_coverage, recent_market_reference_dates


def refresh_incomplete_taiwan50_history(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill official monthly rows only for components missing completed dates."""

    target_date = recent_market_date_for_eod()
    with closing(db()) as conn:
        reference_dates = recent_market_reference_dates(
            conn,
            required_days=120,
            latest_completed_date=target_date,
        )
        incomplete = [
            item
            for item in items
            if not history_date_coverage(
                conn,
                str(item.get("code") or "").zfill(4),
                required_days=120,
                reference_dates=reference_dates,
            ).get("ready")
        ]
    refresh = refresh_twse_stock_day_codes(incomplete, target_date=target_date) if incomplete else {
        "target_date": target_date,
        "total": 0,
        "updated": 0,
        "failed": [],
    }
    with closing(db()) as conn:
        reference_dates = recent_market_reference_dates(
            conn,
            required_days=120,
            latest_completed_date=target_date,
        )
        remaining = [
            str(item.get("code") or "").zfill(4)
            for item in items
            if not history_date_coverage(
                conn,
                str(item.get("code") or "").zfill(4),
                required_days=120,
                reference_dates=reference_dates,
            ).get("ready")
        ]
    return {
        "target_date": target_date,
        "checked": len(items),
        "initial_incomplete": len(incomplete),
        "refreshed": int(refresh.get("updated") or 0),
        "refresh_failures": refresh.get("failed") or [],
        "remaining_incomplete": remaining,
        "ready": not remaining,
    }
