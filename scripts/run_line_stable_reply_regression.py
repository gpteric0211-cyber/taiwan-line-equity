from __future__ import annotations

"""Compare fixed live stable-reply bytes with a reviewed baseline artifact."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ENV = PROJECT_ROOT / ".env.line_bot"
ENDPOINT = "http://127.0.0.1:8021/internal/line-model-benchmark/stable-reply-sample"
SCENARIOS = (
    "fundamental_chip_technical_news",
    "technical_valuation_support_risk",
    "chip_night_us_events",
)


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    baseline_path = _path(args.baseline)
    output_path = _path(args.output)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {output_path}")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    reviewed = list(baseline.get("comparisons") or [])
    if len(reviewed) != len(SCENARIOS):
        raise RuntimeError("baseline must contain exactly three reviewed comparisons")
    baseline_by_scenario = {
        scenario: row for scenario, row in zip(SCENARIOS, reviewed)
    }
    selected_scenarios = tuple(args.scenario or SCENARIOS)
    repeats = max(1, min(int(args.repeats), 10))
    values = dotenv_values(PRIVATE_ENV)
    token = str(values.get("BOT_MARKET_DATA_TOKEN") or "")
    if len(token) < 32:
        raise RuntimeError("persistent BOT_MARKET_DATA_TOKEN is unavailable")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    comparisons: list[dict[str, Any]] = []
    for scenario in selected_scenarios:
        before = baseline_by_scenario[scenario]
        for repeat_index in range(1, repeats + 1):
            response = session.post(
                ENDPOINT,
                json={"scenario": scenario, "code": "2330"},
                timeout=60,
            )
            response.raise_for_status()
            current = response.json()
            comparison = {
                "scenario": scenario,
                "repeat_index": repeat_index,
                "question": str(before.get("question") or ""),
            "before_bytes": int(before.get("before_bytes") or 0),
            "before_sha256": str(before.get("before_sha256") or ""),
            "after_bytes": int(current.get("reply_utf8_bytes") or 0),
            "after_sha256": str(current.get("reply_sha256") or ""),
            "byte_identical": (
                int(before.get("before_bytes") or 0)
                == int(current.get("reply_utf8_bytes") or 0)
                and str(before.get("before_sha256") or "")
                == str(current.get("reply_sha256") or "")
            ),
            "answer_path": str(current.get("answer_path") or ""),
            "shadow_request_prepared": current.get("shadow_request_prepared") is True,
            "candidate_reply_submitted": current.get("candidate_reply_submitted") is True,
            "completed_within_internal_reply_budget": (
                current.get("completed_within_internal_reply_budget") is True
            ),
            "end_to_end_ms": int(current.get("end_to_end_ms") or 0),
            }
            comparisons.append(comparison)
            print(
                json.dumps(
                    {
                        "scenario": scenario,
                        "repeat_index": repeat_index,
                        "byte_identical": comparison["byte_identical"],
                        "end_to_end_ms": comparison["end_to_end_ms"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    all_match = all(
        item["byte_identical"]
        and item["shadow_request_prepared"]
        and not item["candidate_reply_submitted"]
        and item["completed_within_internal_reply_budget"]
        for item in comparisons
    )
    artifact = {
        "contract": "line-stable-reply-regression-v1",
        "created_at": _now(),
        "baseline": baseline_path.name,
        "selected_scenarios": list(selected_scenarios),
        "repeats_per_scenario": repeats,
        "actual_line_reply_send_exercised": False,
        "candidate_reply_replacement_allowed": False,
        "comparisons": comparisons,
        "all_match": all_match,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0 if all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
