"""Synthetic concurrency tests: no production DB, model, tunnel or LINE messages."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.database_access import DatabaseLease, DatabaseBusyError, connect
from equity import lifecycle
from equity.database_middleware import MarketSnapshotMiddleware
from scripts import run_isolated_post_close_pipeline as publisher


def database(path, value):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker VALUES(?)", (value,))
    return path


def publish(candidate, active, **options):
    stat = active.stat()
    return publisher.publish_candidate(candidate, active,
        expected_active_stat=(stat.st_size, stat.st_mtime_ns), **options)


def test_shared_connections_block_replace_until_every_reader_closes(tmp_path):
    active = database(tmp_path / "market.db", "old")
    candidate = database(tmp_path / "next.db", "new")
    with closing(connect(active, readonly=True)) as one, closing(connect(active, readonly=True)) as two:
        assert one.execute("SELECT value FROM marker").fetchone()[0] == "old"
        assert two.execute("SELECT value FROM marker").fetchone()[0] == "old"
        with pytest.raises(DatabaseBusyError):
            publish(candidate, active, retry_seconds=0)
        one.close()
        with pytest.raises(DatabaseBusyError):
            publish(candidate, active, retry_seconds=0)
    publish(candidate, active, retry_seconds=0)
    with closing(connect(active, readonly=True)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "new"


def test_reader_in_another_process_blocks_publication(tmp_path):
    active = database(tmp_path / "market.db", "old")
    candidate = database(tmp_path / "next.db", "new")
    code = """import sys
from core.database_access import connect
from pathlib import Path
conn = connect(Path(sys.argv[1]), readonly=True)
print(conn.execute('SELECT value FROM marker').fetchone()[0], flush=True)
sys.stdin.readline()
conn.close()
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "review_src")
    with subprocess.Popen([sys.executable, "-c", code, str(active)], env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True) as child:
        try:
            assert child.stdout.readline().strip() == "old"
            with pytest.raises(DatabaseBusyError):
                publish(candidate, active, retry_seconds=0)
            child.communicate("close\n", timeout=10)
            assert child.returncode == 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    publish(candidate, active, retry_seconds=0)


def test_web_and_bot_keep_one_snapshot_then_both_see_new_generation(tmp_path, monkeypatch):
    from core import cache

    active = database(tmp_path / "market.db", "old")
    candidate = database(tmp_path / "next.db", "new")
    for name in ("_row_cache", "_score_cache", "_practical_cache"):
        monkeypatch.setattr(cache, name, {})
    app = FastAPI()
    app.add_middleware(MarketSnapshotMiddleware, database=active)
    read_started, finish_read = threading.Event(), threading.Event()

    @app.get("/api/quotes")
    @app.get("/api/bot/market-data/quotes")
    def read(wait: bool = False):
        # Simulate derived cached analysis after the DB connection has closed.
        value = cache._row_cache.get("marker")
        if value is None:
            with closing(connect(active, readonly=True)) as conn:
                value = conn.execute("SELECT value FROM marker").fetchone()[0]
            cache._row_cache["marker"] = value
        if wait:
            read_started.set()
            assert finish_read.wait(10)
        return {"value": value}

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        old = client.get("/api/quotes")
        reading = pool.submit(client.get, "/api/quotes?wait=true")
        assert read_started.wait(5)
        updating = pool.submit(publish, candidate, active, retry_seconds=5)
        try:
            with pytest.raises(FutureTimeout):
                updating.result(timeout=0.2)
            during = client.get("/api/bot/market-data/quotes")
            assert during.json() == {"value": "old"}
            assert during.headers["x-market-generation"] == old.headers["x-market-generation"]
        finally:
            finish_read.set()
        assert reading.result(timeout=5).json() == {"value": "old"}
        updating.result(timeout=5)
        web = client.get("/api/quotes")
        bot = client.get("/api/bot/market-data/quotes")
        assert web.json() == bot.json() == {"value": "new"}
        assert web.headers["x-market-generation"] == bot.headers["x-market-generation"]
        assert web.headers["x-market-generation"] != old.headers["x-market-generation"]


def test_unexpected_writer_changes_are_not_overwritten(tmp_path):
    active = database(tmp_path / "market.db", "old")
    candidate = database(tmp_path / "next.db", "new")
    stat = active.stat()
    with closing(connect(active)) as conn, conn:
        conn.execute("UPDATE marker SET value='user change'")
    with pytest.raises(RuntimeError, match="active database changed"):
        publisher.publish_candidate(candidate, active,
            expected_active_stat=(stat.st_size, stat.st_mtime_ns), retry_seconds=0)
    with closing(connect(active, readonly=True)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "user change"


def test_failed_replace_leaves_no_hard_link_to_live_database(tmp_path, monkeypatch):
    active = database(tmp_path / "market.db", "old")
    candidate = database(tmp_path / "next.db", "new")
    def denied(*_args):
        raise PermissionError("nonparticipating external DB handle")
    monkeypatch.setattr(publisher.os, "replace", denied)
    with pytest.raises(PermissionError):
        publish(candidate, active, retry_seconds=0)
    assert not active.with_name("market.previous.db").exists()
    assert publisher.quick_integrity_messages(active) == ["ok"]


def launcher_args(**changes):
    return SimpleNamespace(**dict(host="127.0.0.1", port=8056, no_model=True,
                                  no_schedule=True, open_browser=False, **changes))


def test_repeated_launch_reuses_one_stack_and_does_not_stop_it(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "ROOT", tmp_path)
    location = tmp_path / "var" / "services"
    launches, opened = [], []
    owner = None

    def launch(args, directory):
        nonlocal owner
        owner = publisher.isolated_update_lock(directory / "supervisor.lock")
        assert owner.__enter__()
        launches.append(args.port)
        (directory / "runtime.json").write_text(json.dumps({"state": "running", "pid": 123,
                                                             "runtime_id": "test-owner"}))
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(lifecycle, "launch_background", launch)
    monkeypatch.setattr(lifecycle, "health", lambda url: {"runtime_id": "test-owner"} if launches else None)
    monkeypatch.setattr(lifecycle.webbrowser, "open", opened.append)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(lifecycle.ensure_started, launcher_args())
            b = pool.submit(lifecycle.ensure_started, launcher_args())
            results = [a.result(timeout=5), b.result(timeout=5)]
        assert launches == [8056]
        assert {r["pid"] for r in results} == {123}
        assert sorted(r["reused"] for r in results) == [False, True]
        args = launcher_args()
        args.open_browser = True
        assert lifecycle.ensure_started(args)["reused"]
        assert opened == ["http://127.0.0.1:8056/"]
        assert lifecycle.supervisor_running(location)
    finally:
        if owner:
            owner.__exit__(None, None, None)


def test_foreign_service_is_not_mistaken_for_our_server(monkeypatch):
    from io import BytesIO

    monkeypatch.setattr(lifecycle, "urlopen", lambda *a, **k: BytesIO(b'{"service":"other"}'))
    with pytest.raises(RuntimeError, match="different project"):
        lifecycle.health("http://127.0.0.1:8056")


def test_publication_timeout_does_not_block_webhook_or_health(tmp_path):
    active = database(tmp_path / "market.db", "old")
    app = FastAPI()
    app.add_middleware(MarketSnapshotMiddleware, database=active)
    @app.get("/healthz")
    @app.get("/webhook")
    def alive():
        return {"ok": True}
    with TestClient(app) as client, DatabaseLease(active, exclusive=True, timeout=0):
        assert client.get("/healthz").status_code == 200
        assert client.get("/webhook").status_code == 200


def test_manual_and_scheduled_updates_share_one_writer_while_clients_read(tmp_path, monkeypatch):
    active = database(tmp_path / "market.db", "old")
    building, complete_build = threading.Event(), threading.Event()
    calls = []
    def pipeline(command, *, cwd, env):
        calls.append(command)
        candidate = Path(env["TAIWAN50_DB_PATH"])
        with closing(sqlite3.connect(candidate)) as conn, conn:
            conn.execute("UPDATE marker SET value='new'")
        building.set()
        assert complete_build.wait(5)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(publisher.subprocess, "run", pipeline)
    options = dict(active=active, pipeline=tmp_path / "synthetic.py", lock_path=tmp_path / "writer.lock")
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(publisher.run_isolated_update, [], **options, report_path=tmp_path / "first.json")
        assert building.wait(5)
        try:
            assert publisher.run_isolated_update([], **options, report_path=tmp_path / "second.json") == 4
            with closing(connect(active, readonly=True)) as conn:
                assert conn.execute("SELECT value FROM marker").fetchone()[0] == "old"
        finally:
            complete_build.set()
        assert first.result(timeout=5) == 0
    assert len(calls) == 1
    with closing(connect(active, readonly=True)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "new"


def test_update_entrypoints_resolve_relative_database_from_shared_base(tmp_path, monkeypatch):
    from scripts import run_isolated_manual_daily_analysis_update as manual
    from scripts import verify_daily_analysis_update as verifier

    monkeypatch.setenv("TAIWAN50_DB_PATH", "data/custom.db")
    monkeypatch.chdir(tmp_path)
    selected = []
    def capture(*args, **kwargs):
        selected.append(kwargs["active"])
        return 0
    monkeypatch.setattr(publisher, "run_isolated_update", capture)
    monkeypatch.setattr(manual, "run_isolated_update", capture)
    monkeypatch.setattr(verifier, "main", lambda args: 0)
    assert publisher.main(["--date", "2026-09-04"]) == 0
    assert manual.main(["--date", "2026-09-04"]) == 0
    assert selected == [(publisher.ROOT / "review_src/data/custom.db").resolve()] * 2


def test_update_defers_canonical_artifact_writes_but_keeps_data_queries(tmp_path, monkeypatch):
    active = database(tmp_path / "market.db", "old")
    writer_lock = tmp_path / "writer.lock"
    monkeypatch.setattr(publisher, "DEFAULT_LOCK", writer_lock)
    app = FastAPI()
    app.add_middleware(MarketSnapshotMiddleware, database=active)
    writes = []
    @app.post("/api/bot/market-data/analysis/model-packet")
    def save_artifact():
        writes.append("sealed")
        return {"ok": True}
    @app.get("/api/bot/market-data/2330/daily")
    def read():
        with closing(connect(active, readonly=True)) as conn:
            return {"value": conn.execute("SELECT value FROM marker").fetchone()[0]}
    with TestClient(app) as client:
        with publisher.isolated_update_lock(writer_lock) as acquired:
            assert acquired
            response = client.post("/api/bot/market-data/analysis/model-packet")
            assert response.status_code == 503 and response.headers["Retry-After"] == "2"
            assert client.get("/api/bot/market-data/2330/daily").json() == {"value": "old"}
            assert not writes
        assert client.post("/api/bot/market-data/analysis/model-packet").status_code == 200
        assert writes == ["sealed"]
