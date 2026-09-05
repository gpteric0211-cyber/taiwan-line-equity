from __future__ import annotations

"""Immutable point-in-time evidence for the V11 candidate stock universe.

The mutable ``stock_master`` remains the operational source used by Stable.
This module records and reads candidate-only official membership revisions so
historical evaluation never projects today's membership backward.
"""

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, time, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from core.provenance_contract import (
    DATASET_AVAILABILITY_CONTRACTS,
    compute_usable_from,
)
from core.provenance_schema import ensure_provenance_schema


DATASET_KEY = "stock_universe_membership"
CONTRACT_VERSION = "StockUniverseContractV1"
TPE = ZoneInfo("Asia/Taipei")
UTC = timezone.utc
MARKET_EXCHANGE = {"listed": "TWSE", "otc": "TPEX"}
EXCLUDED_SECURITY_TYPES = (
    "convertible_bond",
    "emerging_market",
    "etf",
    "etn",
    "fund",
    "preferred_share",
    "tdr",
    "warrant",
)


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return _aware_utc(value, field="timestamp").isoformat(timespec="seconds")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _date_text(value: str | date, *, field: str) -> str:
    try:
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    return parsed.isoformat()


def _normalize_members(
    members: Iterable[dict[str, Any]],
    *,
    market: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    normalized: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, str]] = []
    for raw in members:
        code = str(raw.get("code") or "").strip()
        security_type = str(raw.get("security_type") or "").strip().lower()
        row_market = str(raw.get("market") or market).strip().lower()
        is_active = int(bool(raw.get("is_active", 1)))
        reason = None
        if not re.fullmatch(r"\d{4}", code):
            reason = "invalid_code"
        elif row_market != market:
            reason = "market_mismatch"
        elif security_type != "stock":
            reason = f"excluded_security_type:{security_type or 'unknown'}"
        elif not is_active:
            reason = "inactive_in_source_snapshot"
        if reason:
            excluded.append({"code": code, "reason": reason})
            continue

        listing_date = raw.get("listing_date") or raw.get("first_seen_date")
        if listing_date:
            listing_date = _date_text(str(listing_date), field="listing_date")
        member = {
            "code": code,
            "name": str(raw.get("name") or "").strip(),
            "market": market,
            "exchange": MARKET_EXCHANGE[market],
            "security_type": "stock",
            "listing_date": listing_date,
            "forecast_eligibility": "separate_assessment_required",
        }
        previous = normalized.get(code)
        if previous is not None and previous != member:
            raise ValueError(f"conflicting stock-universe rows for {code}")
        normalized[code] = member
    return [normalized[code] for code in sorted(normalized)], excluded


def record_stock_universe_revision(
    conn: sqlite3.Connection,
    *,
    market: str,
    members: Iterable[dict[str, Any]],
    source_id: str,
    observed_at: datetime,
    membership_effective_from: str | date | None = None,
    source_url: str | None = None,
    source_published_at: datetime | None = None,
) -> dict[str, Any]:
    """Append one official-market membership revision without backdating use.

    When the provider does not supply a source date, the backend observation
    date becomes the effective date and is labelled as such. It is never used
    as evidence for an earlier target date.
    """

    normalized_market = str(market or "").strip().lower()
    if normalized_market not in MARKET_EXCHANGE:
        raise ValueError("market must be listed or otc")
    clean_source_id = str(source_id or "").strip()
    if not clean_source_id:
        raise ValueError("source_id is required")
    observed = _aware_utc(observed_at, field="observed_at")
    if source_published_at is not None:
        published = _aware_utc(source_published_at, field="source_published_at")
        if published > observed:
            raise ValueError("source_published_at cannot be after observed_at")
    else:
        published = None

    if membership_effective_from is None:
        effective_from = observed.astimezone(TPE).date().isoformat()
        effective_date_basis = "backend_first_seen_date"
    else:
        effective_from = _date_text(
            membership_effective_from,
            field="membership_effective_from",
        )
        effective_date_basis = "provider_data_date"

    normalized_members, excluded = _normalize_members(
        members,
        market=normalized_market,
    )
    if not normalized_members:
        raise ValueError("official stock-universe snapshot has no eligible ordinary shares")

    ensure_provenance_schema(conn)
    contract = DATASET_AVAILABILITY_CONTRACTS[DATASET_KEY]
    usable_from = compute_usable_from(
        first_seen_at=observed,
        validation_passed_at=observed,
        quality_status="validated",
        maturity_stage="final",
        schema_valid=True,
        policy=contract.policy,
    )
    if usable_from is None:
        raise ValueError("stock-universe revision did not pass availability policy")

    source_revision_id = _sha256_payload(
        {
            "market": normalized_market,
            "membership_effective_from": effective_from,
            "source_id": clean_source_id,
            "members": normalized_members,
            "excluded": excluded,
        }
    )
    payload = {
        "contract_version": CONTRACT_VERSION,
        "market": normalized_market,
        "exchange": MARKET_EXCHANGE[normalized_market],
        "security_type": "stock",
        "membership_effective_from": effective_from,
        "membership_effective_to": None,
        "effective_date_basis": effective_date_basis,
        "source_id": clean_source_id,
        "source_url": str(source_url or "").strip() or None,
        "source_revision_id": source_revision_id,
        "member_count": len(normalized_members),
        "members": normalized_members,
        "excluded_count": len(excluded),
        "excluded": excluded,
        "explicitly_excluded_security_types": list(EXCLUDED_SECURITY_TYPES),
        "suspended_entity_policy": "retain_entity_assess_forecast_eligibility_separately",
        "insufficient_history_policy": "lower_coverage_or_unavailable_no_imputation",
        "rollout_state": "shadow_only",
    }
    payload_json = _canonical_json(payload)
    content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    natural_key = f"{normalized_market}:{effective_from}"
    existing = conn.execute(
        """
        SELECT revision_no,content_hash
        FROM data_observation_version
        WHERE dataset_key=? AND natural_key=? AND content_hash=?
        """,
        (DATASET_KEY, natural_key, content_hash),
    ).fetchone()
    if existing:
        return {
            "written": False,
            "revision_no": int(existing[0]),
            "snapshot_revision_id": str(existing[1]),
            "member_count": len(normalized_members),
            "excluded_count": len(excluded),
        }

    now_text = _utc_text(observed)
    source_key = f"{DATASET_KEY}:{normalized_market}:{clean_source_id}"
    conn.execute(
        """
        INSERT INTO data_source_contract(
            source_key,source_label,event_semantics,required_maturity_stage,
            safety_delay_seconds,fallback_policy,schema_version,active,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO UPDATE SET
            source_label=excluded.source_label,
            event_semantics=excluded.event_semantics,
            required_maturity_stage=excluded.required_maturity_stage,
            safety_delay_seconds=excluded.safety_delay_seconds,
            fallback_policy=excluded.fallback_policy,
            schema_version=excluded.schema_version,
            active=1,
            updated_at=excluded.updated_at
        """,
        (
            source_key,
            clean_source_id,
            contract.event_semantics,
            contract.policy.required_maturity_stage,
            contract.policy.safety_delay_seconds,
            contract.policy.fallback_policy,
            contract.schema_version,
            1,
            now_text,
            now_text,
        ),
    )
    latest = conn.execute(
        """
        SELECT MAX(revision_no)
        FROM data_observation_version
        WHERE dataset_key=? AND natural_key=?
        """,
        (DATASET_KEY, natural_key),
    ).fetchone()
    revision_no = int((latest[0] if latest else 0) or 0) + 1
    event_at = datetime.combine(
        date.fromisoformat(effective_from),
        time.min,
        tzinfo=TPE,
    )
    conn.execute(
        """
        INSERT INTO data_observation_version(
            dataset_key,natural_key,event_at,source_key,source_published_at,
            first_seen_at,validation_passed_at,usable_from,revised_at,revision_no,
            content_hash,schema_version,quality_status,maturity_stage,payload_json,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            DATASET_KEY,
            natural_key,
            _utc_text(event_at),
            source_key,
            _utc_text(published) if published else None,
            now_text,
            now_text,
            _utc_text(usable_from),
            now_text if revision_no > 1 else None,
            revision_no,
            content_hash,
            CONTRACT_VERSION,
            "validated",
            "final",
            payload_json,
            now_text,
        ),
    )
    return {
        "written": True,
        "revision_no": revision_no,
        "snapshot_revision_id": content_hash,
        "member_count": len(normalized_members),
        "excluded_count": len(excluded),
    }


def load_candidate_stock_universe_asof(
    conn: sqlite3.Connection,
    *,
    target_date: str | date,
    decision_at: datetime,
) -> dict[str, Any]:
    """Read the latest visible membership revision for each official market."""

    target = _date_text(target_date, field="target_date")
    cutoff = _aware_utc(decision_at, field="decision_at")
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_observation_version'"
    ).fetchone()
    if not table:
        return _universe_result(target, {}, status="unavailable")

    rows = conn.execute(
        """
        SELECT payload_json,usable_from,revision_no,content_hash
        FROM data_observation_version
        WHERE dataset_key=? AND quality_status='validated' AND maturity_stage IN ('final','revised')
        """,
        (DATASET_KEY,),
    ).fetchall()
    selected: dict[str, tuple[tuple[str, datetime, int], dict[str, Any], str]] = {}
    for row in rows:
        try:
            payload = json.loads(str(row[0]))
            market = str(payload["market"])
            effective_from = _date_text(
                payload["membership_effective_from"],
                field="membership_effective_from",
            )
            usable = _aware_utc(
                datetime.fromisoformat(str(row[1]).replace("Z", "+00:00")),
                field="usable_from",
            )
            revision_no = int(row[2])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if market not in MARKET_EXCHANGE or effective_from > target or usable > cutoff:
            continue
        rank = (effective_from, usable, revision_no)
        current = selected.get(market)
        if current is None or rank > current[0]:
            selected[market] = (rank, payload, str(row[3]))

    status = "ok" if set(selected) == set(MARKET_EXCHANGE) else ("partial" if selected else "unavailable")
    return _universe_result(target, selected, status=status)


def _universe_result(
    target_date: str,
    selected: dict[str, tuple[tuple[str, datetime, int], dict[str, Any], str]],
    *,
    status: str,
) -> dict[str, Any]:
    codes: set[str] = set()
    revisions: dict[str, dict[str, Any]] = {}
    for market, (rank, payload, content_hash) in selected.items():
        codes.update(str(item["code"]) for item in payload.get("members", []))
        revisions[market] = {
            "membership_effective_from": rank[0],
            "usable_from": rank[1].isoformat(timespec="seconds"),
            "revision_no": rank[2],
            "snapshot_revision_id": content_hash,
            "source_revision_id": payload.get("source_revision_id"),
            "source_id": payload.get("source_id"),
        }
    return {
        "contract_version": CONTRACT_VERSION,
        "target_date": target_date,
        "status": status,
        "coverage": len(selected) / len(MARKET_EXCHANGE),
        "codes": sorted(codes),
        "member_count": len(codes),
        "market_revisions": revisions,
        "candidate_only": True,
        "stable_path_unchanged": True,
        "public_release_eligible": False,
    }
