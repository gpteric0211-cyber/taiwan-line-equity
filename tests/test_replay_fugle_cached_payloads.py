from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from replay_fugle_cached_payloads import (  # noqa: E402
    load_cached_endpoint,
    load_cached_pair,
)
from scripts.update_fugle_intraday_supplemental import save_json  # noqa: E402


def _write_pair(path: Path, *, date: str = "2026-09-01", symbol: str = "2330") -> None:
    for endpoint in ("trades", "volumes"):
        (path / f"2026-09-01_2330_{endpoint}.json").write_text(
            json.dumps({"date": date, "symbol": symbol, "data": []}),
            encoding="utf-8",
        )


def test_load_cached_pair_requires_exact_date_and_symbol(tmp_path: Path) -> None:
    _write_pair(tmp_path)

    result = load_cached_pair(tmp_path, "2026-09-01", "2330")

    assert result["code"] == "2330"
    assert result["trade_date"] == "2026-09-01"
    assert set(result["digests"]) == {"trades", "volumes"}


def test_load_cached_pair_rejects_mismatched_provider_date(tmp_path: Path) -> None:
    _write_pair(tmp_path, date="2026-09-02")

    with pytest.raises(ValueError, match="date mismatch"):
        load_cached_pair(tmp_path, "2026-09-01", "2330")


def test_load_cached_endpoint_can_validate_trades_independently(tmp_path: Path) -> None:
    _write_pair(tmp_path)
    (tmp_path / "2026-09-01_2330_volumes.json").write_bytes(b"\xff" * 8)

    result = load_cached_endpoint(tmp_path, "2026-09-01", "2330", "trades")

    assert result["endpoint"] == "trades"
    assert result["code"] == "2330"


def test_save_json_atomically_writes_valid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"

    save_json(path, {"symbol": "2330", "name": "台積電"})

    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "台積電"
    assert list(tmp_path.glob("*.tmp")) == []
