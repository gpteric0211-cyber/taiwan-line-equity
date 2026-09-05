from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.corporate_action_repository import (  # noqa: E402
    CorporateActionConflictError,
    CorporateActionRecord,
    read_verified_corporate_actions_at_cutoff,
    register_verified_corporate_action,
    upsert_legacy_corporate_action,
)
from repository.market_microstructure_repository import (  # noqa: E402
    _corporate_action_context,
)
from services.target_outcome_materializer import _has_corporate_action  # noqa: E402


OBSERVED_AT = "2026-09-03T10:30:00+08:00"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _wiwynn(**overrides: object) -> CorporateActionRecord:
    values: dict[str, object] = {
        "code": "6669",
        "company_name": "緯穎",
        "action_type": "right",
        "effective_date": "2026-09-02",
        "adjustment_method": "bonus_share_distribution",
        "stock_distribution_ratio": 1.98279460,
        "ratio_unit": "new_shares_per_existing_share",
        "cash_dividend_per_share": None,
        "share_count_factor": 2.98279460,
        "pre_event_price_multiplier": 0.335256071605,
        "source_id": "TWSE_TWT48U",
        "source_url": (
            "https://www.twse.com.tw/exchangeReport/"
            "TWT48U?date=20260818&response=html"
        ),
        "source_record_key": "TWSE_TWT48U:6669:2026-09-02:right",
        "publisher_published_at": None,
    }
    values.update(overrides)
    return CorporateActionRecord(**values)  # type: ignore[arg-type]


def test_first_explicit_write_creates_permanent_authority_and_legacy_projection() -> None:
    conn = _conn()

    result = register_verified_corporate_action(
        conn,
        _wiwynn(),
        observed_at=OBSERVED_AT,
    )

    assert result["status"] == "inserted"
    permanent = dict(
        conn.execute(
            "SELECT * FROM corporate_action_permanent_event"
        ).fetchone()
    )
    assert permanent["code"] == "6669"
    assert permanent["effective_date"] == "2026-09-02"
    assert permanent["adjustment_method"] == "bonus_share_distribution"
    assert permanent["stock_distribution_ratio"] == pytest.approx(1.98279460)
    assert permanent["ratio_unit"] == "new_shares_per_existing_share"
    assert permanent["share_count_factor"] == pytest.approx(2.98279460)
    assert permanent["pre_event_price_multiplier"] == pytest.approx(
        0.335256071605
    )
    assert permanent["publisher_published_at"] is None
    assert permanent["first_seen_at"] == OBSERVED_AT
    assert permanent["available_at"] == OBSERVED_AT
    assert permanent["verified_at"] == OBSERVED_AT
    assert permanent["verification_status"] == "official_verified"
    assert permanent["retention_class"] == "permanent_corporate_action"
    assert permanent["directional_weight_eligible"] == 0

    legacy = dict(conn.execute("SELECT * FROM corporate_actions").fetchone())
    assert legacy["date"] == "2026-09-02"
    assert legacy["action_type"] == "right"
    assert legacy["stock_dividend"] is None
    assert legacy["source"] == "PERMANENT:TWSE_TWT48U"
    assert legacy["is_confirmed"] == 1


def test_existing_legacy_rows_survive_additive_schema_and_new_write() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE corporate_actions(
            code TEXT,date TEXT,action_type TEXT,cash_dividend REAL,
            stock_dividend REAL,source TEXT,is_confirmed INTEGER DEFAULT 0,
            updated_at TEXT,PRIMARY KEY(code,date,action_type)
        );
        INSERT INTO corporate_actions VALUES(
            '6669','2026-06-22','dividend',NULL,NULL,
            'FinMind confirmed',1,'2026-09-01T10:26:56'
        );
        """
    )

    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)

    assert conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 2
    old = conn.execute(
        "SELECT source FROM corporate_actions WHERE date='2026-06-22'"
    ).fetchone()
    assert old[0] == "FinMind confirmed"


def test_identical_replay_is_noop() -> None:
    conn = _conn()
    first = register_verified_corporate_action(
        conn, _wiwynn(), observed_at=OBSERVED_AT
    )
    before_changes = conn.total_changes
    before = dict(
        conn.execute("SELECT * FROM corporate_action_permanent_event").fetchone()
    )

    replay = register_verified_corporate_action(
        conn,
        _wiwynn(),
        observed_at="2026-09-03T11:30:00+08:00",
    )

    after = dict(
        conn.execute("SELECT * FROM corporate_action_permanent_event").fetchone()
    )
    assert first["event_id"] == replay["event_id"]
    assert replay["status"] == "unchanged"
    assert conn.total_changes == before_changes
    assert after == before


def test_provenance_normalization_does_not_change_economic_identity() -> None:
    conn = _conn()
    first = register_verified_corporate_action(
        conn, _wiwynn(), observed_at=OBSERVED_AT
    )

    replay = register_verified_corporate_action(
        conn,
        _wiwynn(
            company_name="緯穎科技服務股份有限公司",
            source_url=(
                "https://www.twse.com.tw/exchangeReport/"
                "TWT48U?response=html&date=20260818"
            ),
        ),
        observed_at="2026-09-03T11:30:00+08:00",
    )

    assert replay["status"] == "unchanged"
    assert replay["fact_digest"] == first["fact_digest"]
    assert conn.execute(
        "SELECT COUNT(*) FROM corporate_action_permanent_event"
    ).fetchone()[0] == 1


def test_replay_reports_when_only_legacy_projection_was_repaired() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)
    conn.execute(
        """
        UPDATE corporate_actions
        SET source='damaged projection',is_confirmed=0
        WHERE code='6669' AND date='2026-09-02' AND action_type='right'
        """
    )

    replay = register_verified_corporate_action(
        conn,
        _wiwynn(),
        observed_at="2026-09-03T11:30:00+08:00",
    )

    assert replay["status"] == "projection_repaired"
    assert replay["canonical_changed"] is False
    assert replay["legacy_changed"] is True


def test_changed_replay_fails_closed_and_preserves_official_record() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)

    with pytest.raises(CorporateActionConflictError):
        register_verified_corporate_action(
            conn,
            _wiwynn(
                stock_distribution_ratio=1.9,
                share_count_factor=2.9,
                pre_event_price_multiplier=1.0 / 2.9,
            ),
            observed_at="2026-09-03T11:30:00+08:00",
        )

    row = conn.execute(
        "SELECT stock_distribution_ratio FROM corporate_action_permanent_event"
    ).fetchone()
    assert row[0] == pytest.approx(1.98279460)


def test_legacy_updater_cannot_downgrade_permanent_projection() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)

    changed = upsert_legacy_corporate_action(
        conn,
        code="6669",
        action_date="2026-09-02",
        action_type="right",
        cash_dividend=None,
        stock_dividend=1.98279460,
        source="legacy refresh",
        is_confirmed=0,
        updated_at="later",
    )
    assert changed is False

    permanent = conn.execute(
        "SELECT stock_distribution_ratio,verification_status,retention_class "
        "FROM corporate_action_permanent_event"
    ).fetchone()
    assert tuple(permanent) == (
        pytest.approx(1.98279460),
        "official_verified",
        "permanent_corporate_action",
    )
    legacy = conn.execute(
        "SELECT stock_dividend,source,is_confirmed FROM corporate_actions"
    ).fetchone()
    assert tuple(legacy) == (None, "PERMANENT:TWSE_TWT48U", 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"effective_date": "2026-02-30"},
        {"stock_distribution_ratio": -1.0},
        {"share_count_factor": 2.0},
        {"pre_event_price_multiplier": 0.5},
        {"ratio_unit": "percent"},
        {"source_url": "http://example.invalid/event"},
        {
            "source_url": "https://www.twse.com.tw/exchangeReport/TWT48U"
        },
        {
            "source_url": (
                "https://www.twse.com.tw/exchangeReport/"
                "TWT48U?date=20260230&response=html"
            )
        },
        {
            "source_id": "FAKE",
            "source_url": "https://example.invalid/event",
            "source_record_key": "FAKE:6669",
        },
        {"verification_status": "unverified"},
        {"directional_weight_eligible": 1},
        {"publisher_published_at": "2026-08-14T14:41:20"},
        {"time_precision": "datetime"},
        {"adjustment_method": "rights_issue"},
        {
            "action_type": "dividend",
            "adjustment_method": "bonus_share_distribution",
        },
    ],
)
def test_invalid_or_unverified_values_are_rejected(overrides: dict[str, object]) -> None:
    conn = _conn()
    with pytest.raises(ValueError):
        register_verified_corporate_action(
            conn,
            _wiwynn(**overrides),
            observed_at=OBSERVED_AT,
        )


def test_cutoff_reader_is_point_in_time_and_read_only() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)
    before = conn.total_changes

    hidden = read_verified_corporate_actions_at_cutoff(
        conn,
        code="6669",
        analysis_cutoff="2026-09-03T10:29:59+08:00",
    )
    visible = read_verified_corporate_actions_at_cutoff(
        conn,
        code="6669",
        analysis_cutoff=OBSERVED_AT,
        effective_on_or_before="2026-09-02",
    )

    assert hidden == []
    assert [row["effective_date"] for row in visible] == ["2026-09-02"]
    assert conn.total_changes == before


def test_shared_context_exposes_typed_adjustment_only_after_available_at() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)

    hidden = _corporate_action_context(
        conn,
        code="6669",
        reference_date="2026-09-01",
        history_dates=["2026-08-28", "2026-09-01"],
        analysis_cutoff="2026-09-03T10:29:59+08:00",
    )
    visible = _corporate_action_context(
        conn,
        code="6669",
        reference_date="2026-09-01",
        history_dates=["2026-08-28", "2026-09-01"],
        analysis_cutoff=OBSERVED_AT,
    )

    assert hidden == {"ready": True, "active_window": False, "status": "clear"}
    assert visible["active_window"] is True
    assert visible["action_date"] == "2026-09-02"
    assert visible["action_type"] == "right"
    assert visible["stock_distribution_ratio"] == pytest.approx(1.98279460)
    assert visible["ratio_unit"] == "new_shares_per_existing_share"
    assert visible["share_count_factor"] == pytest.approx(2.98279460)
    assert visible["pre_event_price_multiplier"] == pytest.approx(
        0.335256071605
    )
    assert visible["directional_weight_eligible"] is False
    assert visible["verification_status"] == "official_verified"

    after_action_without_new_price = _corporate_action_context(
        conn,
        code="6669",
        reference_date="2026-09-03",
        history_dates=["2026-08-28", "2026-09-01"],
        analysis_cutoff=OBSERVED_AT,
    )
    assert after_action_without_new_price["days_from_action"] == 0
    assert after_action_without_new_price["label"] == (
        "除權息後尚無完整新價格基準，技術指標需重新累積"
    )


def test_shared_context_reads_permanent_authority_without_legacy_projection() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)
    conn.execute("DROP TABLE corporate_actions")

    visible = _corporate_action_context(
        conn,
        code="6669",
        reference_date="2026-09-01",
        history_dates=["2026-08-28", "2026-09-01"],
        analysis_cutoff=OBSERVED_AT,
    )

    assert visible["active_window"] is True
    assert visible["stock_distribution_ratio"] == pytest.approx(1.98279460)


def test_legacy_context_hides_rows_imported_after_historical_cutoff() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE corporate_actions(
            code TEXT,date TEXT,action_type TEXT,cash_dividend REAL,
            stock_dividend REAL,source TEXT,is_confirmed INTEGER DEFAULT 0,
            updated_at TEXT,PRIMARY KEY(code,date,action_type)
        );
        INSERT INTO corporate_actions VALUES(
            '6669','2026-09-02','right',NULL,NULL,
            'legacy provider',1,'2026-09-03T10:38:26+08:00'
        );
        """
    )

    hidden = _corporate_action_context(
        conn,
        code="6669",
        reference_date="2026-09-01",
        history_dates=["2026-08-28", "2026-09-01"],
        analysis_cutoff="2026-09-02T07:00:00+08:00",
    )

    assert hidden == {"ready": True, "active_window": False, "status": "clear"}


def test_legacy_context_hides_rows_without_point_in_time_timestamp() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE corporate_actions(
            code TEXT,date TEXT,action_type TEXT,cash_dividend REAL,
            stock_dividend REAL,source TEXT,is_confirmed INTEGER DEFAULT 0,
            updated_at TEXT,PRIMARY KEY(code,date,action_type)
        );
        INSERT INTO corporate_actions VALUES(
            '6669','2026-09-02','right',NULL,NULL,
            'legacy provider',1,NULL
        );
        """
    )

    hidden = _corporate_action_context(
        conn,
        code="6669",
        reference_date="2026-09-01",
        history_dates=["2026-08-28", "2026-09-01"],
        analysis_cutoff="2026-09-02T07:00:00+08:00",
    )

    assert hidden == {"ready": True, "active_window": False, "status": "clear"}


def test_legacy_projection_is_detected_by_existing_outcome_guard() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)

    assert _has_corporate_action(
        conn,
        code="6669",
        prediction_date="2026-09-01",
        outcome_date="2026-09-02",
    )


def test_schema_integrity_remains_ok() -> None:
    conn = _conn()
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)
    assert [row[0] for row in conn.execute("PRAGMA integrity_check")] == ["ok"]


def test_schema_creation_and_write_roll_back_with_caller_transaction() -> None:
    conn = _conn()
    conn.execute("BEGIN IMMEDIATE")
    register_verified_corporate_action(conn, _wiwynn(), observed_at=OBSERVED_AT)
    assert conn.in_transaction is True

    conn.rollback()

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "corporate_action_permanent_event" not in tables
    assert "corporate_actions" not in tables
