from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.provenance_contract import (  # noqa: E402
    AvailabilityPolicy,
    compute_derived_usable_from,
    compute_usable_from,
    select_point_in_time_version,
)
from core.provenance_schema import (  # noqa: E402
    ensure_provenance_schema,
    record_validated_daily_ohlcv,
    record_validated_institution_activity_version,
)


UTC = timezone.utc


def test_usable_from_uses_actual_observation_and_validation_plus_buffer() -> None:
    first_seen = datetime(2026, 8, 24, 6, 2, tzinfo=UTC)
    validated = datetime(2026, 8, 24, 6, 3, tzinfo=UTC)
    result = compute_usable_from(
        first_seen_at=first_seen,
        validation_passed_at=validated,
        quality_status="validated",
        maturity_stage="final",
        schema_valid=True,
        policy=AvailabilityPolicy(safety_delay_seconds=120),
    )

    assert result == datetime(2026, 8, 24, 6, 5, tzinfo=UTC)


def test_provisional_or_failed_validation_is_not_usable() -> None:
    now = datetime(2026, 8, 24, 6, tzinfo=UTC)
    policy = AvailabilityPolicy(required_maturity_stage="final")
    assert compute_usable_from(
        first_seen_at=now,
        validation_passed_at=now,
        quality_status="validated",
        maturity_stage="provisional",
        schema_valid=True,
        policy=policy,
    ) is None
    assert compute_usable_from(
        first_seen_at=now,
        validation_passed_at=now,
        quality_status="failed",
        maturity_stage="final",
        schema_valid=True,
        policy=policy,
    ) is None


def test_derived_feature_cannot_precede_any_input_or_its_own_validation() -> None:
    base = datetime(2026, 8, 24, 6, tzinfo=UTC)
    result = compute_derived_usable_from(
        [base, base + timedelta(minutes=3)],
        computed_at=base + timedelta(minutes=2),
        validation_passed_at=base + timedelta(minutes=4),
    )
    assert result == base + timedelta(minutes=4)


def test_point_in_time_selection_uses_version_visible_at_decision_time() -> None:
    versions = [
        {"revision_no": 1, "usable_from": "2026-08-24T06:00:00+00:00", "close": 100},
        {"revision_no": 2, "usable_from": "2026-08-24T08:00:00+00:00", "close": 101},
    ]
    early = select_point_in_time_version(
        versions,
        decision_at=datetime(2026, 8, 24, 7, tzinfo=UTC),
    )
    late = select_point_in_time_version(
        versions,
        decision_at=datetime(2026, 8, 24, 9, tzinfo=UTC),
    )
    assert early and early["close"] == 100
    assert late and late["close"] == 101


def test_daily_ohlcv_store_is_append_only_and_deduplicated() -> None:
    conn = sqlite3.connect(":memory:")
    ensure_provenance_schema(conn)
    policies = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT source_key,fallback_policy FROM data_source_contract WHERE source_key LIKE 'policy:%'"
        ).fetchall()
    }
    assert policies["policy:margin_daily"] == "degrade"
    assert policies["policy:broker_branch_daily"] == "degrade"
    first_seen = datetime(2026, 8, 24, 6, tzinfo=UTC)
    row = {
        "date": "2026-08-21",
        "code": "2330",
        "open": 100,
        "high": 103,
        "low": 99,
        "close": 102,
        "volume": 1000,
        "source": "official-test",
        "source_quality": "official",
    }
    assert record_validated_daily_ohlcv(conn, row, observed_at=first_seen)
    assert not record_validated_daily_ohlcv(conn, row, observed_at=first_seen + timedelta(minutes=1))
    revised = {**row, "close": 101}
    assert record_validated_daily_ohlcv(conn, revised, observed_at=first_seen + timedelta(hours=1))

    rows = conn.execute(
        "SELECT revision_no,revised_at,first_seen_at,usable_from,schema_version FROM data_observation_version ORDER BY revision_no"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == 1 and rows[0][1] is None
    assert rows[1][0] == 2 and rows[1][1] is not None
    assert rows[0][3] == "2026-08-24T06:02:00+00:00"
    assert rows[1][4] == "daily-ohlcv-v1"


def test_historical_institution_import_is_not_backdated_to_trade_date() -> None:
    conn = sqlite3.connect(":memory:")
    observed = datetime(2026, 9, 2, 5, 30, tzinfo=UTC)
    row = {
        "date": "2026-08-28",
        "code": "2454",
        "market": "listed",
        "foreign_buy": 100,
        "foreign_sell": 80,
        "foreign_net": 20,
        "trust_buy": 10,
        "trust_sell": 8,
        "trust_net": 2,
        "dealer_buy": 5,
        "dealer_sell": 6,
        "dealer_net": -1,
        "source": "TWSE_T86",
    }

    assert record_validated_institution_activity_version(
        conn,
        row,
        observed_at=observed,
    )
    assert not record_validated_institution_activity_version(
        conn,
        row,
        observed_at=observed + timedelta(minutes=1),
    )
    saved = conn.execute(
        """
        SELECT event_at,first_seen_at,usable_from,schema_version
        FROM data_observation_version
        WHERE dataset_key='institution_daily'
        """
    ).fetchone()

    assert saved[0] == "2026-08-28T05:30:00+00:00"
    assert saved[1] == "2026-09-02T05:30:00+00:00"
    assert saved[2] == "2026-09-02T05:35:00+00:00"
    assert saved[3] == "institution-daily-v1"
