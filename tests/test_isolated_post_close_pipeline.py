from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_isolated_post_close_pipeline as isolated  # noqa: E402


def _create_database(path: Path, value: str) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker VALUES(?)", (value,))
        conn.commit()


def test_isolated_update_publishes_only_integrity_checked_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    active = tmp_path / "active.db"
    _create_database(active, "before")

    def run_pipeline(_command, *, cwd, env):
        assert cwd == isolated.ROOT
        candidate = Path(env["TAIWAN50_DB_PATH"])
        with closing(sqlite3.connect(candidate)) as conn:
            conn.execute("UPDATE marker SET value='after'")
            conn.commit()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(isolated.subprocess, "run", run_pipeline)

    code = isolated.run_isolated_update(
        ["--stage", "finalize"],
        active=active,
        pipeline=tmp_path / "pipeline.py",
        lock_path=tmp_path / "update.lock",
        report_path=tmp_path / "report.json",
    )

    assert code == 0
    assert isolated.full_integrity_messages(active) == ["ok"]
    with closing(sqlite3.connect(active)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "after"
    with closing(sqlite3.connect(tmp_path / "active.previous.db")) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "before"


def test_isolated_update_keeps_active_when_candidate_pipeline_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    active = tmp_path / "active.db"
    _create_database(active, "before")
    monkeypatch.setattr(
        isolated.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=4),
    )

    code = isolated.run_isolated_update(
        ["--stage", "capture"],
        active=active,
        pipeline=tmp_path / "pipeline.py",
        lock_path=tmp_path / "update.lock",
        report_path=tmp_path / "report.json",
    )

    assert code == 4
    with closing(sqlite3.connect(active)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "before"


def test_publish_allows_rebuildable_shm_when_wal_is_empty(tmp_path: Path) -> None:
    active = tmp_path / "active.db"
    candidate = tmp_path / "candidate.db"
    _create_database(active, "before")
    _create_database(candidate, "after")
    Path(str(active) + "-wal").write_bytes(b"")
    Path(str(active) + "-shm").write_bytes(b"x" * 32768)
    active_stat = active.stat()

    previous = isolated.publish_candidate(
        candidate,
        active,
        expected_active_stat=(active_stat.st_size, active_stat.st_mtime_ns),
        retry_seconds=0,
    )

    assert previous == tmp_path / "active.previous.db"
    assert not Path(str(active) + "-wal").exists()
    assert not Path(str(active) + "-shm").exists()
    with closing(sqlite3.connect(active)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "after"


def test_pipeline_lock_wait_is_reused_by_isolation_layer() -> None:
    assert isolated.requested_lock_wait_seconds(
        ["--stage", "finalize", "--lock-wait-seconds", "3600"]
    ) == 3600
    assert isolated.requested_lock_wait_seconds(
        ["--stage", "capture", "--lock-wait-seconds=10200"]
    ) == 10200
