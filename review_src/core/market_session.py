from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Any

from core.config import US_EASTERN, safe_error
from core.db import db
from core.market_calendar_cache import taiwan_market_day_status
from core.utils import now_tpe


def normalize_list_mode(mode: str | None) -> str:
    m = str(mode or "").strip().lower().replace("-", "_")
    if m in {"watch", "watchlist", "self", "custom"}:
        return "watchlist"
    if m in {"tw50", "taiwan50", "taiwan_50", "components", "component"}:
        return "tw50"
    return "watchlist" if not m else m


def is_taiwan_market_holiday(d: date | None = None) -> bool:
    day = d or now_tpe().date()
    status = taiwan_market_day_status(day)
    return day.weekday() < 5 and not bool(status.get("is_trading_day"))


def is_taiwan_trading_day(d: date | None = None) -> bool:
    day = d or now_tpe().date()
    return bool(taiwan_market_day_status(day).get("is_trading_day"))


def recent_market_date_for_eod() -> str:
    """Best-effort trading date for EOD sources that may not expose a date."""
    now = now_tpe()
    d = now.date()
    # Before the expected EOD refresh window, use the previous trading day.
    if (now.hour, now.minute) < (15, 30):
        d -= timedelta(days=1)
    while not is_taiwan_trading_day(d):
        d -= timedelta(days=1)
    return d.isoformat()


def recent_market_date_for_post_close() -> str:
    """Trading date whose regular session has passed the 15:00 job boundary.

    This is intentionally separate from ``recent_market_date_for_eod``.  Some
    undated EOD feeds are not expected until 15:30, while the post-close
    orchestrator freezes today's trading date at 15:00 and lets each source
    report ``source_delayed`` until it publishes.
    """

    now = now_tpe()
    d = now.date()
    if (now.hour, now.minute) < (15, 0):
        d -= timedelta(days=1)
    while not is_taiwan_trading_day(d):
        d -= timedelta(days=1)
    return d.isoformat()


def latest_verified_market_date(conn: sqlite3.Connection | None = None) -> str:
    """Return the expected latest market date."""
    if conn is None:
        with closing(db()) as local_conn:
            return latest_verified_market_date(local_conn)
    dates: list[str] = []
    for sql in [
        "SELECT MAX(date) AS d FROM eod_price WHERE source='TWSE OpenAPI'",
        "SELECT MAX(date) AS d FROM history_price WHERE source IN ('FinMind','TWSE OpenAPI')",
    ]:
        try:
            row = conn.execute(sql).fetchone()
            if row and row["d"]:
                dates.append(str(row["d"]))
        except Exception as exc:
            logging.warning("latest market date query failed: %s", safe_error(exc))
    calendar_date = recent_market_date_for_eod()
    if dates:
        return max(max(dates), calendar_date)
    return calendar_date


def tw_market_session_now() -> dict[str, Any]:
    """Taiwan regular trading session used by the frontend auto-refresh gate."""
    now = now_tpe()
    minutes = now.hour * 60 + now.minute
    is_trading_day = is_taiwan_trading_day(now.date())
    pre_open = is_trading_day and (8 * 60 + 30 <= minutes < 9 * 60)
    regular = is_trading_day and (9 * 60 <= minutes <= 13 * 60 + 30)
    closing_buffer = is_trading_day and (13 * 60 + 30 < minutes <= 14 * 60)
    if regular:
        session = "regular"
        label = "台股盤中，自動更新"
    elif pre_open:
        session = "pre_open"
        label = "台股盤前，暫不自動更新"
    elif closing_buffer:
        session = "closing_buffer"
        label = "台股收盤整理，停止自動更新"
    elif is_taiwan_market_holiday(now.date()):
        session = "holiday"
        label = "台股休市/國定假日，停止自動更新"
    else:
        session = "closed"
        label = "台股已收盤/休市，停止自動更新"
    return {
        "session": session,
        "label": label,
        "is_open": regular,
        "should_auto_refresh": regular,
        "now_taipei": now.isoformat(timespec="seconds"),
    }


def market_is_open_now() -> bool:
    # Approximate Taiwan regular-session check for auto refresh.
    now = now_tpe()
    if not is_taiwan_trading_day(now.date()):
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 <= hm <= 13 * 60 + 35


def us_market_session_now() -> dict[str, Any]:
    # Approximate US cash-market session; holidays are not inferred locally.
    now_et = datetime.now(US_EASTERN)
    weekday = now_et.weekday()
    minutes = now_et.hour * 60 + now_et.minute
    is_weekday = weekday < 5
    pre = is_weekday and (4 * 60 <= minutes < 9 * 60 + 30)
    regular = is_weekday and (9 * 60 + 30 <= minutes <= 16 * 60)
    after = is_weekday and (16 * 60 < minutes <= 20 * 60)
    if regular:
        session = "regular"
        label = "美股正常交易時段"
    elif pre:
        session = "pre_market"
        label = "美股盤前時段"
    elif after:
        session = "after_hours"
        label = "美股盤後時段"
    else:
        session = "closed"
        label = "美股休市/非交易時段"
    return {
        "session": session,
        "label": label,
        "is_open": regular,
        "is_extended": pre or after,
        "now_et": now_et.isoformat(timespec="seconds"),
        "now_taipei": now_tpe().isoformat(timespec="seconds"),
        "holiday_adjusted": False,
    }
