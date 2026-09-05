from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


EMPTY_TOKENS = {"", "--", "-", "n/a", "na", "nan", "none", "null", "inf", "infinity"}
OFFICIAL_SOURCE_TYPES = {"official_twse", "official_tpex", "official_twse_calculated", "official_tpex_calculated"}
OFFICIAL_CASH_DIVIDEND_KEYS = (
    "cash_dividend_per_share",
    "cash_dividend",
    "dividend_per_share",
    "DividendPerShare",
    "CashDividend",
    "cash_dividend_amount",
)
OFFICIAL_REFERENCE_PRICE_KEYS = (
    "official_reference_price",
    "reference_price",
    "ex_dividend_reference_price",
    "valuation_close_price",
    "close_price",
    "ClosingPrice",
    "closing_price",
)


def _to_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text)
    return text.replace("\ufeff", "").replace("\u3000", " ").strip()


def parse_valuation_number(value: Any) -> tuple[float | None, list[str]]:
    warnings: list[str] = []
    text = _to_text(value)
    if text.lower() in EMPTY_TOKENS:
        return None, warnings
    if any("\uff10" <= ch <= "\uff19" for ch in str(value)):
        warnings.append("fullwidth_digit_normalized")
    cleaned = text.replace(",", "").replace("%", "").strip()
    if cleaned.lower() in EMPTY_TOKENS:
        return None, warnings
    try:
        num = float(cleaned)
    except (TypeError, ValueError):
        warnings.append("raw_parse_warning")
        return None, warnings
    if not math.isfinite(num):
        warnings.append("raw_parse_warning")
        return None, warnings
    return num, warnings


def normalize_pe_value(value: Any) -> float | None:
    return parse_valuation_number(value)[0]


def normalize_pb_value(value: Any) -> float | None:
    return parse_valuation_number(value)[0]


def normalize_dividend_yield_value(value: Any, source_unit: str | None = None) -> float | None:
    num, _ = parse_valuation_number(value)
    if num is None:
        return None
    unit = str(source_unit or "").strip().lower()
    if unit == "ratio" and 0 <= num <= 1:
        return num * 100
    return num


def classify_pe_status(value: Any) -> str:
    num, warnings = parse_valuation_number(value)
    if num is None:
        return "parse_warning" if warnings else "unavailable"
    if num <= 0:
        return "pe_loss_or_non_meaningful"
    if num > 1000:
        return "pe_extreme_outlier"
    if num > 100:
        return "pe_high_but_not_outlier"
    return "normal"


def classify_pb_status(value: Any) -> str:
    num, warnings = parse_valuation_number(value)
    if num is None:
        return "parse_warning" if warnings else "unavailable"
    if num <= 0:
        return "pb_net_worth_non_positive"
    if num > 100:
        return "pb_extreme_outlier"
    return "normal"


def classify_dividend_yield_status(value: Any, source_unit: str | None = None) -> str:
    raw, warnings = parse_valuation_number(value)
    if raw is None:
        return "parse_warning" if warnings else "unavailable"
    unit = str(source_unit or "").strip().lower()
    if raw < 0:
        return "dividend_yield_suspicious"
    if 0 <= raw <= 1 and unit not in {"percent", "percentage", "pct", "ratio"}:
        return "source_unit_unknown"
    normalized = normalize_dividend_yield_value(raw, source_unit=unit)
    if normalized is None:
        return "unavailable"
    if normalized > 20:
        return "dividend_yield_suspicious"
    return "normal"


def classify_dividend_yield_unavailable_reason(
    value: Any,
    *,
    source_type: str | None = None,
    source_status: str | None = None,
    source_unit: str | None = None,
) -> str | None:
    """Return a reason code for non-displayable dividend-yield values.

    The reason is intentionally separate from the display status.  Normal UI can
    keep showing concise text, while audits can tell official blanks apart from
    parser problems or suspicious official values.
    """
    if not source_status or str(source_status).strip().lower() in {"not_found", "missing", "source_row_missing"}:
        return "source_row_missing"
    source = str(source_type or "").strip().lower()
    text = _to_text(value)
    low = text.lower()
    is_official = source in OFFICIAL_SOURCE_TYPES or source.startswith("official_")
    if low in {"", "none", "null", "nan"}:
        return "official_blank" if is_official else "cannot_verify"
    if low in {"-", "--", "n/a", "na"}:
        return "official_dash" if is_official else "cannot_verify"
    raw, warnings = parse_valuation_number(value)
    if warnings or raw is None:
        return "parser_blank_bug" if is_official else "cannot_verify"
    unit = str(source_unit or "").strip().lower()
    if raw == 0:
        return None
    if raw < 0:
        return "official_suspicious_value" if is_official else "cannot_verify"
    if 0 <= raw <= 1 and unit not in {"percent", "percentage", "pct", "ratio"}:
        return "cannot_verify"
    normalized = normalize_dividend_yield_value(raw, source_unit=unit)
    if normalized is not None and normalized > 20:
        return "official_suspicious_value" if is_official else "cannot_verify"
    return None


def _raw_from_row(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _raw_field_from_row(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[Any, str | None]:
    for key in keys:
        if key in row:
            return row.get(key), key
    return None, None


def _is_official_source_type(source_type: str | None) -> bool:
    source = str(source_type or "").strip().lower()
    return source in OFFICIAL_SOURCE_TYPES or source.startswith("official_")


def derive_dividend_yield_from_official_fields(
    row: dict[str, Any] | Any | None,
    *,
    source_type: str | None = None,
) -> dict[str, Any]:
    """Derive dividend yield only from official cash-dividend and price fields.

    This helper deliberately refuses to use current quote, Yahoo, or inferred
    prices. A suspicious official yield remains suspicious; callers may use the
    derived value only when the native official yield is absent and this result
    is normal.
    """
    data = dict(row) if row is not None else {}
    if not data:
        return {
            "can_derive_dividend_yield": False,
            "derived_dividend_yield": None,
            "derived_dividend_yield_status": "unavailable",
            "derived_dividend_yield_reason": "source_row_missing",
            "dividend_yield_source": None,
        }
    if not _is_official_source_type(source_type):
        return {
            "can_derive_dividend_yield": False,
            "derived_dividend_yield": None,
            "derived_dividend_yield_status": "unavailable",
            "derived_dividend_yield_reason": "source_not_official",
            "dividend_yield_source": None,
        }
    cash_raw, cash_field = _raw_field_from_row(data, OFFICIAL_CASH_DIVIDEND_KEYS)
    price_raw, price_field = _raw_field_from_row(data, OFFICIAL_REFERENCE_PRICE_KEYS)
    cash, cash_warnings = parse_valuation_number(cash_raw)
    price, price_warnings = parse_valuation_number(price_raw)
    base = {
        "can_derive_dividend_yield": False,
        "derived_dividend_yield": None,
        "derived_dividend_yield_status": "unavailable",
        "derived_dividend_yield_reason": None,
        "dividend_yield_source": None,
        "derived_cash_dividend_per_share": cash,
        "derived_cash_dividend_field": cash_field,
        "derived_reference_price": price,
        "derived_reference_price_field": price_field,
        "derived_parse_warnings": sorted(set([*cash_warnings, *price_warnings])),
    }
    if cash is None or price is None:
        return {
            **base,
            "derived_dividend_yield_reason": "missing_official_dividend_or_price",
        }
    if cash < 0:
        return {
            **base,
            "derived_dividend_yield_reason": "invalid_official_dividend",
        }
    if price <= 0:
        return {
            **base,
            "derived_dividend_yield_reason": "invalid_official_reference_price",
        }
    if cash == 0:
        return {
            **base,
            "can_derive_dividend_yield": True,
            "derived_dividend_yield": 0.0,
            "derived_dividend_yield_status": "normal",
            "derived_dividend_yield_reason": "official_zero_dividend",
            "dividend_yield_source": "derived_from_official_cash_dividend",
        }
    derived = cash / price * 100
    if derived < 0 or derived > 20:
        return {
            **base,
            "can_derive_dividend_yield": False,
            "derived_dividend_yield": derived,
            "derived_dividend_yield_status": "dividend_yield_suspicious",
            "derived_dividend_yield_reason": "derived_yield_out_of_range",
            "dividend_yield_source": "derived_from_official_cash_dividend",
        }
    return {
        **base,
        "can_derive_dividend_yield": True,
        "derived_dividend_yield": derived,
        "derived_dividend_yield_status": "normal",
        "derived_dividend_yield_reason": "derived_from_official_cash_dividend",
        "dividend_yield_source": "derived_from_official_cash_dividend",
    }


def _valuation_date_from_row(row: dict[str, Any]) -> Any:
    return _raw_from_row(row, "data_date", "date", "valuation_date")


def _source_unit_from_row(row: dict[str, Any]) -> str | None:
    explicit = _raw_from_row(row, "source_unit", "dividend_yield_unit")
    if explicit:
        return str(explicit)
    source = str(_raw_from_row(row, "source", "source_type", "table_name") or "").lower()
    if any(token in source for token in ("twse", "tpex", "bwibbu", "yahoo", "valuation")):
        return "percent"
    return None


def classify_valuation_source(row: dict[str, Any] | Any | None) -> dict[str, str | None]:
    data = dict(row) if row is not None else {}
    source = str(_raw_from_row(data, "source", "source_id") or "")
    table = str(_raw_from_row(data, "table_name") or "")
    combined = f"{source} {table}".lower()
    if "tpex" in combined:
        return {
            "source_type": "official_tpex",
            "source_market": "otc",
            "source_name": source or "TPEX_PERATIO_ANALYSIS",
        }
    if "twse" in combined or "bwibbu" in combined:
        return {
            "source_type": "official_twse",
            "source_market": "listed",
            "source_name": source or "TWSE_BWIBBU",
        }
    if "yahoo" in combined or "yfinance" in combined:
        return {
            "source_type": "yahoo_fallback",
            "source_market": None,
            "source_name": source or "Yahoo/yfinance fallback",
        }
    if table == "valuation" or source:
        return {
            "source_type": "legacy_local",
            "source_market": None,
            "source_name": source or table or "local valuation",
        }
    return {"source_type": None, "source_market": None, "source_name": None}


def normalize_valuation_row(row: dict[str, Any] | Any | None) -> dict[str, Any]:
    data = dict(row) if row is not None else {}
    source_info = classify_valuation_source(data)
    source_unit = _source_unit_from_row(data)
    source_type = _raw_from_row(data, "source_type") or source_info.get("source_type")
    source_status = _raw_from_row(data, "source_status") or ("ok" if data else "not_found")
    pe_raw = _raw_from_row(data, "pe_ratio", "pe")
    pb_raw = _raw_from_row(data, "pb_ratio", "pb")
    dy_raw = _raw_from_row(data, "dividend_yield", "yield")
    pe, pe_warnings = parse_valuation_number(pe_raw)
    pb, pb_warnings = parse_valuation_number(pb_raw)
    dy_base, dy_warnings = parse_valuation_number(dy_raw)
    dy = normalize_dividend_yield_value(dy_raw, source_unit=source_unit)
    parse_warnings = [*pe_warnings, *pb_warnings, *dy_warnings]
    pe_status = classify_pe_status(pe_raw)
    pb_status = classify_pb_status(pb_raw)
    dy_status = classify_dividend_yield_status(dy_raw, source_unit=source_unit)
    dy_unavailable_reason = classify_dividend_yield_unavailable_reason(
        dy_raw,
        source_type=source_type,
        source_status=source_status,
        source_unit=source_unit,
    )
    derived_yield = derive_dividend_yield_from_official_fields(data, source_type=source_type)
    dividend_yield_source = "official_native" if dy_status == "normal" else None
    if dy_status == "normal":
        dy_unavailable_reason = None
    elif dy_status in {"unavailable", "parse_warning", "source_unit_unknown"} and derived_yield.get("derived_dividend_yield_status") == "normal":
        dy = derived_yield.get("derived_dividend_yield")
        dy_status = "normal"
        dy_unavailable_reason = None
        dividend_yield_source = "derived_from_official_cash_dividend"
    flags = detect_valuation_anomalies(
        {
            "pe_raw": pe_raw,
            "pb_raw": pb_raw,
            "dividend_yield_raw": dy_raw,
            "pe_normalized": pe,
            "pb_normalized": pb,
            "dividend_yield_normalized": dy,
            "pe_status": pe_status,
            "pb_status": pb_status,
            "dividend_yield_status": dy_status,
            "source_unit": source_unit,
        }
    )
    unavailable: list[str] = []
    if pe_status in {"unavailable", "parse_warning", "pe_loss_or_non_meaningful", "pe_extreme_outlier"}:
        unavailable.append("pe")
    if pb_status in {"unavailable", "parse_warning", "pb_net_worth_non_positive", "pb_extreme_outlier"}:
        unavailable.append("pb")
    if dy_status in {"unavailable", "parse_warning", "source_unit_unknown", "dividend_yield_suspicious"}:
        unavailable.append("dividend_yield")
    return {
        "pe_raw": pe_raw,
        "pb_raw": pb_raw,
        "dividend_yield_raw": dy_raw,
        "pe_normalized": pe,
        "pb_normalized": pb,
        "dividend_yield_normalized": dy,
        "pe_status": pe_status,
        "pb_status": pb_status,
        "dividend_yield_status": dy_status,
        "dividend_yield_unavailable_reason": dy_unavailable_reason,
        **derived_yield,
        "dividend_yield_source": dividend_yield_source,
        "suspicious_flags": flags,
        "parse_warnings": sorted(set(parse_warnings)),
        "unavailable_reason": ",".join(unavailable) if unavailable else None,
        "source_unit_unknown": dy_status == "source_unit_unknown",
        "dividend_yield_suspicious": dy_status == "dividend_yield_suspicious",
        "pe_loss_or_non_meaningful": pe_status == "pe_loss_or_non_meaningful",
        "pe_high_but_not_outlier": pe_status == "pe_high_but_not_outlier",
        "pe_extreme_outlier": pe_status == "pe_extreme_outlier",
        "pb_net_worth_non_positive": pb_status == "pb_net_worth_non_positive",
        "pb_extreme_outlier": pb_status == "pb_extreme_outlier",
        "field_shift_suspicious": "dividend_yield_unit_or_field_shift_suspicious" in flags,
        "pe_pb_possible_swap": "pe_pb_possible_swap" in flags,
        "valuation_date": _valuation_date_from_row(data),
        "valuation_close_price": _raw_from_row(data, "close_price", "valuation_close_price"),
        "table_name": _raw_from_row(data, "table_name"),
        "source_id": _raw_from_row(data, "source_id", "source"),
        "source_type": source_type,
        "source_market": _raw_from_row(data, "source_market") or source_info.get("source_market"),
        "source_name": _raw_from_row(data, "source_name") or source_info.get("source_name"),
        "source_unit": source_unit,
    }


def detect_valuation_anomalies(row: dict[str, Any] | Any) -> list[str]:
    data = dict(row) if row is not None else {}
    pe = data.get("pe_normalized")
    pb = data.get("pb_normalized")
    dy = data.get("dividend_yield_normalized")
    if pe is None:
        pe = normalize_pe_value(data.get("pe_raw") if "pe_raw" in data else _raw_from_row(data, "pe_ratio", "pe"))
    if pb is None:
        pb = normalize_pb_value(data.get("pb_raw") if "pb_raw" in data else _raw_from_row(data, "pb_ratio", "pb"))
    if dy is None:
        dy = normalize_dividend_yield_value(
            data.get("dividend_yield_raw") if "dividend_yield_raw" in data else _raw_from_row(data, "dividend_yield"),
            source_unit=data.get("source_unit"),
        )
    flags: list[str] = []
    try:
        if pe is not None and pb is not None and 0.5 <= float(pe) <= 10 and 5 <= float(pb) <= 50:
            flags.append("pe_pb_possible_swap")
    except (TypeError, ValueError):
        pass
    try:
        if dy is not None and float(dy) > 20:
            flags.append("dividend_yield_unit_or_field_shift_suspicious")
    except (TypeError, ValueError):
        pass
    if str(data.get("pe_status") or "") == "pe_extreme_outlier":
        flags.append("pe_extreme_outlier")
    if str(data.get("pb_status") or "") == "pb_extreme_outlier":
        flags.append("pb_extreme_outlier")
    return sorted(set(flags))


def build_valuation_quality_payload(row: dict[str, Any] | Any | None) -> dict[str, Any]:
    return normalize_valuation_row(row)


def valuation_metric_display_value(payload: dict[str, Any], metric: str) -> float | None:
    status_key = f"{metric}_status"
    value_key = f"{metric}_normalized"
    status = str(payload.get(status_key) or "")
    if metric == "pe" and status in {"normal", "pe_high_but_not_outlier"}:
        return payload.get(value_key)
    if metric == "pb" and status == "normal":
        return payload.get(value_key)
    if metric == "dividend_yield" and status == "normal":
        return payload.get(value_key)
    return None
