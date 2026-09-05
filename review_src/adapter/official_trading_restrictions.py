from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from core.config import HEADERS, safe_error
from core.market_session import recent_market_date_for_eod

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


TPE = ZoneInfo("Asia/Taipei")

TWSE_ATTENTION_URL = "https://openapi.twse.com.tw/v1/announcement/notice"
TWSE_DISPOSITION_URL = "https://openapi.twse.com.tw/v1/announcement/punish"
TWSE_ALTERED_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT85U"
TWSE_HALT_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU"
TPEX_ATTENTION_URL = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"
TPEX_DISPOSITION_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
TPEX_TRADING_MODE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_cmode"


def parse_roc_date(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"(?<!\d)(\d{3})\D*(\d{1,2})\D*(\d{1,2})(?!\d)", text)
    if not match:
        digits = re.sub(r"\D", "", text)
        if len(digits) != 7:
            return None
        parts = (digits[:3], digits[3:5], digits[5:7])
    else:
        parts = match.groups()
    try:
        return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2])).isoformat()
    except ValueError:
        return None


def parse_roc_period(value: Any) -> tuple[str | None, str | None]:
    text = str(value or "").strip()
    matches = re.findall(r"(\d{3})\D*(\d{1,2})\D*(\d{1,2})", text)
    parsed: list[str] = []
    for year, month, day in matches[:2]:
        try:
            parsed.append(date(int(year) + 1911, int(month), int(day)).isoformat())
        except ValueError:
            continue
    return (
        parsed[0] if parsed else None,
        parsed[1] if len(parsed) > 1 else parsed[0] if parsed else None,
    )


def _stock_code(value: Any) -> str | None:
    code = str(value or "").strip()
    return code if re.fullmatch(r"\d{4}", code) else None


def _truthy_flag(value: Any) -> bool:
    return str(value or "").strip().upper() in {"Y", "YES", "1", "TRUE", "Ｙ", "是"}


def _event_key(row: dict[str, Any]) -> str:
    text = "|".join(
        str(row.get(key) or "")
        for key in (
            "market",
            "code",
            "restriction_type",
            "announcement_date",
            "effective_from",
            "effective_to",
            "source_id",
        )
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _event(
    *,
    code: str,
    company_name: Any,
    market: str,
    restriction_type: str,
    announcement_date: str,
    effective_from: str | None,
    effective_to: str | None,
    reason: Any,
    source_id: str,
) -> dict[str, Any]:
    row = {
        "code": code,
        "company_name": str(company_name or "").strip()[:200],
        "market": market,
        "restriction_type": restriction_type,
        "announcement_date": announcement_date,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "reason": str(reason or "").strip()[:4000],
        "source_id": source_id,
        "source_quality": "official",
    }
    row["event_key"] = _event_key(row)
    return row


def normalize_twse_attention(row: dict[str, Any], as_of_date: str) -> list[dict[str, Any]]:
    code = _stock_code(row.get("Code"))
    announcement = parse_roc_date(row.get("Date"))
    if not code or not announcement:
        return []
    return [
        _event(
            code=code,
            company_name=row.get("Name"),
            market="listed",
            restriction_type="attention",
            announcement_date=announcement,
            effective_from=announcement,
            effective_to=announcement,
            reason=row.get("TradingInfoForAttention"),
            source_id="twse_attention",
        )
    ]


def normalize_twse_disposition(row: dict[str, Any], as_of_date: str) -> list[dict[str, Any]]:
    code = _stock_code(row.get("Code"))
    announcement = parse_roc_date(row.get("Date"))
    start, end = parse_roc_period(row.get("DispositionPeriod"))
    if not code or not announcement:
        return []
    return [
        _event(
            code=code,
            company_name=row.get("Name"),
            market="listed",
            restriction_type="disposition",
            announcement_date=announcement,
            effective_from=start or announcement,
            effective_to=end or start or announcement,
            reason=row.get("Detail") or row.get("ReasonsOfDisposition"),
            source_id="twse_disposition",
        )
    ]


def normalize_twse_altered(row: dict[str, Any], as_of_date: str) -> list[dict[str, Any]]:
    code = _stock_code(row.get("Code"))
    if not code:
        return []
    return [
        _event(
            code=code,
            company_name=row.get("Name"),
            market="listed",
            restriction_type="altered_trading",
            announcement_date=as_of_date,
            effective_from=as_of_date,
            effective_to=as_of_date,
            reason="official altered-trading list",
            source_id="twse_altered",
        )
    ]


def normalize_twse_halt(row: dict[str, Any], as_of_date: str) -> list[dict[str, Any]]:
    code = _stock_code(row.get("Code"))
    start = parse_roc_date(row.get("TradingHaltDate"))
    resume = parse_roc_date(row.get("TradingResumptionDate"))
    if not code or not start:
        return []
    end = (date.fromisoformat(resume) - timedelta(days=1)).isoformat() if resume else start
    return [
        _event(
            code=code,
            company_name=row.get("Name"),
            market="listed",
            restriction_type="trading_halt",
            announcement_date=min(start, as_of_date),
            effective_from=start,
            effective_to=end,
            reason="official trading-halt list",
            source_id="twse_halt",
        )
    ]


def normalize_tpex_attention(row: dict[str, Any], as_of_date: str) -> list[dict[str, Any]]:
    code = _stock_code(row.get("SecuritiesCompanyCode"))
    announcement = parse_roc_date(row.get("Date"))
    if not code or not announcement:
        return []
    return [
        _event(
            code=code,
            company_name=row.get("CompanyName"),
            market="otc",
            restriction_type="attention",
            announcement_date=announcement,
            effective_from=announcement,
            effective_to=announcement,
            reason=row.get("TradingInformation"),
            source_id="tpex_attention",
        )
    ]


def normalize_tpex_disposition(row: dict[str, Any], as_of_date: str) -> list[dict[str, Any]]:
    code = _stock_code(row.get("SecuritiesCompanyCode"))
    announcement = parse_roc_date(row.get("Date"))
    start, end = parse_roc_period(row.get("DispositionPeriod"))
    if not code or not announcement:
        return []
    return [
        _event(
            code=code,
            company_name=row.get("CompanyName"),
            market="otc",
            restriction_type="disposition",
            announcement_date=announcement,
            effective_from=start or announcement,
            effective_to=end or start or announcement,
            reason=row.get("DisposalCondition") or row.get("DispositionReasons"),
            source_id="tpex_disposition",
        )
    ]


def normalize_tpex_trading_mode(row: dict[str, Any], as_of_date: str) -> list[dict[str, Any]]:
    code = _stock_code(row.get("SecuritiesCompanyCode"))
    announcement = parse_roc_date(row.get("Date")) or as_of_date
    if not code:
        return []
    mappings = (
        ("AlteredTrading", "altered_trading"),
        ("PeriodicTrading", "periodic_trading"),
        ("ManagedStock", "managed_stock"),
        ("SuspensionOfTrading", "trading_halt"),
    )
    return [
        _event(
            code=code,
            company_name=row.get("CompanyName"),
            market="otc",
            restriction_type=restriction_type,
            announcement_date=announcement,
            effective_from=announcement,
            effective_to=announcement,
            reason=f"official trading-mode flag: {field}",
            source_id="tpex_cmode",
        )
        for field, restriction_type in mappings
        if _truthy_flag(row.get(field))
    ]


Normalizer = Callable[[dict[str, Any], str], list[dict[str, Any]]]


def fetch_official_trading_restrictions(
    *,
    as_of_date: str | None = None,
    timeout: float = 20,
) -> dict[str, Any]:
    decision_date = as_of_date or recent_market_date_for_eod()
    if not decision_date:
        decision_date = datetime.now(TPE).date().isoformat()
    sources: tuple[tuple[str, str, str, Normalizer], ...] = (
        ("listed", "twse_attention", TWSE_ATTENTION_URL, normalize_twse_attention),
        ("listed", "twse_disposition", TWSE_DISPOSITION_URL, normalize_twse_disposition),
        ("listed", "twse_altered", TWSE_ALTERED_URL, normalize_twse_altered),
        ("listed", "twse_halt", TWSE_HALT_URL, normalize_twse_halt),
        ("otc", "tpex_attention", TPEX_ATTENTION_URL, normalize_tpex_attention),
        ("otc", "tpex_disposition", TPEX_DISPOSITION_URL, normalize_tpex_disposition),
        ("otc", "tpex_cmode", TPEX_TRADING_MODE_URL, normalize_tpex_trading_mode),
    )
    items: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    for market, source_id, url, normalizer in sources:
        try:
            response = requests.get(
                url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("official restriction payload is not a row list")
            raw_rows = payload
            parsed = [
                event for raw in raw_rows if isinstance(raw, dict) for event in normalizer(raw, decision_date)
            ]
            candidate_rows = sum(
                1
                for raw in raw_rows
                if isinstance(raw, dict) and _stock_code(raw.get("Code") or raw.get("SecuritiesCompanyCode"))
            )
            if candidate_rows and not parsed:
                raise RuntimeError(
                    "official restriction payload contained stock codes but no row could be normalized"
                )
            items.extend(parsed)
            source_results.append(
                {
                    "market": market,
                    "source_id": source_id,
                    "url": url,
                    "ok": True,
                    "status": "ok",
                    "rows_received": len(parsed),
                    "raw_rows_received": len(raw_rows),
                    "candidate_rows_received": candidate_rows,
                    "data_date": decision_date,
                }
            )
        except Exception as exc:
            source_results.append(
                {
                    "market": market,
                    "source_id": source_id,
                    "url": url,
                    "ok": False,
                    "status": "failed",
                    "rows_received": 0,
                    "data_date": decision_date,
                    "error": safe_error(exc),
                }
            )
    from adapter.official_corporate_halts import fetch_corporate_halts

    corporate = fetch_corporate_halts(as_of_date=decision_date, timeout=timeout)
    items.extend(corporate["items"])
    source_results.extend(corporate["sources"])
    ok = all(bool(item.get("ok")) for item in source_results)
    return {
        "ok": ok,
        "status": "ok" if ok else "partial",
        "data_date": decision_date,
        "items": items,
        "rows": len(items),
        "sources": source_results,
    }
