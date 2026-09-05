"""Run pytest with an isolated DB and no outbound network access.

Usage: python tools/run_tests.py [pytest arguments]
"""

from __future__ import annotations
import os
import json
from pathlib import Path
import sys
import tempfile
import weakref
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "review_src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from tools.restore_test_fixtures import restore

restore()
(ROOT / "var" / "tests").mkdir(parents=True, exist_ok=True)


_bound_sockets = weakref.WeakSet()
_fixture_reads = set()


def guard(event, args):
    if event == "open" and isinstance(args[0], str):
        candidate = Path(args[0]).resolve()
        evidence_root = ROOT / "logs" / "line_model_shadow"
        if evidence_root in candidate.parents and not any(
            flag in str(args[1] or "") for flag in ("w", "a", "+")
        ):
            _fixture_reads.add(candidate)
    if event == "socket.bind":
        _bound_sockets.add(args[0])
    if event == "socket.connect":
        destination = args[1]
        for bound in list(_bound_sockets):
            try:
                address = bound.getsockname()
                if address == destination and address[0] in {"127.0.0.1", "::1"}:
                    break
            except OSError:
                continue
        else:
            raise OSError("Outbound network access is disabled in offline tests")
    if event == "sqlite3.connect":
        value = str(args[0])
        if value == ":memory:" or "mode=memory" in value:
            return
        if value.startswith("file:"):
            value = unquote(urlsplit(value).path)
            if os.name == "nt" and value.startswith("/") and len(value) > 2 and value[2] == ":":
                value = value[1:]
        resolved = Path(value).resolve()
        for protected in (ROOT / "review_src" / "data", ROOT / "data", ROOT / "var" / "migration"):
            if resolved == protected or protected in resolved.parents:
                raise RuntimeError("Offline test attempted to access a production data directory")


with tempfile.TemporaryDirectory(prefix="equity-test-", dir=ROOT / "var" / "tests") as test_dir:
    os.environ["TAIWAN50_DB_PATH"] = str(Path(test_dir) / "market.db")
    os.environ["LINE_MEMORY_DB_PATH"] = str(Path(test_dir) / "conversations.sqlite3")
    os.environ["LINE_MEMORY_KEY_FILE"] = str(Path(test_dir) / "memory.key")
    os.environ["LINE_MEMORY_STORAGE"] = "memory"
    os.environ["EQUITY_JWT_KEY_FILE"] = str(Path(test_dir) / "jwt.key")
    os.environ["EQUITY_AUTH_DB"] = str(Path(test_dir) / "accounts.sqlite3")
    os.environ["EQUITY_USER_DB"] = str(Path(test_dir) / "portfolio.sqlite3")
    os.environ["EQUITY_USER_KEY_FILE"] = str(Path(test_dir) / "portfolio.key")
    os.environ["AUTO_REFRESH_MARKET_DATA_ON_START"] = "0"
    os.environ["AUTO_UPDATE_TW50_ON_START"] = "0"
    # Ignore this project's real .env files, but allow tests to exercise temporary config files.
    import dotenv

    _real_values, _real_load = dotenv.dotenv_values, dotenv.load_dotenv
    _private_configs = {
        ROOT / ".env.line_bot",
        ROOT / "review_src" / ".env",
        ROOT / "review_src" / ".env.line_bot",
    }

    def _is_private(value):
        return value is not None and Path(value).resolve() in _private_configs

    def _test_values(dotenv_path=None, *args, **kwargs):
        return {} if _is_private(dotenv_path) else _real_values(dotenv_path, *args, **kwargs)

    def _test_load(dotenv_path=None, *args, **kwargs):
        return False if _is_private(dotenv_path) else _real_load(dotenv_path, *args, **kwargs)

    dotenv.dotenv_values, dotenv.load_dotenv = _test_values, _test_load
    sys.addaudithook(guard)
    import pytest

    result = pytest.main([*(sys.argv[1:] or ["tests", "-q"]), "--basetemp", str(Path(test_dir) / "pytest")])
    evidence = ROOT / "var" / "test-results" / "fixture-dependencies.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        json.dumps(sorted(p.relative_to(ROOT).as_posix() for p in _fixture_reads if p.is_file()), indent=2),
        encoding="utf-8",
    )
    raise SystemExit(result)
