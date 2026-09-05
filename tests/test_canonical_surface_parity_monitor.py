from __future__ import annotations

import sqlite3
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.practical_status import PRACTICAL_STATUS_CORE_VERSION  # noqa: E402
from analysis.support_resistance import (  # noqa: E402
    SUPPORT_RESISTANCE_ASSEMBLER_VERSION,
)
from core.line_model_contract import (  # noqa: E402
    CONTEXT_PROFILES,
    build_model_fact_packet_v2,
)
from scripts import audit_canonical_surface_parity as audit_script  # noqa: E402
from services.canonical_surface_parity_service import (  # noqa: E402
    compare_surface_payloads,
    update_monitor_state,
)
from services.line_bot_service import _compact_daily  # noqa: E402


TRADE_DATE = "2026-08-27"


def _corporate_action() -> dict:
    return {
        "status": "active_window",
        "label": "近期除權息或公司行動，技術指標仍在調整期間",
        "action_date": "2026-09-02",
        "action_type": "right",
        "days_from_action": 1,
        "confirmed": True,
        "adjustment_method": "bonus_share_distribution",
        "stock_distribution_ratio": 1.9827946,
        "ratio_unit": "new_shares_per_existing_share",
        "cash_dividend_per_share": None,
        "share_count_factor": 2.9827946,
        "pre_event_price_multiplier": 0.335256071605,
        "verification_status": "official_verified",
        "available_at": "2026-09-03T10:38:26+08:00",
        "directional_weight_eligible": False,
    }


def _canonical_payload(
    *,
    technical_ready: bool = True,
    corporate_action: bool = False,
) -> dict:
    referee = {
        "decision_ready": technical_ready,
        "main_status": "可觀察" if technical_ready else "資料不足",
        "main_reasons": ["測試裁判理由"],
        "reason_code": None if technical_ready else "technical_not_ready",
        "source": "shared_project_referee",
        "version": PRACTICAL_STATUS_CORE_VERSION,
        "can_be_overridden_by_model": False,
    }
    if technical_ready:
        referee.update(
            {
                "input_assembler_version": SUPPORT_RESISTANCE_ASSEMBLER_VERSION,
                "support_zone": {"zone_low": 95.123, "zone_high": 98.456},
                "resistance_zone": {"zone_low": 105.123, "zone_high": 108.456},
            }
        )
    safety = {
        "status": "pass",
        "trade_date": TRADE_DATE,
        "hard_blocked": False,
        "auto_entry_eligible": True,
        "version": "recommendation-safety-v3",
    }
    if corporate_action:
        safety["corporate_action"] = _corporate_action()
    referee["recommendation_safety"] = dict(safety)
    technical = {
        "available": technical_ready,
        "status": "ok" if technical_ready else "insufficient_history",
        "decision_ready": technical_ready,
        "formula_version": "technical-indicators-v1",
        "input_row_count": 120 if technical_ready else 20,
        "input_end_date": TRADE_DATE,
        "rsi": {
            "rsi5": 41.2345 if technical_ready else None,
            "rsi10": 45.6789 if technical_ready else None,
            "rsi14": 49.8765 if technical_ready else None,
        },
        "moving_averages": {},
        "macd": {},
        "kd": {},
        "bollinger": {},
    }
    cost_item = {
        "value": 257.3611,
        "trade_date": TRADE_DATE,
        "status": "estimated",
        "calculation_state": "ready",
        "confidence": "medium",
        "sample_days": 65,
        "available": True,
        "contract_version": "canonical_cost_context_v1",
        "formula_version": "official_net_flow_incremental_inventory_v3",
        "can_override_main_status": False,
    }
    return {
        "status": "ready" if technical_ready else "insufficient_data",
        "reason": "測試",
        "analysis_contract_version": "canonical-close-batch-analysis-v1",
        "analysis_status": {
            "status": "ready" if technical_ready else "insufficient_data",
            "complete": technical_ready,
            "decision_ready": technical_ready,
            "main_status": referee["main_status"],
            "main_reasons": referee["main_reasons"],
            "reason_code": referee["reason_code"],
            "source": referee["source"],
            "version": referee["version"],
        },
        "microstructure_status": {
            "status": "volume_mismatch",
            "reason": "component excluded",
            "decision_ready": False,
        },
        "code": "2317",
        "trade_date": TRADE_DATE,
        "data_date": TRADE_DATE,
        "analysis_cutoff": "2026-09-03T10:38:26+08:00",
        "analysis_mode": "close_batch",
        "update_mode": "close_batch",
        "is_realtime": False,
        "freshness": {"status": "current", "ready": True},
        "ohlcv": {
            "date": TRADE_DATE,
            "close": 100.0,
            "volume_shares": 1_000_000,
            "official_trusted": True,
        },
        "technical": technical,
        "valuation": {"available": False, "status": "unavailable"},
        "referee": referee,
        "recommendation_safety": safety,
        "trading_state": {"status": "normal_trade"},
        "data_quality": {"decision_ready": False},
        "institutional_context": {
            "status": "ok",
            "trade_date": TRADE_DATE,
            "cost_contract_version": "canonical_cost_context_v1",
            "canonical_costs": {"trust_estimated": cost_item},
            "can_override_main_status": False,
        },
    }


def _web_payload(canonical: dict) -> dict:
    return {
        "analysis_contract_version": canonical["analysis_contract_version"],
        "data_date": canonical["trade_date"],
        "analysis_status": deepcopy(canonical["analysis_status"]),
        "microstructure_status": deepcopy(canonical["microstructure_status"]),
        "referee": deepcopy(canonical["referee"]),
        "canonical_technical": deepcopy(canonical["technical"]),
        "recommendation_safety": deepcopy(canonical.get("recommendation_safety")),
        "trading_state": deepcopy(canonical["trading_state"]),
        "institutional_context": deepcopy(canonical["institutional_context"]),
        "legacy_signal": None,
        "legacy_signal_disabled": True,
        "today_support": None,
        "today_resistance": None,
        "support_5d": None,
        "resistance_5d": None,
        "legacy_period_support_resistance_disabled": True,
    }


def test_ready_payload_matches_web_bot_and_line_with_line_rounding() -> None:
    canonical = _canonical_payload()
    result = compare_surface_payloads(
        code="2317",
        expected_trade_date=TRADE_DATE,
        bot_payload=canonical,
        web_payload=_web_payload(canonical),
        line_payload=_compact_daily(canonical),
    )

    assert result["passed"] is True
    assert result["mismatches"] == []
    assert result["rsi14"] == 49.8765
    assert result["line_rsi14"] == "49.88"


def test_corporate_action_fields_match_across_web_bot_and_line() -> None:
    canonical = _canonical_payload(corporate_action=True)
    line = _compact_daily(canonical)

    result = compare_surface_payloads(
        code="6669",
        expected_trade_date=TRADE_DATE,
        bot_payload=canonical,
        web_payload=_web_payload(canonical),
        line_payload=line,
    )

    assert result["passed"] is True
    assert result["mismatches"] == []
    assert line["display"]["recommendation_safety"]["corporate_action"] == (
        line["recommendation_safety"]["corporate_action"]
    )
    packet = build_model_fact_packet_v2(
        line,
        focus="overview",
        depth="focused",
        profile=CONTEXT_PROFILES["focused-16k-v1"],
        requested_scopes=["risk"],
    ).packet
    packet_fields = {
        fact["field"]: fact
        for fact in packet["facts"]
        if fact["domain"] == "recommendation_safety"
    }
    assert packet["request"]["analysis_cutoff"] == (
        "2026-09-03T10:38:26+08:00"
    )
    assert packet_fields["corporate_action.stock_distribution_ratio"]["value"] == (
        1.9827946
    )
    assert packet_fields["corporate_action.stock_distribution_ratio"]["as_of"] == (
        "2026-09-03T10:38:26+08:00"
    )


def test_corporate_action_ratio_mismatch_reports_exact_projection_path() -> None:
    canonical = _canonical_payload(corporate_action=True)
    web = _web_payload(canonical)
    web["recommendation_safety"]["corporate_action"][
        "stock_distribution_ratio"
    ] = 1.5

    result = compare_surface_payloads(
        code="6669",
        expected_trade_date=TRADE_DATE,
        bot_payload=canonical,
        web_payload=web,
        line_payload=_compact_daily(canonical),
    )

    assert result["passed"] is False
    mismatch = next(
        item
        for item in result["mismatches"]
        if item["surface"] == "web"
        and item["field"]
        == (
            "core.recommendation_safety.corporate_action."
            "stock_distribution_ratio"
        )
    )
    assert mismatch["expected"] == 1.9827946
    assert mismatch["actual"] == 1.5


def test_not_ready_payload_does_not_require_hidden_line_technical_fields() -> None:
    canonical = _canonical_payload(technical_ready=False)
    result = compare_surface_payloads(
        code="2317",
        expected_trade_date=TRADE_DATE,
        bot_payload=canonical,
        web_payload=_web_payload(canonical),
        line_payload=_compact_daily(canonical),
    )

    assert result["passed"] is True
    assert result["rsi14"] is None


def test_missing_safety_context_matches_line_unavailable_defaults() -> None:
    canonical = _canonical_payload(technical_ready=False)
    canonical.pop("recommendation_safety")
    canonical["referee"].pop("recommendation_safety")

    result = compare_surface_payloads(
        code="1538",
        expected_trade_date=TRADE_DATE,
        bot_payload=canonical,
        web_payload=_web_payload(canonical),
        line_payload=_compact_daily(canonical),
    )

    assert result["passed"] is True


def test_raw_web_rsi_mismatch_is_reported_with_exact_path() -> None:
    canonical = _canonical_payload()
    web = _web_payload(canonical)
    web["canonical_technical"]["rsi"]["rsi14"] = 10.0

    result = compare_surface_payloads(
        code="2317",
        expected_trade_date=TRADE_DATE,
        bot_payload=canonical,
        web_payload=web,
        line_payload=_compact_daily(canonical),
    )

    assert result["passed"] is False
    paths = {(item["surface"], item["field"]) for item in result["mismatches"]}
    assert ("web_raw", "rsi.rsi14") in paths
    assert ("web", "display_numbers.rsi.rsi14") in paths


def _observation(trade_date: str, status: str = "passed") -> dict:
    return {
        "trade_date": trade_date,
        "status": status,
        "scope": {"mode": "close_batch", "codes": ["2317"], "code_count": 1},
    }


def test_same_date_rerun_replaces_observation_without_incrementing_distinct_days() -> None:
    first = update_monitor_state(
        {},
        _observation("2026-08-27", "failed"),
        publication_dates=["2026-08-27"],
        generated_at="2026-08-28T01:00:00+08:00",
    )
    second = update_monitor_state(
        first,
        _observation("2026-08-27", "passed"),
        publication_dates=["2026-08-27"],
        generated_at="2026-08-28T02:00:00+08:00",
    )

    assert second["summary"]["observed_distinct_trade_dates"] == 1
    assert second["summary"]["consecutive_pass_count"] == 1
    assert second["observations"][0]["attempt_count"] == 2
    assert second["observations"][0]["ever_failed"] is True


def test_five_distinct_published_passes_reach_stable_state() -> None:
    dates = [f"2026-08-{day:02d}" for day in range(24, 29)]
    state: dict = {}
    for index, trade_date in enumerate(dates):
        state = update_monitor_state(
            state,
            _observation(trade_date),
            publication_dates=dates[: index + 1],
            generated_at=f"{trade_date}T18:00:00+08:00",
        )

    assert state["summary"]["stable"] is True
    assert state["summary"]["consecutive_pass_count"] == 5
    assert state["summary"]["remaining_consecutive_batches"] == 0


def test_missing_published_audit_breaks_consecutive_streak() -> None:
    state = update_monitor_state(
        {},
        _observation("2026-08-27"),
        publication_dates=["2026-08-27"],
        generated_at="2026-08-27T18:00:00+08:00",
    )
    state = update_monitor_state(
        state,
        _observation("2026-08-29"),
        publication_dates=["2026-08-27", "2026-08-28", "2026-08-29"],
        generated_at="2026-08-29T18:00:00+08:00",
    )

    assert state["summary"]["consecutive_pass_count"] == 1
    assert state["summary"]["missing_dates"] == ["2026-08-28"]


def _create_publication_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE full_market_batch_publications(
                trade_date TEXT PRIMARY KEY,run_id TEXT,published_at TEXT,
                contract_version TEXT,expected_active_asof INTEGER,
                classified_ohlcv_count INTEGER,official_no_trade_count INTEGER,
                not_applicable_count INTEGER,expected_universe_hash TEXT,
                classified_universe_hash TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO full_market_batch_publications VALUES(
                ?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                TRADE_DATE,
                "run-1",
                "2026-08-27T14:00:00+08:00",
                "full-market-batch-v1",
                1979,
                1970,
                9,
                0,
                "expected",
                "classified",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_runtime_audit_uses_publication_marker_and_does_not_mutate_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.db"
    state_path = tmp_path / "state.json"
    report_path = tmp_path / "report.json"
    _create_publication_database(database)
    original_bytes = database.read_bytes()
    canonical = _canonical_payload()
    web = _web_payload(canonical)
    line = _compact_daily(canonical)

    def runtime_loader(_database: Path):
        app_module = SimpleNamespace(
            _build_row_uncached=lambda *args, **kwargs: deepcopy(web)
        )

        def bot_builder(*args, **kwargs):
            return deepcopy(canonical)

        return app_module, bot_builder, lambda payload: deepcopy(line)

    exit_code, report = audit_script.run_audit(
        database=database,
        state_path=state_path,
        report_path=report_path,
        expected_trade_date=TRADE_DATE,
        requested_codes=["2317"],
        target_batches=5,
        runtime_loader=runtime_loader,
    )

    assert exit_code == audit_script.EXIT_SUCCESS
    assert report["status"] == "passed"
    assert report["codes_checked"] == 1
    assert report["consecutive_pass_count"] == 1
    assert report["stable"] is False
    assert report["database_writes"] is False
    assert report["source_database_changed_concurrently"] is False
    assert report["database_write_access_forced_read_only"] is True
    assert database.read_bytes() == original_bytes


def test_runtime_audit_rejects_nonmatching_publication_marker(tmp_path: Path) -> None:
    database = tmp_path / "market.db"
    _create_publication_database(database)

    exit_code, report = audit_script.run_audit(
        database=database,
        state_path=tmp_path / "state.json",
        report_path=tmp_path / "report.json",
        expected_trade_date="2026-08-28",
        requested_codes=["2317"],
        target_batches=5,
    )

    assert exit_code == audit_script.EXIT_SAFETY_MISMATCH
    assert report["status"] == "failed"
    assert report["publication_trade_date"] == TRADE_DATE
    assert not (tmp_path / "state.json").exists()


def test_runtime_guard_forces_plain_target_connections_to_read_only(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.db"
    _create_publication_database(database)

    with audit_script._force_target_database_read_only(database):
        conn = sqlite3.connect(database)
        try:
            try:
                conn.execute(
                    "DELETE FROM full_market_batch_publications WHERE trade_date=?",
                    (TRADE_DATE,),
                )
            except sqlite3.OperationalError as exc:
                assert "readonly" in str(exc).lower()
            else:
                raise AssertionError("target database write unexpectedly succeeded")
        finally:
            conn.close()
