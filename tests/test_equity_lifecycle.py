from datetime import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from equity.scheduler import TPE, run_due
from core.accounts_database import initialize


def test_scheduler_checkpoints_each_stage_and_does_not_repeat_success():
    now = datetime(2026, 9, 4, 19, 0, tzinfo=TPE)
    calls = []
    checkpoints = []
    options = dict(
        is_trading_day=lambda day: True,
        run_stage=lambda stage, day: calls.append(stage) or (0 if stage == "capture" else 1),
        schedules={"capture": (15, 5), "finalize": (18, 30)},
        window_end=(23, 59),
        retry_seconds=1800,
    )
    state = run_due(now, {}, **options, checkpoint=lambda value: checkpoints.append(dict(value)))
    assert calls == ["capture", "finalize"] and len(checkpoints) == 2
    run_due(now, state, **options)
    assert calls == ["capture", "finalize"]
    run_due(now.replace(hour=20), state, **options)
    assert calls == ["capture", "finalize", "finalize"]
    assert not run_due(now, {}, **{**options, "is_trading_day": lambda day: False})


def test_local_setup_is_loopback_only_single_use_and_migrates_accounts(tmp_path, monkeypatch):
    from api import local_setup

    monkeypatch.setenv("EQUITY_AUTH_DB", str(tmp_path / "accounts.sqlite3"))
    initialize()
    app = FastAPI()
    app.include_router(local_setup.router)
    payload = {"email": "local@example.invalid", "password": "LocalSetup#2026!"}
    headers = {"X-Equity-Request": "1"}
    remote = TestClient(app, base_url="https://public.example.invalid", client=("127.0.0.1", 1000))
    assert remote.get("/api/setup").json() == {"setup_required": False}
    assert remote.post("/api/setup", json=payload, headers=headers).status_code == 403
    client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1000))
    assert client.get("/api/setup").json()["setup_required"]
    assert client.post("/api/setup", json=payload).status_code == 403
    assert client.post("/api/setup", json=payload, headers=headers).status_code == 200
    assert not client.get("/api/setup").json()["setup_required"]
    assert client.post("/api/setup", json=payload, headers=headers).status_code == 409


def test_weekend_news_runs_only_latest_due_slot_and_does_not_repeat():
    now = datetime(2026, 9, 5, 14, 10, tzinfo=TPE)
    calls = []
    options = dict(
        is_trading_day=lambda day: False,
        run_stage=lambda stage, day: calls.append(stage) or 0,
        schedules={
            "capture": (15, 5),
            "news_0615": (6, 15),
            "news_0900": (9, 0),
            "news_1400": (14, 0),
            "news_1800": (18, 0),
        },
        window_end=(23, 59),
    )
    state = run_due(now, {}, **options)
    assert calls == ["news_1400"]
    run_due(now, state, **options)
    assert calls == ["news_1400"]
    run_due(now.replace(hour=19), state, **options)
    assert calls == ["news_1400", "news_1800"]


def test_tdcc_weekly_checkpoint_survives_weekend_and_monday_restart():
    calls = []
    options = dict(
        is_trading_day=lambda day: False,
        run_stage=lambda stage, day: calls.append(stage) or 0,
        schedules={"tdcc_tw50": (18, 30), "tdcc_watchlist": (18, 35)},
        window_end=(23, 59),
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=TPE)
    state = run_due(now, {}, **options)
    assert calls == ["tdcc_tw50", "tdcc_watchlist"]
    assert all(key.startswith("2026-09-04:") for key in state)
    run_due(datetime(2026, 9, 7, 12, 0, tzinfo=TPE), state, **options)
    assert len(calls) == 2
    run_due(datetime(2026, 9, 11, 18, 32, tzinfo=TPE), state, **options)
    assert calls == ["tdcc_tw50", "tdcc_watchlist", "tdcc_tw50"]
    run_due(datetime(2026, 9, 11, 18, 36, tzinfo=TPE), state, **options)
    assert calls[-1] == "tdcc_watchlist" and len(calls) == 4
