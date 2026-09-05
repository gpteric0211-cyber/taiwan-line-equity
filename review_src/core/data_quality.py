"""Shared data-quality primitives for the Taiwan stock analysis system.

This module is currently only a data-quality standards skeleton.

Phase 1 intentionally does not connect these helpers to any existing
analysis logic. Importing this module must not change API responses,
scoring output, database contents, background jobs, or external data
fetching behavior.

Later phases may gradually move scattered data-quality decisions here.
For now, keep this file dependency-light and side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
import json
import math
import re
from typing import Any

from core.date_utils import TAIPEI, normalize_date


class DataQualityStatus(str, Enum):
    """Canonical data availability and freshness states."""

    OK = "ok"
    STALE = "stale"
    SOURCE_DELAYED = "source_delayed"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"


@dataclass(frozen=True)
class DataQualityInfo:
    """Metadata describing whether a value can be trusted for analysis."""

    status: DataQualityStatus
    source: str | None = None
    source_date: str | None = None
    as_of_date: str | None = None
    confidence: str | None = None
    reason: str | None = None
    is_estimated: bool = False


EXTERNAL_EVENT_MIN_RELIABILITY_SCORE = 0.70
EXTERNAL_EVENT_MIN_REFERENCE_VALUE_SCORE = 0.45
PRICE_VOLUME_MIN_OFFICIAL_COVERAGE_RATIO = 0.95


def assess_news_radar_quality(row: dict[str, Any]) -> dict[str, Any]:
    """Gate supplemental news metadata for display as an unverified lead only."""

    reasons: list[str] = []
    if str(row.get("source_quality") or "").strip().lower() != "supplemental":
        reasons.append("source_is_not_supplemental_index")
    if normalize_quality_status(row.get("quality_status")) != DataQualityStatus.OK:
        reasons.append("radar_quality_status_not_ok")
    if str(row.get("verification_status") or "").strip().lower() != "unverified":
        reasons.append("verification_status_must_be_unverified")
    if not str(row.get("source_url") or "").startswith("https://"):
        reasons.append("public_https_article_url_required")
    if not str(row.get("published_at") or "").strip():
        reasons.append("published_timestamp_required")
    return {
        "ready_for_radar": not reasons,
        "ready_for_referee": False,
        "status": DataQualityStatus.OK.value if not reasons else DataQualityStatus.UNAVAILABLE.value,
        "reasons": reasons,
        "reason": "ok" if not reasons else ",".join(reasons),
        "can_override_main_status": False,
    }


def assess_external_event_quality(row: dict[str, Any]) -> dict[str, Any]:
    """Central gate for official or explicitly licensed external events."""

    reasons: list[str] = []
    source_quality = str(row.get("source_quality") or "").strip().lower()
    quality_status = normalize_quality_status(row.get("quality_status"))
    try:
        reliability = float(row.get("reliability_score"))
    except (TypeError, ValueError):
        reliability = 0.0
    try:
        reference_value = float(row.get("reference_value_score"))
    except (TypeError, ValueError):
        reference_value = 0.0
    if source_quality not in {"official", "licensed"}:
        reasons.append("source_is_not_official_or_licensed")
    if quality_status != DataQualityStatus.OK:
        reasons.append("event_quality_status_not_ok")
    if reliability < EXTERNAL_EVENT_MIN_RELIABILITY_SCORE:
        reasons.append("reliability_below_threshold")
    if reference_value < EXTERNAL_EVENT_MIN_REFERENCE_VALUE_SCORE:
        reasons.append("reference_value_below_threshold")
    return {
        "ready": not reasons,
        "status": DataQualityStatus.OK.value if not reasons else DataQualityStatus.UNAVAILABLE.value,
        "source_quality": source_quality,
        "reliability_score": reliability,
        "reference_value_score": reference_value,
        "minimum_reliability_score": EXTERNAL_EVENT_MIN_RELIABILITY_SCORE,
        "minimum_reference_value_score": EXTERNAL_EVENT_MIN_REFERENCE_VALUE_SCORE,
        "reasons": reasons,
        "reason": "ok" if not reasons else ",".join(reasons),
    }


def normalize_quality_status(value: Any) -> DataQualityStatus:
    """Normalize a raw status value into a canonical data-quality status."""

    if isinstance(value, DataQualityStatus):
        return value

    text = str(value or "").strip().lower()
    aliases = {
        "fresh": DataQualityStatus.OK,
        "valid": DataQualityStatus.OK,
        "available": DataQualityStatus.OK,
        "delay": DataQualityStatus.SOURCE_DELAYED,
        "delayed": DataQualityStatus.SOURCE_DELAYED,
        "not_available": DataQualityStatus.UNAVAILABLE,
        "none": DataQualityStatus.UNAVAILABLE,
        "na": DataQualityStatus.UNAVAILABLE,
        "null": DataQualityStatus.UNAVAILABLE,
    }

    if text in aliases:
        return aliases[text]

    try:
        return DataQualityStatus(text)
    except ValueError:
        return DataQualityStatus.MISSING


def is_available(info_or_status: DataQualityInfo | DataQualityStatus | str | None) -> bool:
    """Return True when data exists in some usable or explainable form."""

    status = (
        info_or_status.status
        if isinstance(info_or_status, DataQualityInfo)
        else normalize_quality_status(info_or_status)
    )
    return status not in {DataQualityStatus.UNAVAILABLE, DataQualityStatus.MISSING}


def is_estimated(info_or_status: DataQualityInfo | DataQualityStatus | str | None) -> bool:
    """Return True when a value must be treated as explicitly estimated."""

    if isinstance(info_or_status, DataQualityInfo):
        return bool(info_or_status.is_estimated) or info_or_status.status == DataQualityStatus.ESTIMATED
    return normalize_quality_status(info_or_status) == DataQualityStatus.ESTIMATED


def is_quality_usable_for_scoring(
    info_or_status: DataQualityInfo | DataQualityStatus | str | None,
    *,
    allow_estimated: bool = False,
    allow_stale: bool = False,
) -> bool:
    """Return whether a value may contribute to scoring.

    This is a generic quality gate only. It does not encode any stock scoring
    formula, weighting, or domain-specific finance rule.
    """

    status = (
        info_or_status.status
        if isinstance(info_or_status, DataQualityInfo)
        else normalize_quality_status(info_or_status)
    )

    if status == DataQualityStatus.OK:
        return True
    if status == DataQualityStatus.ESTIMATED:
        return bool(allow_estimated)
    if status == DataQualityStatus.STALE:
        return bool(allow_stale)
    return False


def assess_component_freshness(
    component_date: Any,
    reference_date: Any,
    *,
    max_lag_days: int = 3,
) -> dict[str, Any]:
    """Fail closed when a dated component is missing or lags the K-line date.

    ``reference_date`` is the latest persisted K-line date for the same stock.
    The calendar-day tolerance intentionally permits a Friday component to be
    used with a Monday K line, while older chip data is explicitly excluded
    from current-day interpretation.
    """

    source_date = normalize_date(component_date)
    as_of_date = normalize_date(reference_date)
    allowed_lag = max(int(max_lag_days), 0)
    base = {
        "source_date": source_date,
        "as_of_date": as_of_date,
        "lag_days": None,
        "max_lag_days": allowed_lag,
    }
    if not source_date:
        return {
            **base,
            "ready": False,
            "status": DataQualityStatus.MISSING.value,
            "reason": "component_date_missing",
        }
    if not as_of_date:
        return {
            **base,
            "ready": False,
            "status": DataQualityStatus.MISSING.value,
            "reason": "reference_date_missing",
        }

    try:
        source_day = date.fromisoformat(source_date)
    except ValueError:
        return {
            **base,
            "ready": False,
            "status": DataQualityStatus.MISSING.value,
            "reason": "component_date_invalid",
        }
    try:
        reference_day = date.fromisoformat(as_of_date)
    except ValueError:
        return {
            **base,
            "ready": False,
            "status": DataQualityStatus.MISSING.value,
            "reason": "reference_date_invalid",
        }

    lag_days = (reference_day - source_day).days
    if lag_days < 0:
        return {
            **base,
            "lag_days": lag_days,
            "ready": False,
            "status": DataQualityStatus.SOURCE_DELAYED.value,
            "reason": "component_date_after_reference",
        }
    if lag_days > allowed_lag:
        return {
            **base,
            "lag_days": lag_days,
            "ready": False,
            "status": DataQualityStatus.SOURCE_DELAYED.value,
            "reason": "component_source_delayed",
        }
    return {
        **base,
        "lag_days": lag_days,
        "ready": True,
        "status": DataQualityStatus.OK.value,
        "reason": "ok",
    }


def assess_recent_trading_date_coverage(
    actual_dates: list[Any],
    reference_dates: list[Any],
    *,
    required_days: int = 30,
) -> dict[str, Any]:
    """Check that a price series contains every recent market trading date.

    ``reference_dates`` must come from the persisted market-date inventory, not
    from calendar weekdays. A suspended or missing stock therefore fails closed
    instead of receiving an RSI calculated across hidden gaps.
    """

    required = max(int(required_days), 1)
    reference = sorted(
        {d for d in (normalize_date(value) for value in reference_dates) if d},
        reverse=True,
    )[:required]
    actual = {d for d in (normalize_date(value) for value in actual_dates) if d}
    missing = [trade_date for trade_date in reference if trade_date not in actual]
    observed = len(reference) - len(missing)
    ratio = observed / len(reference) if reference else 0.0
    ready = len(reference) == required and not missing
    return {
        "ready": ready,
        "status": DataQualityStatus.OK.value if ready else DataQualityStatus.MISSING.value,
        "required_days": required,
        "reference_days": len(reference),
        "observed_days": observed,
        "coverage_ratio": round(ratio, 6),
        "latest_reference_date": reference[0] if reference else None,
        "earliest_reference_date": reference[-1] if reference else None,
        "missing_dates": missing,
        "reason": "ok" if ready else ("market_reference_dates_insufficient" if len(reference) < required else "recent_trading_dates_missing"),
    }


def verified_no_trade_evidence(
    conn: Any,
    code: Any,
    trade_date: Any,
) -> dict[str, Any] | None:
    """Read exact-whitelisted exchange evidence that this stock did not trade.

    This is a read-only central gate used before any daily bar is accepted.
    It prevents Yahoo/FinMind (or a conflicting later import) from recreating a
    K line on a date where TWSE/TPEx explicitly reported no price and no volume.
    """

    normalized_code = str(code or "").strip().zfill(4)
    normalized_date = normalize_date(trade_date)
    if not normalized_date or not (len(normalized_code) == 4 and normalized_code.isdigit()):
        return None
    try:
        date.fromisoformat(normalized_date)
    except ValueError:
        return None
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_no_trade_dates'"
    ).fetchone()
    if not table:
        return None
    cursor = conn.execute(
        """
        SELECT trade_date,code,market,reason,source,source_url,
               source_quality,evidence_json,verified_at
        FROM stock_no_trade_dates
        WHERE code=? AND trade_date=?
          AND LOWER(COALESCE(source_quality,'')) IN ('official','ok','high')
          AND (
              (
                  LOWER(COALESCE(market,''))='otc'
                  AND source IN ('TPEX TRADING_STOCK','TPEX DAILY_QUOTES')
                  AND source_url LIKE 'https://www.tpex.org.tw/%'
              )
              OR (
                  LOWER(COALESCE(market,''))='listed'
                  AND source='TWSE STOCK_DAY'
                  AND (
                      source_url LIKE 'https://www.twse.com.tw/%'
                      OR source_url LIKE 'https://openapi.twse.com.tw/%'
                  )
              )
          )
        LIMIT 1
        """,
        (normalized_code, normalized_date),
    )
    row = cursor.fetchone()
    if not row:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    return dict(zip((description[0] for description in cursor.description), row))


def classify_official_trading_state(
    *,
    history: dict[str, Any] | None,
    no_trade_evidence: dict[str, Any] | None,
    stock: dict[str, Any] | None = None,
    trading_restriction_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a user-safe trading state without fabricating an OHLCV bar.

    A missing daily bar is not automatically missing data.  Exact-date official
    no-trade evidence is classified separately from a source failure so clients
    can state zero volume or an exchange halt explicitly.
    """

    listing_status = "unknown"
    if stock and stock.get("is_active") in {0, 1, False, True}:
        listing_status = "active" if bool(stock.get("is_active")) else "inactive"
    if history:
        try:
            volume = float(history.get("volume"))
        except (TypeError, ValueError):
            volume = None
        return {
            "status": "traded",
            "label": "正常成交",
            "trade_date": normalize_date(history.get("date") or history.get("trade_date")),
            "volume_shares": volume,
            "has_daily_ohlcv": True,
            "listing_status": listing_status,
            "decision_ready": bool(volume is not None and volume > 0),
            "reason": "官方日線有成交價量",
        }

    if no_trade_evidence:
        evidence: dict[str, Any] = {}
        raw_evidence = no_trade_evidence.get("evidence_json")
        if isinstance(raw_evidence, dict):
            evidence = dict(raw_evidence)
        elif raw_evidence:
            try:
                parsed = json.loads(str(raw_evidence))
                evidence = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence = {}
        reason_code = str(no_trade_evidence.get("reason") or "")
        restrictions = list((trading_restriction_context or {}).get("items") or [])
        embedded_restriction = evidence.get("restriction")
        halted = bool(
            (isinstance(embedded_restriction, dict)
             and embedded_restriction.get("restriction_type") == "trading_halt")
            or "trading_halt" in reason_code.lower()
            or any(item.get("restriction_type") == "trading_halt" for item in restrictions)
        )
        residual_activity = reason_code in {
            "official_no_regular_lot_ohlcv_with_residual_activity",
            "official_no_ohlcv_with_residual_activity",
        }
        try:
            if evidence.get("reported_volume_shares") is not None:
                reported_volume_shares = float(evidence.get("reported_volume_shares"))
            else:
                reported_volume_shares = float(
                    evidence.get("reported_regular_lot_volume") or 0
                ) * 1000
        except (TypeError, ValueError):
            reported_volume_shares = 0
        if halted:
            status = "trading_halt"
            label = "官方暫停交易"
            reason = "交易所公告當日暫停交易，官方成交量為 0"
        elif reason_code == "official_no_ohlcv_with_residual_activity":
            status = "no_ohlcv_residual_activity"
            label = "有少量成交但無日 K"
            reason = "官方月報有成交量、金額與筆數，但未形成開高低收；不拿來計算 RSI"
        elif residual_activity:
            status = "no_regular_lot_ohlcv"
            label = "無整股日 K"
            reason = "官方整股月報為 0 張且無 OHLC；另保留小額活動欄位，不拿來計算 RSI"
        else:
            status = "no_trade"
            label = "當日無成交量"
            reason = "官方日報無成交價量，成交量為 0"
        return {
            "status": status,
            "label": label,
            "trade_date": normalize_date(no_trade_evidence.get("trade_date")),
            "volume_shares": reported_volume_shares if residual_activity else 0,
            "regular_lot_volume_lots": evidence.get("reported_regular_lot_volume", 0),
            "reported_amount_thousands": evidence.get("reported_amount_thousands"),
            "reported_transaction_count": evidence.get("reported_transaction_count"),
            "has_daily_ohlcv": False,
            "listing_status": listing_status,
            "decision_ready": False,
            "reason": reason,
            "source": no_trade_evidence.get("source"),
            "source_quality": no_trade_evidence.get("source_quality"),
        }

    if listing_status == "inactive":
        return {
            "status": "inactive_official_universe",
            "label": "不在官方有效交易清單",
            "trade_date": None,
            "volume_shares": None,
            "has_daily_ohlcv": False,
            "listing_status": listing_status,
            "decision_ready": False,
            "reason": "官方主檔已標為非有效；仍需用下市／終止交易公告確認法律狀態",
        }
    return {
        "status": "unavailable",
        "label": "交易狀態待查",
        "trade_date": None,
        "volume_shares": None,
        "has_daily_ohlcv": False,
        "listing_status": listing_status,
        "decision_ready": False,
        "reason": "未找到同日官方日線或可驗證的零成交／暫停交易證據",
    }


def derive_official_monthly_no_bar_evidence(
    *,
    code: str,
    market: str,
    month: str,
    official_rows: list[dict[str, Any]],
    explicit_no_bar_rows: list[dict[str, Any]],
    market_reference_dates: list[str],
    first_seen_date: str | None,
    source: str,
    source_url: str,
) -> list[dict[str, Any]]:
    """Classify absent dates only after a complete official monthly report.

    Callers must invoke this helper only for a successful stock-specific
    exchange report.  It never treats a failed/blocked request as zero volume.
    """

    normalized_code = str(code or "").strip().zfill(4)
    normalized_market = str(market or "").strip().lower()
    month_prefix = str(month or "")[:7]
    first_seen = normalize_date(first_seen_date)
    if (
        not re.fullmatch(r"\d{4}", normalized_code)
        or normalized_market not in {"listed", "otc"}
        or not re.fullmatch(r"\d{4}-\d{2}", month_prefix)
    ):
        return []
    observed = {
        normalize_date(row.get("date") or row.get("trade_date"))
        for row in [*official_rows, *explicit_no_bar_rows]
    }
    observed.discard(None)
    derived: list[dict[str, Any]] = []
    for raw_date in market_reference_dates:
        trade_date = normalize_date(raw_date)
        if (
            not trade_date
            or trade_date[:7] != month_prefix
            or trade_date in observed
            or (first_seen and trade_date < first_seen)
        ):
            continue
        derived.append({
            "trade_date": trade_date,
            "code": normalized_code,
            "market": normalized_market,
            "reason": "official_complete_monthly_report_no_daily_bar",
            "source": source,
            "source_url": source_url,
            "source_quality": "official",
            "evidence": {
                "report_month": month_prefix,
                "official_report_complete": True,
                "market_reference_date": True,
                "reported_volume": 0,
                "classification": "no_daily_bar_in_complete_official_monthly_report",
            },
        })
    return derived


def has_verified_no_trade_evidence(conn: Any, code: Any, trade_date: Any) -> bool:
    """Return whether exact-whitelisted official no-trade evidence exists."""

    return verified_no_trade_evidence(conn, code, trade_date) is not None


def assess_daily_ohlcv(row: dict[str, Any]) -> dict[str, Any]:
    """Validate one completed daily bar before it enters technical analysis.

    A completed Taiwan-stock daily bar must have an explicit date, positive
    OHLC prices, a positive traded volume, and internally consistent high/low
    bounds.  Rows that fail this check are not safe inputs for RSI, MACD, KD,
    ATR, moving averages, support/resistance, or OBV.
    """

    reasons: list[str] = []
    trade_date = normalize_date(row.get("date") or row.get("trade_date"))
    code = str(row.get("code") or row.get("stock_id") or "").strip()
    if not trade_date:
        reasons.append("invalid_or_missing_date")
    if not (len(code) == 4 and code.isdigit()):
        reasons.append("invalid_stock_code")

    values: dict[str, float | None] = {}
    for field in ("open", "high", "low", "close", "volume"):
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            value = None
        if value is None or not math.isfinite(value):
            reasons.append(f"{field}_missing_or_nonfinite")
            values[field] = None
        else:
            values[field] = value

    for field in ("open", "high", "low", "close"):
        value = values.get(field)
        if value is not None and value <= 0:
            reasons.append(f"{field}_not_positive")
    volume = values.get("volume")
    if volume is not None and volume <= 0:
        reasons.append("volume_not_positive")

    open_ = values.get("open")
    high = values.get("high")
    low = values.get("low")
    close = values.get("close")
    if high is not None and low is not None and high < low:
        reasons.append("high_below_low")
    if None not in (open_, high, low) and not float(low) <= float(open_) <= float(high):
        reasons.append("open_outside_low_high")
    if None not in (close, high, low) and not float(low) <= float(close) <= float(high):
        reasons.append("close_outside_low_high")

    # Preserve order while removing duplicate reasons caused by related checks.
    unique_reasons = list(dict.fromkeys(reasons))
    ready = not unique_reasons
    return {
        "ready": ready,
        "status": DataQualityStatus.OK.value if ready else DataQualityStatus.UNAVAILABLE.value,
        "date": trade_date,
        "code": code.zfill(4) if code.isdigit() else code,
        "reasons": unique_reasons,
        "reason": "ok" if ready else ",".join(unique_reasons),
    }


def assess_post_close_snapshot(
    trade_date: Any,
    snapshot_values: list[Any] | tuple[Any, ...] | set[Any],
    *,
    market_close: time = time(13, 30),
) -> dict[str, Any]:
    """Verify that one consistently timestamped snapshot was captured after close."""

    target_date = normalize_date(trade_date)
    parsed: list[datetime] = []
    invalid_count = 0
    for raw in snapshot_values:
        text = str(raw or "").strip()
        if not text:
            invalid_count += 1
            continue
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            invalid_count += 1
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=TAIPEI)
        else:
            value = value.astimezone(TAIPEI)
        parsed.append(value)

    distinct = {value.isoformat(timespec="seconds") for value in parsed}
    reasons: list[str] = []
    if not target_date:
        reasons.append("invalid_trade_date")
    if invalid_count:
        reasons.append("missing_or_invalid_snapshot_time")
    if len(distinct) != 1:
        reasons.append("snapshot_times_are_not_single_consistent_capture")
    if parsed and target_date and any(value.date().isoformat() != target_date for value in parsed):
        reasons.append("snapshot_date_mismatch")
    if parsed and any(value.timetz().replace(tzinfo=None) <= market_close for value in parsed):
        reasons.append("snapshot_not_after_market_close")
    reasons = list(dict.fromkeys(reasons))
    ready = not reasons
    captured_at = next(iter(distinct)) if len(distinct) == 1 else None
    return {
        "ready": ready,
        "session_complete": ready,
        "status": DataQualityStatus.OK.value if ready else DataQualityStatus.SOURCE_DELAYED.value,
        "trade_date": target_date,
        "captured_at": captured_at,
        "reason": "validated_post_close_snapshot" if ready else ",".join(reasons),
        "reasons": reasons,
    }


def assess_post_close_or_delayed_capture(
    trade_date: Any,
    snapshot_values: list[Any] | tuple[Any, ...] | set[Any],
    capture: dict[str, Any] | None,
    *,
    market_close: time = time(13, 30),
) -> dict[str, Any]:
    """Accept a later historical capture only with persisted full-session evidence.

    A different capture calendar day is not sufficient by itself.  The provider
    trade session must be explicitly complete, fully normalized/stored, and carry
    a positive cumulative volume for the requested trading date.
    """

    base = assess_post_close_snapshot(
        trade_date,
        snapshot_values,
        market_close=market_close,
    )
    if base.get("ready"):
        return {**base, "capture_mode": "same_day_post_close"}
    if set(base.get("reasons") or []) != {"snapshot_date_mismatch"}:
        return {**base, "capture_mode": "rejected"}

    target_date = normalize_date(trade_date)
    evidence = dict(capture or {})
    reasons: list[str] = []
    captured_at_text = str(base.get("captured_at") or "").strip()
    try:
        captured_at = datetime.fromisoformat(captured_at_text)
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=TAIPEI)
        else:
            captured_at = captured_at.astimezone(TAIPEI)
    except ValueError:
        captured_at = None
        reasons.append("invalid_delayed_capture_time")
    if target_date and captured_at:
        target_close = datetime.combine(
            date.fromisoformat(target_date),
            market_close,
            tzinfo=TAIPEI,
        )
        if captured_at <= target_close:
            reasons.append("delayed_capture_not_after_target_close")

    if str(evidence.get("trade_date") or "") != str(target_date or ""):
        reasons.append("capture_trade_date_mismatch")
    if str(evidence.get("endpoint") or "").lower() != "trades":
        reasons.append("capture_endpoint_is_not_trades")
    if str(evidence.get("source") or "").upper() != "FUGLE":
        reasons.append("capture_source_is_not_fugle")
    capture_complete = str(evidence.get("capture_complete") or "").strip().lower()
    if capture_complete not in {"1", "true", "yes", "on"}:
        reasons.append("capture_is_not_complete")
    if str(evidence.get("data_quality") or "").upper() != "SESSION_COMPLETE":
        reasons.append("capture_quality_is_not_session_complete")

    def positive_int(name: str) -> int:
        try:
            return int(evidence.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    provider_rows = positive_int("provider_row_count")
    normalized_rows = positive_int("normalized_row_count")
    stored_rows = positive_int("stored_row_count")
    if provider_rows <= 0:
        reasons.append("capture_has_no_provider_rows")
    if normalized_rows != provider_rows or stored_rows != normalized_rows:
        reasons.append("capture_row_counts_do_not_reconcile")
    if positive_int("latest_cumulative_volume") <= 0:
        reasons.append("capture_has_no_cumulative_volume")
    if not str(evidence.get("latest_trade_time") or "").strip():
        reasons.append("capture_has_no_latest_trade_time")

    reasons = list(dict.fromkeys(reasons))
    ready = not reasons
    return {
        "ready": ready,
        "session_complete": ready,
        "status": DataQualityStatus.OK.value if ready else DataQualityStatus.SOURCE_DELAYED.value,
        "trade_date": target_date,
        "captured_at": base.get("captured_at"),
        "reason": "validated_delayed_full_session_capture" if ready else ",".join(reasons),
        "reasons": reasons,
        "capture_mode": "delayed_full_session" if ready else "rejected",
    }


def assess_persisted_price_volume_reconciliation(
    profile: dict[str, Any] | None,
    *,
    official_volume_shares: Any,
    captured_volume_shares: Any,
    scope_capture_complete: bool = False,
    tolerance_shares: float = 500.0,
    minimum_official_coverage_ratio: float = PRICE_VOLUME_MIN_OFFICIAL_COVERAGE_RATIO,
) -> dict[str, Any]:
    """Validate persisted regular-session reconciliation evidence.

    Official daily OHLCV is an all-session total, while a licensed intraday
    distribution can intentionally cover only the regular session. A small
    scope gap is allowed, but a materially incomplete distribution must not be
    labelled high quality or enter the referee.
    """

    evidence = dict(profile or {})

    def finite_positive(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    source_name = str(evidence.get("source_name") or "").upper()
    quality = str(evidence.get("quality") or "").lower()
    trade_scope = str(evidence.get("trade_scope") or "").lower()
    profile_total = finite_positive(evidence.get("total_volume_shares"))
    profile_eod = finite_positive(evidence.get("eod_volume_shares"))
    official_total = finite_positive(official_volume_shares)
    captured_total = finite_positive(captured_volume_shares)
    minimum_coverage_ratio = max(
        0.0,
        min(float(minimum_official_coverage_ratio), 1.0),
    )
    official_coverage_ratio = (
        profile_total / official_total
        if profile_total is not None and official_total is not None
        else None
    )
    reasons: list[str] = []

    if "FUGLE" not in source_name:
        reasons.append("profile_source_is_not_fugle")
    if quality not in {"high", "ok"}:
        reasons.append("profile_quality_is_not_validated")
    if trade_scope not in {"regular_intraday", "fugle_captured_session"}:
        reasons.append("profile_trade_scope_is_not_supported")
    if profile_total is None or profile_eod is None:
        reasons.append("profile_volume_evidence_is_missing")
    if official_total is None or captured_total is None:
        reasons.append("comparison_volume_is_missing")

    if profile_total is not None and captured_total is not None:
        allowed = max(float(tolerance_shares), profile_total * 0.001)
        if abs(profile_total - captured_total) > allowed:
            reasons.append("profile_total_does_not_match_price_bins")
    if profile_eod is not None and official_total is not None:
        allowed = max(float(tolerance_shares), profile_eod * 0.001)
        if abs(profile_eod - official_total) > allowed:
            reasons.append("profile_official_total_does_not_match_ohlcv")
    if profile_total is not None and profile_eod is not None:
        if profile_total > profile_eod + float(tolerance_shares):
            reasons.append("regular_session_total_exceeds_official_all_session_total")
    if (
        official_coverage_ratio is not None
        and official_coverage_ratio < minimum_coverage_ratio
    ):
        reasons.append("profile_official_volume_coverage_below_threshold")

    reasons = list(dict.fromkeys(reasons))
    ready = not reasons
    # A terminal post-close pagination result can prove that the licensed
    # regular-session price bins are complete for their own scope even when
    # the exchange daily total also contains odd-lot or after-hours volume.
    # Keep that narrower profile auditable, but do not make it referee-ready
    # while the official-volume coverage gate is not met.
    scope_only_reasons = {"profile_official_volume_coverage_below_threshold"}
    scope_ready = bool(
        scope_capture_complete
        and reasons
        and set(reasons).issubset(scope_only_reasons)
    )
    return {
        "ready": ready,
        "scope_ready": scope_ready,
        "ready_for_scoring": ready,
        "status": (
            DataQualityStatus.OK.value
            if ready
            else DataQualityStatus.ESTIMATED.value
            if scope_ready
            else DataQualityStatus.UNAVAILABLE.value
        ),
        "reason": (
            "validated_persisted_regular_session_reconciliation"
            if ready
            else "validated_scoped_regular_session_but_below_official_coverage"
            if scope_ready
            else ",".join(reasons)
        ),
        "reasons": reasons,
        "scope_capture_complete": bool(scope_capture_complete),
        "trade_scope": trade_scope or None,
        "profile_total_volume_shares": profile_total,
        "profile_eod_volume_shares": profile_eod,
        "official_coverage_ratio": (
            round(official_coverage_ratio, 6)
            if official_coverage_ratio is not None
            else None
        ),
        "minimum_official_coverage_ratio": minimum_coverage_ratio,
    }


def assess_chart_image_analysis(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Gate model-read chart observations before they reach a financial reply.

    Image-derived values remain ``estimated`` even when usable.  They may be
    shown as visible chart observations or compared with official data, but
    they never become referee/scoring inputs.
    """

    payload = dict(raw or {})

    def confidence(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(number, 1.0)) if math.isfinite(number) else 0.0

    def finite(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    is_chart = payload.get("is_stock_chart") is True
    image_quality = str(payload.get("image_quality") or "unusable").lower()
    overall_confidence = confidence(payload.get("overall_confidence"))
    reasons: list[str] = []
    if not is_chart:
        reasons.append("image_is_not_a_stock_chart")
    if image_quality not in {"high", "medium"}:
        reasons.append("image_quality_is_too_low")
    if overall_confidence < 0.6:
        reasons.append("chart_confidence_is_too_low")

    stock_raw = payload.get("stock") if isinstance(payload.get("stock"), dict) else {}
    stock_code = str(stock_raw.get("code") or "").strip()
    if not (len(stock_code) == 4 and stock_code.isdigit()):
        stock_code = ""
    stock_name = str(stock_raw.get("name") or "").strip()[:40]
    stock_confidence = confidence(stock_raw.get("confidence"))
    if stock_confidence < 0.65:
        stock_code = ""
        stock_name = ""

    indicators: list[dict[str, Any]] = []
    for item in list(payload.get("indicators") or [])[:16]:
        if not isinstance(item, dict) or item.get("visible") is not True:
            continue
        item_confidence = confidence(item.get("confidence"))
        value = finite(item.get("value"))
        name = re.sub(r"[^A-Za-z0-9%+\-]", "", str(item.get("name") or "").upper())[:20]
        if not name or item_confidence < 0.65:
            continue
        indicators.append(
            {
                "name": name,
                "period": str(item.get("period") or "").strip()[:12],
                "value": value,
                "signal": str(item.get("signal") or "").strip()[:80],
                "confidence": round(item_confidence, 3),
            }
        )

    price_values: list[dict[str, Any]] = []
    for item in list(payload.get("price_values") or [])[:10]:
        if not isinstance(item, dict) or item.get("visible") is not True:
            continue
        item_confidence = confidence(item.get("confidence"))
        value = finite(item.get("value"))
        label = str(item.get("label") or "").strip()[:30]
        if not label or value is None or item_confidence < 0.7:
            continue
        price_values.append(
            {"label": label, "value": value, "confidence": round(item_confidence, 3)}
        )

    observations = [
        str(item).strip()[:160]
        for item in list(payload.get("chart_observations") or [])[:6]
        if str(item).strip()
    ]
    uncertainties = [
        str(item).strip()[:120]
        for item in list(payload.get("uncertainty_reasons") or [])[:6]
        if str(item).strip()
    ]
    ready = not reasons and bool(indicators or price_values or observations)
    if not (indicators or price_values or observations):
        reasons.append("no_reliable_visible_chart_evidence")
    return {
        "ready": ready,
        "status": DataQualityStatus.ESTIMATED.value if ready else DataQualityStatus.UNAVAILABLE.value,
        "reason": "usable_model_read_chart_observations" if ready else ",".join(dict.fromkeys(reasons)),
        "reasons": list(dict.fromkeys(reasons)),
        "is_stock_chart": is_chart,
        "image_quality": image_quality,
        "overall_confidence": round(overall_confidence, 3),
        "stock": {
            "code": stock_code or None,
            "name": stock_name or None,
            "confidence": round(stock_confidence, 3),
        },
        "chart_type": str(payload.get("chart_type") or "").strip()[:40],
        "timeframe": str(payload.get("timeframe") or "").strip()[:30],
        "visible_date_range": str(payload.get("visible_date_range") or "").strip()[:50],
        "indicators": indicators,
        "price_values": price_values,
        "chart_observations": observations,
        "uncertainty_reasons": uncertainties,
        "can_enter_referee": False,
    }
