from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = REPO_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import safe_error  # noqa: E402
from repository.watchlist_repository import get_watchlist_codes  # noqa: E402
try:  # Support both ``python scripts/x.py`` and ``import scripts.x``.
    from scripts.run_isolated_post_close_pipeline import (  # type: ignore
        DEFAULT_LOCK as MARKET_DATABASE_UPDATE_LOCK,
        isolated_update_lock,
    )
except ModuleNotFoundError:
    from run_isolated_post_close_pipeline import (  # type: ignore
        DEFAULT_LOCK as MARKET_DATABASE_UPDATE_LOCK,
        isolated_update_lock,
    )


def normalize_codes(value: str | list[str] | None) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,;\s]+", str(value or ""))
    out: list[str] = []
    for item in raw:
        code = str(item or "").strip().zfill(4)
        if re.fullmatch(r"\d{4}", code) and code not in out:
            out.append(code)
    return out


def resolve_codes(mode: str, explicit_codes: str | None = None) -> list[str]:
    if explicit_codes:
        return normalize_codes(explicit_codes)
    if mode != "watchlist":
        raise ValueError(f"unsupported mode: {mode}")
    return normalize_codes(get_watchlist_codes())


def build_command(args: argparse.Namespace, codes: list[str]) -> list[str]:
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "update_fugle_intraday_supplemental.py"),
        "--codes",
        ",".join(codes),
        "--output",
        str(args.output),
        "--evidence-codes",
        str(args.evidence_codes),
        "--evidence-max-age-seconds",
        str(args.evidence_max_age_seconds),
        "--window-start",
        str(args.window_start),
        "--window-end",
        str(args.window_end),
        "--capture-phase",
        str(args.capture_phase),
    ]
    command.append("--dry-run" if args.dry_run else "--write")
    if args.include_quote:
        command.append("--include-quote")
    return command


def _run(args: argparse.Namespace) -> int:
    try:
        codes = resolve_codes(args.mode, args.codes)
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "WATCHLIST_READ_FAILED", "error": safe_error(exc)}, ensure_ascii=False))
        return 2
    if not codes:
        print(json.dumps({"ok": True, "status": "SKIPPED_EMPTY_WATCHLIST", "writes_db": False}, ensure_ascii=False))
        return 0

    args.output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    completed = subprocess.run(build_command(args, codes), cwd=REPO_ROOT)
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the gated Fugle supplemental update for the local watchlist.")
    parser.add_argument("--mode", choices=["watchlist"], default="watchlist")
    parser.add_argument("--codes", help="Optional explicit codes; default reads the local watchlist at run time.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-quote", action="store_true")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "docs" / "FUGLE_WATCHLIST_UPDATE_REPORT.md")
    parser.add_argument("--evidence-codes", default="0050,2330")
    parser.add_argument("--evidence-max-age-seconds", type=int, default=300)
    parser.add_argument("--capture-phase", choices=["intraday", "post_close"], default="post_close")
    parser.add_argument("--window-start", default="13:31")
    parser.add_argument("--window-end", default="14:10")
    parser.add_argument("--lock-wait-seconds", type=float, default=3300)
    args = parser.parse_args(argv)

    if args.dry_run:
        return _run(args)
    with isolated_update_lock(
        MARKET_DATABASE_UPDATE_LOCK,
        wait_seconds=max(0.0, args.lock_wait_seconds),
    ) as acquired:
        if not acquired:
            print(json.dumps({"ok": False, "status": "MARKET_DATABASE_UPDATE_LOCK_BUSY"}))
            return 4
        return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
