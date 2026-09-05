from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH  # noqa: E402
from services.statistical_release_evidence_service import (  # noqa: E402
    DEFAULT_OUTCOME_REVISION,
    evaluate_release_evidence_database,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate read-only Single-Track V3 release evidence.")
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--outcome-revision", default=DEFAULT_OUTCOME_REVISION)
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        raise SystemExit(f"database does not exist: {database}")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        result = evaluate_release_evidence_database(
            connection,
            outcome_revision=str(args.outcome_revision),
        )
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
