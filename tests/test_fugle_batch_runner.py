from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_fugle_all_from_xlsx_progress as runner  # noqa: E402


def test_aggregate_exit_code_never_uses_failed_batch_count() -> None:
    assert runner.aggregate_exit_code([]) == 0
    assert runner.aggregate_exit_code([4, 4, 4]) == 4
    assert runner.aggregate_exit_code([5, 4]) == 5
    assert runner.aggregate_exit_code([3, 4]) == 3
    assert runner.aggregate_exit_code([6]) == 2


def _run_batch(tmp_path: Path, *, return_codes: list[int], max_retries: int) -> tuple[int, int]:
    report = tmp_path / "current.md"
    report.write_text("- 是否寫 DB：是\n", encoding="utf-8")
    completed = [SimpleNamespace(returncode=value) for value in return_codes]
    with patch.object(runner.subprocess, "run", side_effect=completed) as run, patch.object(
        runner.time,
        "sleep",
    ):
        code = runner.run_batch(
            repo_root=tmp_path,
            python_exe="python",
            batch_id=1,
            total_batches=1,
            codes=["2330"],
            sleep_seconds=0,
            current_report_path=report,
            max_retries=max_retries,
            retry_wait_seconds=0,
            capture_phase="post_close",
            window_start="13:31",
            window_end="23:59",
        )
    return code, run.call_count


def test_nonretryable_child_exit_stops_immediately(tmp_path: Path) -> None:
    code, calls = _run_batch(tmp_path, return_codes=[1], max_retries=2)
    assert code == 1
    assert calls == 1


def test_partial_child_exit_retries_and_preserves_fixed_code(tmp_path: Path) -> None:
    code, calls = _run_batch(tmp_path, return_codes=[4, 0], max_retries=1)
    assert code == 0
    assert calls == 2
