"""Local account migration/owner bootstrap, using configured project-relative paths."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from equity.__main__ import load_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["migrate", "owner", "create-owner"])
    parser.add_argument("--email", help="Existing verified account for first owner initialization")
    parser.add_argument("--allow-temporary-password", action="store_true", help="Explicit local-only exception for a temporary first-owner password")
    args = parser.parse_args()
    load_settings()
    from core.accounts_database import initialize, path
    from repository.member_repository import bootstrap_owner

    if args.action == "create-owner":
        from equity.owner_setup import setup_owner
        try:
            setup_owner(allow_temporary_password=args.allow_temporary_password)
        except ValueError as exc:
            parser.exit(1, str(exc) + "\n")
        return

    if args.action == "owner":
        if not args.email:
            parser.error("owner requires --email")
        bootstrap_owner(args.email)
        print("Owner initialized; membership benefits remain separate.")
        return
    source = path()
    if source.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = source.with_name(source.name + ".before-membership-" + stamp + ".bak")
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as src, closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Account backup failed verification")
    initialize()
    print("Account membership schema ready; no market database modified.")


if __name__ == "__main__":
    main()
