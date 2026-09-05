from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.technical_ensemble_v1 import (  # noqa: E402
    COMPONENT_SPECS,
    TECHNICAL_FORMULA_VERSION_V1,
    compute_technical_ensemble_frame,
)
from core.market_analytics_schema import ensure_market_analytics_schema  # noqa: E402
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.rsi_adjustment_repository import apply_rsi_split_adjustments  # noqa: E402
from services import technical_ensemble_materializer  # noqa: E402
from services.technical_ensemble_materializer import (  # noqa: E402
    input_snapshot_digest_chain,
    materialize_technical_ensemble_v1,
)


def _trading_dates(start: date, count: int) -> list[str]:
    values: list[str] = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def _rising_rows(count: int = 260) -> list[dict]:
    dates = _trading_dates(date(2025, 1, 2), count)
    return [
        {
            "date": dates[index],
            "code": "2454",
            "open": index + 0.5,
            "high": index + 2.0,
            "low": index - 1.0,
            "close": index + 1.0,
            "volume": 1_000.0 + (index % 2) * 100.0,
            "volume_unit": "shares",
            "source": "TWSE STOCK_DAY",
            "source_quality": "official",
            "market": "listed",
        }
        for index in range(count)
    ]


def test_golden_linear_series_covers_each_formula_family() -> None:
    frame = compute_technical_ensemble_frame(_rising_rows())
    latest = frame.iloc[-1]

    assert latest["ma5"] == pytest.approx(258.0, abs=1e-12)
    assert latest["ema12"] == pytest.approx(254.5, abs=1e-12)
    assert latest["ema26"] == pytest.approx(247.5, abs=1e-12)
    assert latest["macd_dif"] == pytest.approx(7.0, abs=1e-12)
    assert latest["macd_signal"] == pytest.approx(7.0, abs=1e-12)
    assert latest["macd_histogram"] == pytest.approx(0.0, abs=1e-12)
    assert latest["plus_di14"] > latest["minus_di14"]
    assert latest["adx14"] == pytest.approx(100.0, abs=1e-12)

    assert latest["rsi14"] == pytest.approx(100.0, abs=1e-12)
    assert latest["mtm10"] == pytest.approx(10.0, abs=1e-12)
    assert latest["roc10"] == pytest.approx(4.0, abs=1e-12)
    assert latest["kd_j"] == pytest.approx(3 * latest["kd_k"] - 2 * latest["kd_d"])

    assert latest["obv"] == pytest.approx(sum(row["volume"] for row in _rising_rows()[1:]))
    assert latest["nvi"] > 1000
    assert latest["pvi"] > 1000
    assert latest["atr14"] == pytest.approx(3.0, abs=1e-12)
    assert latest["weighted_close"] == pytest.approx(259.75, abs=1e-12)
    assert latest["psy12"] == pytest.approx(100.0, abs=1e-12)
    assert latest["ar26"] == pytest.approx(100.0, abs=1e-12)
    assert latest["br26"] == pytest.approx(200.0, abs=1e-12)

    assert latest["ensemble_coverage"] == pytest.approx(1.0, abs=1e-12)
    assert -100.0 <= latest["ensemble_score"] <= 100.0


def test_component_inventory_has_no_duplicate_vote_and_all_required_indicators() -> None:
    keys = [str(spec["component_key"]) for spec in COMPONENT_SPECS]
    assert len(keys) == len(set(keys))
    assert "kd_k" in keys and "kd_d" in keys and "kd_j" in keys
    kd_specs = [spec for spec in COMPONENT_SPECS if str(spec["component_key"]).startswith("kd_")]
    assert {spec["parameters"].get("family_vote") for spec in kd_specs if "family_vote" in spec["parameters"]} == {"KD_KDJ_one_vote"}
    for required in (
        "ma240",
        "macd_cross_date",
        "plus_di14",
        "adx14",
        "williams_r14",
        "cci20",
        "vr26",
        "eom14",
        "nvi",
        "pvi",
        "vao14",
        "weighted_close",
        "psy12",
        "ar26",
        "br26",
        "ensemble_score",
    ):
        assert required in keys


def test_cumulative_series_and_digest_do_not_rebase_when_a_new_row_is_appended() -> None:
    first = _rising_rows(260)
    extended = _rising_rows(261)
    first_frame = compute_technical_ensemble_frame(first)
    extended_frame = compute_technical_ensemble_frame(extended)

    for key in ("obv", "ad", "nvi", "pvi"):
        assert extended_frame.iloc[259][key] == pytest.approx(first_frame.iloc[-1][key])
    first_digests = input_snapshot_digest_chain(first)
    extended_digests = input_snapshot_digest_chain(extended)
    assert extended_digests[:260] == first_digests


def test_verified_split_adjustment_preserves_raw_prices_and_technical_continuity() -> None:
    rows = _rising_rows(260)
    split_index = 130
    split_date = rows[split_index]["date"]
    for index, row in enumerate(rows):
        base = 100.0 + index * 0.1
        multiplier = 2.0 if index < split_index else 1.0
        row.update(
            {
                "open": (base - 0.2) * multiplier,
                "high": (base + 0.5) * multiplier,
                "low": (base - 0.5) * multiplier,
                "close": base * multiplier,
            }
        )
    raw_before = rows[split_index - 1]["close"]
    adjusted = apply_rsi_split_adjustments(
        rows,
        [{"event_date": split_date, "pre_event_factor": 0.5}],
    )
    frame = compute_technical_ensemble_frame(adjusted)

    assert rows[split_index - 1]["close"] == raw_before
    assert adjusted[split_index - 1]["technical_close"] == pytest.approx(raw_before * 0.5)
    assert abs(
        adjusted[split_index]["technical_close"]
        - adjusted[split_index - 1]["technical_close"]
    ) < 1.0
    assert frame.iloc[split_index]["atr14"] < 2.0


def test_scheduled_materializer_is_idempotent_and_records_no_request_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "analytics.sqlite3"
    rows = _rising_rows()
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE history_price(
                date TEXT,code TEXT,open REAL,high REAL,low REAL,close REAL,
                volume REAL,volume_unit TEXT,source TEXT,source_quality TEXT,
                market TEXT,PRIMARY KEY(date,code)
            );
            CREATE TABLE stock_industry_profile(code TEXT PRIMARY KEY);
            CREATE TABLE full_market_batch_publications(trade_date TEXT PRIMARY KEY);
            """
        )
        ensure_market_analytics_schema(conn)
        ensure_single_track_v3_schema(conn)
        conn.execute(
            """
            INSERT INTO stock_master(
                code,name,market,exchange,security_type,is_active,source,
                source_status,updated_at
            ) VALUES('2454','聯發科','listed','TWSE','stock',1,'TEST','ok','2026-01-01')
            """
        )
        conn.executemany(
            """
            INSERT INTO history_price(
                date,code,open,high,low,close,volume,volume_unit,
                source,source_quality,market
            ) VALUES(
                :date,:code,:open,:high,:low,:close,:volume,:volume_unit,
                :source,:source_quality,:market
            )
            """,
            rows,
        )
        conn.execute(
            "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
            (rows[-1]["date"],),
        )
        conn.commit()

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(technical_ensemble_materializer, "db", connect)
    monkeypatch.setattr(
        technical_ensemble_materializer,
        "history_date_coverage",
        lambda *_args, **_kwargs: {"ready": True, "reason": "ok"},
    )
    monkeypatch.setattr(
        technical_ensemble_materializer,
        "recent_market_reference_dates",
        lambda *_args, **_kwargs: [row["date"] for row in rows[-240:]],
    )

    first = materialize_technical_ensemble_v1(codes=["2454"])
    second = materialize_technical_ensemble_v1(codes=["2454"])

    assert first["ok"] is True
    assert first["component_rows_written"] == len(COMPONENT_SPECS)
    assert first["state_rows_written"] == 4
    assert first["request_path_computation_count"] == 0
    assert first["request_path_write_count"] == 0
    assert first["write_commit_interval_codes"] == 50
    assert first["write_batch_count"] == 1
    assert second["component_rows_written"] == 0
    assert second["state_rows_written"] == 0
    assert second["write_batch_count"] == 1
    with closing(sqlite3.connect(path)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM technical_indicator_component WHERE stock_code='2454'"
        ).fetchone()[0]
        formula_versions = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT formula_version FROM technical_indicator_component"
            ).fetchall()
        }
        state_dates = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT last_trade_date FROM technical_indicator_state"
            ).fetchall()
        }
    assert count == len(COMPONENT_SPECS)
    assert formula_versions == {TECHNICAL_FORMULA_VERSION_V1}
    assert state_dates == {rows[-1]["date"]}

    backfill = materialize_technical_ensemble_v1(codes=["2454"], backfill=True)
    assert backfill["ok"] is True
    assert backfill["historical_storage"] == "one_versioned_feature_vector_per_stock_trade_date"
    assert backfill["normalized_component_scope"] == "latest_trade_date_only_when_backfill"
    with closing(sqlite3.connect(path)) as conn:
        vector_count = conn.execute(
            "SELECT COUNT(*) FROM technical_indicator_vector_daily WHERE stock_code='2454'"
        ).fetchone()[0]
        component_count = conn.execute(
            "SELECT COUNT(*) FROM technical_indicator_component WHERE stock_code='2454'"
        ).fetchone()[0]
    assert vector_count == backfill["code_result_sample"][0]["history_rows"]
    assert component_count == len(COMPONENT_SPECS)


def test_request_modules_do_not_import_candidate_materializer() -> None:
    for relative in (
        "review_src/app.py",
        "review_src/services/bot_market_data_service.py",
        "review_src/services/line_bot_service.py",
    ):
        text = (Path(__file__).resolve().parents[1] / relative).read_text(encoding="utf-8")
        assert "technical_ensemble_materializer" not in text
