from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import update_external_analysis_context as external_update  # noqa: E402


def test_busy_market_database_lock_blocks_external_writer(tmp_path, monkeypatch) -> None:
    calls: list[bool] = []

    @contextmanager
    def busy_lock(_path, **_kwargs):
        yield False

    monkeypatch.setattr(external_update, "isolated_update_lock", busy_lock)
    monkeypatch.setattr(
        external_update,
        "update_external_analysis_context",
        lambda **_kwargs: calls.append(True),
    )

    code = external_update.main(
        ["--lock-wait-seconds", "0", "--report", str(tmp_path / "report.json")]
    )

    assert code == 4
    assert calls == []
