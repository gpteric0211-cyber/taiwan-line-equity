from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "review_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "review_src"))
from core.database_access import DatabaseLease
DEFAULT_ACTIVE_DB = ROOT / "review_src" / "data" / "taiwan50.db"
DEFAULT_PIPELINE = ROOT / "scripts" / "run_post_close_daily_pipeline.py"
DEFAULT_LOCK = ROOT / "logs" / "post_close_scheduler" / "isolated_publish.lock"
DEFAULT_REPORT = (
    ROOT / "logs" / "post_close_scheduler" / "isolated_update_latest.json"
)
INTEGRITY_FAILURE_EXIT_CODE = 10
PUBLISH_FAILURE_EXIT_CODE = 11


def full_integrity_messages(path: Path) -> list[str]:
    target = path.resolve()
    uri = f"{target.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=60)) as conn:
        return [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]


def quick_integrity_messages(path: Path) -> list[str]:
    target = path.resolve()
    uri = f"{target.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=60)) as conn:
        return [str(row[0]) for row in conn.execute("PRAGMA quick_check")]


def _set_journal_mode(path: Path, mode: str) -> str:
    with closing(sqlite3.connect(path, timeout=60)) as conn:
        conn.execute("PRAGMA busy_timeout=60000")
        if mode.lower() == "delete":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        actual = str(conn.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]).lower()
        conn.execute("PRAGMA synchronous=FULL")
        conn.commit()
    return actual


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


@contextmanager
def isolated_update_lock(
    path: Path,
    *,
    wait_seconds: float = 0,
    poll_seconds: float = 1,
) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        while True:
            if os.name == "nt":
                import msvcrt

                if path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    acquired = False
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    acquired = False
            if acquired or time.monotonic() >= deadline:
                break
            time.sleep(max(0.05, min(float(poll_seconds), deadline - time.monotonic())))
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def create_consistent_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    with closing(
        sqlite3.connect(source_uri, uri=True, timeout=60)
    ) as source_conn:
        with closing(sqlite3.connect(destination, timeout=60)) as destination_conn:
            source_conn.backup(destination_conn, pages=4096, sleep=0.05)
            destination_conn.execute("PRAGMA synchronous=FULL")
            destination_conn.commit()


def requested_lock_wait_seconds(pipeline_args: list[str]) -> float:
    for index, argument in enumerate(pipeline_args):
        if argument == "--lock-wait-seconds" and index + 1 < len(pipeline_args):
            try:
                return max(0.0, float(pipeline_args[index + 1]))
            except ValueError:
                return 0.0
        if argument.startswith("--lock-wait-seconds="):
            try:
                return max(0.0, float(argument.split("=", 1)[1]))
            except ValueError:
                return 0.0
    return 0.0


def publish_candidate(
    candidate: Path,
    active: Path,
    *,
    expected_active_stat: tuple[int, int],
    retry_seconds: int = 60,
    integrity_verified: bool = False,
) -> Path:
    if _set_journal_mode(candidate, "DELETE") != "delete":
        raise RuntimeError("candidate database could not enter DELETE publication mode")
    integrity_probe = quick_integrity_messages if integrity_verified else full_integrity_messages
    if integrity_probe(candidate) != ["ok"]:
        raise RuntimeError("candidate failed final integrity_check before publication")
    # Build/check the candidate while clients keep reading. Only the file switch
    # drains existing connections and temporarily waits new analysis requests.
    with DatabaseLease(active, exclusive=True, timeout=retry_seconds):
        return _publish_verified_candidate(candidate, active, expected_active_stat=expected_active_stat,
                                           retry_seconds=retry_seconds)


def _publish_verified_candidate(candidate, active, *, expected_active_stat, retry_seconds):
    current = active.stat()
    if (current.st_size, current.st_mtime_ns) != expected_active_stat:
        raise RuntimeError(
            "active database changed while the isolated update was running"
        )
    wal_sidecar = Path(str(active) + "-wal")
    if wal_sidecar.exists() and wal_sidecar.stat().st_size > 0:
        raise RuntimeError(
            f"active database has a non-empty WAL during publication: {wal_sidecar.name}"
        )
    # A WAL shared-memory file is normally 32 KiB even when the WAL itself is
    # empty. It contains only a rebuildable index, not committed database rows.
    # Refusing every non-empty -shm therefore prevents safe Windows publication
    # after a clean checkpoint. Unlink both sidecars only after proving WAL is
    # empty; Windows will still reject this if another process has either open.
    wal_sidecar.unlink(missing_ok=True)
    Path(str(active) + "-shm").unlink(missing_ok=True)
    previous = active.with_name(f"{active.stem}.previous{active.suffix}")
    previous.unlink(missing_ok=True)
    os.link(active, previous)
    deadline = time.monotonic() + max(0, retry_seconds)
    while True:
        try:
            os.replace(candidate, active)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                previous.unlink(missing_ok=True)
                raise
            time.sleep(1)
        except BaseException:
            previous.unlink(missing_ok=True)
            raise
    # os.replace moved the already verified file, without copying/changing its
    # bytes. Re-reading all GB here would unnecessarily block LINE data queries.
    return previous


def run_isolated_update(
    pipeline_args: list[str],
    *,
    active: Path = DEFAULT_ACTIVE_DB,
    pipeline: Path = DEFAULT_PIPELINE,
    lock_path: Path = DEFAULT_LOCK,
    report_path: Path = DEFAULT_REPORT,
) -> int:
    active = active.resolve()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    result: dict[str, Any] = {
        "status": "running",
        "started_at": started_at,
        "active_database": str(active),
        "pipeline_args": list(pipeline_args),
        "published": False,
    }
    lock_wait_seconds = requested_lock_wait_seconds(pipeline_args)
    result["isolation_lock_wait_seconds"] = lock_wait_seconds
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with isolated_update_lock(lock_path, wait_seconds=lock_wait_seconds) as acquired:
        if not acquired:
            result.update(
                status="isolated_update_lock_busy",
                exit_code=4,
                finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            report_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return 4
        try:
            if full_integrity_messages(active) != ["ok"]:
                raise RuntimeError("active database failed preflight integrity_check")
            active_stat = active.stat()
            expected_active_stat = (active_stat.st_size, active_stat.st_mtime_ns)
            staging_dir = active.parent / ".update_staging"
            staging_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            candidate = staging_dir / f"{active.stem}.{stamp}.{os.getpid()}.db"
            result["candidate_database"] = str(candidate)
            create_consistent_snapshot(active, candidate)
            if quick_integrity_messages(candidate) != ["ok"]:
                raise RuntimeError("isolated snapshot failed quick_check")
            if _set_journal_mode(candidate, "WAL") != "wal":
                raise RuntimeError("isolated snapshot could not enable WAL mode")
            environment = os.environ.copy()
            environment["TAIWAN50_DB_PATH"] = str(candidate)
            completed = subprocess.run(
                [sys.executable, str(pipeline), *pipeline_args],
                cwd=ROOT,
                env=environment,
            )
            result["pipeline_exit_code"] = int(completed.returncode)
            if completed.returncode != 0:
                result["status"] = "pipeline_failed_candidate_not_published"
                result["exit_code"] = int(completed.returncode)
                return int(completed.returncode)
            if full_integrity_messages(candidate) != ["ok"]:
                result["status"] = "candidate_integrity_failed_not_published"
                result["exit_code"] = INTEGRITY_FAILURE_EXIT_CODE
                return INTEGRITY_FAILURE_EXIT_CODE
            previous = publish_candidate(
                candidate,
                active,
                expected_active_stat=expected_active_stat,
                integrity_verified=True,
            )
            result.update(
                status="published",
                published=True,
                previous_database=str(previous),
                exit_code=0,
            )
            return 0
        except Exception as exc:
            result.update(
                status="isolated_update_failed",
                error_class=type(exc).__name__,
                error=str(exc),
                exit_code=(
                    INTEGRITY_FAILURE_EXIT_CODE
                    if "integrity" in str(exc).lower()
                    else PUBLISH_FAILURE_EXIT_CODE
                ),
            )
            return int(result["exit_code"])
        finally:
            candidate_text = result.get("candidate_database")
            if candidate_text:
                candidate_path = Path(str(candidate_text))
                if candidate_path.exists() and not result.get("published"):
                    candidate_path.unlink(missing_ok=True)
                _remove_sidecars(candidate_path)
            result["finished_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            report_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def main(argv: list[str] | None = None) -> int:
    from core.market_database_config import resolve_market_db_path

    active = resolve_market_db_path(base_dir=ROOT / "review_src")
    return run_isolated_update(list(argv if argv is not None else sys.argv[1:]), active=active)


if __name__ == "__main__":
    raise SystemExit(main())
