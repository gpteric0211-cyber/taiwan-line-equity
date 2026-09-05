"""Build shadow-only next Taiwan trading day outlook context.

This module must not import ``app.py`` and must not call production
``synthesize_next_day_outlook``.  It exists only for Calendar-B debug
inspection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.outlook_context import (
    NextTradingDayOutlookContext,
    ensure_taipei_aware,
    make_factor_freshness,
)
from market.calendar_status import get_market_calendar_status


def _calendar_debug_item(status) -> dict[str, object]:
    data = status.to_dict()
    return {
        "market": data.get("market"),
        "is_trading_day": data.get("is_trading_day"),
        "is_open": data.get("is_open"),
        "session": data.get("session"),
        "source": data.get("source"),
        "calendar_confidence": data.get("calendar_confidence"),
        "reason": data.get("reason"),
        "override_active": data.get("override_active"),
        "override_reason": data.get("override_reason"),
        "override_expires_at": data.get("override_expires_at"),
        "previous_trading_day": data.get("previous_trading_day"),
        "next_trading_day": data.get("next_trading_day"),
        "latest_complete_trade_date": data.get("latest_complete_trade_date"),
    }


def build_shadow_outlook_context(
    stock_code: str,
    as_of_time: datetime | None = None,
) -> NextTradingDayOutlookContext:
    """Return a shadow context without touching production scoring."""

    asof = ensure_taipei_aware(as_of_time)
    twse = get_market_calendar_status("TWSE", asof)
    us = get_market_calendar_status("US", asof)
    taifex = get_market_calendar_status("TAIFEX", asof)
    target_trade_date = twse.next_trading_day or twse.date

    factor_freshness = [
        make_factor_freshness(
            factor="us",
            market="US",
            data_time=us.latest_available_time,
            data_trade_date=us.latest_complete_trade_date,
            freshness="fresh_intraday" if us.session in {"regular", "pre_market", "after_hours"} else "previous_complete",
            confidence="medium",
            usable=us.session != "unknown",
            data_quality="ok" if us.session in {"regular", "pre_market", "after_hours"} else "stale",
            reason="shadow-only US session heuristic; not connected to production quote data",
            calendar_context_note=f"US session={us.session}; calendar_source={us.source}",
        ),
        make_factor_freshness(
            factor="taifex",
            market="TAIFEX",
            data_time=taifex.latest_available_time,
            data_trade_date=taifex.next_trading_day,
            freshness="fresh_intraday" if taifex.is_open else "unavailable",
            confidence="low" if not taifex.is_open else "medium",
            usable=bool(taifex.is_open),
            data_quality="ok" if taifex.is_open else "unavailable",
            reason="shadow-only TAIFEX heuristic; not connected to official futures rows",
            calendar_context_note=f"TAIFEX session={taifex.session}; belongs_to_trade_date={taifex.next_trading_day}",
        ),
        make_factor_freshness(
            factor="chip",
            market="TWSE",
            data_time=None,
            data_trade_date=twse.latest_complete_trade_date,
            published_at=None,
            freshness="stale_but_expected" if twse.latest_complete_trade_date else "unavailable",
            confidence="low",
            usable=bool(twse.latest_complete_trade_date),
            data_quality="stale" if twse.latest_complete_trade_date else "unavailable",
            reason="shadow-only placeholder; not connected to production chip factor",
            calendar_context_note=f"TWSE session={twse.session}; chip placeholder uses latest_complete_trade_date",
        ),
        make_factor_freshness(
            factor="tech",
            market="TWSE",
            data_time=twse.latest_available_time,
            data_trade_date=twse.latest_complete_trade_date,
            freshness="previous_complete" if twse.latest_complete_trade_date else "unavailable",
            confidence="medium" if twse.latest_complete_trade_date else "low",
            usable=bool(twse.latest_complete_trade_date),
            data_quality="stale" if twse.latest_complete_trade_date else "unavailable",
            reason="shadow-only placeholder; not connected to production technical factor",
            calendar_context_note=f"TWSE session={twse.session}; technical placeholder uses latest complete trading day",
        ),
    ]

    notes = [
        "Shadow-only context; production next_day_outlook is unchanged.",
        "No production factor score, formula, DB write, or API response takeover is performed.",
        f"stock_code={str(stock_code).zfill(4)}",
    ]

    return NextTradingDayOutlookContext(
        as_of_time=asof,
        target_market="TWSE",
        target_trade_date=target_trade_date,
        calendar_status={
            "twse": twse.to_dict(),
            "us": us.to_dict(),
            "taifex": taifex.to_dict(),
        },
        calendar_debug={
            "twse": _calendar_debug_item(twse),
            "us": _calendar_debug_item(us),
            "taifex": _calendar_debug_item(taifex),
        },
        factor_freshness=factor_freshness,
        calendar_confidence=min(
            [twse.calendar_confidence, us.calendar_confidence, taifex.calendar_confidence],
            key=lambda value: {"official": 3, "cached": 2, "heuristic": 1, "low": 0}.get(str(value), 0),
        ),
        notes=notes,
    )
