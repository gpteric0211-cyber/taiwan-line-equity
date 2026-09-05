from __future__ import annotations

from typing import Any

from core.utils import fmt

def cost_meta_text(meta: dict[str, Any], label: str = "") -> str:
    if not meta:
        return f"{label} no data" if label else "no data"
    reason = meta.get("reason", "")
    used = meta.get("used_days", 0)
    valid = meta.get("valid_days", 0)
    total = meta.get("total_net", 0)
    start = meta.get("from", "--")
    end = meta.get("to", "--")
    qmap = {"high": "reliable", "medium": "limited", "low": "provisional", "missing": "missing", "error": "error"}
    q = qmap.get(meta.get("quality"), meta.get("quality", ""))
    prefix = f"{label}: " if label else ""
    if reason == "ok":
        return f"{prefix}{q}; used {used}/{valid}; net {fmt(total,0)}; range {start} to {end}"
    return f"{prefix}{q}; {reason}; used {used}/{valid}"


def cost_display(cost: float | None, meta: dict[str, Any], fallback_price: float | None = None, fallback_label: str = "") -> str:
    """Display only verified numeric cost values.

    Rows should be blocked by data_readiness_for_items() before this fallback is
    visible in normal UI. force=1/debug paths may still show the missing marker.
    """
    meta = meta or {}
    q = meta.get("quality")
    if cost is not None and q in {"high", "medium", "low"}:
        return fmt(cost)
    if is_no_event_cost(meta):
        return ""
    return "璩囨枡缂哄彛"


def is_no_event_cost(meta: dict[str, Any] | None) -> bool:
    meta = meta or {}
    return (
        meta.get("quality") == "missing"
        and meta.get("reason") in {"no valid net-buy days", "no margin increase"}
        and int(meta.get("valid_days") or 0) >= 20
    )


def is_required_cost_ready(cost: float | None, meta: dict[str, Any] | None) -> bool:
    return cost is not None or is_no_event_cost(meta)


def cost_payload(cost: float | None, meta: dict[str, Any] | None, label: str) -> dict[str, Any]:
    text = cost_display(cost, meta or {})
    note = cost_meta_text(meta or {}, label)
    if (meta or {}).get("overlap_note"):
        note += " | " + str((meta or {}).get("overlap_note"))
    return {
        "value": text or None,
        "available": bool(text),
        "not_applicable": is_no_event_cost(meta),
        "quality": (meta or {}).get("quality"),
        "source_meta": meta or {},
        "note": note,
    }


def chip_indicator_display(item: dict[str, Any] | None, missing: str = "資料缺口") -> str:
    item = item or {}
    value = item.get("value")
    confidence = str(item.get("confidence") or "invalid")
    if value is None or confidence in {"invalid", "none"}:
        note = str(item.get("note") or "").strip()
        if note:
            if "校正差異" in note or "推估不可靠" in note:
                return "校正差異過大"
            if "缺少" in note or "缺" in note:
                return missing
            return note[:18]
        return missing
    return fmt(value)


def cost_display(cost: float | None, meta: dict[str, Any], fallback_price: float | None = None, fallback_label: str = "") -> str:
    """Display only verified numeric cost values in normal UI."""
    meta = meta or {}
    if cost is not None and meta.get("quality") in {"high", "medium", "low"}:
        return fmt(cost)
    if is_no_event_cost(meta):
        return ""
    return "—"


def chip_indicator_display(item: dict[str, Any] | None, missing: str = "—") -> str:
    """Display only whitelisted numeric chip-cost values."""
    item = item or {}
    value = item.get("value")
    confidence = str(item.get("confidence") or "invalid")
    if value is None or confidence in {"invalid", "none"}:
        return missing
    return fmt(value)


def mark_cost_overlap(wave_meta: dict[str, Any], short_meta: dict[str, Any], wave_cost: float | None, short_cost: float | None) -> dict[str, Any]:
    out = dict(wave_meta or {})
    if not out or not short_meta:
        return out
    same_window = out.get("from") == short_meta.get("from") and out.get("to") == short_meta.get("to")
    same_cost = wave_cost is not None and short_cost is not None and abs(float(wave_cost) - float(short_cost)) <= 0.01
    if same_window or same_cost:
        out["overlaps_short_cost"] = True
        out["overlap_note"] = "與近期買超成本使用同一段資料，屬同一訊號，不是第二個獨立確認"
    return out


def cost_display(cost: float | None, meta: dict[str, Any], fallback_price: float | None = None, fallback_label: str = "") -> str:
    """Display only verified numeric cost values; missing values render no row."""
    meta = meta or {}
    if cost is not None and meta.get("quality") in {"high", "medium", "low"}:
        return fmt(cost)
    return ""


def chip_indicator_display(item: dict[str, Any] | None, missing: str = "") -> str:
    """Display only whitelisted numeric chip-cost values; missing values render no row."""
    item = item or {}
    value = item.get("value")
    confidence = str(item.get("confidence") or "invalid")
    if value is None or confidence in {"invalid", "none"}:
        return missing
    return fmt(value)

