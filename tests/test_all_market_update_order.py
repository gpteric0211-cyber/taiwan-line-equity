from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core import market_session  # noqa: E402
from scripts import update_all_market_database as update_all  # noqa: E402


def test_post_close_trade_date_switches_at_1500_without_changing_eod_gate(monkeypatch) -> None:
    tpe = ZoneInfo("Asia/Taipei")
    monkeypatch.setattr(market_session, "is_taiwan_trading_day", lambda _day: True)
    monkeypatch.setattr(
        market_session,
        "now_tpe",
        lambda: datetime(2026, 8, 28, 15, 5, tzinfo=tpe),
    )

    assert market_session.recent_market_date_for_post_close() == "2026-08-28"
    assert market_session.recent_market_date_for_eod() == "2026-08-27"


def test_current_restrictions_refresh_precedes_exact_history(monkeypatch) -> None:
    calls: list[str] = []
    ok = {"ok": True, "status": "ok"}
    exact = {
        "ok": True,
        "status": "ok",
        "storage_allowed": True,
        "trade_date": "2026-08-28",
    }
    price_volume = {
        "ok": True,
        "status": "ok",
        "operational_complete": True,
        "required_trading_stock_count": 1,
        "decision_ready_count": 1,
    }

    monkeypatch.setattr(update_all, "recent_market_date_for_post_close", lambda: "2026-08-28")
    monkeypatch.setattr(update_all, "sync_official_stock_master", lambda **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "run_market_foundation_update", lambda **_kwargs: dict(ok))

    def restrictions(**_kwargs):
        calls.append("restrictions")
        return dict(ok)

    def history(_date, **_kwargs):
        calls.append("exact_history")
        return dict(exact)

    monkeypatch.setattr(update_all, "refresh_official_trading_restrictions", restrictions)
    monkeypatch.setattr(update_all, "refresh_full_market_history_date", history)
    monkeypatch.setattr(update_all, "update_twse_daily_valuation", lambda *_args, **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "active_stock_codes", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(update_all, "refresh_tpex_valuation_codes", lambda *_args, **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "rebuild_daily_technical_snapshots", lambda **_kwargs: dict(ok))
    def candidate_failure(**_kwargs):
        raise RuntimeError("candidate-only failure")

    monkeypatch.setattr(update_all, "materialize_technical_ensemble_v1", candidate_failure)
    monkeypatch.setattr(update_all, "refresh_official_institution_snapshot", lambda **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "refresh_official_credit_balances", lambda *_args, **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "refresh_estimated_chip_costs", lambda **_kwargs: dict(ok))
    monkeypatch.setattr(
        update_all,
        "recover_missing_price_volume_from_persisted_trades",
        lambda *_args, **_kwargs: dict(ok),
    )
    monkeypatch.setattr(
        update_all,
        "reconcile_full_market_price_volume_after_official_update",
        lambda *_args, **_kwargs: dict(price_volume),
    )
    monkeypatch.setattr(update_all, "refresh_global_market_snapshot", lambda **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "refresh_taifex_night_snapshot", lambda **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "refresh_official_company_events", lambda **_kwargs: dict(ok))
    monkeypatch.setattr(update_all, "db", lambda: sqlite3.connect(":memory:"))

    result = update_all.run_all_market_update(run_date="2026-08-28")

    assert calls == ["restrictions", "exact_history"]
    assert result["official_trading_restrictions"]["ok"] is True
    assert result["effective_trade_date"] == "2026-08-28"
    assert result["ok"] is True
    assert result["technical_ensemble_candidate"]["status"] == "candidate_materializer_failed"
    assert result["technical_ensemble_candidate"]["required_for_official_update"] is False
