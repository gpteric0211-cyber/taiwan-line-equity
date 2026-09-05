from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
SERVER_READY_TIMEOUT_SECONDS = 30
DASHBOARD_PRELOAD_TIMEOUT_SECONDS = 600
DASHBOARD_PRELOAD_RETRY_SECONDS = 5
DASHBOARD_REPAIR_RETRY_SECONDS = 60
DASHBOARD_REPAIR_MAX_ATTEMPTS = 3
DEPENDENCY_CHECK_CODE = (
    "import sys; print(sys.executable); "
    "import uvicorn; import fastapi; "
    "print('__PORTABLE_DEPENDENCY_CHECK_OK__')"
)
APP_IMPORT_CHECK_CODE = (
    "import sys; print(sys.executable); "
    "import app; "
    "print('APP_IMPORT_OK')"
)


@dataclass(frozen=True)
class PythonCandidate:
    executable: str
    source: str


@dataclass(frozen=True)
class RuntimeContext:
    mode: str
    base_dir: Path
    app_cwd: Path
    candidates: list[PythonCandidate]


@dataclass(frozen=True)
class DependencyCheck:
    ok: bool
    output: str


@dataclass(frozen=True)
class AppImportCheck:
    ok: bool
    command: list[str]
    output: str


def is_windows() -> bool:
    return os.name == "nt"


def portable_python_candidates(base_dir: Path) -> list[PythonCandidate]:
    if is_windows():
        return [
            PythonCandidate(str(base_dir / "python" / "python.exe"), "python_runtime"),
            PythonCandidate(str(base_dir / ".venv" / "Scripts" / "python.exe"), "venv"),
        ]
    return [
        PythonCandidate(str(base_dir / "python" / "bin" / "python"), "python_runtime"),
        PythonCandidate(str(base_dir / ".venv" / "bin" / "python"), "venv"),
    ]


def development_python_candidates(base_dir: Path) -> list[PythonCandidate]:
    candidates: list[PythonCandidate]
    if is_windows():
        candidates = [
            PythonCandidate(str(base_dir / ".venv" / "Scripts" / "python.exe"), "repo_venv"),
            PythonCandidate(str(base_dir / "review_src" / ".venv" / "Scripts" / "python.exe"), "review_src_venv"),
            PythonCandidate(sys.executable, "current_python"),
            PythonCandidate("python", "system_python"),
        ]
    else:
        candidates = [
            PythonCandidate(str(base_dir / ".venv" / "bin" / "python"), "repo_venv"),
            PythonCandidate(str(base_dir / "review_src" / ".venv" / "bin" / "python"), "review_src_venv"),
            PythonCandidate(sys.executable, "current_python"),
            PythonCandidate("python3", "system_python3"),
            PythonCandidate("python", "system_python"),
        ]
    deduped: list[PythonCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.executable.lower() if is_windows() else candidate.executable
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def command_exists(executable: str) -> bool:
    path = Path(executable)
    if path.is_absolute() or any(part in executable for part in ("/", "\\")):
        return path.exists()
    return shutil.which(executable) is not None


def resolve_runtime_context() -> RuntimeContext:
    base_dir = Path(__file__).resolve().parent
    portable_app = base_dir / "app" / "review_src"
    if (portable_app / "app.py").exists():
        return RuntimeContext(
            mode="portable",
            base_dir=base_dir,
            app_cwd=portable_app,
            candidates=portable_python_candidates(base_dir),
        )
    dev_app = base_dir / "review_src"
    if (dev_app / "app.py").exists():
        return RuntimeContext(
            mode="development",
            base_dir=base_dir,
            app_cwd=dev_app,
            candidates=development_python_candidates(base_dir),
        )
    cwd_app = Path.cwd() / "review_src"
    if (cwd_app / "app.py").exists():
        return RuntimeContext(
            mode="development",
            base_dir=Path.cwd(),
            app_cwd=cwd_app,
            candidates=development_python_candidates(Path.cwd()),
        )
    raise FileNotFoundError("Cannot find review_src in this folder or in app/review_src.")


def check_dependencies(candidate: PythonCandidate) -> DependencyCheck:
    if not command_exists(candidate.executable):
        return DependencyCheck(False, "not found")
    try:
        result = subprocess.run(
            [candidate.executable, "-c", DEPENDENCY_CHECK_CODE],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return DependencyCheck(False, str(exc))
    output = (result.stdout + result.stderr).strip()
    ok = result.returncode == 0 and "__PORTABLE_DEPENDENCY_CHECK_OK__" in output
    return DependencyCheck(ok, output)


def select_python(context: RuntimeContext) -> tuple[PythonCandidate | None, DependencyCheck]:
    last_check = DependencyCheck(False, "no candidates checked")
    for candidate in context.candidates:
        check = check_dependencies(candidate)
        last_check = check
        if check.ok:
            return candidate, check
    return None, last_check


def check_app_import(candidate: PythonCandidate, app_cwd: Path) -> AppImportCheck:
    command = [candidate.executable, "-c", APP_IMPORT_CHECK_CODE]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(app_cwd),
        )
    except Exception as exc:
        return AppImportCheck(False, command, str(exc))
    output = (result.stdout + result.stderr).strip()
    ok = result.returncode == 0 and "APP_IMPORT_OK" in output
    return AppImportCheck(ok, command, output)


def portable_error() -> str:
    return (
        "Portable Python environment not found or incomplete.\n"
        "Please rebuild the portable package."
    )


def build_command(host: str, port: int) -> tuple[RuntimeContext, PythonCandidate, DependencyCheck, AppImportCheck, list[str], str]:
    context = resolve_runtime_context()
    candidate, check = select_python(context)
    if candidate is None:
        if context.mode == "portable":
            raise RuntimeError(portable_error())
        raise RuntimeError(f"No Python candidate can import uvicorn and fastapi. Last check: {check.output}")
    app_check = check_app_import(candidate, context.app_cwd)
    if not app_check.ok:
        raise RuntimeError(
            "App import check failed before launching uvicorn.\n"
            f"Selected Python: {candidate.executable}\n"
            f"Python source: {candidate.source}\n"
            f"App cwd: {context.app_cwd}\n"
            "Command: " + " ".join(app_check.command) + "\n"
            f"Output: {app_check.output}"
        )
    url = f"http://{host}:{port}"
    command = [
        candidate.executable,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        host,
        "--port",
        str(port),
        "--workers",
        "1",
    ]
    return context, candidate, check, app_check, command, url


def wait_until_ready(url: str, timeout_seconds: int = SERVER_READY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def dashboard_payload_ready(payload: dict[str, object]) -> bool:
    """Accept complete rows, including an explicitly filtered partial universe."""
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    checked = int(readiness.get("checked") or payload.get("checked") or 0)
    if readiness.get("global_issues") or payload.get("display_ready") is False:
        return False
    ready = bool(payload.get("ready")) and bool(readiness.get("ready", True))
    if ready and checked > 0 and len(rows) == checked:
        return True
    if payload.get("display_ready") is not True or payload.get("partial_ready") is not True:
        return False
    codes = [str(row.get("code", "")) for row in rows if isinstance(row, dict)]
    hidden = [str(code) for code in payload.get("hidden_codes") or []]
    failed = [str(code) for code in readiness.get("failed_codes") or []]
    return bool(
        rows and hidden and len(codes) == len(rows) == len(set(codes))
        and len(hidden) == len(set(hidden)) == int(readiness.get("fail_count") or 0)
        and set(hidden) == set(failed) and not set(codes).intersection(hidden)
        and len(rows) == int(readiness.get("pass_count") or 0)
        and len(rows) + len(hidden) == checked
    )


def _request_json(url: str, *, payload: dict[str, object] | None = None, timeout: int = 120) -> dict[str, object]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def wait_until_dashboard_data_ready(
    base_url: str,
    *,
    timeout_seconds: int = DASHBOARD_PRELOAD_TIMEOUT_SECONDS,
) -> tuple[bool, dict[str, object]]:
    """Open once complete rows can display; do not repair excluded analyses."""

    deadline = time.monotonic() + max(int(timeout_seconds), 1)
    next_repair_at = 0.0
    repair_attempts = 0
    last_summary: dict[str, object] = {}
    last_progress: tuple[int, int] | None = None
    quotes_url = f"{base_url}/api/quotes?mode=tw50"
    repair_url = f"{base_url}/api/update/ensure-complete"
    while time.monotonic() < deadline:
        try:
            status_payload = _request_json(f"{base_url}/api/status", timeout=30)
            statuses = status_payload.get("statuses") if isinstance(status_payload.get("statuses"), dict) else {}
            official_history = statuses.get("tw50_official_history") if isinstance(statuses.get("tw50_official_history"), dict) else {}
            if official_history.get("status") == "loading":
                message = str(official_history.get("message") or "Official history preload is running")
                if last_summary.get("official_history") != message:
                    print(f"Data preload: {message}")
                last_summary = {"official_history": message}
                time.sleep(DASHBOARD_PRELOAD_RETRY_SECONDS)
                continue
            payload = _request_json(quotes_url)
            readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
            last_summary = {
                "ready": bool(payload.get("ready")),
                "display_ready": bool(payload.get("display_ready")),
                "partial_ready": bool(payload.get("partial_ready")),
                "checked": int(readiness.get("checked") or 0),
                "pass_count": int(readiness.get("pass_count") or 0),
                "fail_count": int(readiness.get("fail_count") or 0),
                "failed_codes": list(readiness.get("failed_codes") or []),
            }
            if dashboard_payload_ready(payload):
                return True, last_summary
            progress = (int(last_summary["pass_count"]), int(last_summary["fail_count"]))
            if progress != last_progress:
                print(f"Data preload: {progress[0]}/{last_summary['checked']} ready; {progress[1]} incomplete.")
                last_progress = progress
            now = time.monotonic()
            if repair_attempts < DASHBOARD_REPAIR_MAX_ATTEMPTS and now >= next_repair_at:
                _request_json(repair_url, payload={"mode": "tw50", "days": 120}, timeout=30)
                repair_attempts += 1
                next_repair_at = now + DASHBOARD_REPAIR_RETRY_SECONDS
                print(f"Data completion requested ({repair_attempts}/{DASHBOARD_REPAIR_MAX_ATTEMPTS}).")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_summary = {"error": str(exc)}
        time.sleep(DASHBOARD_PRELOAD_RETRY_SECONDS)
    return False, last_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Taiwan50 dashboard.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host, default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port, default: 8000")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    parser.add_argument(
        "--preload-timeout",
        type=int,
        default=DASHBOARD_PRELOAD_TIMEOUT_SECONDS,
        help="Seconds to wait for displayable Taiwan 50 analysis before opening the browser.",
    )
    preload_group = parser.add_mutually_exclusive_group()
    preload_group.add_argument(
        "--preload",
        action="store_true",
        help="Explicitly allow startup completeness checks and missing-data repair requests.",
    )
    preload_group.add_argument(
        "--no-preload",
        action="store_true",
        help="Deprecated compatibility flag; startup already skips automatic data repair by default.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the command without starting the server")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        context, candidate, check, app_check, command, url = build_command(args.host, args.port)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        return 1

    print("Taiwan50 Dashboard")
    print(f"Mode: {context.mode}")
    print(f"Base folder: {context.base_dir}")
    print(f"Selected Python: {candidate.executable}")
    print(f"Python source: {candidate.source}")
    print(f"App cwd: {context.app_cwd}")
    print("Uvicorn app: app:app")
    print("Dependency check: PASS")
    print(check.output)
    print("App import check: PASS")
    print("  " + " ".join(app_check.command))
    print(app_check.output)
    print(f"URL: {url}")
    print("Command:")
    print("  " + " ".join(command))

    if args.dry_run:
        return 0

    process = subprocess.Popen(command, cwd=str(context.app_cwd))
    try:
        if wait_until_ready(url):
            print(f"Server is ready: {url}")
            if args.preload and not args.no_preload:
                print("Checking Taiwan 50 data before opening the browser...")
                preload_ok, preload_summary = wait_until_dashboard_data_ready(
                    url,
                    timeout_seconds=args.preload_timeout,
                )
                if not preload_ok:
                    print(f"[ERROR] Taiwan 50 data did not become complete: {preload_summary}")
                    print("The browser was not opened, so incomplete figures are not shown.")
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return 2
                if preload_summary.get("partial_ready"):
                    print(f"Taiwan 50 display ready: {preload_summary['pass_count']}/{preload_summary['checked']} stocks; excluded: {preload_summary['failed_codes']}")
                else:
                    print("Taiwan 50 data preload: PASS")
            if not args.no_browser:
                webbrowser.open(url)
        else:
            print(f"[WARN] Server did not respond within {SERVER_READY_TIMEOUT_SECONDS} seconds.")
            print(f"Open manually after startup finishes: {url}")
        return process.wait()
    except KeyboardInterrupt:
        print()
        print("Stopping server...")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
