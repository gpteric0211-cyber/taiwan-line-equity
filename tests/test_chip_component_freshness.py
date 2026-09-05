from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

import app  # noqa: E402
from analysis import technical  # noqa: E402
from core.data_quality import DataQualityStatus, assess_component_freshness  # noqa: E402


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


class ChipComponentFreshnessTests(unittest.TestCase):
    def test_public_cost_reader_uses_the_callers_exact_analysis_date(self) -> None:
        canonical = {
            "trade_date": None,
            "costs": {},
            "estimated_costs": [],
            "formula_version": "official_net_flow_incremental_inventory_v3",
        }
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            with (
                patch.object(app, "db", return_value=conn),
                patch.object(
                    app,
                    "get_canonical_cost_snapshot",
                    return_value=canonical,
                ) as canonical_reader,
                patch.object(app, "compute_poc60", None),
            ):
                result = app.calculate_public_chip_costs(
                    "2454",
                    as_of_date="2026-08-26",
                )
        finally:
            conn.close()

        canonical_reader.assert_called_once_with("2454", as_of_date="2026-08-26")
        self.assertEqual(result["formula_version"], canonical["formula_version"])

    def test_debug_cost_route_cannot_execute_legacy_cost_algorithms(self) -> None:
        snapshot = {
            "trade_date": "2026-08-27",
            "contract_version": "canonical_cost_context_v1",
            "formula_version": "official_net_flow_incremental_inventory_v3",
            "costs": {"foreign_estimated": {"value": None}},
            "estimated_costs": [],
            "poc60_estimate": {"value": 100.0, "is_institution_cost": False},
            "main_force_branch_cost": {"value": None, "status": "unavailable"},
        }
        with (
            patch.object(app, "calculate_public_chip_costs", return_value=snapshot),
            patch.object(app, "calc_cost") as legacy_window_cost,
            patch.object(app, "calc_wave_cost") as legacy_wave_cost,
            patch.object(app, "calc_main_force_cost") as legacy_main_cost,
            patch.object(app, "calc_volume_poc") as legacy_poc,
        ):
            result = app.api_debug_cost("2454")

        legacy_window_cost.assert_not_called()
        legacy_wave_cost.assert_not_called()
        legacy_main_cost.assert_not_called()
        legacy_poc.assert_not_called()
        self.assertEqual(result["costs"], snapshot["costs"])
        self.assertEqual(result["formula_version"], snapshot["formula_version"])
        self.assertNotIn("foreign_20d", result)
        self.assertNotIn("main_force_estimate", result)

    def test_component_freshness_uses_three_calendar_day_limit(self) -> None:
        friday_to_monday = assess_component_freshness("2026-08-14", "2026-08-17")
        four_days_old = assess_component_freshness("2026-08-13", "2026-08-17")
        missing = assess_component_freshness(None, "2026-08-17")

        self.assertTrue(friday_to_monday["ready"])
        self.assertEqual(friday_to_monday["lag_days"], 3)
        self.assertFalse(four_days_old["ready"])
        self.assertEqual(four_days_old["status"], DataQualityStatus.SOURCE_DELAYED.value)
        self.assertEqual(four_days_old["source_date"], "2026-08-13")
        self.assertEqual(four_days_old["as_of_date"], "2026-08-17")
        self.assertEqual(missing["status"], DataQualityStatus.MISSING.value)

    def test_component_states_does_not_treat_old_twenty_row_tables_as_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "freshness.sqlite3"
            conn = _connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE institution_daily(
                        date TEXT, code TEXT, foreign_net REAL, trust_net REAL,
                        dealer_net REAL, source TEXT, updated_at REAL
                    );
                    CREATE TABLE margin_daily(
                        date TEXT, code TEXT, margin_delta REAL, margin_balance REAL,
                        short_delta REAL, short_balance REAL, source TEXT, updated_at REAL
                    );
                    """
                )
                last_chip_date = date(2026, 6, 9)
                for offset in range(20):
                    trade_date = (last_chip_date - timedelta(days=offset)).isoformat()
                    conn.execute(
                        "INSERT INTO institution_daily VALUES(?,?,?,?,?,?,?)",
                        (trade_date, "2454", -100.0, 0.0, 0.0, "test", 1.0),
                    )
                    conn.execute(
                        "INSERT INTO margin_daily VALUES(?,?,?,?,?,?,?,?)",
                        (trade_date, "2454", 10.0, 1000.0, 0.0, 0.0, "test", 1.0),
                    )
                conn.commit()
            finally:
                conn.close()

            hist = [
                {"date": (date(2026, 8, 21) - timedelta(days=offset)).isoformat(), "close": 3790.0}
                for offset in range(30)
            ]
            with patch.object(technical, "db", side_effect=lambda: _connect(db_path)):
                states = technical.component_states(
                    "2454",
                    {"date": "2026-08-21", "close": 3790.0},
                    hist,
                )

        self.assertEqual(states["latest_k_date"], "2026-08-21")
        self.assertEqual(states["institution_date"], "2026-06-09")
        self.assertEqual(states["margin_date"], "2026-06-09")
        self.assertEqual(states["institution"], "inst_source_delayed")
        self.assertEqual(states["margin"], "margin_source_delayed")
        self.assertNotEqual(states["institution"], "inst_ok")
        self.assertNotEqual(states["margin"], "margin_ok")
        self.assertIn("2026-06-09", states["text"])

    def test_app_gate_marks_stale_chip_data_and_preserves_dates(self) -> None:
        inst_quality = assess_component_freshness("2026-06-09", "2026-08-21")
        margin_quality = assess_component_freshness("2026-06-09", "2026-08-21")
        gate = app._chip_component_gate(
            {
                "latest_k_date": "2026-08-21",
                "institution_date": "2026-06-09",
                "margin_date": "2026-06-09",
                "institution_freshness": inst_quality,
                "margin_freshness": margin_quality,
                "inst_count": 120,
                "margin_count": 120,
            }
        )

        self.assertFalse(gate["interpretation_ready"])
        self.assertFalse(gate["full_ready"])
        self.assertEqual(gate["quality_label"], "籌碼資料延遲")
        self.assertIn("法人資料延遲（2026-06-09；K線 2026-08-21）", gate["notice"])
        self.assertIn("融資資料延遲（2026-06-09；K線 2026-08-21）", gate["notice"])

        display = app._chip_list_display(gate, None, "安全", "法人偏多，融資未追高")
        self.assertEqual(display["light"], "警戒")
        self.assertNotIn("法人偏多", display["risk_summary"])
        self.assertEqual(display["risk_summary"], gate["notice"])
        self.assertEqual(display["action_reason"], gate["notice"])

    def test_support_resistance_excludes_stale_chip_costs_but_keeps_price_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "support-freshness.sqlite3"
            conn = _connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE institution_daily(date TEXT, code TEXT);
                    CREATE TABLE margin_daily(date TEXT, code TEXT);
                    INSERT INTO institution_daily VALUES('2026-06-09', '2454');
                    INSERT INTO margin_daily VALUES('2026-06-09', '2454');
                    """
                )
                conn.commit()
            finally:
                conn.close()

            rows = [
                {
                    "date": (date(2026, 7, 23) + timedelta(days=offset)).isoformat(),
                    "code": "2454",
                    "open": 90.0,
                    "high": 110.0,
                    "low": 80.0,
                    "close": 90.0,
                    "volume": 1000.0 + offset,
                }
                for offset in range(30)
            ]
            with (
                patch.object(app, "db", side_effect=lambda: _connect(db_path)),
                patch.object(app, "history_rows_asc", return_value=rows),
                patch.object(app, "calc_cost", return_value=(98.0, {"quality": "high"})) as calc_cost_mock,
                patch.object(app, "calc_main_force_cost", return_value=(98.0, {"quality": "high"})) as main_cost_mock,
            ):
                detail = app.calc_support_resistance_detail(
                    "2454",
                    100.0,
                    latest_k_date="2026-08-21",
                )

        calc_cost_mock.assert_not_called()
        main_cost_mock.assert_not_called()
        self.assertIsNotNone(detail["support"])
        self.assertIsNotNone(detail["resistance"])
        self.assertEqual(
            detail["component_freshness"]["institution"]["status"],
            "background_only",
        )
        self.assertEqual(
            detail["component_freshness"]["margin"]["status"],
            "background_only",
        )
        self.assertFalse(detail["component_freshness"]["institution"]["included_in_levels"])
        self.assertFalse(detail["component_freshness"]["margin"]["included_in_levels"])
        self.assertIn("法人與融資成本僅作背景", detail["method"])

    def test_support_resistance_gates_institution_and_margin_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "mixed-support-freshness.sqlite3"
            conn = _connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE institution_daily(date TEXT, code TEXT);
                    CREATE TABLE margin_daily(date TEXT, code TEXT);
                    INSERT INTO institution_daily VALUES('2026-08-21', '2454');
                    INSERT INTO margin_daily VALUES('2026-06-09', '2454');
                    """
                )
                conn.commit()
            finally:
                conn.close()

            rows = [
                {
                    "date": (date(2026, 7, 23) + timedelta(days=offset)).isoformat(),
                    "code": "2454",
                    "open": 90.0,
                    "high": 110.0,
                    "low": 80.0,
                    "close": 90.0,
                    "volume": 1000.0 + offset,
                }
                for offset in range(30)
            ]
            institution_levels: list[dict[str, object]] = []
            real_cluster_levels = app.cluster_levels

            def capture_institution_levels(levels: list[dict[str, object]], current: float) -> list[dict[str, object]]:
                institution_levels.extend(levels)
                return real_cluster_levels(levels, current)

            with (
                patch.object(app, "db", side_effect=lambda: _connect(db_path)),
                patch.object(app, "history_rows_asc", return_value=rows),
                patch.object(app, "calc_cost", return_value=(98.0, {"quality": "high"})) as calc_cost_mock,
                patch.object(app, "calc_main_force_cost", return_value=(97.0, {"quality": "high"})) as main_cost_mock,
                patch.object(app, "cluster_levels", side_effect=capture_institution_levels),
            ):
                detail = app.calc_support_resistance_detail(
                    "2454",
                    100.0,
                    latest_k_date="2026-08-21",
                )

        calc_cost_mock.assert_not_called()
        main_cost_mock.assert_not_called()
        self.assertFalse(detail["component_freshness"]["institution"]["ready"])
        self.assertFalse(detail["component_freshness"]["margin"]["ready"])
        method_sources = detail["method"].split("｜", 1)[0]
        self.assertNotIn("法人成本區", method_sources)
        self.assertNotIn("融資成本區", method_sources)
        fresh_institution_sources = [str(level.get("source") or "") for level in institution_levels]
        self.assertFalse(any("法人" in source or "外資" in source or "投信" in source for source in fresh_institution_sources))

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "reverse-mixed-support-freshness.sqlite3"
            conn = _connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE institution_daily(date TEXT, code TEXT);
                    CREATE TABLE margin_daily(date TEXT, code TEXT);
                    INSERT INTO institution_daily VALUES('2026-06-09', '2454');
                    INSERT INTO margin_daily VALUES('2026-08-21', '2454');
                    """
                )
                conn.commit()
            finally:
                conn.close()

            margin_levels: list[dict[str, object]] = []

            def capture_margin_levels(levels: list[dict[str, object]], current: float) -> list[dict[str, object]]:
                margin_levels.extend(levels)
                return real_cluster_levels(levels, current)

            with (
                patch.object(app, "db", side_effect=lambda: _connect(db_path)),
                patch.object(app, "history_rows_asc", return_value=rows),
                patch.object(app, "calc_cost", return_value=(102.0, {"quality": "high"})) as reverse_cost_mock,
                patch.object(app, "calc_main_force_cost") as reverse_main_cost_mock,
                patch.object(app, "cluster_levels", side_effect=capture_margin_levels),
            ):
                reverse_detail = app.calc_support_resistance_detail(
                    "2454",
                    100.0,
                    latest_k_date="2026-08-21",
                )

        reverse_cost_mock.assert_not_called()
        reverse_main_cost_mock.assert_not_called()
        self.assertFalse(reverse_detail["component_freshness"]["institution"]["ready"])
        self.assertFalse(reverse_detail["component_freshness"]["margin"]["ready"])
        reverse_method_sources = reverse_detail["method"].split("｜", 1)[0]
        self.assertNotIn("法人成本區", reverse_method_sources)
        self.assertNotIn("融資成本區", reverse_method_sources)
        fresh_margin_sources = [str(level.get("source") or "") for level in margin_levels]
        self.assertFalse(any("融資增加估算壓力" in source for source in fresh_margin_sources))

    def test_classification_excludes_stale_chip_direction_from_current_reasons(self) -> None:
        stale = assess_component_freshness("2026-06-09", "2026-08-21")
        context = {
            "date": "2026-08-21",
            "close": 100.0,
            "open": 100.0,
            "high": 101.0,
            "volume": 1000.0,
            "vol_ma20": 1000.0,
            "rsi10": 50.0,
            "rsi14": 50.0,
            "ma20": 100.0,
            "ma60": 99.0,
            "atr14": 2.0,
            "prev_10d_low": 95.0,
            "prev_close": 99.0,
            "osc": 0.1,
        }
        inst_info = {
            "date": "2026-06-09",
            "note": "外資投信同步賣超",
            "raw_note": "外資投信同步賣超",
            "data_quality": stale,
        }
        margin_info = {
            "date": "2026-06-09",
            "note": "融資變化 100",
            "margin_delta": 100.0,
            "data_quality": stale,
        }
        with (
            patch.object(app, "technical_context_from_rows", return_value=context),
            patch.object(app, "get_recent_corporate_action", return_value=None),
            patch.object(app, "possible_ex_gap_fallback", return_value=False),
            patch.object(app, "build_institution_info", return_value=inst_info),
            patch.object(app, "latest_margin_info", return_value=margin_info),
            patch.object(app, "build_valuation_tags", return_value=[]),
            patch.object(app, "_indicator_value_at", return_value=None),
            patch.object(app, "_classify_practical_status_core", return_value={"status": "中性", "reasons": ["技術中性"]}),
        ):
            result = app.classify_practical_status(
                "2454",
                [{"date": "2026-08-21"}],
                100.0,
                {},
                None,
                "2026-08-21",
                True,
            )

        self.assertEqual(result["main_reasons"], ["技術中性"])
        self.assertNotIn("外資投信同步賣超", result["main_reasons"])
        self.assertFalse(result["chip_components_current"])
        self.assertEqual(result["institution_date"], "2026-06-09")
        self.assertEqual(result["margin_date"], "2026-06-09")

    def test_action_hint_does_not_describe_stale_chip_direction_as_current(self) -> None:
        hint = app.build_action_hint(
            "偏多但不追價",
            {"state": "no_data"},
            {"state": "no_data"},
            "",
            {"chip_components_current": False, "main_reasons": []},
            sr_detail={},
            price=100.0,
            chip_reason="法人資料延遲（2026-06-09；K線 2026-08-21）",
            simple_sr={},
        )

        self.assertIn("不以舊籌碼確認當日方向", hint)
        self.assertNotIn("法人籌碼若呈現分歧，代表有人承接", hint)


if __name__ == "__main__":
    unittest.main()
