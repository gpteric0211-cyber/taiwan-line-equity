from __future__ import annotations

# Refactor R6 note:
# `upsert_finmind_stock_data()` and `update_finmind_codes()` remain in app.py for now.
# They still coordinate update-slot locks, status messages, quota sleeps, DB writes, and
# cache pruning. Move them in a later narrower phase with explicit callback wiring.

MOVED_FUNCTIONS: tuple[str, ...] = ()
