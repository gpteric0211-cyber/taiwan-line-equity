from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.provenance_schema import (  # noqa: E402
    ensure_provenance_schema,
    record_validated_credit_balance_versions,
)
from repository.estimated_chip_cost_repository import (  # noqa: E402
    ensure_estimated_chip_cost_schema,
    upsert_estimated_chip_cost_rows,
)
from task.v11_structured_fact_materializer import (  # noqa: E402
    materialize_corporate_action_safe_cost_facts,
    materialize_credit_balance_candidate_facts,
    materialize_tdcc_candidate_revisions,
    read_corporate_action_safe_cost_facts,
    read_credit_balance_candidate_fact_bundle,
    read_tdcc_candidate_revision,
)


UTC = timezone.utc


def _legacy_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE margin_daily(
            date TEXT,code TEXT,margin_delta REAL,margin_balance REAL,
            short_delta REAL,short_balance REAL,source TEXT,updated_at REAL,
            source_quality TEXT,fetched_at REAL,PRIMARY KEY(date,code)
        );
        CREATE TABLE lending_daily(
            date TEXT,code TEXT,lending_delta REAL,lending_balance REAL,
            source TEXT,updated_at REAL,PRIMARY KEY(date,code)
        );
        """
    )


def _credit_row(*, margin_balance: int = 106) -> dict[str, object]:
    return {
        "trade_date": "2026-09-02",
        "code": "2330",
        "market": "listed",
        "margin_prev_balance_lots": 100,
        "margin_buy_lots": 10,
        "margin_sell_lots": 3,
        "margin_cash_repayment_lots": 1,
        "margin_balance_lots": margin_balance,
        "margin_delta_lots": margin_balance - 100,
        "margin_utilization_pct": 21.2,
        "margin_utilization_method": "source_reported",
        "margin_limit_lots": 500,
        "short_prev_balance_lots": 20,
        "short_sell_lots": 2,
        "short_buy_lots": 4,
        "short_stock_repayment_lots": 1,
        "short_balance_lots": 17,
        "short_delta_lots": -3,
        "short_utilization_pct": 3.4,
        "short_utilization_method": "source_reported",
        "short_limit_lots": 500,
        "sbl_prev_balance_shares": 100_000,
        "sbl_sell_shares": 8_000,
        "sbl_return_shares": 3_000,
        "sbl_adjust_shares": 1_000,
        "sbl_balance_shares": 106_000,
        "sbl_delta_shares": 6_000,
        "margin_source": "TWSE_MI_MARGN",
        "lending_source": "TWSE_TWT93U",
    }


def test_credit_facts_ignore_polluted_legacy_values_and_preserve_units() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _legacy_tables(conn)
    ensure_provenance_schema(conn)
    observed = datetime(2026, 9, 3, 1, tzinfo=UTC)
    assert record_validated_credit_balance_versions(
        conn,
        _credit_row(),
        observed_at=observed,
    ) == 2
    conn.execute(
        """
        INSERT INTO margin_daily(date,code,margin_delta,source)
        VALUES('2026-09-02','2330',999999,'POLLUTED_LEGACY')
        """
    )

    result = materialize_credit_balance_candidate_facts(conn)
    again = materialize_credit_balance_candidate_facts(conn)
    saved = {
        row["field"]: (json.loads(row["value_json"]), row["unit"], json.loads(row["provenance_json"]))
        for row in conn.execute(
            """
            SELECT field,value_json,unit,provenance_json
            FROM canonical_evidence_fact
            WHERE formula_source_version='ChipAnalysisContractV1:official-credit-raw-v1'
            """
        ).fetchall()
    }

    assert result["status"] == "ok"
    assert result["source_observations"] == 2
    assert result["legacy_margin_daily_read"] is False
    assert result["facts_written"] > 0
    assert again["facts_written"] == 0
    assert saved["margin_delta_lots"][0:2] == (6, "lots")
    assert saved["sbl_delta_shares"][0:2] == (6000, "shares")
    assert saved["short_delta_lots"][0:2] == (-3, "lots")
    assert saved["margin_delta_lots"][2]["candidate_contribution"] == 0.0
    assert saved["margin_delta_lots"][2]["referee_eligible"] is False
    assert 999999 not in [item[0] for item in saved.values()]


def test_request_reader_is_cutoff_safe_and_write_free_across_revisions() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _legacy_tables(conn)
    ensure_provenance_schema(conn)
    first_seen = datetime(2026, 9, 3, 1, tzinfo=UTC)
    second_seen = first_seen + timedelta(hours=1)
    record_validated_credit_balance_versions(
        conn,
        _credit_row(margin_balance=106),
        observed_at=first_seen,
    )
    record_validated_credit_balance_versions(
        conn,
        _credit_row(margin_balance=107),
        observed_at=second_seen,
    )
    materialize_credit_balance_candidate_facts(conn)

    before_changes = conn.total_changes
    before = read_credit_balance_candidate_fact_bundle(
        conn,
        stock_code="2330",
        target_date="2026-09-02",
        decision_at=first_seen + timedelta(minutes=4),
    )
    revision_one = read_credit_balance_candidate_fact_bundle(
        conn,
        stock_code="2330",
        target_date="2026-09-02",
        decision_at=first_seen + timedelta(minutes=5),
    )
    revision_two = read_credit_balance_candidate_fact_bundle(
        conn,
        stock_code="2330",
        target_date="2026-09-02",
        decision_at=second_seen + timedelta(minutes=5),
    )

    assert conn.total_changes == before_changes
    assert before["status"] == "unavailable"
    assert {item["field"]: item["value"] for item in revision_one["facts"]}[
        "margin_delta_lots"
    ] == 6
    assert {item["field"]: item["value"] for item in revision_two["facts"]}[
        "margin_delta_lots"
    ] == 7
    assert revision_two["candidate_contribution"] == 0.0
    assert revision_two["request_time_computation"] is False
    assert revision_two["request_time_writes"] == 0


def _tdcc_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE tdcc_holding_distribution(
            date TEXT,code TEXT,level TEXT,holders INTEGER,shares REAL,
            percent REAL,source TEXT,updated_at REAL,
            PRIMARY KEY(date,code,level)
        );
        """
    )


def _insert_tdcc_weeks(conn: sqlite3.Connection) -> None:
    dates = ["2026-07-24", "2026-07-31", "2026-08-07", "2026-08-14", "2026-08-21"]
    for date_index, source_week_date in enumerate(dates):
        for level in range(1, 16):
            conn.execute(
                """
                INSERT INTO tdcc_holding_distribution(
                    date,code,level,holders,shares,percent,source,updated_at
                ) VALUES(?,?,?,?,?,?,?,0)
                """,
                (
                    source_week_date,
                    "2454",
                    str(level),
                    1,
                    1,
                    float(level + (date_index if level == 15 else 0)),
                    "TDCC_OPEN_DATA_1_5",
                ),
            )


def test_tdcc_revisions_are_weekly_idempotent_and_use_corrected_bucket() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _tdcc_tables(conn)
    _insert_tdcc_weeks(conn)
    observed = datetime(2026, 8, 22, 2, tzinfo=UTC)

    first = materialize_tdcc_candidate_revisions(conn, observed_at=observed)
    observation_count = conn.execute(
        """
        SELECT COUNT(*) FROM data_observation_version
        WHERE dataset_key='holding_distribution_weekly'
        """
    ).fetchone()[0]
    replay = materialize_tdcc_candidate_revisions(
        conn,
        observed_at=observed + timedelta(hours=1),
    )
    latest_payload = json.loads(
        conn.execute(
            """
            SELECT payload_json FROM data_observation_version
            WHERE dataset_key='holding_distribution_weekly'
            ORDER BY event_at DESC LIMIT 1
            """
        ).fetchone()[0]
    )

    assert first["status"] == "ok"
    assert first["source_snapshots"] == 5
    assert first["revisions_written"] == 5
    assert first["fake_daily_observations"] == 0
    assert replay["revisions_written"] == 0
    assert replay["revisions_replayed"] == 5
    assert observation_count == 5
    assert latest_payload["source_week_date"] == "2026-08-21"
    assert latest_payload["summary"]["small_10_share_pct"] == 6.0
    assert latest_payload["summary"]["big_1000_change_4w"] == 4.0
    assert latest_payload["publisher_published_at"] is None
    assert latest_payload["publisher_timestamp_status"] == "not_promised_by_official_source"
    assert latest_payload["expected_next_publish_at"] == "2026-08-28T23:59:59+08:00"
    assert latest_payload["grace_deadline"] == "2026-08-31T23:59:59+08:00"
    assert latest_payload["hard_stale_at"] == "2026-09-04T23:59:59+08:00"
    assert latest_payload["candidate_contribution"] == 0.0


def test_tdcc_reader_applies_grace_delayed_and_hard_stale_without_writes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _tdcc_tables(conn)
    _insert_tdcc_weeks(conn)
    observed = datetime(2026, 8, 22, 2, tzinfo=UTC)
    materialize_tdcc_candidate_revisions(conn, observed_at=observed)

    before_changes = conn.total_changes
    unavailable = read_tdcc_candidate_revision(
        conn,
        stock_code="2454",
        target_date="2026-08-21",
        decision_at=observed + timedelta(minutes=9),
    )
    within_grace = read_tdcc_candidate_revision(
        conn,
        stock_code="2454",
        target_date="2026-08-21",
        decision_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
    )
    delayed = read_tdcc_candidate_revision(
        conn,
        stock_code="2454",
        target_date="2026-08-21",
        decision_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    stale = read_tdcc_candidate_revision(
        conn,
        stock_code="2454",
        target_date="2026-08-21",
        decision_at=datetime(2026, 9, 5, 12, tzinfo=UTC),
    )

    assert conn.total_changes == before_changes
    assert unavailable["status"] == "unavailable"
    assert within_grace["status"] == "ok"
    assert within_grace["data_eligible"] is True
    assert within_grace["source_revision_citation_count"] == 1
    assert len(within_grace["source_revision_ids"]) == 1
    assert delayed["status"] == "source_delayed"
    assert delayed["data_eligible"] is False
    assert stale["status"] == "stale"
    assert stale["data_eligible"] is False
    assert stale["candidate_contribution"] == 0.0
    assert stale["request_time_computation"] is False
    assert stale["request_time_writes"] == 0


def _cost_row(code: str = "2454") -> dict[str, object]:
    return {
        "code": code,
        "trade_date": "2026-08-27",
        "cost_type": "foreign_estimated",
        "cost_label": "外資近期增量成本推估",
        "estimated_cost": 101.25,
        "cost_status": "estimated",
        "confidence": "medium",
        "data_source_confidence": "medium",
        "data_source_status": "official_only",
        "source_license": "public_local_db",
        "source_detail": "official institution flow and official OHLCV",
        "source_tables": "institution_activity_daily,history_price",
        "calculation_method": "official_net_flow_incremental_inventory",
        "formula_version": "official_net_flow_incremental_inventory_v3",
        "price_basis": "vwap_from_amount_volume",
        "price_basis_value": 102.0,
        "price_to_cost_deviation_pct": 0.74,
        "accumulation_status": "neutral",
        "position_shares": 1000.0,
        "total_cost_amount": 101250.0,
        "cumulative_net_shares": 1000.0,
        "estimate_start_date": "2026-07-01",
        "estimate_end_date": "2026-08-27",
        "sample_days": 40,
        "display_reason": "官方法人淨流量近期增量成本推估。",
        "debug_reason": "official_net_flow_incremental_inventory",
        "missing_required_fields": None,
    }


def _corporate_action_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE corporate_actions(
            code TEXT,date TEXT,action_type TEXT,cash_dividend REAL,
            stock_dividend REAL,source TEXT,is_confirmed INTEGER,updated_at TEXT,
            PRIMARY KEY(code,date,action_type)
        )
        """
    )


def test_cost_fact_is_suppressed_when_estimate_crosses_corporate_action() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_estimated_chip_cost_schema(conn)
    _corporate_action_table(conn)
    upsert_estimated_chip_cost_rows(conn, [_cost_row()])
    conn.execute(
        """
        INSERT INTO corporate_actions
        VALUES('2454','2026-08-01','right_dividend',2.0,0.1,'official',1,'2026-08-01')
        """
    )
    original = conn.execute(
        "SELECT estimated_cost,cost_status FROM estimated_chip_cost_daily"
    ).fetchone()
    observed = datetime(2026, 8, 28, 2, tzinfo=UTC)

    result = materialize_corporate_action_safe_cost_facts(
        conn,
        observed_at=observed,
        corporate_action_coverage_verified=True,
    )
    replay = materialize_corporate_action_safe_cost_facts(
        conn,
        observed_at=observed + timedelta(hours=1),
        corporate_action_coverage_verified=True,
    )
    saved = conn.execute(
        """
        SELECT value_json,quality,availability_reason,provenance_json
        FROM canonical_evidence_fact
        WHERE formula_source_version='ChipAnalysisContractV1:corporate-action-safe-cost-v1'
        """
    ).fetchone()

    assert result["status"] == "unavailable"
    assert result["suppressed_by_reason"] == {
        "corporate_action_adjustment_unavailable": 1
    }
    assert result["stable_formula_recomputed"] is False
    assert result["stable_rows_mutated"] == 0
    assert replay["facts_written"] == 0
    assert json.loads(saved["value_json"]) is None
    assert saved["quality"] == "unavailable"
    assert saved["availability_reason"] == "corporate_action_adjustment_unavailable"
    assert json.loads(saved["provenance_json"])["weight"] == 0.0
    assert conn.execute(
        "SELECT estimated_cost,cost_status FROM estimated_chip_cost_daily"
    ).fetchone() == original


def test_cost_coverage_gate_and_request_reader_are_fail_closed_and_write_free() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_estimated_chip_cost_schema(conn)
    _corporate_action_table(conn)
    upsert_estimated_chip_cost_rows(conn, [_cost_row("2330")])
    first_seen = datetime(2026, 8, 28, 2, tzinfo=UTC)
    verified_at = first_seen + timedelta(hours=1)

    unverified = materialize_corporate_action_safe_cost_facts(
        conn,
        stock_codes=["2330"],
        observed_at=first_seen,
    )
    verified = materialize_corporate_action_safe_cost_facts(
        conn,
        stock_codes=["2330"],
        observed_at=verified_at,
        corporate_action_coverage_verified=True,
    )
    before_changes = conn.total_changes
    before_visible = read_corporate_action_safe_cost_facts(
        conn,
        stock_code="2330",
        target_date="2026-08-27",
        decision_at=first_seen - timedelta(seconds=1),
    )
    unverified_visible = read_corporate_action_safe_cost_facts(
        conn,
        stock_code="2330",
        target_date="2026-08-27",
        decision_at=first_seen,
    )
    verified_visible = read_corporate_action_safe_cost_facts(
        conn,
        stock_code="2330",
        target_date="2026-08-27",
        decision_at=verified_at,
    )

    assert conn.total_changes == before_changes
    assert unverified["status"] == "unavailable"
    assert unverified["suppressed_by_reason"] == {
        "corporate_action_coverage_unverified": 1
    }
    assert verified["status"] == "ok"
    assert verified["available_explanation_only"] == 1
    assert before_visible["status"] == "unavailable"
    assert unverified_visible["status"] == "unavailable"
    assert unverified_visible["facts"][0]["value"] is None
    assert verified_visible["status"] == "ok"
    assert verified_visible["facts"][0]["value"] == 101.25
    assert verified_visible["weight"] == 0.0
    assert verified_visible["referee_eligible"] is False
    assert verified_visible["request_time_computation"] is False
    assert verified_visible["request_time_writes"] == 0
