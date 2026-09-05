from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_full_market_history import _write_json  # noqa: E402


def test_state_write_is_atomic_and_keeps_previous_backup(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    _write_json(state, {"completed_dates": ["2026-08-21"]})
    _write_json(
        state,
        {"completed_dates": ["2026-08-21", "2026-08-22"]},
        backup_existing=True,
    )

    current = json.loads(state.read_text(encoding="utf-8"))
    backup = json.loads(state.with_suffix(".json.bak").read_text(encoding="utf-8"))

    assert current["completed_dates"] == ["2026-08-21", "2026-08-22"]
    assert backup["completed_dates"] == ["2026-08-21"]
    assert not state.with_suffix(".json.tmp").exists()
    assert not state.with_suffix(".json.bak.tmp").exists()
