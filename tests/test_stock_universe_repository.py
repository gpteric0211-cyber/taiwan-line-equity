from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.stock_universe_repository import (  # noqa: E402
    load_candidate_stock_universe_asof,
    record_stock_universe_revision,
)


UTC = timezone.utc


def _members(market: str, *codes: str) -> list[dict[str, object]]:
    return [
        {
            "code": code,
            "name": code,
            "market": market,
            "security_type": "stock",
            "is_active": 1,
            "listing_date": "2020-01-02",
        }
        for code in codes
    ]


def test_membership_revisions_are_append_only_deduplicated_and_filtered() -> None:
    conn = sqlite3.connect(":memory:")
    observed = datetime(2026, 9, 3, 2, tzinfo=UTC)
    mixed = _members("listed", "2330") + [
        {
            "code": "0050",
            "name": "ETF",
            "market": "listed",
            "security_type": "etf",
            "is_active": 1,
        }
    ]

    first = record_stock_universe_revision(
        conn,
        market="listed",
        members=mixed,
        source_id="TWSE_COMPANY_OPENAPI",
        membership_effective_from="2026-09-03",
        observed_at=observed,
    )
    duplicate = record_stock_universe_revision(
        conn,
        market="listed",
        members=mixed,
        source_id="TWSE_COMPANY_OPENAPI",
        membership_effective_from="2026-09-03",
        observed_at=observed + timedelta(minutes=1),
    )
    revised = record_stock_universe_revision(
        conn,
        market="listed",
        members=_members("listed", "2330", "2454"),
        source_id="TWSE_COMPANY_OPENAPI",
        membership_effective_from="2026-09-03",
        observed_at=observed + timedelta(minutes=2),
    )

    assert first == {
        "written": True,
        "revision_no": 1,
        "snapshot_revision_id": first["snapshot_revision_id"],
        "member_count": 1,
        "excluded_count": 1,
    }
    assert duplicate["written"] is False
    assert duplicate["revision_no"] == 1
    assert revised["revision_no"] == 2
    rows = conn.execute(
        "SELECT revision_no,revised_at,payload_json FROM data_observation_version ORDER BY revision_no"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] is None
    assert rows[1][1] is not None
    first_payload = json.loads(rows[0][2])
    assert [item["code"] for item in first_payload["members"]] == ["2330"]
    assert first_payload["excluded"] == [
        {"code": "0050", "reason": "excluded_security_type:etf"}
    ]
    assert first_payload["rollout_state"] == "shadow_only"


def test_candidate_reader_uses_target_membership_and_decision_cutoff() -> None:
    conn = sqlite3.connect(":memory:")
    first_seen = datetime(2026, 9, 3, 2, tzinfo=UTC)
    record_stock_universe_revision(
        conn,
        market="listed",
        members=_members("listed", "2330"),
        source_id="TWSE_COMPANY_OPENAPI",
        membership_effective_from="2026-09-03",
        observed_at=first_seen,
    )
    record_stock_universe_revision(
        conn,
        market="otc",
        members=_members("otc", "6488"),
        source_id="TPEX_COMPANY_OPENAPI",
        membership_effective_from="2026-09-03",
        observed_at=first_seen,
    )

    before_seen = load_candidate_stock_universe_asof(
        conn,
        target_date="2026-09-03",
        decision_at=first_seen - timedelta(seconds=1),
    )
    historical = load_candidate_stock_universe_asof(
        conn,
        target_date="2026-09-02",
        decision_at=first_seen + timedelta(days=1),
    )
    visible = load_candidate_stock_universe_asof(
        conn,
        target_date="2026-09-03",
        decision_at=first_seen,
    )

    assert before_seen["status"] == "unavailable"
    assert historical["status"] == "unavailable"
    assert visible["status"] == "ok"
    assert visible["codes"] == ["2330", "6488"]
    assert visible["coverage"] == 1.0
    assert visible["candidate_only"] is True
    assert visible["public_release_eligible"] is False


def test_membership_timestamps_and_rows_fail_closed() -> None:
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="timezone-aware"):
        record_stock_universe_revision(
            conn,
            market="listed",
            members=_members("listed", "2330"),
            source_id="TWSE",
            observed_at=datetime(2026, 9, 3, 2),
        )
    with pytest.raises(ValueError, match="no eligible ordinary shares"):
        record_stock_universe_revision(
            conn,
            market="listed",
            members=[
                {
                    "code": "0050",
                    "market": "listed",
                    "security_type": "etf",
                }
            ],
            source_id="TWSE",
            observed_at=datetime(2026, 9, 3, 2, tzinfo=UTC),
        )
