from __future__ import annotations

"""Run additive TDCC and institution-PIT repairs inside one DB candidate."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(script: str, report: Path) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / script),
            "--allow-candidate-write",
            "--report",
            str(report),
        ],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{script} failed with exit code {completed.returncode}")
    return json.loads(report.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate-only metadata repair bundle.")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = args.report if args.report.is_absolute() else ROOT / args.report
    report.parent.mkdir(parents=True, exist_ok=True)
    tdcc_report = report.with_name(report.stem + ".tdcc.json")
    institution_report = report.with_name(report.stem + ".institution.json")
    payload = {
        "ok": True,
        "tdcc": _run("materialize_tdcc_full_market_candidate.py", tdcc_report),
        "institution_provenance": _run(
            "backfill_institution_provenance_candidate.py", institution_report
        ),
    }
    report.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
