from __future__ import annotations

from typing import Any, Callable


ENTITY_RESOLUTION_GATE_VERSION = "EntityResolutionGateV1"
UNIQUE_CASES = (
    ("星宇", "2646", None),
    ("星宇呢", "2646", None),
    ("所以星宇呢", "2646", "2454"),
    ("台積", "2330", None),
    ("聯發科", "2454", None),
    ("M31", "6643", None),
    ("長榮", "2603", None),
    ("長榮航", "2618", None),
    ("它明天呢", "2454", "2454"),
    ("那現在呢", "2603", "2603"),
)
CLARIFICATION_CASES = (
    "2317 台積電",
    "星雨",
    "長榮或長榮航",
)


def evaluate_entity_resolution_gate(
    resolver: Callable[[str, str | None], dict[str, Any]],
) -> dict[str, Any]:
    unique_rows = []
    for query, expected, active in UNIQUE_CASES:
        result = resolver(query, active)
        actual = str((result.get("stock") or {}).get("code") or "")
        unique_rows.append(
            {
                "query": query,
                "expected": expected,
                "actual": actual,
                "resolved": bool(result.get("ok")),
                "unnecessary_confirmation": bool(result.get("requires_confirmation")),
                "wrong_entity": actual != expected,
                "active_switch_error": bool(active and expected != active and actual == active),
            }
        )
    clarification_rows = []
    for query in CLARIFICATION_CASES:
        result = resolver(query, None)
        clarification_rows.append(
            {
                "query": query,
                "incorrect_resolution": bool(result.get("ok")),
                "confirmation_required": bool(result.get("requires_confirmation")),
                "status": result.get("status"),
            }
        )
    unnecessary = sum(row["unnecessary_confirmation"] for row in unique_rows)
    wrong = sum(row["wrong_entity"] for row in unique_rows)
    switches = sum(row["active_switch_error"] for row in unique_rows)
    confirmations = sum(row["confirmation_required"] for row in clarification_rows)
    return {
        "gate_version": ENTITY_RESOLUTION_GATE_VERSION,
        "unique_case_count": len(unique_rows),
        "unique_alias_unnecessary_confirmation_count": unnecessary,
        "unique_alias_unnecessary_confirmation_rate": unnecessary / len(unique_rows),
        "wrong_entity_resolution_count": wrong,
        "active_stock_switch_error_count": switches,
        "clarification_case_count": len(clarification_rows),
        "clarification_count": confirmations,
        "clarification_rate": confirmations / len(clarification_rows),
        "passed": bool(
            unnecessary == 0
            and wrong == 0
            and switches == 0
            and confirmations == len(clarification_rows)
            and not any(row["incorrect_resolution"] for row in clarification_rows)
        ),
        "unique_cases": unique_rows,
        "clarification_cases": clarification_rows,
    }
