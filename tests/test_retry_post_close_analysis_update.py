from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import retry_post_close_analysis_update as retry  # noqa: E402


def test_retry_delegates_to_locked_finalize_pipeline(monkeypatch) -> None:
    delegated: list[list[str]] = []
    monkeypatch.setattr(
        retry,
        "_run_pipeline",
        lambda argv: delegated.append(list(argv)) or 5,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retry_post_close_analysis_update.py",
            "--date",
            "2026-08-28",
            "--window-end",
            "23:59",
            "--retry-delay-seconds",
            "1800",
            "--lock-wait-seconds",
            "600",
        ],
    )

    assert retry.main() == 5
    assert delegated == [[
        "--stage",
        "finalize",
        "--max-retries",
        "18",
        "--retry-delay-seconds",
        "1800",
        "--window-end",
        "23:59",
        "--lock-wait-seconds",
        "600",
        "--date",
        "2026-08-28",
    ]]
