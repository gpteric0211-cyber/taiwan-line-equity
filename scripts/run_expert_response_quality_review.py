from __future__ import annotations

"""Build or evaluate an integrity-bound independent-human quality review kit."""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from evaluation.expert_response_quality_review_kit import (  # noqa: E402
    build_review_kit,
    evaluate_review_kit,
    load_json_document,
    write_review_kit,
)


def _write_result_exclusive(path: Path, result: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _build(args: argparse.Namespace) -> int:
    stable = load_json_document(args.stable_outputs)
    candidate = load_json_document(args.candidate_outputs)
    paths = write_review_kit(args.output_directory, build_review_kit(stable, candidate))
    print(
        json.dumps(
            {key: str(path) for key, path in paths.items()},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    result = evaluate_review_kit(
        load_json_document(args.public_review),
        load_json_document(args.private_role_key),
        load_json_document(args.ratings),
        load_json_document(args.stable_outputs),
        load_json_document(args.candidate_outputs),
    )
    _write_result_exclusive(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") is True else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or evaluate ExpertResponseQualityCorpusV1 human-review evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build separated blind-review artifacts.")
    build_parser.add_argument("--stable-outputs", type=Path, required=True)
    build_parser.add_argument("--candidate-outputs", type=Path, required=True)
    build_parser.add_argument("--output-directory", type=Path, required=True)
    build_parser.set_defaults(handler=_build)

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Verify bindings and evaluate completed human ratings."
    )
    evaluate_parser.add_argument("--public-review", type=Path, required=True)
    evaluate_parser.add_argument("--private-role-key", type=Path, required=True)
    evaluate_parser.add_argument("--ratings", type=Path, required=True)
    evaluate_parser.add_argument("--stable-outputs", type=Path, required=True)
    evaluate_parser.add_argument("--candidate-outputs", type=Path, required=True)
    evaluate_parser.add_argument("--output", type=Path, required=True)
    evaluate_parser.set_defaults(handler=_evaluate)

    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
