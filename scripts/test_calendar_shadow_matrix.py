from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.outlook_context import apply_holiday_dampener, is_factor_usable, make_factor_freshness
from market import calendar_data
from market.calendar_data import US_EASTERN, normalize_calendar_date
from market.calendar_override import apply_override, clear_expired_overrides, get_active_override
from market.calendar_status import get_market_calendar_status, get_next_twse_trading_day_after
from outlook.shadow_builder import build_shadow_outlook_context


@dataclass
class CaseResult:
    case_id: str
    case_name: str
    status: str
    expected: str
    actual: str
    reason: str
    related_module: str


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _pass(case_id: str, name: str, expected: str, actual: Any, reason: str, module: str) -> CaseResult:
    return CaseResult(case_id, name, "PASS", expected, _json(actual), reason, module)


def _fail(case_id: str, name: str, expected: str, actual: Any, reason: str, module: str) -> CaseResult:
    return CaseResult(case_id, name, "FAIL", expected, _json(actual), reason, module)


def _skip(case_id: str, name: str, expected: str, actual: Any, reason: str, module: str) -> CaseResult:
    return CaseResult(case_id, name, "SKIP", expected, _json(actual), reason, module)


def _check(
    case_id: str,
    name: str,
    expected: str,
    module: str,
    fn: Callable[[], tuple[bool, Any, str]],
) -> CaseResult:
    try:
        ok, actual, reason = fn()
    except Exception as exc:
        return _fail(case_id, name, expected, {"exception": repr(exc)}, "Unexpected exception", module)
    if ok:
        return _pass(case_id, name, expected, actual, reason, module)
    return _fail(case_id, name, expected, actual, reason, module)


def case_01() -> CaseResult:
    return _check(
        "C01",
        "一般 TWSE 平日交易日",
        "is_trading_day=True and source != manual_override",
        "market.calendar_status",
        lambda: (
            (status := get_market_calendar_status("TWSE", datetime.fromisoformat("2026-06-18T10:00:00+08:00"))).is_trading_day
            and status.source != "manual_override",
            status.to_dict(),
            "Weekday heuristic should mark a normal weekday as trading day.",
        ),
    )


def case_02() -> CaseResult:
    return _check(
        "C02",
        "一般 TWSE 週末",
        "is_trading_day=False",
        "market.calendar_status",
        lambda: (
            not (status := get_market_calendar_status("TWSE", datetime.fromisoformat("2026-06-20T10:00:00+08:00"))).is_trading_day,
            status.to_dict(),
            "Weekend should not be a TWSE trading day.",
        ),
    )


def case_03() -> CaseResult:
    holidays = sorted(getattr(calendar_data, "TAIWAN_MARKET_HOLIDAYS", set()) or [])
    if not holidays:
        return _skip(
            "C03",
            "TWSE static holiday",
            "is_trading_day=False and source=static_holiday_list",
            {"holiday_count": 0},
            "No reliable static holiday list is available in this environment.",
            "market.calendar_data",
        )
    day = holidays[0]
    status = get_market_calendar_status("TWSE", datetime.fromisoformat(f"{day}T10:00:00+08:00"))
    ok = (not status.is_trading_day) and status.source == "static_holiday_list"
    if ok:
        return _pass(
            "C03",
            "TWSE static holiday",
            "is_trading_day=False and source=static_holiday_list",
            status.to_dict(),
            "Static holiday list was available and matched.",
            "market.calendar_data",
        )
    return _fail(
        "C03",
        "TWSE static holiday",
        "is_trading_day=False and source=static_holiday_list",
        status.to_dict(),
        "Static holiday list exists but status did not match expected semantics.",
        "market.calendar_data",
    )


def case_04() -> CaseResult:
    apply_override(
        "TWSE",
        "2026-07-24",
        "closed",
        "matrix closed test",
        expires_at="2026-07-25T00:00:00+08:00",
        created_by="matrix",
    )
    status = get_market_calendar_status("TWSE", datetime.fromisoformat("2026-07-24T10:00:00+08:00"))
    ok = (not status.is_trading_day) and status.source == "manual_override" and status.override_active and status.reason == "matrix closed test"
    return (_pass if ok else _fail)(
        "C04",
        "active override closed",
        "is_trading_day=False, source=manual_override, override_active=True",
        status.to_dict(),
        "Active closed override should dominate calendar data.",
        "market.calendar_override",
    )


def case_05() -> CaseResult:
    apply_override(
        "TWSE",
        "2026-07-25",
        "open",
        "matrix open weekend test",
        expires_at="2026-07-26T00:00:00+08:00",
        created_by="matrix",
    )
    status = get_market_calendar_status("TWSE", datetime.fromisoformat("2026-07-25T10:00:00+08:00"))
    ok = status.is_trading_day and status.source == "manual_override" and status.override_active
    return (_pass if ok else _fail)(
        "C05",
        "active override open",
        "is_trading_day=True, source=manual_override, override_active=True",
        status.to_dict(),
        "Active open override should allow a weekend/closed date to be marked trading.",
        "market.calendar_override",
    )


def case_06() -> CaseResult:
    apply_override(
        "TWSE",
        "2026-07-26",
        "closed",
        "matrix expired test",
        expires_at="2026-07-24T00:00:00+08:00",
        created_by="matrix",
    )
    removed = clear_expired_overrides(datetime.fromisoformat("2026-07-24T10:00:00+08:00"))
    active = get_active_override("TWSE", "2026-07-26", as_of_time=datetime.fromisoformat("2026-07-24T10:00:00+08:00"))
    ok = active is None and removed >= 1
    return (_pass if ok else _fail)(
        "C06",
        "expired override ignored",
        "get_active_override() returns None after cleanup",
        {"removed": removed, "active": active.to_dict() if active else None},
        "Expired overrides should not remain active.",
        "market.calendar_override",
    )


def case_07() -> CaseResult:
    try:
        apply_override("TWSE", "2026-07-27", "closed", "missing expires", expires_at=None)
    except ValueError as exc:
        return _pass("C07", "expires_at=None rejected", "ValueError", repr(exc), "Permanent overrides are rejected.", "market.calendar_override")
    except Exception as exc:
        return _fail("C07", "expires_at=None rejected", "ValueError", repr(exc), "Wrong exception type.", "market.calendar_override")
    return _fail("C07", "expires_at=None rejected", "ValueError", "no exception", "Override was accepted without expires_at.", "market.calendar_override")


def case_08() -> CaseResult:
    apply_override(
        "TWSE",
        "2026-07-27",
        "closed",
        "timezone aware compare",
        expires_at=datetime.fromisoformat("2026-07-28T00:00:00+08:00"),
        created_by="matrix",
    )
    status = get_market_calendar_status("TWSE", datetime.fromisoformat("2026-07-27T10:00:00+08:00"))
    ok = status.override_active and status.override_expires_at is not None
    return (_pass if ok else _fail)(
        "C08",
        "timezone-aware override comparison",
        "No TypeError and active override",
        status.to_dict(),
        "Aware expires_at/as_of_time comparison should work.",
        "market.calendar_override",
    )


def case_09() -> CaseResult:
    result = normalize_calendar_date(datetime(2026, 6, 20, 0, 30), "US")
    ok = result == date(2026, 6, 19)
    return (_pass if ok else _fail)(
        "C09",
        "naive datetime normalization",
        "US market date should normalize to 2026-06-19",
        result.isoformat(),
        "Naive datetime is treated as Taipei time before market-date conversion.",
        "market.calendar_data",
    )


def case_10() -> CaseResult:
    status = get_market_calendar_status("US", datetime(2026, 6, 19, 10, 0, tzinfo=US_EASTERN))
    ok = status.session == "regular" and status.is_open
    return (_pass if ok else _fail)(
        "C10",
        "US regular session",
        "session=regular and is_open=True",
        status.to_dict(),
        "US status uses America/New_York local time when supplied.",
        "market.calendar_status",
    )


def case_11() -> CaseResult:
    pre = get_market_calendar_status("US", datetime(2026, 6, 19, 8, 0, tzinfo=US_EASTERN))
    after = get_market_calendar_status("US", datetime(2026, 6, 19, 17, 0, tzinfo=US_EASTERN))
    ok = pre.session == "pre_market" and after.session == "after_hours"
    return (_pass if ok else _fail)(
        "C11",
        "US pre_market / after_hours",
        "pre_market and after_hours, not regular",
        {"pre": pre.to_dict(), "after": after.to_dict()},
        "US session boundaries should not collapse all trading-adjacent times into regular.",
        "market.calendar_status",
    )


def case_12() -> CaseResult:
    status = get_market_calendar_status("US", datetime(2026, 6, 20, 10, 0, tzinfo=US_EASTERN))
    ok = not status.is_trading_day
    return (_pass if ok else _fail)(
        "C12",
        "US weekend / holiday heuristic",
        "weekend is_trading_day=False",
        status.to_dict(),
        "US official holidays are not implemented; weekend heuristic should still work.",
        "market.calendar_status",
    )


def case_13() -> CaseResult:
    result = get_next_twse_trading_day_after("2026-06-19", as_of_time=datetime.fromisoformat("2026-06-19T21:00:00+08:00"))
    ok = result == "2026-06-22"
    return (_pass if ok else _fail)(
        "C13",
        "TAIFEX belongs_to_trade_date",
        "Next TWSE trading day after Friday should skip weekend to 2026-06-22",
        {"belongs_to_trade_date": result},
        "Prototype helper should not simply add one calendar day.",
        "market.calendar_status",
    )


def case_14() -> CaseResult:
    factor = make_factor_freshness(
        factor="taifex",
        market="TAIFEX",
        freshness="unavailable",
        confidence="low",
        usable=False,
        reason="matrix unavailable",
    )
    ok = not is_factor_usable(factor)
    return (_pass if ok else _fail)(
        "C14",
        "unavailable is not neutral",
        "is_factor_usable()=False",
        factor.to_dict(),
        "Unavailable factor must not be treated as a neutral score input.",
        "core.outlook_context",
    )


def case_15() -> CaseResult:
    result = apply_holiday_dampener(62, 0.5)
    ok = abs(result - 56.0) < 0.00001
    return (_pass if ok else _fail)(
        "C15",
        "holiday dampener only affects score amplitude",
        "apply_holiday_dampener(62, 0.5)=56",
        {"result": result},
        "Dampener compresses score around 50 and does not alter weights/confidence.",
        "core.outlook_context",
    )


def case_16() -> CaseResult:
    context = build_shadow_outlook_context("2317", datetime.fromisoformat("2026-06-19T22:30:00+08:00"))
    notes = [item.calendar_context_note for item in context.factor_freshness]
    ok = bool(notes) and all(bool(note) for note in notes)
    return (_pass if ok else _fail)(
        "C16",
        "calendar_context_note exists",
        "Every factor has calendar_context_note",
        [item.to_dict() for item in context.factor_freshness],
        "Shadow factor freshness should explain market-calendar relationship.",
        "outlook.shadow_builder",
    )


def case_17() -> CaseResult:
    return _skip(
        "C17",
        "formal API protection",
        "Formal detail API does not expose shadow/calendar fields",
        {"server_started": False},
        "This matrix script intentionally does not start FastAPI. Phase Calendar-C already verified formal API protection; smoke/manual tests cover HTTP separately.",
        "review_src.app",
    )


CASES: list[Callable[[], CaseResult]] = [
    case_01,
    case_02,
    case_03,
    case_04,
    case_05,
    case_06,
    case_07,
    case_08,
    case_09,
    case_10,
    case_11,
    case_12,
    case_13,
    case_14,
    case_15,
    case_16,
    case_17,
]


def _markdown(results: list[CaseResult]) -> str:
    total = len(results)
    passes = sum(1 for item in results if item.status == "PASS")
    fails = sum(1 for item in results if item.status == "FAIL")
    skips = sum(1 for item in results if item.status == "SKIP")
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    lines = [
        "# Calendar Shadow Matrix Report",
        "",
        "Generated by `scripts/test_calendar_shadow_matrix.py`.",
        "",
        "## Summary",
        "",
        f"* total cases: {total}",
        f"* pass: {passes}",
        f"* fail: {fails}",
        f"* skip: {skips}",
        f"* generated_at: {generated_at}",
        "",
        "## Case Results",
        "",
        "| Case | Name | Status | Expected | Actual | Reason | Related module |",
        "| ---- | ---- | ------ | -------- | ------ | ------ | -------------- |",
    ]
    for item in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.case_id,
                    item.case_name,
                    item.status,
                    item.expected.replace("|", "\\|"),
                    item.actual.replace("|", "\\|"),
                    item.reason.replace("|", "\\|"),
                    item.related_module,
                ]
            )
            + " |"
        )
    lines.extend(["", "## Failures", ""])
    failures = [item for item in results if item.status == "FAIL"]
    if failures:
        for item in failures:
            lines.append(f"* `{item.case_id}` {item.case_name}: {item.reason}; actual={item.actual}")
    else:
        lines.append("No failures.")
    lines.extend(["", "## Skips", ""])
    skips_list = [item for item in results if item.status == "SKIP"]
    if skips_list:
        for item in skips_list:
            lines.append(f"* `{item.case_id}` {item.case_name}: {item.reason}")
    else:
        lines.append("No skips.")
    lines.extend(
        [
            "",
            "## Production Safety",
            "",
            "* No DB writes.",
            "* No API response changes.",
            "* No frontend changes.",
            "* No external website/API fetches.",
            "* No production scoring or next-day outlook takeover.",
            "* No FastAPI server startup inside this matrix script.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    results = [case() for case in CASES]
    report = _markdown(results)
    target = ROOT / "docs" / "calendar_shadow_matrix_report.md"
    target.write_text(report, encoding="utf-8")

    total = len(results)
    passes = sum(1 for item in results if item.status == "PASS")
    fails = sum(1 for item in results if item.status == "FAIL")
    skips = sum(1 for item in results if item.status == "SKIP")
    print(f"Calendar shadow matrix: total={total} pass={passes} fail={fails} skip={skips}")
    for item in results:
        print(f"{item.status} {item.case_id} {item.case_name}")
    print(f"Wrote {target}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
