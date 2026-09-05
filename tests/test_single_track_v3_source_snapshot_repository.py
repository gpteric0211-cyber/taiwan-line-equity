from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_schema import (  # noqa: E402
    SINGLE_TRACK_V3_SCHEMA_VERSION,
    ensure_single_track_v3_schema,
)
from repository.single_track_v3_source_snapshot_repository import (  # noqa: E402
    seal_source_snapshot_receipt,
    source_snapshot_receipt,
    source_snapshot_receipts_for_run,
    source_snapshot_statuses,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    conn.execute(
        """
        INSERT INTO news_retrieval_run(
            run_id,idempotency_key,slot_key,target_trade_date,scheduled_for,
            cutoff_at,status,source_policy_version,calendar_revision,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "run-snapshot-1",
            "snapshot-idempotency-1",
            "preopen_final_scan",
            "2026-09-02",
            "2026-09-02T06:45:00+08:00",
            "2026-09-02T07:00:00+08:00",
            "running",
            "SourceAuthorityPolicyV1",
            "calendar-r1",
            "2026-09-02T06:45:00+08:00",
            "2026-09-02T06:45:00+08:00",
        ),
    )
    return conn


def _receipt(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": "run-snapshot-1",
        "source_key": "us_market_snapshot",
        "scope_key": "us_market_taiwan_night",
        "target_entity_id": "*",
        "target_trade_date": "2026-09-02",
        "cutoff_at": "2026-09-02T07:00:00+08:00",
        "source_as_of_date": "2026-09-01",
        "available_at": "2026-09-02T06:40:00+08:00",
        "source_id": "YAHOO_FINANCE_CHART",
        "authority_tier": "canonical_normalized_supplemental",
        "source_quality": "supplemental",
        "snapshot_status": "ok",
        "availability_reason": None,
        "payload": {
            "rows": [
                {
                    "ticker": "^GSPC",
                    "market_date": "2026-09-01",
                    "close": 6500.0,
                    "change_pct": 0.4,
                    "currency": "USD",
                }
            ]
        },
        "provenance": {
            "source_table": "global_market_daily_snapshot",
            "availability_field": "available_at",
            "selection": "latest_visible_at_cutoff",
        },
        "row_count": 1,
        "sealed_at": "2026-09-02T07:00:01+08:00",
        "created_at": "2026-09-02T07:00:01+08:00",
    }
    row.update(overrides)
    return row


def test_seals_cutoff_safe_snapshot_idempotently_with_exact_digest() -> None:
    conn = _connection()

    saved = seal_source_snapshot_receipt(conn, _receipt())

    assert SINGLE_TRACK_V3_SCHEMA_VERSION == "single-track-v3-v11-stage1.1"
    assert saved["snapshot_id"].startswith("source-snapshot:")
    assert len(saved["snapshot_key"]) == 64
    assert len(saved["payload_digest"]) == 64
    assert len(saved["snapshot_digest"]) == 64
    assert saved["source_revision_id"].startswith("source-revision:")
    assert saved["first_seen_at"] == saved["created_at"]
    assert saved["usable_from"] == saved["available_at"]
    assert saved["source_policy_version"] == "SourceAuthorityPolicyV1"
    assert saved["payload"]["rows"][0]["ticker"] == "^GSPC"
    assert seal_source_snapshot_receipt(conn, _receipt()) == saved
    assert source_snapshot_receipt(conn, saved["snapshot_id"]) == saved
    assert source_snapshot_receipts_for_run(conn, "run-snapshot-1") == [saved]
    assert source_snapshot_statuses([saved]) == {"us_market_snapshot": "ok"}


def test_rejects_future_unbounded_or_fabricated_snapshot_evidence() -> None:
    conn = _connection()

    with pytest.raises(ValueError, match="not available at the run cutoff"):
        seal_source_snapshot_receipt(
            conn,
            _receipt(available_at="2026-09-02T07:00:01+08:00"),
        )
    with pytest.raises(ValueError, match="forbidden field: raw_body"):
        seal_source_snapshot_receipt(
            conn,
            _receipt(payload={"rows": [], "raw_body": "not allowed"}, row_count=0),
        )
    with pytest.raises(ValueError, match="require data rows"):
        seal_source_snapshot_receipt(
            conn,
            _receipt(payload={"rows": []}, row_count=0),
        )
    with pytest.raises(ValueError, match="require a target entity"):
        seal_source_snapshot_receipt(
            conn,
            _receipt(
                source_key="dilution_valuation_snapshot",
                scope_key="dilution_valuation_risk",
                target_entity_id="*",
                authority_tier="canonical_official",
                source_id="TWSE_BWIBBU",
            ),
        )
    with pytest.raises(ValueError, match="cannot be later than available_at"):
        seal_source_snapshot_receipt(
            conn,
            _receipt(publisher_published_at="2026-09-02T06:41:00+08:00"),
        )


def test_unavailable_receipt_is_explicit_and_conflicting_replay_fails_closed() -> None:
    conn = _connection()
    unavailable = _receipt(
        source_key="taifex_night_snapshot",
        scope_key="us_market_taiwan_night",
        authority_tier="canonical_official",
        source_id="TAIFEX_DAILY_MARKET_REPORT_FUT",
        source_quality="official",
        source_as_of_date=None,
        available_at=None,
        snapshot_status="unavailable",
        availability_reason="no_offset_aware_availability_at_or_before_cutoff",
        payload={"rows": []},
        row_count=0,
    )

    saved = seal_source_snapshot_receipt(conn, unavailable)

    assert saved["available_at"] is None
    assert source_snapshot_statuses([saved]) == {"taifex_night_snapshot": "unavailable"}
    with pytest.raises(ValueError, match="different content"):
        seal_source_snapshot_receipt(
            conn,
            {**unavailable, "availability_reason": "conflicting reason"},
        )


def test_one_failed_entity_keeps_a_logical_snapshot_source_incomplete() -> None:
    receipts = [
        {
            "source_key": "related_overseas_price_snapshot",
            "snapshot_status": "ok",
        },
        {
            "source_key": "related_overseas_price_snapshot",
            "snapshot_status": "stale",
        },
    ]

    assert source_snapshot_statuses(receipts) == {
        "related_overseas_price_snapshot": "stale"
    }
