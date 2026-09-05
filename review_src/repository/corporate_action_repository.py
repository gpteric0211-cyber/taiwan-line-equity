from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from core.corporate_action_schema import (
    PERMANENT_CORPORATE_ACTION_TABLE,
    ensure_corporate_action_schema,
)


TAIPEI = ZoneInfo("Asia/Taipei")
ACTION_TYPES = {
    "right",
    "dividend",
    "right_dividend",
    "split",
    "capital_reduction",
    "capital_increase",
    "other",
}
RATIO_UNIT = "new_shares_per_existing_share"
RETENTION_CLASS = "permanent_corporate_action"
OFFICIAL_VERIFICATION = "official_verified"
PERMANENT_LEGACY_SOURCE_PREFIX = "PERMANENT:"
OFFICIAL_SOURCE_POLICIES = {
    "TWSE_TWT48U": {
        "hosts": {"twse.com.tw", "www.twse.com.tw"},
        "path": "/exchangeReport/TWT48U",
        "record_key_prefix": "TWSE_TWT48U:",
    },
}
ACTION_METHOD_COMPATIBILITY = {
    "right": {"bonus_share_distribution", "rights_issue", "other"},
    "dividend": {"cash_dividend", "other"},
    "right_dividend": {"bonus_share_distribution", "other"},
    "split": {"share_split"},
    "capital_reduction": {"capital_reduction"},
    "capital_increase": {"rights_issue", "other"},
    "other": {"none", "other"},
}


class CorporateActionConflictError(ValueError):
    """Raised when a replay changes an already recorded permanent event."""


@dataclass(frozen=True)
class CorporateActionRecord:
    code: str
    company_name: str | None
    action_type: str
    effective_date: str
    adjustment_method: str
    stock_distribution_ratio: float | None
    ratio_unit: str | None
    cash_dividend_per_share: float | None
    share_count_factor: float | None
    pre_event_price_multiplier: float | None
    source_id: str
    source_url: str
    source_record_key: str
    verification_status: str = OFFICIAL_VERIFICATION
    publisher_published_at: str | None = None
    effective_session: str = "regular_market_open"
    time_precision: str = "date"
    retention_class: str = RETENTION_CLASS
    directional_weight_eligible: int = 0


def _aware_taipei_timestamp(value: str, *, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(TAIPEI).isoformat(timespec="seconds")


def _optional_nonnegative(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return parsed


def _canonical_record(record: CorporateActionRecord) -> dict[str, Any]:
    values = asdict(record)
    code = str(values["code"] or "").strip()
    if not re.fullmatch(r"\d{4}", code):
        raise ValueError("code must contain exactly four digits")
    try:
        effective_date = date.fromisoformat(str(values["effective_date"])).isoformat()
    except ValueError as exc:
        raise ValueError("effective_date must be an ISO-8601 date") from exc

    action_type = str(values["action_type"] or "").strip()
    if action_type not in ACTION_TYPES:
        raise ValueError(f"unsupported action_type: {action_type}")
    if values["effective_session"] not in {
        "regular_market_open",
        "after_market_close",
        "unknown",
    }:
        raise ValueError("unsupported effective_session")
    if values["time_precision"] != "date":
        raise ValueError("only date-precision corporate actions are supported")

    adjustment_method = str(values["adjustment_method"] or "").strip()
    if adjustment_method not in {
        "bonus_share_distribution",
        "cash_dividend",
        "rights_issue",
        "share_split",
        "capital_reduction",
        "none",
        "other",
    }:
        raise ValueError(f"unsupported adjustment_method: {adjustment_method}")
    if adjustment_method not in ACTION_METHOD_COMPATIBILITY[action_type]:
        raise ValueError(
            f"adjustment_method {adjustment_method} is incompatible with "
            f"action_type {action_type}"
        )

    stock_ratio = _optional_nonnegative(
        values["stock_distribution_ratio"],
        field="stock_distribution_ratio",
    )
    cash_dividend = _optional_nonnegative(
        values["cash_dividend_per_share"],
        field="cash_dividend_per_share",
    )
    share_count_factor = _optional_nonnegative(
        values["share_count_factor"],
        field="share_count_factor",
    )
    pre_event_price_multiplier = _optional_nonnegative(
        values["pre_event_price_multiplier"],
        field="pre_event_price_multiplier",
    )
    if share_count_factor == 0:
        raise ValueError("share_count_factor must be greater than zero")
    if pre_event_price_multiplier == 0:
        raise ValueError("pre_event_price_multiplier must be greater than zero")
    ratio_unit = values["ratio_unit"]
    if stock_ratio is None and ratio_unit is not None:
        raise ValueError("ratio_unit must be null when no stock ratio is present")
    if stock_ratio is not None and ratio_unit != RATIO_UNIT:
        raise ValueError(f"ratio_unit must be {RATIO_UNIT}")
    if adjustment_method == "bonus_share_distribution":
        if (
            stock_ratio is None
            or share_count_factor is None
            or pre_event_price_multiplier is None
        ):
            raise ValueError(
                "stock ratio, share-count factor, and price multiplier are required "
                "for this action"
            )
        expected = 1.0 + stock_ratio
        if not math.isclose(share_count_factor, expected, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError(
                "share_count_factor must equal 1 + stock_distribution_ratio"
            )
        if not math.isclose(
            pre_event_price_multiplier,
            1.0 / share_count_factor,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "pre_event_price_multiplier must equal 1 / share_count_factor"
            )
    elif share_count_factor is not None or pre_event_price_multiplier is not None:
        raise ValueError(
            "derived share/price factors require bonus_share_distribution"
        )

    source_id = str(values["source_id"] or "").strip()
    source_url = str(values["source_url"] or "").strip()
    source_record_key = str(values["source_record_key"] or "").strip()
    parsed_url = urlparse(source_url)
    if not source_id or not source_record_key:
        raise ValueError("source_id and source_record_key are required")
    if parsed_url.scheme.lower() != "https" or not parsed_url.netloc:
        raise ValueError("source_url must be an absolute HTTPS URL")
    if values["verification_status"] != OFFICIAL_VERIFICATION:
        raise ValueError("permanent canonical writes require official_verified evidence")
    source_policy = OFFICIAL_SOURCE_POLICIES.get(source_id)
    if source_policy is None:
        raise ValueError(f"source_id is not an approved official source: {source_id}")
    if (
        (parsed_url.hostname or "").lower() not in source_policy["hosts"]
        or parsed_url.path != source_policy["path"]
        or not source_record_key.startswith(source_policy["record_key_prefix"])
    ):
        raise ValueError("source URL or record key does not match source_id policy")
    if source_id == "TWSE_TWT48U":
        query_dates = parse_qs(parsed_url.query, keep_blank_values=True).get("date", [])
        if len(query_dates) != 1 or not re.fullmatch(r"\d{8}", query_dates[0]):
            raise ValueError("TWSE_TWT48U source_url must contain one YYYYMMDD date")
        try:
            datetime.strptime(query_dates[0], "%Y%m%d")
        except ValueError as exc:
            raise ValueError(
                "TWSE_TWT48U source_url contains an invalid report date"
            ) from exc
    if values["retention_class"] != RETENTION_CLASS:
        raise ValueError(f"retention_class must be {RETENTION_CLASS}")
    if int(values["directional_weight_eligible"]) != 0:
        raise ValueError(
            "corporate-action records are price-basis facts, not directional weight"
        )

    publisher_published_at = values["publisher_published_at"]
    if publisher_published_at is not None:
        publisher_published_at = _aware_taipei_timestamp(
            str(publisher_published_at),
            field="publisher_published_at",
        )

    return {
        "code": code,
        "company_name": str(values["company_name"] or "").strip() or None,
        "action_type": action_type,
        "effective_date": effective_date,
        "effective_session": values["effective_session"],
        "time_precision": values["time_precision"],
        "adjustment_method": adjustment_method,
        "stock_distribution_ratio": stock_ratio,
        "ratio_unit": ratio_unit,
        "cash_dividend_per_share": cash_dividend,
        "share_count_factor": share_count_factor,
        "pre_event_price_multiplier": pre_event_price_multiplier,
        "source_id": source_id,
        "source_url": source_url,
        "source_record_key": source_record_key,
        "verification_status": values["verification_status"],
        "publisher_published_at": publisher_published_at,
        "retention_class": values["retention_class"],
        "directional_weight_eligible": 0,
    }


def _fact_digest(values: dict[str, Any]) -> str:
    economic_fact = {
        key: values[key]
        for key in (
            "code",
            "action_type",
            "effective_date",
            "effective_session",
            "time_precision",
            "adjustment_method",
            "stock_distribution_ratio",
            "ratio_unit",
            "cash_dividend_per_share",
            "share_count_factor",
            "pre_event_price_multiplier",
        )
    }
    payload = json.dumps(
        economic_fact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upsert_legacy_corporate_action(
    conn: sqlite3.Connection,
    *,
    code: str,
    action_date: str,
    action_type: str,
    cash_dividend: float | None,
    stock_dividend: float | None,
    source: str,
    is_confirmed: int,
    updated_at: str,
) -> bool:
    """Upsert the legacy projection without downgrading permanent authority."""

    before = conn.total_changes
    conn.execute(
        """
        INSERT INTO corporate_actions(
            code,date,action_type,cash_dividend,stock_dividend,
            source,is_confirmed,updated_at
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(code,date,action_type) DO UPDATE SET
            cash_dividend=excluded.cash_dividend,
            stock_dividend=excluded.stock_dividend,
            source=excluded.source,
            is_confirmed=excluded.is_confirmed,
            updated_at=excluded.updated_at
        WHERE (
                corporate_actions.source NOT LIKE 'PERMANENT:%'
                OR excluded.source LIKE 'PERMANENT:%'
              )
          AND (
                corporate_actions.cash_dividend IS NOT excluded.cash_dividend
                OR corporate_actions.stock_dividend IS NOT excluded.stock_dividend
                OR corporate_actions.source IS NOT excluded.source
                OR corporate_actions.is_confirmed IS NOT excluded.is_confirmed
                OR (
                    corporate_actions.source NOT LIKE 'PERMANENT:%'
                    AND corporate_actions.updated_at IS NOT excluded.updated_at
                )
              )
        """,
        (
            code,
            action_date,
            action_type,
            cash_dividend,
            stock_dividend,
            source,
            int(is_confirmed),
            updated_at,
        ),
    )
    return conn.total_changes > before


def _event_id(values: dict[str, Any]) -> str:
    identity = "|".join(
        (
            values["code"],
            values["effective_date"],
            values["action_type"],
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def register_verified_corporate_action(
    conn: sqlite3.Connection,
    record: CorporateActionRecord,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Persist one official action and maintain the legacy analysis projection.

    The caller owns the transaction and commit.  A semantic replay is a no-op;
    a changed payload for the same company/date/type fails closed so an official
    correction cannot silently rewrite point-in-time history.
    """

    values = _canonical_record(record)
    observed = _aware_taipei_timestamp(
        observed_at or datetime.now(TAIPEI).isoformat(timespec="seconds"),
        field="observed_at",
    )
    fact_digest = _fact_digest(values)
    event_id = _event_id(values)
    ensure_corporate_action_schema(conn)

    existing = conn.execute(
        f"""
        SELECT event_id,fact_digest
        FROM {PERMANENT_CORPORATE_ACTION_TABLE}
        WHERE code=? AND effective_date=? AND action_type=?
        """,
        (values["code"], values["effective_date"], values["action_type"]),
    ).fetchone()
    if existing is not None and str(existing[1]) != fact_digest:
        raise CorporateActionConflictError(
            "permanent corporate action already exists with different content"
        )

    inserted = False
    if existing is None:
        conn.execute(
            f"""
            INSERT INTO {PERMANENT_CORPORATE_ACTION_TABLE}(
                event_id,code,company_name,action_type,effective_date,
                effective_session,time_precision,adjustment_method,
                stock_distribution_ratio,
                ratio_unit,cash_dividend_per_share,share_count_factor,
                pre_event_price_multiplier,
                source_id,source_url,source_record_key,verification_status,
                publisher_published_at,first_seen_at,available_at,verified_at,
                retention_class,directional_weight_eligible,fact_digest
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                values["code"],
                values["company_name"],
                values["action_type"],
                values["effective_date"],
                values["effective_session"],
                values["time_precision"],
                values["adjustment_method"],
                values["stock_distribution_ratio"],
                values["ratio_unit"],
                values["cash_dividend_per_share"],
                values["share_count_factor"],
                values["pre_event_price_multiplier"],
                values["source_id"],
                values["source_url"],
                values["source_record_key"],
                values["verification_status"],
                values["publisher_published_at"],
                observed,
                observed,
                observed,
                values["retention_class"],
                values["directional_weight_eligible"],
                fact_digest,
            ),
        )
        inserted = True

    # Existing analysis code reads this eight-column table.  It is explicitly a
    # compatibility projection: complete evidence remains in the permanent table.
    legacy_changed = upsert_legacy_corporate_action(
        conn,
        code=values["code"],
        action_date=values["effective_date"],
        action_type=values["action_type"],
        cash_dividend=values["cash_dividend_per_share"],
        # The legacy column mixes TWD/share and distribution-ratio units.
        # Keep the typed ratio exclusively in the permanent authority.
        stock_dividend=None,
        source=f"{PERMANENT_LEGACY_SOURCE_PREFIX}{values['source_id']}",
        is_confirmed=1,
        updated_at=observed,
    )
    status = "inserted" if inserted else (
        "projection_repaired" if legacy_changed else "unchanged"
    )
    return {
        "status": status,
        "event_id": event_id,
        "fact_digest": fact_digest,
        "canonical_changed": inserted,
        "legacy_changed": legacy_changed,
        "code": values["code"],
        "effective_date": values["effective_date"],
        "action_type": values["action_type"],
    }


def read_verified_corporate_actions_at_cutoff(
    conn: sqlite3.Connection,
    *,
    code: str,
    analysis_cutoff: str,
    effective_on_or_after: str | None = None,
    effective_on_or_before: str | None = None,
) -> list[dict[str, Any]]:
    """Read visible permanent actions without creating or mutating schema."""

    normalized_code = str(code or "").strip()
    if not re.fullmatch(r"\d{4}", normalized_code):
        raise ValueError("code must contain exactly four digits")
    cutoff = _aware_taipei_timestamp(analysis_cutoff, field="analysis_cutoff")
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (PERMANENT_CORPORATE_ACTION_TABLE,),
    ).fetchone()
    if exists is None:
        return []
    params: list[Any] = [normalized_code, cutoff]
    date_clause = ""
    if effective_on_or_after is not None:
        try:
            lower = date.fromisoformat(str(effective_on_or_after)).isoformat()
        except ValueError as exc:
            raise ValueError(
                "effective_on_or_after must be an ISO-8601 date"
            ) from exc
        date_clause += " AND effective_date>=?"
        params.append(lower)
    if effective_on_or_before is not None:
        try:
            upper = date.fromisoformat(str(effective_on_or_before)).isoformat()
        except ValueError as exc:
            raise ValueError(
                "effective_on_or_before must be an ISO-8601 date"
            ) from exc
        date_clause += " AND effective_date<=?"
        params.append(upper)
    cursor = conn.execute(
        f"""
        SELECT *
        FROM {PERMANENT_CORPORATE_ACTION_TABLE}
        WHERE code=?
          AND available_at<=?
          AND verification_status='official_verified'
          {date_clause}
        ORDER BY effective_date DESC,event_id
        """,
        tuple(params),
    )
    columns = [str(item[0]) for item in cursor.description or ()]
    return [
        dict(row)
        if isinstance(row, sqlite3.Row)
        else dict(zip(columns, row, strict=True))
        for row in cursor.fetchall()
    ]
