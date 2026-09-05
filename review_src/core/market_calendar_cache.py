from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from core.config import (
    DATA_DIR,
    DEFAULT_TAIWAN_MARKET_HOLIDAYS,
    OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURES,
    OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURE_URLS,
    OFFICIAL_TAIWAN_MARKET_HOLIDAY_YEARS,
    TAIWAN_MARKET_HOLIDAYS,
    TWSE_HOLIDAY_SCHEDULE_URL,
)


TWSE_CALENDAR_CACHE_PATH = DATA_DIR / "market_calendar" / "twse_holiday_schedule.json"


def load_twse_calendar_cache(path: Path | None = None) -> dict[str, Any] | None:
    """Read the adapter-owned TWSE calendar cache without creating or changing files."""

    cache_path = path or TWSE_CALENDAR_CACHE_PATH
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        return None
    closures = payload.get("closure_dates")
    years = payload.get("years")
    if not isinstance(closures, list) or not isinstance(years, list):
        return None
    return payload


def _cached_years(payload: dict[str, Any] | None) -> set[int]:
    out: set[int] = set()
    for raw in (payload or {}).get("years") or []:
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def _cached_closures(payload: dict[str, Any] | None) -> set[str]:
    return {
        str(raw).strip()
        for raw in (payload or {}).get("closure_dates") or []
        if str(raw).strip()
    }


def taiwan_market_day_status(
    value: date,
    *,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Return a side-effect-free planned-session decision.

    A cached official annual schedule or the bundled complete 2026 schedule can
    verify planned weekdays.  Unexpected same-day closures still require a live
    official-session evidence gate (TWSE MIS) before any intraday provider write.
    """

    day = value
    day_text = day.isoformat()
    if day.weekday() >= 5:
        return {
            "date": day_text,
            "is_trading_day": False,
            "verified": True,
            "source": "weekend",
            "reason": "weekend",
        }

    cache = load_twse_calendar_cache(cache_path)
    cached_years = _cached_years(cache)
    cached_closures = _cached_closures(cache)
    configured_closures = set(TAIWAN_MARKET_HOLIDAYS)

    if day_text in OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURES:
        return {
            "date": day_text,
            "is_trading_day": False,
            "verified": True,
            "source": "twse_extraordinary_closure",
            "source_url": OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURE_URLS.get(day_text),
            "reason": OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURES[day_text],
        }
    if day_text in cached_closures:
        return {
            "date": day_text,
            "is_trading_day": False,
            "verified": True,
            "source": str((cache or {}).get("source") or "twse_holiday_cache"),
            "source_url": (cache or {}).get("source_url"),
            "reason": "official_schedule_closure",
        }
    if day_text in DEFAULT_TAIWAN_MARKET_HOLIDAYS:
        return {
            "date": day_text,
            "is_trading_day": False,
            "verified": True,
            "source": "bundled_twse_2026_schedule",
            "source_url": TWSE_HOLIDAY_SCHEDULE_URL,
            "reason": "official_schedule_closure",
        }
    if day_text in configured_closures:
        return {
            "date": day_text,
            "is_trading_day": False,
            "verified": True,
            "source": "configured_market_closure",
            "reason": "configured_market_closure",
        }
    if day.year in cached_years:
        return {
            "date": day_text,
            "is_trading_day": True,
            "verified": True,
            "source": str((cache or {}).get("source") or "twse_holiday_cache"),
            "source_url": (cache or {}).get("source_url"),
            "reason": "planned_open_day_not_in_official_closures",
        }
    if day.year in OFFICIAL_TAIWAN_MARKET_HOLIDAY_YEARS:
        return {
            "date": day_text,
            "is_trading_day": True,
            "verified": True,
            "source": "bundled_twse_2026_schedule",
            "source_url": TWSE_HOLIDAY_SCHEDULE_URL,
            "reason": "planned_open_day_not_in_official_closures",
        }
    return {
        "date": day_text,
        "is_trading_day": True,
        "verified": False,
        "source": "weekday_heuristic",
        "reason": "official_calendar_year_unavailable",
    }
