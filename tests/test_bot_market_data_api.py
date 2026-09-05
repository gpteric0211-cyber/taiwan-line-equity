from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from auth.bot_dependencies import require_bot_market_data_token  # noqa: E402
from analysis.support_resistance import SUPPORT_RESISTANCE_ASSEMBLER_VERSION  # noqa: E402
from bot_app import app as bot_app  # noqa: E402
from services.bot_market_data_service import (  # noqa: E402
    _shared_referee_payload,
    build_bot_daily_history,
    build_bot_daily_market_data,
    build_bot_intraday_trade_page,
    build_bot_market_brief,
    build_bot_stock_screen,
    resolve_bot_stock_query,
)
from services import line_bot_service  # noqa: E402


CODE = "2454"
TRADE_DATE = "2026-08-21"


class BotMarketDataServiceTest(unittest.TestCase):
    def test_market_brief_expands_chinese_us_iran_alias_to_english_official_event(self) -> None:
        snapshot = {
            "reference_date": "2026-08-29",
            "global_market_rows": [],
            "external_event_rows": [
                {
                    "event_key": "official-hormuz-update",
                    "event_date": "2026-08-29",
                    "published_at": "2026-08-29T08:00:00+08:00",
                    "source_id": "white-house",
                    "publisher": "The White House",
                    "source_url": "https://example.invalid/official-hormuz",
                    "source_class": "official_foreign_government",
                    "event_type": "government_policy",
                    "title": "Official update on Iran and the Strait of Hormuz",
                    "summary_excerpt": "Iran and global energy shipping were discussed.",
                    "direction": "unknown",
                    "confidence": "low",
                    "affected_terms": ["能源", "航運"],
                    "matched_stock_terms": [],
                    "mapping_method": "general_market_event",
                    "metrics": {},
                    "quality_status": "ok",
                    "source_quality": "official",
                    "reliability_score": 0.97,
                    "reference_value_score": 0.6,
                    "content_fingerprint": "official-hormuz-update",
                }
            ],
        }

        with patch(
            "services.bot_market_data_service.read_general_market_context",
            return_value=snapshot,
        ):
            result = build_bot_market_brief("美伊現況對今日股市有什麼影響")

        self.assertIn("Iran", result["topic_terms"])
        self.assertIn("Hormuz", result["topic_terms"])
        self.assertTrue(result["topic_match"])
        self.assertEqual(result["matched_events"][0]["publisher"], "The White House")

    def test_market_brief_is_dated_quality_gated_and_topic_aware(self) -> None:
        snapshot = {
            "reference_date": "2026-08-27",
            "global_market_rows": [],
            "external_event_rows": [
                {
                    "event_key": "official-semiconductor-policy",
                    "event_date": "2026-08-26",
                    "published_at": "2026-08-26T10:00:00+08:00",
                    "source_id": "moea",
                    "publisher": "經濟部",
                    "source_url": "https://example.invalid/official",
                    "source_class": "government",
                    "event_type": "government_policy",
                    "title": "半導體供應鏈政策說明",
                    "summary_excerpt": "說明半導體供應鏈政策方向。",
                    "direction": "unknown",
                    "confidence": "medium",
                    "affected_terms": ["半導體"],
                    "matched_stock_terms": [],
                    "mapping_method": "general_market_event",
                    "metrics": {},
                    "quality_status": "ok",
                    "source_quality": "official",
                    "reliability_score": 0.95,
                    "reference_value_score": 0.8,
                    "content_fingerprint": "official-semiconductor-policy",
                }
            ],
        }
        with patch(
            "services.bot_market_data_service.read_general_market_context",
            return_value=snapshot,
        ):
            matched = build_bot_market_brief("最近半導體政策有什麼影響")
            unmatched = build_bot_market_brief("川普這個消息會怎樣")

        self.assertTrue(matched["ok"])
        self.assertTrue(matched["topic_match"])
        self.assertEqual(matched["matched_events"][0]["publisher"], "經濟部")
        self.assertEqual(matched["matched_events"][0]["event_date"], "2026-08-26")
        self.assertFalse(matched["matched_events"][0]["can_override_main_status"])
        self.assertTrue(unmatched["topic_match_required"])
        self.assertFalse(unmatched["topic_match"])

    def test_hard_safety_veto_skips_the_technical_referee(self) -> None:
        result = _shared_referee_payload(
            snapshot={},
            history={},
            technical={},
            freshness={},
            recommendation_safety={
                "hard_blocked": True,
                "blocking_reasons": ["目前列入官方注意股票，排除自動進場判斷"],
            },
        )

        self.assertFalse(result["decision_ready"])
        self.assertEqual(result["main_status"], "不判斷")
        self.assertEqual(result["reason_code"], "recommendation_safety_hard_block")

    def test_one_sided_ohlcv_structure_is_partial_without_a_main_verdict(self) -> None:
        history_rows = [
            {
                "date": f"2026-06-{(index % 28) + 1:02d}",
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1_000_000.0,
                "source": "TWSE",
                "source_quality": "OFFICIAL",
            }
            for index in range(60)
        ]
        history_rows[-1]["date"] = "2026-08-28"
        recent = [
            {
                "date": date_value,
                "close": 100.0,
                "ma20": 99.0,
                "macd_osc": 0.5,
                "source": "TWSE",
                "source_quality": "OFFICIAL",
                "technical_decision_ready": True,
                "technical_data_quality": "ok",
            }
            for date_value in ("2026-08-28", "2026-08-27", "2026-08-26", "2026-08-25")
        ]
        support = {"side": "support", "zone_low": 95.0, "zone_high": 98.0}
        with (
            patch(
                "services.bot_market_data_service.build_ohlcv_support_resistance_levels",
                return_value=[support],
            ),
            patch(
                "services.bot_market_data_service.cluster_levels",
                side_effect=lambda levels, current: list(levels),
            ),
        ):
            result = _shared_referee_payload(
                snapshot={
                    "selected_date": "2026-08-28",
                    "recent_referee_context": recent,
                    "referee_history": history_rows,
                    "referee_component_dates": {},
                },
                history={"close": 100.0, "volume": 1_000_000.0, "source": "TWSE", "source_quality": "OFFICIAL"},
                technical={"decision_ready": True},
                freshness={"ready": True},
            )

        self.assertFalse(result["decision_ready"])
        self.assertTrue(result["partial_analysis_available"])
        self.assertEqual(result["reason_code"], "partial_support_resistance")
        self.assertEqual(result["main_status"], "資料部分可用")
        self.assertEqual(result["support_zone"], support)
        self.assertIsNone(result["resistance_zone"])
        self.assertFalse(result["can_be_overridden_by_model"])

    def test_stock_screen_requires_shared_referee_and_advisory(self) -> None:
        prefilter = {
            "trade_date": TRADE_DATE,
            "rows": [
                {
                    "code": CODE,
                    "name": "聯發科",
                    "close": 3765,
                    "volume": 1_000_000,
                    "turnover_value": 3_765_000_000,
                    "ma20": 3700,
                    "ma60": 3500,
                    "rsi14": 55,
                    "macd_osc": 2,
                    "previous_macd_osc": 1,
                    "volume_ma20": 900_000,
                }
            ],
        }
        daily = {
            "ohlcv": {"close": 3765},
            "stock": {"name": "聯發科"},
            "recommendation_safety": {
                "status": "pass",
                "auto_entry_eligible": True,
                "liquidity": {"average_turnover_twd": 3_765_000_000},
            },
            "referee": {
                "decision_ready": True,
                "main_status": "可觀察",
                "main_reasons": ["均線結構偏多，走勢維持穩定"],
            },
            "advisory": {
                "decision_ready": True,
                "action_state": "可小比例分批觀察",
                "headline": "可以列入分批觀察，但目前不是非買不可的位置。",
                "buy_plan": "第一批宜小，保留資金等待支撐回測。",
                "support": {"label": "3700～3720"},
                "resistance": {"label": "3850～3900"},
            },
        }
        with (
            patch(
                "services.bot_market_data_service.read_screening_prefilter_rows",
                return_value=prefilter,
            ),
            patch(
                "services.bot_market_data_service.build_canonical_close_batch_snapshot",
                return_value=daily,
            ) as build_daily,
        ):
            result = build_bot_stock_screen(strategy="balanced", limit=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["trade_date"], TRADE_DATE)
        self.assertEqual(result["candidates"][0]["code"], CODE)
        self.assertFalse(result["candidates"][0]["can_override_main_status"])
        self.assertFalse(result["quality_contract"]["model_generated_symbols_allowed"])
        build_daily.assert_called_once_with(
            CODE,
            trade_date=TRADE_DATE,
            include_levels=False,
            level_limit=1,
            allow_live_quote_fetch=False,
        )

    def test_bottom_screen_requires_backend_low_zone_confirmation(self) -> None:
        prefilter = {
            "trade_date": TRADE_DATE,
            "rows": [
                {
                    "code": "3481",
                    "name": "群創",
                    "close": 46.8,
                    "volume": 186_000_000,
                    "turnover_value": 8_700_000_000,
                    "ma20": 47.6,
                    "ma60": 54.5,
                    "rsi14": 43.7,
                    "previous_rsi14": 40.1,
                    "macd_osc": -0.01,
                    "previous_macd_osc": -0.05,
                    "volume_ma20": 282_000_000,
                }
            ],
        }
        daily = {
            "ohlcv": {"close": 46.8},
            "technical": {"rsi": {"rsi14": 43.7}},
            "stock": {"name": "群創"},
            "recommendation_safety": {
                "status": "pass",
                "auto_entry_eligible": True,
                "liquidity": {"average_turnover_twd": 8_700_000_000},
            },
            "referee": {
                "decision_ready": True,
                "main_status": "警戒",
                "main_reasons": ["股價位於月線下方"],
            },
            "advisory": {
                "decision_ready": True,
                "action_state": "低檔止跌，可條件式第一批",
                "headline": "低檔止跌條件已通過。",
                "buy_plan": "第一批只在支撐續守時小比例評估。",
                "invalidation": "收盤跌破 44.45 即取消後續批次。",
                "support": {"label": "44.45～46.5"},
                "resistance": {"label": "47.45～51"},
                "low_zone_assessment": {
                    "stage": "batch_entry_ready",
                    "stage_label": "初步止跌，可條件式第一批",
                    "summary": "RSI14 43.7 回升，支撐守住且價格止穩。",
                    "rsi_direction": "較前一日回升",
                    "confirmation_count": 3,
                    "reward_risk_ratio": 1.79,
                    "batch_entry_eligible": True,
                    "can_override_main_status": False,
                },
            },
        }
        with (
            patch(
                "services.bot_market_data_service.read_screening_prefilter_rows",
                return_value=prefilter,
            ),
            patch(
                "services.bot_market_data_service.build_canonical_close_batch_snapshot",
                return_value=daily,
            ),
        ):
            result = build_bot_stock_screen(strategy="bottom", limit=5)

        self.assertTrue(result["ok"])
        candidate = result["candidates"][0]
        self.assertEqual(candidate["code"], "3481")
        self.assertEqual(candidate["rsi14"], 43.7)
        self.assertEqual(candidate["low_zone_stage"], "batch_entry_ready")
        self.assertTrue(candidate["batch_entry_eligible"])
        self.assertFalse(candidate["can_override_main_status"])
        self.assertTrue(result["quality_contract"]["low_zone_entry_required"])

    def test_bottom_screen_rejects_low_rsi_without_confirmation(self) -> None:
        prefilter = {
            "trade_date": TRADE_DATE,
            "rows": [
                {
                    "code": CODE,
                    "close": 3_700,
                    "volume": 1_000_000,
                    "turnover_value": 3_700_000_000,
                    "ma20": 3_800,
                    "ma60": 4_000,
                    "rsi14": 28,
                    "previous_rsi14": 30,
                    "macd_osc": -2,
                    "previous_macd_osc": -1,
                    "volume_ma20": 1_000_000,
                }
            ],
        }
        daily = {
            "ohlcv": {"close": 3_700},
            "technical": {"rsi": {"rsi14": 28}},
            "stock": {"name": "聯發科"},
            "referee": {"decision_ready": True, "main_status": "警戒", "main_reasons": ["弱勢"]},
            "advisory": {
                "decision_ready": True,
                "action_state": "低檔尚未止跌，等待確認",
                "support": {"label": "3600～3700"},
                "resistance": {"label": "3900～4000"},
                "low_zone_assessment": {
                    "batch_entry_eligible": False,
                    "stage": "extreme_oversold_wait",
                },
            },
        }
        with (
            patch(
                "services.bot_market_data_service.read_screening_prefilter_rows",
                return_value=prefilter,
            ),
            patch(
                "services.bot_market_data_service.build_canonical_close_batch_snapshot",
                return_value=daily,
            ),
        ):
            result = build_bot_stock_screen(strategy="bottom", limit=5)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "no_qualified_candidates")
        self.assertEqual(result["candidates"], [])

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "bot_market_data.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE history_price(
                    date TEXT, code TEXT, open REAL, high REAL, low REAL, close REAL,
                    volume REAL, source TEXT, source_quality TEXT,
                    PRIMARY KEY(date,code)
                );
                CREATE TABLE full_market_batch_publications(
                    trade_date TEXT PRIMARY KEY
                );
                CREATE TABLE price_volume_distribution(
                    stock_id TEXT, trade_date TEXT, price REAL, volume_lots INTEGER,
                    volume_shares INTEGER, total_volume_lots INTEGER,
                    volume_at_bid INTEGER, volume_at_ask INTEGER,
                    neutral_volume_lots INTEGER, source TEXT, data_quality TEXT,
                    source_quality TEXT, snapshot_time TEXT, fetched_at REAL,
                    UNIQUE(stock_id,trade_date,price)
                );
                CREATE TABLE price_volume_profile_daily(
                    date TEXT, code TEXT, source_name TEXT, source_hash TEXT,
                    quality TEXT, trade_scope TEXT, total_volume_shares REAL,
                    eod_volume_shares REAL, volume_diff_pct REAL,
                    PRIMARY KEY(date,code)
                );
                CREATE TABLE price_volume_score_daily(
                    date TEXT, code TEXT, status TEXT, quality TEXT,
                    coverage_days INTEGER, required_days INTEGER,
                    grade TEXT, total_score INTEGER, PRIMARY KEY(date,code)
                );
                CREATE TABLE fugle_intraday_trades(
                    code TEXT, trade_date TEXT, trade_time TEXT, price REAL,
                    size INTEGER, volume INTEGER, bid REAL, ask REAL, serial TEXT,
                    data_quality TEXT, side_inferred TEXT, side_label_zh TEXT,
                    side_method TEXT, side_confidence TEXT, prev_price REAL,
                    prev_price_source TEXT
                );
                CREATE TABLE fugle_intraday_capture_runs(
                    code TEXT,trade_date TEXT,endpoint TEXT,source TEXT,
                    snapshot_time TEXT,page_count INTEGER,provider_row_count INTEGER,
                    normalized_row_count INTEGER,stored_row_count INTEGER,
                    capture_complete INTEGER,data_quality TEXT,reason TEXT,
                    latest_trade_time TEXT,latest_cumulative_volume INTEGER,
                    captured_volume_lots INTEGER,fetched_at REAL,
                    PRIMARY KEY(code,trade_date,endpoint,source)
                );
                CREATE TABLE stock_master(
                    code TEXT PRIMARY KEY,name TEXT,market TEXT,exchange TEXT,
                    security_type TEXT,is_active INTEGER,source TEXT,
                    source_status TEXT,last_seen_date TEXT
                );
                CREATE TABLE daily_technical_snapshot(
                    trade_date TEXT,code TEXT,formula_version TEXT,input_row_count INTEGER,
                    rsi5 REAL,rsi10 REAL,rsi14 REAL,ma5 REAL,ma10 REAL,ma20 REAL,ma60 REAL,
                    macd_dif REAL,macd_signal REAL,macd_osc REAL,kd_k REAL,kd_d REAL,
                    atr14 REAL,boll_mid REAL,boll_upper REAL,boll_lower REAL,obv REAL,
                    volume_ma20 REAL,data_quality TEXT,decision_ready INTEGER,
                    quality_reason TEXT,PRIMARY KEY(trade_date,code)
                );
                CREATE TABLE twse_daily_valuation(
                    data_date TEXT,symbol TEXT,close_price REAL,dividend_yield REAL,
                    pe_ratio REAL,pb_ratio REAL,source TEXT,source_status TEXT,
                    PRIMARY KEY(data_date,symbol)
                );
                """
            )
            conn.execute(
                "INSERT INTO history_price VALUES(?,?,?,?,?,?,?,?,?)",
                (TRADE_DATE, CODE, 99, 102, 98, 100, 110_000, "TWSE STOCK_DAY", "OFFICIAL"),
            )
            conn.execute(
                "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
                (TRADE_DATE,),
            )
            levels = [
                (98.0, 20, 15, 4, 1),
                (99.0, 40, 10, 28, 2),
                (100.0, 10, 4, 5, 1),
                (101.0, 35, 25, 8, 2),
                (102.0, 5, 3, 1, 1),
            ]
            for price, lots, inner, outer, neutral in levels:
                conn.execute(
                    """
                    INSERT INTO price_volume_distribution VALUES(
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        CODE, TRADE_DATE, price, lots, lots * 1000, 110,
                        inner, outer, neutral, "FUGLE", "VALIDATED", "VALIDATED",
                        "2026-08-21 13:35:00", 1_787_307_300,
                    ),
                )
            conn.execute(
                """
                INSERT INTO price_volume_profile_daily(
                    date,code,source_name,source_hash,quality
                ) VALUES(?,?,?,?,?)
                """,
                (TRADE_DATE, CODE, "Fugle intraday volumes", "hash", "high"),
            )
            conn.execute(
                "INSERT INTO price_volume_score_daily VALUES(?,?,?,?,?,?,?,?)",
                (TRADE_DATE, CODE, "ok", "high", 24, 30, "B", 70),
            )
            conn.execute(
                "INSERT INTO fugle_intraday_trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    CODE, TRADE_DATE, "1787271000000000", 100, 2, 110,
                    99.5, 100, "1", "OK", "ASK", "外盤",
                    "PRICE_VS_BID_ASK", "HIGH", None, "NOT_USED",
                ),
            )
            conn.execute(
                "INSERT INTO stock_master VALUES(?,?,?,?,?,?,?,?,?)",
                (CODE, "聯發科", "listed", "TWSE", "stock", 1, "TWSE_COMPANY_OPENAPI", "ok", TRADE_DATE),
            )
            conn.execute(
                "INSERT INTO daily_technical_snapshot VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    TRADE_DATE, CODE, "scoring-v1.0.4-asof-260", 130,
                    38.8, 45.8, 47.2, 99, 98, 97, 96,
                    1.2, 1.0, 0.2, 55, 52, 3.1, 97, 103, 91,
                    123456, 90000, "ok", 1, "verified",
                ),
            )
            conn.execute(
                "INSERT INTO twse_daily_valuation VALUES(?,?,?,?,?,?,?,?)",
                (TRADE_DATE, CODE, 100, 2.5, 18.2, 3.4, "TWSE_BWIBBU", "ok"),
            )
            conn.commit()

        def connect_read_only() -> sqlite3.Connection:
            conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            return conn

        patcher = patch(
            "repository.market_microstructure_repository.read_only_db",
            side_effect=connect_read_only,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        date_patcher = patch(
            "services.bot_market_data_service.recent_market_date_for_eod",
            return_value=TRADE_DATE,
        )
        date_patcher.start()
        self.addCleanup(date_patcher.stop)

    def test_validated_daily_distribution_exposes_flow_and_support_pressure(self) -> None:
        result = build_bot_daily_market_data(CODE)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["captured_volume_lots"], 110)
        self.assertEqual(result["flow_summary"]["inner_lots"], 57)
        self.assertEqual(result["flow_summary"]["outer_lots"], 46)
        self.assertFalse(result["flow_summary"]["is_institutional_net_buy_sell"])
        self.assertTrue(result["support_pressure"]["available"])
        self.assertEqual(result["support_pressure"]["support_zone"]["price"], 99.0)
        self.assertEqual(result["support_pressure"]["pressure_zone"]["price"], 101.0)
        self.assertFalse(result["multi_day_score"]["referee_eligible"])
        self.assertEqual(
            result["multi_day_score"]["status"],
            "not_enabled_pending_trading_date_coverage",
        )

    def test_default_close_batch_ignores_newer_partial_stock_day_end_to_end(self) -> None:
        partial_date = "2026-08-22"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO history_price VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    partial_date,
                    CODE,
                    100,
                    110,
                    99,
                    109,
                    999_000,
                    "TWSE STOCK_DAY",
                    "OFFICIAL",
                ),
            )
            conn.commit()

        daily = build_bot_daily_market_data(CODE)
        history = build_bot_daily_history(CODE, limit=3)
        line_facts = line_bot_service._compact_daily(daily)

        self.assertEqual(daily["trade_date"], TRADE_DATE)
        self.assertEqual(daily["ohlcv"]["date"], TRADE_DATE)
        self.assertEqual(daily["freshness"]["status"], "current")
        self.assertEqual(
            daily["freshness"]["expected_latest_trade_date"],
            TRADE_DATE,
        )
        self.assertIn(
            daily["freshness"]["publication_status"],
            {"current", "source_delayed"},
        )
        self.assertEqual(history["items"][0]["trade_date"], TRADE_DATE)
        self.assertEqual(line_facts["trade_date"], TRADE_DATE)

        explicit = build_bot_daily_market_data(CODE, trade_date=partial_date)
        self.assertEqual(explicit["trade_date"], partial_date)
        self.assertEqual(explicit["ohlcv"]["date"], partial_date)

    def test_close_batch_is_default_and_does_not_require_an_intraday_quote(self) -> None:
        ready_referee = {
            "decision_ready": True,
            "main_status": "中性",
            "main_reasons": ["測試用盤後裁判理由"],
            "support_zone": {"zone_low": 95.0, "zone_high": 99.0, "strength": "強"},
            "resistance_zone": {"zone_low": 101.0, "zone_high": 105.0, "strength": "中"},
            "can_be_overridden_by_model": False,
        }
        with (
            patch(
                "services.bot_market_data_service._shared_referee_payload",
                return_value=ready_referee,
            ),
            patch("services.bot_market_data_service._current_intraday_quote") as intraday,
        ):
            result = build_bot_daily_market_data(CODE)

        intraday.assert_not_called()
        self.assertEqual(result["analysis_mode"], "close_batch")
        self.assertEqual(result["update_mode"], "close_batch")
        self.assertFalse(result["is_realtime"])
        self.assertEqual(result["intraday_quote"]["status"], "not_used_for_close_batch")
        self.assertFalse(result["intraday_quote"]["required"])
        self.assertTrue(result["referee"]["decision_ready"])
        self.assertTrue(result["advisory"]["decision_ready"])
        self.assertEqual(result["advisory"]["price_basis"], "completed_close")
        self.assertEqual(result["decision_audit"]["price_basis"], "completed_close")
        self.assertNotIn("目前資料不足", result["advisory"]["headline"])

    def test_explicit_intraday_mode_keeps_the_fresh_quote_gate(self) -> None:
        ready_referee = {
            "decision_ready": True,
            "main_status": "中性",
            "main_reasons": ["測試用盤後裁判理由"],
            "support_zone": {"zone_low": 95.0, "zone_high": 99.0, "strength": "強"},
            "resistance_zone": {"zone_low": 101.0, "zone_high": 105.0, "strength": "中"},
            "can_be_overridden_by_model": False,
        }
        unavailable_quote = {
            "available": False,
            "required": True,
            "status": "source_delayed",
            "reason": "current-session quote is not fresh enough for an intraday decision",
        }
        with (
            patch(
                "services.bot_market_data_service._shared_referee_payload",
                return_value=ready_referee,
            ),
            patch(
                "services.bot_market_data_service._current_intraday_quote",
                return_value=unavailable_quote,
            ) as intraday,
        ):
            result = build_bot_daily_market_data(
                CODE,
                analysis_mode="intraday",
                allow_live_quote_fetch=True,
            )

        intraday.assert_called_once()
        self.assertEqual(result["analysis_mode"], "intraday")
        self.assertFalse(result["is_realtime"])
        self.assertTrue(result["referee"]["decision_ready"])
        self.assertFalse(result["advisory"]["decision_ready"])
        self.assertEqual(result["advisory"]["referee_status"], "中性")
        self.assertEqual(result["advisory"]["reason_code"], "intraday_price_not_ready")
        self.assertEqual(result["advisory"]["price_basis"], "unavailable_current_session")
        self.assertEqual(result["decision_audit"]["price_basis"], "unavailable_current_session")

    def test_provisional_distribution_keeps_levels_but_hides_decision(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE price_volume_distribution SET data_quality='INTRADAY_SNAPSHOT',source_quality='INTRADAY_SNAPSHOT'"
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "source_delayed")
        self.assertEqual(len(result["price_levels"]), 5)
        self.assertFalse(result["support_pressure"]["available"])
        self.assertIsNone(result["support_pressure"]["poc_price"])

    def test_regular_session_profile_may_be_below_official_all_session_volume(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE history_price SET volume=114000 WHERE code=? AND date=?",
                (CODE, TRADE_DATE),
            )
            conn.execute(
                """
                UPDATE price_volume_profile_daily
                SET trade_scope='regular_intraday',total_volume_shares=110000,
                    eod_volume_shares=114000,volume_diff_pct=3.5088
                WHERE code=? AND date=?
                """,
                (CODE, TRADE_DATE),
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data_quality"]["volume_matches_official"])
        self.assertTrue(result["data_quality"]["official_scope_compatible"])
        self.assertTrue(result["data_quality"]["profile_reconciliation"]["ready"])

    def test_materially_incomplete_profile_cannot_enter_referee(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE history_price SET volume=150000 WHERE code=? AND date=?",
                (CODE, TRADE_DATE),
            )
            conn.execute(
                """
                UPDATE price_volume_profile_daily
                SET trade_scope='regular_intraday',total_volume_shares=110000,
                    eod_volume_shares=150000,volume_diff_pct=26.6667
                WHERE code=? AND date=?
                """,
                (CODE, TRADE_DATE),
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "volume_mismatch")
        self.assertFalse(result["data_quality"]["official_scope_compatible"])
        self.assertIn(
            "profile_official_volume_coverage_below_threshold",
            result["data_quality"]["profile_reconciliation"]["reasons"],
        )
        self.assertFalse(result["support_pressure"]["available"])

    def test_missing_distribution_still_returns_exact_date_official_ohlcv(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM price_volume_distribution WHERE stock_id=?", (CODE,))
            conn.commit()

        result = build_bot_daily_market_data(CODE, trade_date=TRADE_DATE)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "source_delayed")
        self.assertEqual(result["ohlcv"]["date"], TRADE_DATE)
        self.assertEqual(result["ohlcv"]["close"], 100.0)
        self.assertTrue(result["ohlcv"]["official_trusted"])
        self.assertFalse(result["data_quality"]["decision_ready"])

    def test_official_halt_returns_zero_volume_state_instead_of_missing_data(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM history_price WHERE code=? AND date=?", (CODE, TRADE_DATE))
            conn.execute(
                """
                CREATE TABLE stock_no_trade_dates(
                    trade_date TEXT NOT NULL,code TEXT NOT NULL,market TEXT NOT NULL,
                    reason TEXT NOT NULL,source TEXT NOT NULL,source_url TEXT,
                    source_quality TEXT,evidence_json TEXT,verified_at REAL,
                    PRIMARY KEY(trade_date,code)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO stock_no_trade_dates VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    TRADE_DATE,
                    CODE,
                    "listed",
                    "official_daily_report_absent_official_trading_halt",
                    "TWSE MI_INDEX",
                    "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                    "official",
                    '{"reported_volume":0}',
                    1.0,
                ),
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE, trade_date=TRADE_DATE)
        facts = line_bot_service._compact_daily(result)

        self.assertEqual(result["status"], "trading_halt")
        self.assertEqual(result["trading_state"]["volume_shares"], 0)
        self.assertEqual(result["referee"]["main_status"], "不判斷")
        self.assertEqual(facts["referee"]["main_status"], "不判斷")
        self.assertIn("成交量（股）：0", facts["verified_claims"][0])
        rendered = line_bot_service._fallback_daily(facts)
        self.assertIn("官方成交量為 0 股", rendered)
        self.assertIn(f"{TRADE_DATE} 是「官方暫停交易」", rendered)
        self.assertIn("這不是資料抓取失敗", rendered)

    def test_inactive_official_stock_is_not_reported_as_generic_missing_data(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("DELETE FROM history_price WHERE code=?", (CODE,))
            conn.execute("UPDATE stock_master SET is_active=0 WHERE code=?", (CODE,))
            conn.commit()

        result = build_bot_daily_market_data(CODE, trade_date=TRADE_DATE)
        facts = line_bot_service._compact_daily(result)
        rendered = line_bot_service._fallback_daily(facts)

        self.assertEqual(result["status"], "inactive_official_universe")
        self.assertEqual(result["referee"]["main_status"], "不判斷")
        self.assertIn("不在官方有效交易清單", rendered)
        self.assertIn("成交量：不適用", rendered)
        self.assertNotIn("目前資料不足", rendered)

    def test_mixed_source_distribution_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE price_volume_distribution SET source='OTHER' WHERE stock_id=? AND price=102",
                (CODE,),
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "source_mismatch")
        self.assertFalse(result["support_pressure"]["available"])

    def test_missing_bid_ask_is_not_reported_as_balanced_zero(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE price_volume_distribution SET volume_at_bid=NULL,volume_at_ask=NULL WHERE stock_id=? AND price=102",
                (CODE,),
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE)

        self.assertTrue(result["ok"])
        self.assertFalse(result["flow_summary"]["direction_available"])
        self.assertIsNone(result["flow_summary"]["inner_lots"])
        self.assertIsNone(result["flow_summary"]["net_active_lots"])
        missing = next(item for item in result["price_levels"] if item["price"] == 102.0)
        self.assertFalse(missing["direction_available"])
        self.assertIsNone(missing["inner_lots"])

    def test_delayed_capture_requires_persisted_complete_session_evidence(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE price_volume_distribution SET snapshot_time='2026-08-22 15:47:18'"
            )
            conn.execute(
                """
                INSERT INTO fugle_intraday_capture_runs VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    CODE, TRADE_DATE, "trades", "FUGLE", "2026-08-22 15:47:16",
                    2, 110, 110, 110, 1, "SESSION_COMPLETE", "",
                    "14:30:00.000000", 110, 110, 1_787_384_832,
                ),
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data_quality"]["capture_mode"], "delayed_full_session")

    def test_delayed_snapshot_without_complete_session_evidence_is_rejected(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE price_volume_distribution SET snapshot_time='2026-08-22 15:47:18'"
            )
            conn.commit()

        result = build_bot_daily_market_data(CODE)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            result["data_quality"]["snapshot_quality"]["capture_mode"],
            "rejected",
        )

    def test_trade_page_marks_legacy_capture_completeness_unverified(self) -> None:
        result = build_bot_intraday_trade_page(CODE, limit=20)

        self.assertTrue(result["ok"])
        self.assertEqual(result["capture_completeness"], "unverified")
        self.assertTrue(result["side_is_estimated"])
        self.assertEqual(result["items"][0]["inferred_side"], "ASK")
        self.assertRegex(result["items"][0]["trade_time"], r"^2026-08-21 \d{2}:\d{2}:\d{2}")

    def test_read_only_service_does_not_change_database_file(self) -> None:
        before_hash = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        before_mtime = self.db_path.stat().st_mtime_ns

        build_bot_daily_market_data(CODE)
        build_bot_intraday_trade_page(CODE)

        self.assertEqual(hashlib.sha256(self.db_path.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(self.db_path.stat().st_mtime_ns, before_mtime)

    def test_daily_and_history_include_same_date_technical_and_valuation(self) -> None:
        daily = build_bot_daily_market_data(CODE, trade_date=TRADE_DATE)
        history = build_bot_daily_history(CODE, limit=20)

        self.assertEqual(daily["technical"]["rsi"]["rsi5"], 38.8)
        self.assertEqual(daily["technical"]["macd"]["dif"], 1.2)
        self.assertEqual(daily["valuation"]["pe_ratio"], 18.2)
        self.assertTrue(history["ok"])
        self.assertEqual(history["date_order"], "descending")
        self.assertEqual(history["items"][0]["trade_date"], TRADE_DATE)
        self.assertEqual(history["items"][0]["technical"]["rsi"]["rsi10"], 45.8)

    def test_invalid_code_and_date_fail_closed(self) -> None:
        self.assertEqual(build_bot_daily_market_data("245")['status'], "invalid_request")
        self.assertEqual(
            build_bot_daily_market_data(CODE, trade_date="2026-02-30")["status"],
            "invalid_request",
        )
        self.assertEqual(
            build_bot_daily_market_data(CODE, analysis_mode="unknown")["status"],
            "invalid_request",
        )
        self.assertEqual(
            build_bot_daily_market_data(
                CODE,
                trade_date=TRADE_DATE,
                analysis_mode="intraday",
            )["status"],
            "invalid_request",
        )


class BotMarketDataAuthTest(unittest.TestCase):
    def test_token_must_be_configured_and_match(self) -> None:
        valid_token = "correct-token-with-at-least-32-bytes-123456"
        credential = HTTPAuthorizationCredentials(scheme="Bearer", credentials=valid_token)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOT_MARKET_DATA_TOKEN", None)
            with self.assertRaises(HTTPException) as missing:
                require_bot_market_data_token(credential)
            self.assertEqual(missing.exception.status_code, 503)

        with patch.dict(os.environ, {"BOT_MARKET_DATA_TOKEN": valid_token}, clear=False):
            wrong = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
            with self.assertRaises(HTTPException) as rejected:
                require_bot_market_data_token(wrong)
            self.assertEqual(rejected.exception.status_code, 401)
            self.assertTrue(require_bot_market_data_token(credential))

    def test_short_or_placeholder_token_is_misconfigured(self) -> None:
        credential = HTTPAuthorizationCredentials(scheme="Bearer", credentials="change-me")
        with patch.dict(os.environ, {"BOT_MARKET_DATA_TOKEN": "change-me"}, clear=False):
            with self.assertRaises(HTTPException) as rejected:
                require_bot_market_data_token(credential)
        self.assertEqual(rejected.exception.status_code, 503)

    def test_bot_routes_live_only_on_minimal_read_only_app(self) -> None:
        route_paths = {getattr(route, "path", "") for route in bot_app.routes}
        self.assertIn("/api/bot/market-data/{code}/daily", route_paths)
        self.assertIn("/api/bot/market-data/{code}/history", route_paths)
        self.assertIn("/api/bot/market-data/{code}/trades", route_paths)
        self.assertIn("/api/bot/market-data/market-brief", route_paths)
        self.assertIn("/api/bot/market-data/analysis", route_paths)
        self.assertIn("/api/bot/market-data/analysis/model-packet", route_paths)
        self.assertIn("/api/bot/market-data/analysis/model-answer", route_paths)
        self.assertNotIn("/api/update/eod", route_paths)


class BotStockResolverTest(unittest.TestCase):
    MASTER_ROWS = [
        {"code": "2330", "name": "台積電", "trading_name": "台積電", "market": "listed", "exchange": "TWSE"},
        {"code": "2331", "name": "精英", "trading_name": "精英", "market": "listed", "exchange": "TWSE"},
        {"code": "2327", "name": "國巨*", "trading_name": "國巨*", "market": "listed", "exchange": "TWSE"},
        {"code": "2449", "name": "京元電子", "trading_name": "京元電子", "market": "listed", "exchange": "TWSE"},
        {"code": "6261", "name": "久元電子股份有限公司", "trading_name": "久元", "market": "otc", "exchange": "TPEX"},
        {"code": "6643", "name": "圓星科技股份有限公司", "trading_name": "M31", "market": "otc", "exchange": "TPEX"},
        {"code": "2603", "name": "長榮海運股份有限公司", "trading_name": "長榮", "market": "listed", "exchange": "TWSE"},
        {"code": "2618", "name": "長榮航空股份有限公司", "trading_name": "長榮航", "market": "listed", "exchange": "TWSE"},
        {"code": "2646", "name": "星宇航空股份有限公司", "trading_name": "星宇航空", "market": "listed", "exchange": "TWSE"},
    ]

    def resolve(self, query: str) -> dict[str, object]:
        with patch(
            "services.bot_market_data_service.read_active_stock_master_rows",
            return_value=self.MASTER_ROWS,
        ):
            return resolve_bot_stock_query(query)

    def test_date_year_is_not_mistaken_for_stock_code(self) -> None:
        result = self.resolve("台積電 2026/08/21")
        self.assertTrue(result["ok"])
        self.assertEqual(result["stock"]["code"], "2330")

    def test_name_code_conflict_and_multi_stock_query_fail_closed(self) -> None:
        self.assertEqual(self.resolve("台積電 2331")["status"], "conflict")
        self.assertEqual(self.resolve("台積電和京元電子")["status"], "ambiguous")

    def test_official_trading_name_aliases_and_typo_suggestions(self) -> None:
        yageo = self.resolve("國巨 今天收盤多少")
        self.assertTrue(yageo["ok"])
        self.assertEqual(yageo["stock"]["code"], "2327")
        self.assertEqual(yageo["stock"]["name"], "國巨")
        self.assertEqual(self.resolve("請分析久元")["stock"]["code"], "6261")
        self.assertEqual(self.resolve("請分析M31")["stock"]["code"], "6643")
        typo = self.resolve("請分析金元電子")
        self.assertEqual(typo["status"], "not_found")
        self.assertEqual(
            {item["code"] for item in typo["suggestions"]},
            {"2449", "6261"},
        )

    def test_longer_exact_name_outranks_nested_shorter_stock_name(self) -> None:
        result = self.resolve("長榮航 今天收盤價")
        self.assertTrue(result["ok"])
        self.assertEqual(result["stock"]["code"], "2618")

        both = self.resolve("請比較長榮航和長榮")
        self.assertEqual(both["status"], "ambiguous")

    def test_natural_short_name_is_suggested_instead_of_dropped_or_guessed(self) -> None:
        for query in ("星宇呢", "所以星宇呢", "所以想問星宇呢", "那所以星宇怎麼樣"):
            with self.subTest(query=query):
                result = self.resolve(query)

                self.assertEqual(result["status"], "not_found")
                self.assertEqual(result["suggestions"], [
                    {
                        "code": "2646",
                        "name": "星宇航空",
                        "official_name": "星宇航空股份有限公司",
                        "market": "listed",
                        "exchange": "TWSE",
                    }
                ])


class LineBotFactGuardTest(unittest.TestCase):
    def test_total_deadline_skips_qwen_and_returns_deterministic_fallback(self) -> None:
        with (
            patch.object(
                line_bot_service,
                "resolve_stock_query",
                return_value={"ok": True, "stock": {"code": "2330"}},
            ),
            patch.object(
                line_bot_service,
                "fetch_daily_market_data",
                return_value={},
            ) as fetch_daily,
            patch.object(line_bot_service, "_compact_daily", return_value={}),
            patch.object(line_bot_service, "_fallback_daily", return_value="fallback"),
            patch.object(line_bot_service, "qwen_chat") as qwen,
            # Reach the deadline gate without queueing a real cold-load worker.
            # Patching line_bot_service.qwen_chat alone does not isolate the
            # independent adapter binding used by line_model_warmup_service.
            patch.object(
                line_bot_service, "ensure_background_text_model_warmup",
                return_value={"status": "resident", "scheduled": False},
            ) as warmup,
            patch("requests.sessions.Session.request", side_effect=AssertionError("offline test HTTP")) as http,
            patch.dict(
                os.environ,
                {"QWEN_ENABLED": "true", "LINE_REPLY_TIMEOUT_SECONDS": "3"},
                clear=False,
            ),
        ):
            answer = line_bot_service.answer_stock_question(
                "分析台積電",
                deadline_monotonic=time.monotonic() + 1,
            )
        self.assertEqual(answer, "fallback")
        fetch_daily.assert_called_once_with(
            "2330",
            trade_date=None,
            analysis_mode="close_batch",
        )
        qwen.assert_not_called()
        warmup.assert_called_once_with()
        http.assert_not_called()

    def test_compact_daily_supplies_display_claims_and_referee_limits(self) -> None:
        facts = line_bot_service._compact_daily(
            {
                "status": "source_delayed",
                "code": "2330",
                "trade_date": TRADE_DATE,
                "freshness": {"status": "current", "ready": True},
                "stock": {"name": "台積電", "market": "listed", "exchange": "TWSE"},
                "ohlcv": {
                    "date": TRADE_DATE,
                    "open": 99,
                    "high": 102,
                    "low": 98,
                    "close": 100,
                    "volume_shares": 110_000,
                    "source": "TWSE MI_INDEX",
                    "source_quality": "official",
                    "official_trusted": True,
                },
                "technical": {
                    "available": True,
                    "status": "ok",
                    "decision_ready": True,
                    "rsi": {"rsi5": 60.5500129, "rsi10": 54.812645, "rsi14": 53.373121},
                    "moving_averages": {"ma5": 100, "ma10": 99, "ma20": 98, "ma60": 97},
                    "macd": {"dif": 1.234567, "signal": 1.0, "oscillator": 0.234567},
                    "volume_ma20": 120_000,
                },
                "valuation": {"available": False, "status": "unavailable"},
                "data_quality": {"decision_ready": False, "reason_code": "missing"},
            }
        )
        self.assertIn("RSI5／RSI10／RSI14：60.55／54.81／53.37", facts["verified_claims"])
        self.assertFalse(facts["evidence_summary"]["can_override_main_status"])
        self.assertEqual(facts["evidence_summary"]["main_status"], "資料不足")
        self.assertFalse(facts["referee"]["decision_ready"])

    def test_compact_daily_only_exposes_shared_referee_main_status(self) -> None:
        payload = {
            "status": "ok",
            "code": "2330",
            "trade_date": TRADE_DATE,
            "freshness": {"status": "current", "ready": True},
            "stock": {"name": "台積電", "market": "listed", "exchange": "TWSE"},
            "ohlcv": {
                "date": TRADE_DATE,
                "open": 99,
                "high": 102,
                "low": 98,
                "close": 100,
                "volume_shares": 110_000,
                "source": "TWSE MI_INDEX",
                "source_quality": "official",
                "official_trusted": True,
            },
            "technical": {
                "available": True,
                "status": "ok",
                "decision_ready": True,
                "rsi": {"rsi5": 60, "rsi10": 55, "rsi14": 53},
                "moving_averages": {"ma5": 100, "ma10": 99, "ma20": 98, "ma60": 97},
                "macd": {"dif": 1.2, "signal": 1.0, "oscillator": 0.2},
                "volume_ma20": 100_000,
            },
            "valuation": {"available": False, "status": "unavailable"},
            "data_quality": {"decision_ready": True, "reason_code": "ok"},
            "support_pressure": {"available": True},
            "referee": {
                "decision_ready": True,
                "main_status": "可觀察",
                "main_reasons": ["均線結構偏多，走勢維持穩定"],
                "source": "shared_project_referee",
                "version": "dashboard-practical-status-core-v1",
                "input_assembler_version": SUPPORT_RESISTANCE_ASSEMBLER_VERSION,
                "support_zone": {
                    "zone_low": 95,
                    "zone_high": 98,
                    "strength": "強",
                },
                "resistance_zone": {
                    "zone_low": 102,
                    "zone_high": 105,
                    "strength": "中",
                },
                "can_be_overridden_by_model": False,
            },
        }
        facts = line_bot_service._compact_daily(payload)
        self.assertEqual(facts["evidence_summary"]["main_status"], "可觀察")
        self.assertIn(
            "目前判斷：可觀察；理由：均線結構偏多，走勢維持穩定",
            facts["verified_claims"],
        )
        self.assertFalse(facts["referee"]["can_be_overridden_by_model"])

    def test_malformed_referee_contract_fails_closed(self) -> None:
        result = line_bot_service._normalize_referee(
            {
                "decision_ready": True,
                "main_status": "自訂強力買進",
                "main_reasons": "不是理由陣列",
                "source": "unknown",
                "version": "unknown",
                "can_be_overridden_by_model": True,
            },
            official_ready=True,
            technical_ready=True,
        )
        self.assertFalse(result["decision_ready"])
        self.assertEqual(result["main_status"], "資料不足")
        self.assertEqual(result["reason_code"], "referee_contract_invalid")

    def test_policy_guard_rejects_invented_advice_even_with_grounded_number(self) -> None:
        facts = {
            "verified_claims": [],
            "evidence_summary": {},
            "valuation": {"available": False},
            "code": "2330",
            "display": {"close": "100"},
        }
        answer = "目標價 100，建議買進。\n僅供資料整理，不構成投資建議。"
        self.assertFalse(
            line_bot_service._answer_respects_financial_policy(
                answer,
                facts,
                "分析 2330",
                require_daily_claims=False,
            )
        )

    def test_policy_guard_rejects_competing_referee_status(self) -> None:
        claim = "目前判斷：可觀察；理由：均線結構偏多，走勢維持穩定"
        facts = {
            "verified_claims": [claim],
            "evidence_summary": {
                "technical_observation": "可用技術證據偏多",
                "trend_logic": "收盤、月線與季線排列偏多",
                "momentum_logic": "RSI14 與 MACD 同步偏多",
                "volume_logic": "當日成交量高於 20 日均量",
            },
            "valuation": {"available": False},
            "referee": {"decision_ready": True, "main_status": "可觀察"},
            "display": {},
        }
        answer = (
            "資料品質\n"
            f"{claim}\n"
            "另行判斷的目前判斷：警戒\n"
            "綜合觀察\n可用技術證據偏多\n"
            "判斷邏輯\n收盤、月線與季線排列偏多\n"
            "RSI14 與 MACD 同步偏多\n當日成交量高於 20 日均量\n"
            "風險\n僅供資料整理，不構成投資建議。"
        )
        self.assertFalse(
            line_bot_service._answer_respects_financial_policy(
                answer,
                facts,
                "分析 2330",
                require_daily_claims=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
