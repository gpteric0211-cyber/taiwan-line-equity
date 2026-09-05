"""Portable lifecycle commands. Paths are relative to this project, not the shell."""

from __future__ import annotations
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from equity import ROOT, SOURCE, __version__, bootstrap

bootstrap()


def load_settings():
    from dotenv import load_dotenv

    load_dotenv(SOURCE / ".env", override=False)
    from core.line_bot_config import load_line_bot_env

    load_line_bot_env()
    from core.tls_config import configure_tls

    configure_tls()


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def initialize():
    for example, target in [
        (SOURCE / ".env.example", SOURCE / ".env"),
        (SOURCE / ".env.line_bot.example", ROOT / ".env.line_bot"),
    ]:
        if not target.exists():
            shutil.copyfile(example, target)
    load_settings()
    from core.db import init_db, db, DB_PATH
    from core.material_news_schema import archive_material_news
    from core.accounts_database import initialize as initialize_accounts
    from repository.portfolio_repository import initialize as initialize_portfolios
    from core.application_secrets import jwt_secret

    init_db()
    with closing(db()) as conn, conn:
        archived = archive_material_news(conn)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS equity_schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO equity_schema_migrations VALUES('1',?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
    accounts = initialize_accounts(DB_PATH)
    initialize_portfolios()
    jwt_secret()
    # A stable private token links the unified API to the shared analysis service.
    from dotenv import dotenv_values, set_key

    target = ROOT / ".env.line_bot"
    values = dotenv_values(target)
    if len(str(values.get("BOT_MARKET_DATA_TOKEN") or "")) < 32:
        set_key(target, "BOT_MARKET_DATA_TOKEN", secrets.token_urlsafe(48))
    print_json({"initialized": True, "account_rows": accounts, "archived_material_events": archived})


def doctor(full=False, services=False):
    import importlib.metadata
    from core.config import DB_PATH
    from core.accounts_database import path as account_path
    from core.portfolio_storage import database_path, key_path

    result = {"version": __version__, "python": sys.version.split()[0], "databases": {}, "dependencies": {}}
    for package in ("fastapi", "uvicorn", "requests", "pandas", "numpy", "cryptography", "Pillow", "tzdata"):
        try:
            result["dependencies"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["dependencies"][package] = "missing"
    for label, path in [("market", DB_PATH), ("accounts", account_path()), ("portfolio", database_path())]:
        item = {"exists": path.is_file()}
        if path.is_file():
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                item["integrity"] = [
                    row[0] for row in conn.execute("PRAGMA integrity_check" if full else "PRAGMA quick_check")
                ]
                item["foreign_key_violations"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
                item["tables"] = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
            item["bytes"] = path.stat().st_size
        result["databases"][label] = item
    result["portfolio_key_present"] = key_path().is_file()
    result["line_configured"] = bool(
        os.getenv("LINE_CHANNEL_SECRET") and os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    )
    if services:
        import requests

        base = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:8020/v1").rstrip("/")
        try:
            response = requests.get(base + "/models", timeout=5)
            response.raise_for_status()
            result["model_endpoint"] = "ok"
            result["models"] = [item.get("id") for item in response.json().get("data", [])]
        except (requests.RequestException, ValueError):
            result["model_endpoint"] = "unavailable"
    result["ok"] = (
        all(
            item.get("integrity") == ["ok"] and item.get("foreign_key_violations") == 0
            for item in result["databases"].values()
        )
        and "missing" not in result["dependencies"].values()
        and result["portfolio_key_present"]
    )
    print_json(result)
    return 0 if result["ok"] else 1


def serve(args):
    port = args.port
    # Updates belong to the shared scheduler/manual pipeline, never web startup.
    os.environ["AUTO_REFRESH_MARKET_DATA_ON_START"] = "0"
    os.environ["AUTO_UPDATE_TW50_ON_START"] = "0"
    # All internal Bot API calls stay on the same local service by default.
    os.environ["BOT_MARKET_DATA_BASE_URL"] = os.getenv("EQUITY_INTERNAL_BASE_URL", f"http://127.0.0.1:{port}")
    import uvicorn

    os.chdir(SOURCE)
    uvicorn.run("equity.application:create_app", factory=True, host=args.host, port=port, access_log=False)


def create_user(email):
    from core.accounts_database import db
    from auth.security import hash_password, password_policy_error

    password = getpass.getpass("Password: ")
    error = password_policy_error(password)
    if error:
        raise ValueError(error)
    if password != getpass.getpass("Repeat password: "):
        raise ValueError("Passwords do not match")
    now = time.time()
    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO users(email,hashed_password,is_verified,is_active,created_at,updated_at) VALUES(?,?,1,1,?,?)",
            (email.strip().lower(), hash_password(password), now, now),
        )
    print("Account created. Sign in at /portfolio.")


def update_market(args):
    from core.config import DB_PATH

    child_env = os.environ.copy()
    child_env["TAIWAN50_DB_PATH"] = str(DB_PATH)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "run_isolated_post_close_pipeline.py"),
        "--stage",
        args.stage,
        "--max-retries",
        str(args.max_retries),
    ]
    if args.stage == "finalize":
        command.append("--publish-official-core")
    if args.date:
        date.fromisoformat(args.date)
        command.extend(["--date", args.date])
    return subprocess.call(command, cwd=ROOT, env=child_env)


def backup(args):
    from core.config import DB_PATH
    from core.accounts_database import path as accounts_path
    from core.portfolio_storage import database_path
    from tools.migrate_legacy import snapshot

    destination = args.destination
    destination = (ROOT / destination).resolve() if not destination.is_absolute() else destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    from core.line_memory_config import line_memory_settings

    sources = [("market", DB_PATH), ("accounts", accounts_path()), ("portfolio", database_path())]
    memory = line_memory_settings()
    if memory.database_path.is_file():
        sources.append(("line-memory", memory.database_path))
    report = {}
    from core.database_access import DatabaseLease

    for name, path in sources:
        with DatabaseLease(path):
            report[name] = snapshot(path, destination / (name + ".sqlite3"))
    (destination / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print_json(
        {
            "backed_up": list(report),
            "keys_included": False,
            "note": "Keep encryption/signing keys separately. They are required to restore private data.",
        }
    )


def model_server():
    executable = os.getenv("OLLAMA_EXE_PATH")
    if executable:
        path = Path(executable)
        executable = str(path if path.is_absolute() else ROOT / path)
    else:
        bundled = ROOT / "runtime" / "ollama" / ("ollama.exe" if os.name == "nt" else "ollama")
        executable = str(bundled) if bundled.exists() else shutil.which("ollama")
    if not executable:
        raise RuntimeError("Ollama is not installed. Configure OLLAMA_EXE_PATH.")
    env = os.environ.copy()
    env["OLLAMA_MODELS"] = str((ROOT / os.getenv("EQUITY_MODELS_DIR", "models/ollama")).resolve())
    env.setdefault("OLLAMA_NO_CLOUD", "1")
    env.setdefault("OLLAMA_NOPRUNE", "1")
    env.setdefault("OLLAMA_MAX_QUEUE", "8")
    env["OLLAMA_HOST"] = os.getenv("QWEN_NATIVE_BASE_URL", "http://127.0.0.1:8020")
    return subprocess.call([executable, "serve"], cwd=ROOT, env=env)


def main():
    load_settings()
    parser = argparse.ArgumentParser(
        prog="python -m equity", description="Portable Taiwan stock, LINE and portfolio system"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "init", help="Initialize schemas and migrate existing accounts without deleting market data"
    )
    check = sub.add_parser("doctor", help="Check local dependencies and DB integrity")
    check.add_argument("--full", action="store_true")
    check.add_argument("--services", action="store_true")
    web = sub.add_parser("serve", help="Serve mobile web, Bot API and LINE webhook together")
    web.add_argument("--host", default=os.getenv("EQUITY_HOST", "127.0.0.1"))
    web.add_argument("--port", type=int, default=int(os.getenv("EQUITY_PORT", "8056")))
    for command, description in [("run", "Run the shared stack in the foreground"),
                                 ("start", "Ensure one background stack; safe to repeat")]:
        run = sub.add_parser(command, help=description)
        run.add_argument("--host", default=os.getenv("EQUITY_HOST", "127.0.0.1"))
        run.add_argument("--port", type=int, default=int(os.getenv("EQUITY_PORT", "8056")))
        run.add_argument("--no-model", action="store_true")
        run.add_argument("--no-schedule", action="store_true")
        if command == "start":
            run.add_argument("--open-browser", action="store_true")
    user = sub.add_parser(
        "create-user", help="Create a verified local account; password is prompted privately"
    )
    user.add_argument("--email", required=True)
    update = sub.add_parser(
        "update", help="Run the inherited isolated market update with integrity-gated publication"
    )
    update.add_argument("--stage", choices=["capture", "finalize"], default="finalize")
    update.add_argument("--date")
    update.add_argument("--max-retries", type=int, default=0)
    news = sub.add_parser(
        "news", help="Update news, official company events and global context with the shared writer lock"
    )
    news.add_argument("--dry-run", action="store_true")
    news.add_argument("--lock-wait-seconds", type=float, default=0)
    tdcc = sub.add_parser("tdcc", help="Update weekly official TDCC holdings distribution")
    tdcc.add_argument("--mode", choices=["tw50", "watchlist", "all"], default="tw50")
    tdcc.add_argument("--dry-run", action="store_true")
    tdcc.add_argument("--lock-wait-seconds", type=float, default=0)
    save = sub.add_parser("backup", help="Make integrity-checked DB snapshots; encryption keys are excluded")
    save.add_argument("destination", type=Path)
    scheduled = sub.add_parser("schedule", help="Run the local daily capture/finalization scheduler")
    scheduled.add_argument("--once", action="store_true")
    tunnel = sub.add_parser("tunnel", help="Start an optional temporary HTTPS Quick Tunnel")
    tunnel.add_argument("--port", type=int, default=int(os.getenv("EQUITY_PORT", "8056")))
    sub.add_parser("warmup", help="Preload configured models before interactive requests")
    sub.add_parser(
        "model-server", help="Start the configured local Ollama runtime without sending LINE messages"
    )
    args = parser.parse_args()
    if args.command == "init":
        initialize()
        return 0
    if args.command == "doctor":
        return doctor(args.full, args.services)
    if args.command == "serve":
        serve(args)
        return 0
    if args.command == "run":
        from equity.supervisor import main as run_services

        return run_services(args)
    if args.command == "start":
        from equity.lifecycle import ensure_started

        print_json(ensure_started(args))
        return 0
    if args.command == "create-user":
        create_user(args.email)
        return 0
    if args.command == "update":
        return update_market(args)
    if args.command == "news":
        from scripts.update_external_analysis_context import main as update_news

        arguments = [
            "--lock-wait-seconds",
            str(args.lock_wait_seconds),
            "--report",
            str(ROOT / "var" / "jobs" / "external-context-latest.json"),
        ]
        if args.dry_run:
            arguments.append("--dry-run")
        return update_news(arguments)
    if args.command == "tdcc":
        from scripts.update_tdcc_equity_concentration import main as update_tdcc

        arguments = [
            "--mode",
            args.mode,
            "--source",
            "tdcc",
            "--lock-wait-seconds",
            str(args.lock_wait_seconds),
        ]
        if args.dry_run:
            arguments.append("--dry-run")
        return update_tdcc(arguments)
    if args.command == "backup":
        backup(args)
        return 0
    if args.command == "schedule":
        from equity.scheduler import main as run_scheduler

        return run_scheduler(args.once)
    if args.command == "tunnel":
        from equity.public_endpoint import main as run_tunnel

        return run_tunnel(args.port)
    if args.command == "warmup":
        from equity.model_runtime import warmup

        print_json(warmup())
        return 0
    if args.command == "model-server":
        return model_server()
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"Operation failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        raise SystemExit(1)
