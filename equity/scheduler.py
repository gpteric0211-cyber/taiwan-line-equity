"""Restart-safe local market scheduler; market write locking stays in the inherited pipeline."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
from equity.processes import run_bounded
from equity import ROOT, bootstrap

bootstrap()
TPE = ZoneInfo("Asia/Taipei")


@contextmanager
def scheduler_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("A scheduler is already running for this project") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def parse_time(value):
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.hour, parsed.minute


def run_due(
    now, state, *, is_trading_day, run_stage, schedules, window_end, retry_seconds=1800, checkpoint=None
):
    """Pure scheduling decisions except for the injected bounded stage runner."""
    now = now.astimezone(TPE)
    day = now.date().isoformat()
    end = now.replace(hour=window_end[0], minute=window_end[1], second=59, microsecond=0)
    if now > end:
        return state
    trading_day = is_trading_day(now.date())
    news_due = [
        stage
        for stage, clock in schedules.items()
        if stage.startswith("news_") and (now.hour, now.minute) >= clock
    ]
    latest_news = max(news_due, key=lambda stage: schedules[stage]) if news_due else None
    for stage, clock in schedules.items():
        if stage.startswith("news_"):
            if stage != latest_news:
                continue
        elif not stage.startswith("tdcc_") and not trading_day:
            continue
        due = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        if stage.startswith("tdcc_"):
            # One checkpoint per Friday publication, including restart catch-up.
            due -= timedelta(days=(now.weekday() - 4) % 7)
            if due > now:
                due -= timedelta(days=7)
        key = due.date().isoformat() + ":" + stage
        prior = state.get(key, {})
        if now < due or prior.get("completed") or now.timestamp() < prior.get("retry_after", 0):
            continue
        code = run_stage(stage, day)
        state[key] = {
            "completed": code == 0,
            "exit_code": code,
            "attempts": int(prior.get("attempts", 0)) + 1,
            "finished_at": datetime.now(TPE).isoformat(),
            "retry_after": now.timestamp() + retry_seconds,
        }
        if checkpoint:
            checkpoint(state)
    cutoff = (now.date() - timedelta(days=32)).isoformat()
    return {key: value for key, value in state.items() if key[:10] >= cutoff}


def atomic_state(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(once=False):
    from core.market_session import is_taiwan_trading_day

    location = ROOT / "var" / "jobs"
    location.mkdir(parents=True, exist_ok=True)
    state_path = location / "daily-state.json"
    schedules = {
        "capture": parse_time(os.getenv("EQUITY_CAPTURE_TIME", "15:05")),
        "finalize": parse_time(os.getenv("EQUITY_FINALIZE_TIME", "18:30")),
    }
    for value in os.getenv("EQUITY_NEWS_TIMES", "06:15,09:00,11:30,14:00,18:00").split(","):
        clock = parse_time(value.strip())
        schedules["news_" + value.strip().replace(":", "")] = clock
    schedules["tdcc_tw50"] = parse_time(os.getenv("EQUITY_TDCC_TW50_TIME", "18:30"))
    schedules["tdcc_watchlist"] = parse_time(os.getenv("EQUITY_TDCC_WATCHLIST_TIME", "18:35"))
    window_end = parse_time(os.getenv("EQUITY_WINDOW_END", "23:59"))
    retry = max(60, int(os.getenv("EQUITY_RETRY_SECONDS", "1800")))

    def run_stage(stage, day):
        log_path = location / (day + "-" + stage + ".log")
        with log_path.open("a", encoding="utf-8") as log:
            arguments = (
                ["news", "--lock-wait-seconds", "0"]
                if stage.startswith("news_")
                else ["update", "--stage", stage, "--date", day, "--max-retries", "0"]
            )
            if stage.startswith("tdcc_"):
                arguments = ["tdcc", "--mode", stage.removeprefix("tdcc_"), "--lock-wait-seconds", "0"]
            timeout = int(
                os.getenv("EQUITY_NEWS_TIMEOUT_SECONDS", "900")
                if stage.startswith("news_")
                else os.getenv("EQUITY_JOB_TIMEOUT_SECONDS", "7200")
            )
            code = run_bounded(
                [sys.executable, "-m", "equity", *arguments],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            if code == 124:
                log.write("\nJob exceeded its configured time budget; its process tree was stopped.\n")
            return code

    with scheduler_lock(location / "scheduler.lock"):
        while True:
            from repository.portfolio_repository import PortfolioStore

            PortfolioStore().cleanup_expired()
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            state = run_due(
                datetime.now(TPE),
                state,
                is_trading_day=is_taiwan_trading_day,
                run_stage=run_stage,
                schedules=schedules,
                window_end=window_end,
                retry_seconds=retry,
                checkpoint=lambda value: atomic_state(state_path, value),
            )
            atomic_state(state_path, state)
            atomic_state(
                location / "heartbeat.json",
                {"checked_at": datetime.now(TPE).isoformat(), "state": "running", "stages": list(schedules)},
            )
            if once:
                return 0
            time.sleep(30)
