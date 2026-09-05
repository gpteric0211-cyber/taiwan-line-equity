from __future__ import annotations

import sys
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.next_day_outlook import (  # noqa: E402
    us_sentiment_factor,
    weighted_available_score,
)


def _factor(score: float, *, available: bool = True, decay: float = 1.0) -> dict:
    return {
        "available": available,
        "score": score,
        "decay": decay,
    }


def test_uncalibrated_probability_is_not_converted_to_neutral_score() -> None:
    factor = us_sentiment_factor(
        {
            "available": True,
            "probability_up": None,
            "sentiment_score": None,
            "label": "中性",
        },
        [],
    )

    assert factor["available"] is False
    assert factor["score"] == 0.0
    assert factor["score_basis"] == "unavailable"
    assert "未校準機率不以 50 補值" in factor["reason"]


def test_qualitative_sentiment_uses_its_own_score_basis() -> None:
    factor = us_sentiment_factor(
        {
            "available": True,
            "probability_up": None,
            "sentiment_score": 18.0,
            "weighted_change_pct": 1.2,
            "positive_weight_ratio": 70,
            "confidence": "中",
        },
        [],
    )

    assert factor["available"] is True
    assert factor["raw_score"] == 18.0
    assert factor["score"] == 9.0  # unknown-date decay remains explicit
    assert factor["score_basis"] == "qualitative_sentiment_score"


def test_missing_factor_weights_are_renormalized_only_after_coverage_gate() -> None:
    factors = {
        "us": _factor(10.0),
        "night": _factor(20.0),
        "chip": _factor(-5.0),
        "tech": _factor(30.0, available=False),
    }
    result = weighted_available_score(
        factors,
        {"us": 0.28, "night": 0.37, "chip": 0.25, "tech": 0.10},
        minimum_coverage_ratio=0.60,
        minimum_factor_count=2,
        required_groups=(("us", "night"), ("chip", "tech")),
    )

    assert result["available"] is True
    assert result["coverage_ratio"] == 0.9
    assert result["missing_factors"] == ["tech"]
    assert sum(result["effective_weights"].values()) == 1.0
    assert result["score"] == 9.9444


def test_one_surviving_factor_cannot_be_amplified_to_complete_outlook() -> None:
    factors = {
        "us": _factor(20.0),
        "night": _factor(0.0, available=False),
        "chip": _factor(0.0, available=False),
        "tech": _factor(0.0, available=False),
    }
    result = weighted_available_score(
        factors,
        {"us": 0.28, "night": 0.37, "chip": 0.25, "tech": 0.10},
        minimum_coverage_ratio=0.60,
        minimum_factor_count=2,
        required_groups=(("us", "night"), ("chip", "tech")),
    )

    assert result["available"] is False
    assert result["score"] is None
    assert result["effective_weights"] == {}
    assert result["coverage_ratio"] == 0.28


def test_expired_factor_does_not_count_as_available_coverage() -> None:
    result = weighted_available_score(
        {"us": _factor(20.0, decay=0.0), "night": _factor(-10.0)},
        {"us": 0.45, "night": 0.55},
        minimum_coverage_ratio=0.50,
    )

    assert result["available"] is True
    assert result["usable_factors"] == ["night"]
    assert result["score"] == -10.0


def test_chip_factor_does_not_double_count_referee_main_status(tmp_path, monkeypatch) -> None:
    import app

    database = tmp_path / "outlook.db"
    conn = sqlite3.connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE institution_daily(
                code TEXT,date TEXT,foreign_net REAL,trust_net REAL,dealer_net REAL
            );
            CREATE TABLE margin_daily(
                code TEXT,date TEXT,margin_delta REAL,short_delta REAL,
                margin_balance REAL,short_balance REAL
            );
            CREATE TABLE history_price(code TEXT,date TEXT,close REAL);
            """
        )
        for day in range(23, 28):
            date_text = f"2026-08-{day:02d}"
            conn.execute(
                "INSERT INTO institution_daily VALUES(?,?,?,?,?)",
                ("2330", date_text, 1_000_000, 500_000, 100_000),
            )
            conn.execute(
                "INSERT INTO margin_daily VALUES(?,?,?,?,?,?)",
                ("2330", date_text, -100, 10, 10_000, 1_000),
            )
        conn.execute("INSERT INTO history_price VALUES('2330','2026-08-27',100)")
        conn.execute("INSERT INTO history_price VALUES('2330','2026-08-26',99)")
        conn.commit()
    finally:
        conn.close()

    def open_database():
        local = sqlite3.connect(database)
        local.row_factory = sqlite3.Row
        return local

    monkeypatch.setattr(app, "db", open_database)
    monkeypatch.setattr(app, "resolve_full_market_analysis_date", lambda _conn: "2026-08-27")

    blocked = app.chip_factor_for_stock("2330", {"main_status": "禁止"})
    watch = app.chip_factor_for_stock("2330", {"main_status": "可觀察"})

    assert blocked["raw_score"] == watch["raw_score"]
    assert blocked["reason"] == watch["reason"]
