from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Skeleton importer for authorized margin financing amount files."
    )
    parser.add_argument("--input", required=True, help="User-provided official or authorized CSV/XLSX/ZIP file.")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parents[1] / "review_src" / "data" / "taiwan50.db"))
    parser.add_argument("--dry-run", action="store_true", help="Validate the provided file path only; do not write DB.")
    parser.add_argument("--write", action="store_true", help="Reserved for a future explicit importer implementation.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print("ERROR: Input file not found.")
        return 1
    if args.write:
        print("ERROR: Margin financing amount write importer is a skeleton; no DB write was performed.")
        return 2
    print(
        {
            "ok": True,
            "writes_db": False,
            "input": str(input_path),
            "db": args.db,
            "status": "skeleton_ready_for_authorized_file_mapping",
            "supported_future_fields": [
                "financing_buy_amount",
                "financing_loan_amount",
                "financing_balance_amount",
                "margin_balance_unit",
            ],
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

