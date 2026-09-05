from __future__ import annotations

"""Seal cutoff-safe market context rows for one Single-Track retrieval run."""

import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from repository.company_size_repository import (
    MAX_COMPANY_SIZE_AGE_DAYS,
    read_company_size_rows_at_cutoff,
)
from repository.global_market_repository import read_global_market_rows_at_cutoff
from repository.single_track_v3_source_snapshot_repository import (
    SOURCE_SNAPSHOT_CONTRACT_VERSION,
    seal_source_snapshot_receipt,
    source_snapshot_receipts_for_run,
    source_snapshot_statuses,
)
from repository.taifex_night_repository import read_taifex_night_rows_at_cutoff
from repository.twse_valuation_repository import get_twse_valuation_at_cutoff
from services.global_market_snapshot_service import GLOBAL_MARKET_TICKERS


TPE = ZoneInfo("Asia/Taipei")
SOURCE_SNAPSHOT_MATERIALIZER_VERSION = "SingleTrackV3SourceSnapshotMaterializerV1"
_US_MARKET_TICKERS = tuple(sorted(GLOBAL_MARKET_TICKERS))
_MAX_CROSS_MARKET_CALENDAR_GAP_DAYS = 4


def _clock_value(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(TPE)
    if not isinstance(value, datetime):
        raise TypeError("clock must return datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an offset-aware datetime")
    return value.astimezone(TPE)


def _iso(value: datetime) -> str:
    return value.astimezone(TPE).isoformat(timespec="seconds")


def _parse_timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(TPE)


def _run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    cursor = conn.execute("SELECT * FROM news_retrieval_run WHERE run_id=?", (str(run_id),))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("run_id must reference a retrieval run")
    if isinstance(row, sqlite3.Row):
        return dict(row)
    columns = [str(column[0]) for column in cursor.description or ()]
    return dict(zip(columns, row, strict=True))


def _previous_official_trade_date(
    conn: sqlite3.Connection,
    *,
    calendar_revision: str,
    target_trade_date: str,
) -> str | None:
    row = conn.execute(
        """
        SELECT trade_date
        FROM single_track_v3_calendar_session
        WHERE calendar_revision=?
          AND trade_date<?
          AND session_state IN ('scheduled','delayed','special_session','early_close')
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (calendar_revision, target_trade_date),
    ).fetchone()
    return str(row[0]) if row else None


def _global_payload(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "rows": [
            {
                "market_date": row.get("market_date"),
                "ticker": row.get("ticker"),
                "display_name": row.get("display_name"),
                "close": row.get("close"),
                "previous_close": row.get("previous_close"),
                "change_pct": row.get("change_pct"),
                "currency": row.get("currency"),
                "exchange_timezone": row.get("exchange_timezone"),
                "source_market_timestamp": row.get("source_market_timestamp"),
            }
            for row in sorted(rows, key=lambda item: str(item.get("ticker") or ""))
        ]
    }


def _global_snapshot(
    *,
    run: Mapping[str, Any],
    source_key: str,
    target_entity_id: str,
    requested_tickers: tuple[str, ...],
    rows: list[dict[str, Any]],
    sealed_at: str,
) -> dict[str, Any]:
    found = {str(row.get("ticker") or "") for row in rows}
    missing = sorted(set(requested_tickers) - found)
    dates = sorted({str(row.get("market_date") or "") for row in rows if row.get("market_date")})
    available_values = [
        _parse_timestamp(row.get("available_at"), "global.available_at") for row in rows
    ]
    status = "ok"
    reasons: list[str] = []
    if not requested_tickers:
        status = "unavailable"
        reasons.append("no_audited_related_symbols")
    elif not rows:
        status = "unavailable"
        reasons.append("no_offset_aware_rows_visible_at_cutoff")
    else:
        if missing:
            status = "partial"
            reasons.append("missing_requested_tickers:" + ",".join(missing))
        if len(dates) != 1:
            status = "partial"
            reasons.append("mixed_source_market_dates")
        if dates:
            target = date.fromisoformat(str(run["target_trade_date"]))
            as_of = date.fromisoformat(dates[-1])
            gap = (target - as_of).days
            if gap < 0:
                status = "invalid_response"
                reasons.append("source_market_date_after_target_trade_date")
            elif gap > _MAX_CROSS_MARKET_CALENDAR_GAP_DAYS:
                status = "stale"
                reasons.append("cross_market_snapshot_older_than_four_calendar_days")
    retained_rows = [] if status == "invalid_response" else rows
    retained_dates = [] if status == "invalid_response" else dates
    retained_available = [] if status == "invalid_response" else available_values
    return {
        "run_id": run["run_id"],
        "source_key": source_key,
        "scope_key": (
            "related_overseas_price_reaction"
            if source_key == "related_overseas_price_snapshot"
            else "us_market_taiwan_night"
        ),
        "target_entity_id": target_entity_id,
        "target_trade_date": run["target_trade_date"],
        "cutoff_at": run["cutoff_at"],
        "source_as_of_date": retained_dates[-1] if retained_rows and retained_dates else None,
        "available_at": _iso(max(retained_available)) if retained_available else None,
        "source_id": "GLOBAL_MARKET_DAILY_SNAPSHOT",
        "authority_tier": "canonical_normalized_supplemental",
        "source_quality": "supplemental",
        "snapshot_status": status,
        "availability_reason": ";".join(reasons) if reasons else None,
        "payload": _global_payload(retained_rows) if retained_rows else {"rows": []},
        "provenance": {
            "availability_field": "available_at",
            "cutoff_enforced": True,
            "legacy_naive_or_missing_availability_excluded": True,
            "requested_tickers": list(requested_tickers),
            "source_ids": sorted({str(row.get("source") or "") for row in rows}),
            "source_table": "global_market_daily_snapshot",
            "freshness_contract": "cross_market_max_4_calendar_days_v1",
        },
        "row_count": len(retained_rows),
        "sealed_at": sealed_at,
        "created_at": sealed_at,
    }


def _taifex_snapshot(
    *,
    run: Mapping[str, Any],
    rows: list[dict[str, Any]],
    expected_trade_date: str | None,
    sealed_at: str,
) -> dict[str, Any]:
    dates = sorted({str(row.get("trade_date") or "") for row in rows if row.get("trade_date")})
    contracts = {str(row.get("contract") or "") for row in rows}
    fetched = [_parse_timestamp(row.get("fetched_at"), "taifex.fetched_at") for row in rows]
    status = "ok"
    reasons: list[str] = []
    if not rows:
        status = "unavailable"
        reasons.append("no_offset_aware_rows_visible_at_cutoff")
    elif expected_trade_date is None:
        status = "partial"
        reasons.append("previous_official_taiwan_session_unavailable")
    elif dates != [expected_trade_date]:
        status = "source_delayed" if dates and dates[-1] < expected_trade_date else "invalid_response"
        reasons.append(f"expected_trade_date:{expected_trade_date};observed:{','.join(dates)}")
    elif "TX" not in contracts:
        status = "partial"
        reasons.append("tx_contract_missing")
    payload_rows = [
        {
            "trade_date": row.get("trade_date"),
            "contract": row.get("contract"),
            "contract_month": row.get("contract_month"),
            "last": row.get("last"),
            "change_pct": row.get("change_pct"),
            "volume": row.get("volume"),
            "trading_session": row.get("trading_session"),
        }
        for row in sorted(rows, key=lambda item: str(item.get("contract") or ""))
    ]
    if status == "invalid_response":
        payload_rows = []
        dates = []
        fetched = []
    return {
        "run_id": run["run_id"],
        "source_key": "taifex_night_snapshot",
        "scope_key": "us_market_taiwan_night",
        "target_entity_id": "*",
        "target_trade_date": run["target_trade_date"],
        "cutoff_at": run["cutoff_at"],
        "source_as_of_date": dates[-1] if dates else None,
        "available_at": _iso(max(fetched)) if fetched else None,
        "source_id": "TAIFEX_DAILY_MARKET_REPORT_FUT",
        "authority_tier": "canonical_official",
        "source_quality": "official",
        "snapshot_status": status,
        "availability_reason": ";".join(reasons) if reasons else None,
        "payload": {"rows": payload_rows},
        "provenance": {
            "availability_field": "fetched_at",
            "cutoff_enforced": True,
            "expected_trade_date": expected_trade_date,
            "source_table": "taifex_night_daily_snapshot",
        },
        "row_count": len(payload_rows),
        "sealed_at": sealed_at,
        "created_at": sealed_at,
    }


def _valuation_snapshot(
    *,
    run: Mapping[str, Any],
    stock_code: str,
    row: Mapping[str, Any] | None,
    company_size_rows: list[dict[str, Any]],
    expected_trade_date: str | None,
    sealed_at: str,
) -> dict[str, Any]:
    component_statuses: dict[str, str] = {}
    component_reasons: dict[str, str | None] = {}
    payload_rows: list[dict[str, Any]] = []
    available_values: list[datetime] = []
    source_dates: list[str] = []
    source_ids: set[str] = set()

    valuation_status = "ok"
    valuation_reason = None
    if row is None:
        valuation_status = "unavailable"
        valuation_reason = "no_offset_aware_valuation_visible_at_cutoff"
    else:
        valuation_date = str(row.get("data_date") or "") or None
        valuation_available_at = str(row.get("available_at") or "") or None
        valuation_source = str(
            row.get("source") or "TWSE_OR_TPEX_OFFICIAL_VALUATION"
        )
        metrics = {
            "dividend_yield": row.get("dividend_yield"),
            "pe_ratio": row.get("pe_ratio"),
            "pb_ratio": row.get("pb_ratio"),
        }
        valuation_payload = {
                "record_type": "official_valuation",
                "data_date": valuation_date,
                "symbol": stock_code,
                "close_price": row.get("close_price"),
                **metrics,
                "financial_year_quarter": row.get("financial_year_quarter"),
                "source_id": valuation_source,
                "candidate_contribution": 0,
                "referee_eligible": False,
                "next_day_outlook_eligible": False,
            }
        if not any(value is not None for value in metrics.values()):
            valuation_status = "invalid_response"
            valuation_reason = "all_valuation_metrics_missing"
        elif expected_trade_date is None:
            valuation_status = "partial"
            valuation_reason = "previous_official_taiwan_session_unavailable"
        elif valuation_date < expected_trade_date:
            valuation_status = "source_delayed"
            valuation_reason = "valuation_older_than_expected_session"
        elif valuation_date > expected_trade_date:
            valuation_status = "invalid_response"
            valuation_reason = "valuation_date_after_expected_session"
        if valuation_status != "invalid_response":
            payload_rows.append(valuation_payload)
            source_dates.append(str(valuation_date))
            available_values.append(
                _parse_timestamp(valuation_available_at, "valuation.available_at")
            )
            source_ids.add(valuation_source)
    component_statuses["valuation"] = valuation_status
    component_reasons["valuation"] = valuation_reason

    dilution_status = "ok"
    dilution_reason = None
    if not company_size_rows:
        dilution_status = "unavailable"
        dilution_reason = "no_offset_aware_company_size_visible_at_cutoff"
    else:
        current = company_size_rows[0]
        current_date = str(current["data_date"])
        current_shares = float(current["issued_shares"])
        current_capital = float(current["paid_in_capital_twd"])
        prior = company_size_rows[1] if len(company_size_rows) > 1 else None
        age_days = None
        if expected_trade_date is None:
            dilution_status = "partial"
            dilution_reason = "previous_official_taiwan_session_unavailable"
        else:
            age_days = (
                date.fromisoformat(expected_trade_date) - date.fromisoformat(current_date)
            ).days
            if age_days < 0:
                dilution_status = "invalid_response"
                dilution_reason = "company_size_date_after_expected_session"
            elif age_days > MAX_COMPANY_SIZE_AGE_DAYS:
                dilution_status = "stale"
                dilution_reason = "company_size_snapshot_stale"
        if prior is None and dilution_status != "invalid_response":
            dilution_status = "partial"
            dilution_reason = "prior_official_share_count_unavailable"
        if dilution_status != "invalid_response":
            prior_shares = float(prior["issued_shares"]) if prior else None
            prior_capital = float(prior["paid_in_capital_twd"]) if prior else None
            shares_delta = current_shares - prior_shares if prior_shares else None
            capital_delta = current_capital - prior_capital if prior_capital else None
            shares_change_pct = (
                shares_delta / prior_shares * 100.0
                if shares_delta is not None and prior_shares
                else None
            )
            payload_rows.append(
                {
                    "record_type": "official_share_count_comparison",
                    "symbol": stock_code,
                    "current": {
                        "data_date": current_date,
                        "issued_shares": current_shares,
                        "paid_in_capital_twd": current_capital,
                    },
                    "prior": (
                        {
                            "data_date": str(prior["data_date"]),
                            "issued_shares": prior_shares,
                            "paid_in_capital_twd": prior_capital,
                        }
                        if prior
                        else None
                    ),
                    "issued_shares_delta": shares_delta,
                    "issued_shares_change_pct": shares_change_pct,
                    "paid_in_capital_delta_twd": capital_delta,
                    "dilution_observed": bool(
                        shares_delta is not None and shares_delta > 0
                    ),
                    "units": {
                        "issued_shares": "shares",
                        "paid_in_capital": "TWD",
                        "issued_shares_change": "percent",
                    },
                    "candidate_contribution": 0,
                    "referee_eligible": False,
                    "next_day_outlook_eligible": False,
                }
            )
            source_dates.append(current_date)
            available_values.extend(
                _parse_timestamp(item["fetched_at"], "company_size.fetched_at")
                for item in company_size_rows
            )
            source_ids.update(
                str(item.get("source_id") or "OFFICIAL_COMPANY_OPENAPI")
                for item in company_size_rows
            )
    component_statuses["dilution"] = dilution_status
    component_reasons["dilution"] = dilution_reason

    if all(value == "ok" for value in component_statuses.values()):
        status = "ok"
    elif payload_rows:
        status = "partial"
    elif "invalid_response" in component_statuses.values():
        status = "invalid_response"
    else:
        status = "unavailable"
    reasons = [
        f"{component}:{component_reasons[component]}"
        for component, component_status in component_statuses.items()
        if component_status != "ok" and component_reasons[component]
    ]
    source_as_of_date = max(source_dates) if source_dates else None
    available_at = _iso(max(available_values)) if available_values else None
    source_id = "+".join(sorted(source_ids)) or "TWSE_TPEX_OFFICIAL_VALUATION_COMPANY_SIZE"
    payload = (
        {
            "rows": payload_rows,
            "component_statuses": component_statuses,
            "component_reasons": component_reasons,
        }
        if payload_rows
        else {"rows": []}
    )
    return {
        "run_id": run["run_id"],
        "source_key": "dilution_valuation_snapshot",
        "scope_key": "dilution_valuation_risk",
        "target_entity_id": stock_code,
        "target_trade_date": run["target_trade_date"],
        "cutoff_at": run["cutoff_at"],
        "source_as_of_date": source_as_of_date,
        "available_at": available_at,
        "source_id": source_id,
        "authority_tier": "canonical_official",
        "source_quality": "official",
        "snapshot_status": status,
        "availability_reason": ";".join(reasons) if reasons else None,
        "payload": payload,
        "provenance": {
            "availability_fields": {
                "valuation": "available_at",
                "dilution": "fetched_at",
            },
            "cutoff_enforced": True,
            "expected_trade_date": expected_trade_date,
            "legacy_naive_or_missing_availability_excluded": True,
            "source_tables": [
                "twse_daily_valuation",
                "official_company_size_snapshot",
            ],
            "component_statuses": component_statuses,
            "company_size_maximum_age_days": MAX_COMPANY_SIZE_AGE_DAYS,
            "candidate_contribution": 0,
            "referee_eligible": False,
            "next_day_outlook_eligible": False,
        },
        "row_count": len(payload_rows),
        "sealed_at": sealed_at,
        "created_at": sealed_at,
    }


def materialize_source_snapshots_for_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    entity: Mapping[str, Any],
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Seal four non-news source obligations without fetching or model execution."""

    if not isinstance(entity, Mapping):
        raise ValueError("entity must be an object")
    stock_code = str(entity.get("stock_code") or "").strip()
    if not re.fullmatch(r"\d{4}", stock_code):
        raise ValueError("entity.stock_code must be a four-digit official code")
    related = entity.get("related_symbols") or []
    if not isinstance(related, (list, tuple)):
        raise ValueError("entity.related_symbols must be a list")
    related_symbols = tuple(
        sorted({str(value or "").strip() for value in related if str(value or "").strip()})
    )
    if len(related_symbols) > 16 or any(len(value) > 32 for value in related_symbols):
        raise ValueError("entity.related_symbols exceed their bounded contract")

    run = _run(conn, run_id)
    expected_identities = {
        ("related_overseas_price_snapshot", stock_code),
        ("us_market_snapshot", "*"),
        ("taifex_night_snapshot", "*"),
        ("dilution_valuation_snapshot", stock_code),
    }
    existing = source_snapshot_receipts_for_run(conn, str(run_id))
    if existing:
        existing_identities = {
            (str(item["source_key"]), str(item["target_entity_id"]))
            for item in existing
        }
        if existing_identities != expected_identities:
            raise ValueError("existing source snapshot set is incomplete or conflicts with the entity")
        statuses = source_snapshot_statuses(existing)
        return {
            "contract_version": SOURCE_SNAPSHOT_CONTRACT_VERSION,
            "materializer_version": SOURCE_SNAPSHOT_MATERIALIZER_VERSION,
            "run_id": str(run_id),
            "source_snapshot_ids": sorted(str(item["snapshot_id"]) for item in existing),
            "source_statuses": statuses,
            "complete": all(status == "ok" for status in statuses.values()),
            "expected_previous_official_trade_date": (
                next(
                    (
                        item.get("provenance", {}).get("expected_trade_date")
                        for item in existing
                        if item.get("provenance", {}).get("expected_trade_date")
                    ),
                    None,
                )
            ),
            "sealed_at": max(str(item["sealed_at"]) for item in existing),
            "replayed": True,
            "network_fetches": 0,
            "model_calls": 0,
            "canonical_table_writes": 0,
        }
    cutoff = _parse_timestamp(run["cutoff_at"], "run.cutoff_at")
    observed = _clock_value(clock)
    if observed < cutoff:
        raise ValueError("source snapshot materialization cannot precede the run cutoff")
    sealed_at = _iso(observed)
    expected_trade_date = _previous_official_trade_date(
        conn,
        calendar_revision=str(run["calendar_revision"]),
        target_trade_date=str(run["target_trade_date"]),
    )

    us_rows = read_global_market_rows_at_cutoff(
        conn,
        analysis_cutoff=str(run["cutoff_at"]),
        tickers=_US_MARKET_TICKERS,
    )
    related_rows = read_global_market_rows_at_cutoff(
        conn,
        analysis_cutoff=str(run["cutoff_at"]),
        tickers=related_symbols,
    ) if related_symbols else []
    taifex_rows = read_taifex_night_rows_at_cutoff(
        conn,
        analysis_cutoff=str(run["cutoff_at"]),
    )
    valuation = get_twse_valuation_at_cutoff(
        stock_code,
        analysis_cutoff=str(run["cutoff_at"]),
        conn=conn,
    )
    company_size_rows = read_company_size_rows_at_cutoff(
        conn,
        code=stock_code,
        analysis_cutoff=str(run["cutoff_at"]),
        as_of_date=expected_trade_date,
        limit=2,
    )

    inputs = (
        _global_snapshot(
            run=run,
            source_key="related_overseas_price_snapshot",
            target_entity_id=stock_code,
            requested_tickers=related_symbols,
            rows=related_rows,
            sealed_at=sealed_at,
        ),
        _global_snapshot(
            run=run,
            source_key="us_market_snapshot",
            target_entity_id="*",
            requested_tickers=_US_MARKET_TICKERS,
            rows=us_rows,
            sealed_at=sealed_at,
        ),
        _taifex_snapshot(
            run=run,
            rows=taifex_rows,
            expected_trade_date=expected_trade_date,
            sealed_at=sealed_at,
        ),
        _valuation_snapshot(
            run=run,
            stock_code=stock_code,
            row=valuation,
            company_size_rows=company_size_rows,
            expected_trade_date=expected_trade_date,
            sealed_at=sealed_at,
        ),
    )
    savepoint = "single_track_source_snapshot_materialize"
    conn.execute(f'SAVEPOINT "{savepoint}"')
    try:
        receipts = [
            seal_source_snapshot_receipt(conn, item, ensure_schema=False)
            for item in inputs
        ]
    except Exception:
        conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
        conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        raise
    conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
    statuses = source_snapshot_statuses(receipts)
    return {
        "contract_version": SOURCE_SNAPSHOT_CONTRACT_VERSION,
        "materializer_version": SOURCE_SNAPSHOT_MATERIALIZER_VERSION,
        "run_id": str(run_id),
        "source_snapshot_ids": sorted(str(item["snapshot_id"]) for item in receipts),
        "source_statuses": statuses,
        "complete": all(status == "ok" for status in statuses.values()),
        "expected_previous_official_trade_date": expected_trade_date,
        "sealed_at": sealed_at,
        "replayed": False,
        "network_fetches": 0,
        "model_calls": 0,
        "canonical_table_writes": 0,
    }
