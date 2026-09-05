"""Idempotent desktop launch; the server outlives the browser and launcher."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

from equity import ROOT, SOURCE
from scripts.run_isolated_post_close_pipeline import isolated_update_lock


def instance_key():
    from core.market_database_config import resolve_market_db_path

    database = resolve_market_db_path(base_dir=SOURCE)
    return hashlib.sha256(f"{ROOT.resolve()}\n{database}".encode()).hexdigest()


def base_url(host, port):
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    return f"http://{'[' + host + ']' if ':' in host else host}:{port}"


def health(url):
    try:
        with urlopen(url + "/healthz", timeout=3) as response:
            value = json.loads(response.read(16384))
    except HTTPError as exc:
        raise RuntimeError("The configured port belongs to another HTTP service") from exc
    except URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return None
        raise RuntimeError("The service port is occupied or unavailable; inspect var/services") from exc
    except (ValueError, TimeoutError) as exc:
        raise RuntimeError("The configured port did not return a valid service identity") from exc
    if not isinstance(value, dict) or value.get("service") != "taiwan-line-equity" or value.get("instance_key") != instance_key():
        raise RuntimeError("The configured port serves a different project, DB, or older runtime; stop its owner first")
    return value


def supervisor_running(location):
    with isolated_update_lock(location / "supervisor.lock") as acquired:
        return not acquired


def launch_background(args, location):
    arguments = ["--host", args.host, "--port", str(args.port)]
    if args.no_model:
        arguments.append("--no-model")
    if args.no_schedule:
        arguments.append("--no-schedule")
    options = dict(cwd=ROOT, stdin=subprocess.DEVNULL)
    if os.name == "nt":
        command = ["powershell", "-NoLogo", "-NoProfile", "-File",
                   str(ROOT / "tools" / "run_windows_service.ps1"), *arguments]
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        command = [sys.executable, "-u", "-m", "equity", "run", *arguments]
        options["start_new_session"] = True
    with (location / "launch.log").open("a", encoding="utf-8") as log:
        return subprocess.Popen(command, stdout=log, stderr=log, **options)


def ensure_started(args):
    location = ROOT / "var" / "services"
    location.mkdir(parents=True, exist_ok=True)
    timeout = max(1, int(os.getenv("EQUITY_START_TIMEOUT_SECONDS", "1000")))
    url = base_url(args.host, args.port)
    with isolated_update_lock(location / "launch.lock", wait_seconds=timeout) as acquired:
        if not acquired:
            raise RuntimeError("Another launcher is still waiting for startup; inspect var/services")
        running = supervisor_running(location)
        status = health(url)
        if running:
            try:
                record = json.loads((location / "runtime.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = {}
            if record.get("port", args.port) != args.port:
                raise RuntimeError("The shared stack is running on another port; restart its owner to change configuration")
        if status and not running:
            raise RuntimeError("A standalone web server owns this port; stop it before starting the shared stack")
        child = None
        if not running:
            child = launch_background(args, location)
        deadline = time.monotonic() + timeout
        while True:
            status = health(url)
            if status and status.get("runtime_id"):
                record_path = location / "runtime.json"
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    record = {}
                if (record.get("runtime_id") == status["runtime_id"] and record.get("state") == "running"
                        and supervisor_running(location)):
                    result = {"state": "running", "reused": running, "pid": record["pid"],
                              "dashboard": url + "/", "portfolio": url + "/portfolio"}
                    if args.open_browser:
                        webbrowser.open(url + "/")
                    return result
            if child is not None and child.poll() is not None and not supervisor_running(location):
                raise RuntimeError("Background startup failed; inspect var/services/launch.log and supervisor logs")
            if time.monotonic() >= deadline:
                raise RuntimeError("Startup is not ready yet; inspect var/services before retrying")
            time.sleep(0.5)
