from __future__ import annotations

from typing import Any


def select_displayable_dashboard_items(
    items: list[dict[str, Any]], readiness: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Select complete rows without upgrading whole-universe data readiness.

    Global failures or inconsistent coverage still block the entire list.
    This projection never repairs data or changes financial quality/decisions.
    """
    if readiness is None:
        return list(items)
    if readiness.get("global_issues"):
        return []
    codes = [str(item.get("code", "")).zfill(4) for item in items]
    failed_codes = [str(code).zfill(4) for code in readiness.get("failed_codes") or []]
    try:
        checked = int(readiness.get("checked") or 0)
        passed = int(readiness.get("pass_count") or 0)
        failed = int(readiness.get("fail_count") or 0)
    except (TypeError, ValueError):
        return []
    if (
        checked != len(items)
        or len(set(codes)) != checked
        or failed != len(failed_codes)
        or len(set(failed_codes)) != failed
        or not set(failed_codes).issubset(codes)
        or passed <= 0
        or passed + failed != checked
        or bool(readiness.get("ready")) != (failed == 0)
    ):
        return []
    excluded = set(failed_codes)
    return [item for item, code in zip(items, codes) if code not in excluded]
