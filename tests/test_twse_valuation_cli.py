from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_twse_daily_valuation import result_exit_code  # noqa: E402


def test_twse_valuation_cli_uses_fixed_job_exit_contract() -> None:
    assert result_exit_code({"ok": True, "status": "ok"}) == 0
    assert result_exit_code({"ok": False, "status": "source_delayed"}) == 5
    assert result_exit_code({"ok": False, "status": "failed"}) == 2
