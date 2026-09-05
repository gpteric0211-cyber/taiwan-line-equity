from __future__ import annotations

import math
from typing import Any


MONITOR_CONTRACT_VERSION = "canonical-surface-parity-monitor-v1"
DEFAULT_TARGET_BATCHES = 5
MAX_OBSERVATIONS = 30

_ANALYSIS_STATUS_FIELDS = (
    "status",
    "complete",
    "decision_ready",
    "main_status",
    "main_reasons",
    "reason_code",
    "source",
    "version",
)
_REFEREE_FIELDS = (
    "decision_ready",
    "main_status",
    "main_reasons",
    "reason_code",
    "source",
    "version",
    "input_assembler_version",
    "can_be_overridden_by_model",
)
_TECHNICAL_FIELDS = (
    "status",
    "decision_ready",
    "formula_version",
    "input_row_count",
    "input_end_date",
)
_SAFETY_FIELDS = (
    "status",
    "trade_date",
    "hard_blocked",
    "auto_entry_eligible",
    "version",
)
_CORPORATE_ACTION_FIELDS = (
    "status",
    "label",
    "action_date",
    "action_type",
    "days_from_action",
    "confirmed",
    "adjustment_method",
    "stock_distribution_ratio",
    "ratio_unit",
    "cash_dividend_per_share",
    "share_count_factor",
    "pre_event_price_multiplier",
    "verification_status",
    "available_at",
    "directional_weight_eligible",
)
_COST_FIELDS = (
    "value",
    "trade_date",
    "status",
    "calculation_state",
    "confidence",
    "sample_days",
    "available",
    "contract_version",
    "formula_version",
    "can_override_main_status",
)
_ZONE_FIELDS = ("zone_low", "zone_high")
_RSI_FIELDS = ("rsi5", "rsi10", "rsi14")


def _pick(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {field: source.get(field) for field in fields}


def _finite_number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _display_number(value: Any, digits: int = 2) -> str | None:
    number = _finite_number(value)
    if number is None:
        return None
    rounded = round(number, digits)
    if rounded == 0:
        rounded = 0.0
    text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _technical_for_surface(payload: dict[str, Any], surface: str) -> dict[str, Any]:
    if surface == "web":
        return dict(payload.get("canonical_technical") or {})
    return dict(payload.get("technical") or {})


def _trade_date_for_surface(payload: dict[str, Any], surface: str) -> Any:
    return payload.get("data_date") if surface == "web" else payload.get("trade_date")


def _canonical_cost_projection(context: Any) -> dict[str, Any]:
    source = context if isinstance(context, dict) else {}
    costs = source.get("canonical_costs")
    if not isinstance(costs, dict):
        costs = {}
    return {
        "cost_contract_version": source.get("cost_contract_version"),
        "trade_date": source.get("trade_date"),
        "status": source.get("status"),
        "can_override_main_status": source.get("can_override_main_status"),
        "canonical_costs": {
            str(cost_type): _pick(item, _COST_FIELDS)
            for cost_type, item in sorted(costs.items())
            if isinstance(item, dict)
        },
    }


def _display_zone(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {field: _display_number(source.get(field), 2) for field in _ZONE_FIELDS}


def _raw_zone(value: Any) -> dict[str, Any]:
    return _pick(value, _ZONE_FIELDS)


def _safety_projection(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    projected = _pick(source, _SAFETY_FIELDS)
    projected["corporate_action"] = _pick(
        source.get("corporate_action"),
        _CORPORATE_ACTION_FIELDS,
    )
    if not source:
        projected["status"] = "unavailable"
        projected["hard_blocked"] = False
        projected["auto_entry_eligible"] = False
    return projected


def build_surface_projection(payload: dict[str, Any], surface: str) -> dict[str, Any]:
    """Project only contract fields that must agree across user surfaces.

    LINE intentionally formats market numbers to two decimals.  The display
    projection normalizes all three surfaces to that public representation,
    while ``raw_internal`` separately keeps the unrounded Web/Bot values.
    """

    if surface not in {"bot", "web", "line"}:
        raise ValueError(f"unsupported surface: {surface}")
    referee = dict(payload.get("referee") or {})
    technical = _technical_for_surface(payload, surface)
    rsi = dict(technical.get("rsi") or {})
    safety = payload.get("recommendation_safety") or referee.get("recommendation_safety")
    analysis_status = dict(payload.get("analysis_status") or {})
    microstructure_status = dict(payload.get("microstructure_status") or {})
    trading_state = dict(payload.get("trading_state") or {})
    institution = dict(payload.get("institutional_context") or {})

    technical_meta_fields = _TECHNICAL_FIELDS
    if surface == "line" and technical.get("decision_ready") is not True:
        technical_meta_fields = ("status", "decision_ready")

    projection = {
        "core": {
            "analysis_contract_version": payload.get("analysis_contract_version"),
            "trade_date": _trade_date_for_surface(payload, surface),
            "analysis_status": _pick(analysis_status, _ANALYSIS_STATUS_FIELDS),
            "referee": _pick(referee, _REFEREE_FIELDS),
            "technical": _pick(technical, technical_meta_fields),
            "trading_state": {"status": trading_state.get("status")},
            "microstructure_status": _pick(
                microstructure_status,
                ("status", "reason", "decision_ready"),
            ),
            "recommendation_safety": _safety_projection(safety),
        },
        "display_numbers": {
            "rsi": {
                field: _display_number(rsi.get(field), 2)
                for field in _RSI_FIELDS
            },
            "support_zone": _display_zone(referee.get("support_zone")),
            "resistance_zone": _display_zone(referee.get("resistance_zone")),
        },
        "cost_contract": _canonical_cost_projection(institution),
        "raw_internal": {
            "rsi": {field: rsi.get(field) for field in _RSI_FIELDS},
            "support_zone": _raw_zone(referee.get("support_zone")),
            "resistance_zone": _raw_zone(referee.get("resistance_zone")),
        },
    }
    if surface == "web":
        projection["legacy_guards"] = {
            "legacy_signal": payload.get("legacy_signal"),
            "legacy_signal_disabled": payload.get("legacy_signal_disabled"),
            "today_support": payload.get("today_support"),
            "today_resistance": payload.get("today_resistance"),
            "support_5d": payload.get("support_5d"),
            "resistance_5d": payload.get("resistance_5d"),
            "legacy_period_support_resistance_disabled": payload.get(
                "legacy_period_support_resistance_disabled"
            ),
        }
    return projection


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            flattened.update(_flatten(item, path))
        else:
            flattened[path] = item
    return flattened


def _compare(
    *,
    code: str,
    surface: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_flat = _flatten(expected)
    actual_flat = _flatten(actual)
    mismatches: list[dict[str, Any]] = []
    for field in sorted(set(expected_flat) | set(actual_flat)):
        expected_value = expected_flat.get(field)
        actual_value = actual_flat.get(field)
        if expected_value == actual_value:
            continue
        mismatches.append(
            {
                "code": code,
                "surface": surface,
                "field": field,
                "expected": expected_value,
                "actual": actual_value,
            }
        )
    return mismatches


def compare_surface_payloads(
    *,
    code: str,
    expected_trade_date: str,
    bot_payload: dict[str, Any],
    web_payload: dict[str, Any],
    line_payload: dict[str, Any],
) -> dict[str, Any]:
    bot = build_surface_projection(bot_payload, "bot")
    web = build_surface_projection(web_payload, "web")
    line = build_surface_projection(line_payload, "line")

    mismatches: list[dict[str, Any]] = []
    for surface, projection in (("web", web), ("line", line)):
        expected_core = dict(bot["core"])
        if surface == "line" and (bot["core"].get("technical") or {}).get(
            "decision_ready"
        ) is not True:
            expected_core["technical"] = _pick(
                bot["core"].get("technical"),
                ("status", "decision_ready"),
            )
        mismatches.extend(
            _compare(
                code=code,
                surface=surface,
                expected={
                    "core": expected_core,
                    "display_numbers": bot["display_numbers"],
                    "cost_contract": bot["cost_contract"],
                },
                actual={
                    "core": projection["core"],
                    "display_numbers": projection["display_numbers"],
                    "cost_contract": projection["cost_contract"],
                },
            )
        )
    mismatches.extend(
        _compare(
            code=code,
            surface="web_raw",
            expected=bot["raw_internal"],
            actual=web["raw_internal"],
        )
    )
    expected_legacy_guards = {
        "legacy_signal": None,
        "legacy_signal_disabled": True,
        "today_support": None,
        "today_resistance": None,
        "support_5d": None,
        "resistance_5d": None,
        "legacy_period_support_resistance_disabled": True,
    }
    mismatches.extend(
        _compare(
            code=code,
            surface="web_legacy_guard",
            expected=expected_legacy_guards,
            actual=web.get("legacy_guards") or {},
        )
    )
    for surface, projection in (("bot", bot), ("web", web), ("line", line)):
        actual_date = (projection.get("core") or {}).get("trade_date")
        if actual_date == expected_trade_date:
            continue
        mismatches.append(
            {
                "code": code,
                "surface": surface,
                "field": "core.trade_date",
                "expected": expected_trade_date,
                "actual": actual_date,
            }
        )

    formula_versions = sorted(
        {
            str(item.get("formula_version"))
            for item in (bot.get("cost_contract") or {}).get("canonical_costs", {}).values()
            if item.get("formula_version")
        }
    )
    return {
        "code": code,
        "passed": not mismatches,
        "analysis_status": (bot["core"].get("analysis_status") or {}).get("status"),
        "main_status": (bot["core"].get("referee") or {}).get("main_status"),
        "reason_code": (bot["core"].get("referee") or {}).get("reason_code"),
        "rsi14": (bot["raw_internal"].get("rsi") or {}).get("rsi14"),
        "line_rsi14": (line["display_numbers"].get("rsi") or {}).get("rsi14"),
        "cost_contract_version": (bot.get("cost_contract") or {}).get(
            "cost_contract_version"
        ),
        "cost_formula_versions": formula_versions,
        "mismatches": mismatches,
    }


def update_monitor_state(
    previous: dict[str, Any] | None,
    observation: dict[str, Any],
    *,
    publication_dates: list[str],
    target_batches: int = DEFAULT_TARGET_BATCHES,
    generated_at: str,
) -> dict[str, Any]:
    """Upsert one publication-date observation and calculate the live streak."""

    target = max(1, int(target_batches))
    old = dict(previous or {})
    trade_date = str(observation.get("trade_date") or "")
    if not trade_date:
        raise ValueError("observation trade_date is required")
    history = [
        dict(item)
        for item in list(old.get("observations") or [])
        if isinstance(item, dict) and str(item.get("trade_date") or "") != trade_date
    ]
    prior_same = next(
        (
            dict(item)
            for item in list(old.get("observations") or [])
            if isinstance(item, dict) and str(item.get("trade_date") or "") == trade_date
        ),
        {},
    )
    current = {
        **observation,
        "trade_date": trade_date,
        "first_checked_at": prior_same.get("first_checked_at") or generated_at,
        "last_checked_at": generated_at,
        "attempt_count": int(prior_same.get("attempt_count") or 0) + 1,
        "ever_failed": bool(
            prior_same.get("ever_failed") or observation.get("status") != "passed"
        ),
    }
    history.append(current)
    history.sort(key=lambda item: str(item.get("trade_date") or ""))
    history = history[-MAX_OBSERVATIONS:]

    started_trade_date = str(
        old.get("monitoring_started_trade_date") or history[0]["trade_date"]
    )
    published = sorted(
        {
            str(item)
            for item in publication_dates
            if str(item) and str(item) >= started_trade_date
        }
    )
    observed_by_date = {
        str(item.get("trade_date")): item
        for item in history
        if str(item.get("trade_date") or "")
    }
    consecutive_pass_count = 0
    for date_value in reversed(published):
        item = observed_by_date.get(date_value)
        if not item or item.get("status") != "passed":
            break
        consecutive_pass_count += 1
    stable = consecutive_pass_count >= target
    pass_dates = [
        date_value
        for date_value in published
        if (observed_by_date.get(date_value) or {}).get("status") == "passed"
    ]
    failed_dates = [
        date_value
        for date_value in published
        if (observed_by_date.get(date_value) or {}).get("status") == "failed"
    ]
    missing_dates = [
        date_value for date_value in published if date_value not in observed_by_date
    ]
    current_failed = observation.get("status") != "passed"
    monitor_status = "stable" if stable else "failed" if current_failed else "collecting"
    return {
        "schema_version": MONITOR_CONTRACT_VERSION,
        "target_distinct_trade_dates": target,
        "monitoring_started_trade_date": started_trade_date,
        "started_at": old.get("started_at") or generated_at,
        "updated_at": generated_at,
        "scope": dict(observation.get("scope") or old.get("scope") or {}),
        "summary": {
            "status": monitor_status,
            "stable": stable,
            "observed_distinct_trade_dates": len(history),
            "published_dates_since_start": len(published),
            "consecutive_pass_count": consecutive_pass_count,
            "remaining_consecutive_batches": max(target - consecutive_pass_count, 0),
            "pass_dates": pass_dates,
            "failed_dates": failed_dates,
            "missing_dates": missing_dates,
        },
        "observations": history,
    }
