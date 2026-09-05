from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any

import requests

from core.components import read_components
from core.config import HEADERS, mask_secret_text, safe_error
from core.db import db
from core.market_session import normalize_list_mode
from core.utils import parse_num
from core.valuation_freshness import valuation_freshness_for_row
from core.valuation_normalizer import normalize_valuation_row, valuation_metric_display_value
from repository.watchlist_repository import get_watchlist_codes

_db_lock = threading.RLock()

def configure_industry_profile_service(*, db_lock: threading.RLock) -> None:
    global _db_lock
    _db_lock = db_lock


THEME_PROFILE_TAG_PRIORITY = [
    "subindustry",
    "theme",
    "sector_group",
]

THEME_PROFILE_OFFICIAL_FALLBACK = "official_industry"
MANUAL_CURATED_THEME_SOURCE = "MANUAL_CURATED_EXCEL"
USER_TAXONOMY_PRIMARY = "primary_revenue"
USER_TAXONOMY_SECONDARY_TYPES = [
    "secondary_revenue_1",
    "secondary_revenue_2",
    "secondary_revenue_3",
]
USER_TAXONOMY_BROAD_PRIMARY_NAMES = {
    "半導體",
    "電子",
    "電子零組件",
    "金融",
    "傳產",
    "其他",
    "其他電子",
    "生技",
    "電機",
    "化工",
    "航運",
}
USER_TAXONOMY_BROAD_PRIMARY_SIZE = 80

THEME_PROFILE_SOURCE_PRECHECKS = [
    {
        "source": "TWSE_OFFICIAL_COMPANY_PROFILE",
        "status": "unavailable",
        "reason": "official_company_profile_has_broad_industry_only",
        "note": "TWSE 官方公司基本資料目前只有官方產業大類代碼，沒有可重跑的細分題材/供應鏈標籤。",
    },
    {
        "source": "TPEX_OFFICIAL_COMPANY_PROFILE",
        "status": "unavailable",
        "reason": "official_company_profile_has_broad_industry_only",
        "note": "TPEx 官方公司基本資料目前只有官方產業大類代碼，沒有可重跑的細分題材/供應鏈標籤。",
    },
]

INDUSTRY_PROFILE_SOURCES: dict[str, list[dict[str, str]]] = {
    "twse": [
        {
            "source": "TWSE_OFFICIAL_COMPANY_PROFILE",
            "market": "上市",
            "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        }
    ],
    "tpex": [
        {
            "source": "TPEX_OFFICIAL_COMPANY_PROFILE",
            "market": "上櫃",
            "url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        }
    ],
}

INDUSTRY_CODE_COLUMNS = [
    "公司代號",
    "公司代碼",
    "公司簡稱代號",
    "證券代號",
    "有價證券代號",
    "代號",
    "SecuritiesCompanyCode",
    "SecuritiesCode",
    "Code",
    "stock_id",
    "symbol",
]

INDUSTRY_NAME_COLUMNS = [
    "公司名稱",
    "公司簡稱",
    "有價證券名稱",
    "證券名稱",
    "名稱",
    "CompanyName",
    "CompanyAbbreviation",
    "Name",
    "stock_name",
    "name",
]

INDUSTRY_VALUE_COLUMNS = [
    "產業別",
    "產業分類",
    "產業類別",
    "SecuritiesIndustryCode",
    "industry",
    "Industry",
]

INDUSTRY_CODE_VALUE_COLUMNS = [
    "產業代號",
    "產業編號",
    "industry_code",
]

TWSE_ISIN_INDUSTRY_CODE_NAME_SOURCE = "TWSE ISIN 證券編碼_分類查詢 產業別代碼"

TWSE_ISIN_INDUSTRY_CODE_NAME_MAP: dict[str, str] = {
    # Source: TWSE ISIN「證券編碼_分類查詢」產業別代碼對照。
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "07": "化學生技醫療",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "13": "電子工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光事業",
    "17": "金融保險業",
    "18": "貿易百貨",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "34": "電子商務",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}

INDUSTRY_PROFILE_RAW_SUMMARY_KEYS = [
    "公司代號",
    "公司名稱",
    "公司簡稱",
    "產業別",
    "SecuritiesCompanyCode",
    "CompanyName",
    "CompanyAbbreviation",
    "SecuritiesIndustryCode",
    "Symbol",
    "Date",
    "出表日期",
    "上市日期",
    "DateOfListing",
]


def _industry_unavailable(reason: str, *, quality: str = "missing") -> dict[str, Any]:
    return {
        "available": False,
        "quality": quality,
        "label": "同業估值資料不足",
        "reason": reason,
        "code": None,
        "industry": None,
        "market": None,
        "peer_count": 0,
        "peer_group_type": "official_industry",
        "peer_group_name": None,
        "fallback_reason": reason,
        "source": None,
        "date": None,
        "pe": None,
        "pb": None,
        "dividend_yield": None,
        "disclaimer": "同業估值比較僅為同產業相對參考，不代表買賣建議。",
    }


def _theme_unavailable_payload(code: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "code": str(code).zfill(4)[:4] if code else None,
        "tags": [],
        "display_tags": [],
        "fine_categories_display": [],
        "official_category_display": None,
        "peer_basis_display": None,
        "classification_display_tags_deduped": [],
        "primary_peer_group": None,
        "reason": reason or "尚未接入可信、可重跑的全市場細分產業/題材/供應鏈資料源；不得用 LLM、股票名稱或大量硬編 mapping 猜測。",
        "source": None,
        "updated_at": None,
    }


def _theme_profile_rows_for_code(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    norm_code = str(code or "").strip().zfill(4)[:4]
    if not norm_code.isdigit():
        return []
    try:
        rows = conn.execute(
            """
            SELECT code, tag_type, tag_name, source, source_key, quality, updated_at
            FROM stock_theme_profile
            WHERE code=?
              AND COALESCE(quality, 'ok') != 'missing'
            ORDER BY
              CASE tag_type
                WHEN 'primary_revenue' THEN 1
                WHEN 'secondary_revenue_1' THEN 2
                WHEN 'secondary_revenue_2' THEN 3
                WHEN 'secondary_revenue_3' THEN 4
                WHEN 'subindustry' THEN 5
                WHEN 'theme' THEN 6
                WHEN 'sector_group' THEN 7
                WHEN 'official_category' THEN 8
                ELSE 9
              END,
              tag_name,
              source
            """,
            (norm_code,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def _theme_peer_count(conn: sqlite3.Connection, tag_type: str, tag_name: str) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT code) AS c
            FROM stock_theme_profile
            WHERE tag_type=?
              AND tag_name=?
              AND COALESCE(quality, 'ok') != 'missing'
            """,
            (tag_type, tag_name),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["c"] or 0) if row else 0


def _theme_peer_valuation_count(
    conn: sqlite3.Connection,
    tag_type: str,
    tag_name: str,
    data_date: Any | None,
    valuation_source: str = "twse",
) -> int:
    if not data_date:
        return _theme_peer_count(conn, tag_type, tag_name)
    if valuation_source == "legacy":
        try:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT t.code) AS c
                FROM stock_theme_profile t
                JOIN valuation v ON v.code = t.code
                WHERE t.tag_type = ?
                  AND t.tag_name = ?
                  AND v.date = (
                    SELECT MAX(v2.date)
                    FROM valuation v2
                    WHERE v2.code = t.code
                  )
                  AND COALESCE(t.quality, 'ok') != 'missing'
                """,
                (tag_type, tag_name),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row["c"] or 0) if row else 0
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT t.code) AS c
            FROM stock_theme_profile t
            JOIN twse_daily_valuation v ON v.symbol = t.code
            WHERE t.tag_type = ?
              AND t.tag_name = ?
              AND v.data_date = ?
              AND COALESCE(t.quality, 'ok') != 'missing'
            """,
            (tag_type, tag_name, data_date),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["c"] or 0) if row else 0


def _manual_tag_values(rows: list[dict[str, Any]], tag_type: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        if row.get("source") != MANUAL_CURATED_THEME_SOURCE:
            continue
        if row.get("tag_type") != tag_type:
            continue
        tag_name = str(row.get("tag_name") or "").strip()
        if tag_name and tag_name not in values:
            values.append(tag_name)
    return values


THEME_DISPLAY_PRIORITY = [
    USER_TAXONOMY_PRIMARY,
    "secondary_revenue_1",
    "secondary_revenue_2",
    "secondary_revenue_3",
    "official_category",
    "sector_group",
    "theme",
]

THEME_FINE_DISPLAY_PRIORITY = [
    USER_TAXONOMY_PRIMARY,
    "secondary_revenue_1",
    "secondary_revenue_2",
    "secondary_revenue_3",
]


def _clean_theme_tag(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "undefined", "nan"}:
        return ""
    return text


def _theme_display_values(
    rows: list[dict[str, Any]],
    *,
    priority: list[str],
    limit: int,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_type.setdefault(str(row.get("tag_type") or ""), []).append(row)
    for tag_type in priority:
        for row in by_type.get(tag_type, []):
            value = _clean_theme_tag(row.get("tag_name"))
            if not value or value in seen:
                continue
            seen.add(value)
            values.append(value)
            if len(values) >= limit:
                return values
    return values


def _first_theme_display_value(rows: list[dict[str, Any]], tag_types: list[str]) -> str | None:
    for tag_type in tag_types:
        for row in rows:
            if row.get("tag_type") != tag_type:
                continue
            value = _clean_theme_tag(row.get("tag_name"))
            if value:
                return value
    return None


def _theme_peer_count_for_conditions(
    conn: sqlite3.Connection,
    conditions: list[tuple[str, str]],
    data_date: Any | None,
    valuation_source: str = "twse",
) -> int:
    if not conditions:
        return 0
    values: list[Any] = []
    clauses: list[str] = []
    for tag_type, tag_name in conditions:
        clauses.append("(t.tag_type=? AND t.tag_name=?)")
        values.extend([tag_type, tag_name])
    params: list[Any] = []
    join = ""
    if data_date and valuation_source == "legacy":
        join = """
        JOIN valuation v ON v.code = t.code
          AND v.date = (
            SELECT MAX(v2.date)
            FROM valuation v2
            WHERE v2.code = t.code
          )
        """
    elif data_date:
        join = "JOIN twse_daily_valuation v ON v.symbol = t.code AND v.data_date = ?"
        params.append(data_date)
    params.extend([MANUAL_CURATED_THEME_SOURCE, *values, len(conditions)])
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM (
              SELECT t.code
              FROM stock_theme_profile t
              {join}
              WHERE t.source=?
                AND COALESCE(t.quality, 'ok') != 'missing'
                AND ({' OR '.join(clauses)})
              GROUP BY t.code
              HAVING COUNT(DISTINCT t.tag_type || ':' || t.tag_name)=?
            )
            """,
            params,
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["c"] or 0) if row else 0


def _theme_peer_rows_for_conditions(
    conn: sqlite3.Connection,
    conditions: list[tuple[str, str]],
    data_date: Any,
    valuation_source: str = "twse",
) -> list[sqlite3.Row]:
    if not conditions:
        return []
    values: list[Any] = []
    clauses: list[str] = []
    for tag_type, tag_name in conditions:
        clauses.append("(t.tag_type=? AND t.tag_name=?)")
        values.extend([tag_type, tag_name])
    params = [*values, len(conditions), data_date]
    if valuation_source == "legacy":
        try:
            rows = conn.execute(
                f"""
                WITH peer_codes AS (
                  SELECT t.code
                  FROM stock_theme_profile t
                  WHERE t.source=?
                    AND COALESCE(t.quality, 'ok') != 'missing'
                    AND ({' OR '.join(clauses)})
                  GROUP BY t.code
                  HAVING COUNT(DISTINCT t.tag_type || ':' || t.tag_name)=?
                )
                SELECT v.code AS symbol, v.pe AS pe_ratio, v.pb AS pb_ratio, v.dividend_yield AS dividend_yield
                FROM peer_codes p
                JOIN valuation v ON v.code = p.code
                WHERE v.date = (
                  SELECT MAX(v2.date)
                  FROM valuation v2
                  WHERE v2.code = p.code
                )
                """,
                [MANUAL_CURATED_THEME_SOURCE, *values, len(conditions)],
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return rows
    try:
        rows = conn.execute(
            f"""
            WITH peer_codes AS (
              SELECT t.code
              FROM stock_theme_profile t
              WHERE t.source=?
                AND COALESCE(t.quality, 'ok') != 'missing'
                AND ({' OR '.join(clauses)})
              GROUP BY t.code
              HAVING COUNT(DISTINCT t.tag_type || ':' || t.tag_name)=?
            )
            SELECT v.symbol, v.pe_ratio, v.pb_ratio, v.dividend_yield
            FROM peer_codes p
            JOIN twse_daily_valuation v ON v.symbol = p.code
            WHERE v.data_date = ?
            """,
            [MANUAL_CURATED_THEME_SOURCE, *params],
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return rows


def _primary_revenue_is_broad(conn: sqlite3.Connection, primary: str) -> tuple[bool, int]:
    if not primary:
        return False, 0
    count = _theme_peer_count_for_conditions(conn, [(USER_TAXONOMY_PRIMARY, primary)], None)
    return bool(count > USER_TAXONOMY_BROAD_PRIMARY_SIZE or primary in USER_TAXONOMY_BROAD_PRIMARY_NAMES), count


def _user_taxonomy_peer_group(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    data_date: Any | None = None,
    valuation_source: str = "twse",
) -> dict[str, Any] | None:
    primary_values = _manual_tag_values(rows, USER_TAXONOMY_PRIMARY)
    if not primary_values:
        return None
    primary = primary_values[0]
    primary_is_broad, primary_count = _primary_revenue_is_broad(conn, primary)
    if primary_is_broad:
        for idx, secondary_type in enumerate(USER_TAXONOMY_SECONDARY_TYPES, start=1):
            for secondary in _manual_tag_values(rows, secondary_type):
                conditions = [(USER_TAXONOMY_PRIMARY, primary), (secondary_type, secondary)]
                count = _theme_peer_count_for_conditions(conn, conditions, data_date, valuation_source)
                if count >= 3:
                    return {
                        "type": f"primary_secondary_{idx}",
                        "name": f"{primary} + {secondary}",
                        "peer_count": count,
                        "source": MANUAL_CURATED_THEME_SOURCE,
                        "conditions": conditions,
                        "peer_refinement_reason": "broad_primary_refined_by_secondary",
                        "peer_intersection_logic": "AND",
                        "broad_primary_group": True,
                        "primary_revenue": primary,
                        "secondary_revenue": secondary,
                    }
        return None
    count = _theme_peer_count_for_conditions(conn, [(USER_TAXONOMY_PRIMARY, primary)], data_date, valuation_source)
    if count >= 3:
        return {
            "type": "primary_revenue",
            "name": primary,
            "peer_count": count,
            "source": MANUAL_CURATED_THEME_SOURCE,
            "conditions": [(USER_TAXONOMY_PRIMARY, primary)],
            "peer_refinement_reason": "primary_revenue_specific_enough",
            "peer_intersection_logic": "AND",
            "broad_primary_group": False,
            "primary_revenue": primary,
            "primary_revenue_total_count": primary_count,
        }
    return None


def _theme_primary_peer_group(
    conn: sqlite3.Connection,
    code: str,
    rows: list[dict[str, Any]] | None = None,
    *,
    data_date: Any | None = None,
    valuation_source: str = "twse",
) -> dict[str, Any] | None:
    tag_rows = rows if rows is not None else _theme_profile_rows_for_code(conn, code)
    user_group = _user_taxonomy_peer_group(conn, tag_rows, data_date=data_date, valuation_source=valuation_source)
    if user_group:
        return user_group
    for tag_type in THEME_PROFILE_TAG_PRIORITY:
        candidates: list[dict[str, Any]] = []
        seen_tags: set[str] = set()
        for row in tag_rows:
            if row.get("tag_type") != tag_type:
                continue
            tag_name = str(row.get("tag_name") or "").strip()
            if not tag_name or tag_name in seen_tags:
                continue
            seen_tags.add(tag_name)
            count = _theme_peer_valuation_count(conn, tag_type, tag_name, data_date, valuation_source)
            candidates.append(
                {
                    "type": tag_type,
                    "name": tag_name,
                    "peer_count": count,
                    "source": row.get("source"),
                    "conditions": [(tag_type, tag_name)],
                    "peer_intersection_logic": "AND",
                }
            )
        eligible = [item for item in candidates if int(item.get("peer_count") or 0) >= 3]
        if eligible:
            return max(eligible, key=lambda item: (int(item.get("peer_count") or 0), str(item.get("name") or "")))
    return None


def latest_theme_profile_payload(code: str) -> dict[str, Any]:
    norm_code = str(code or "").strip().zfill(4)[:4]
    if not norm_code.isdigit():
        return _theme_unavailable_payload(None, "股票代號格式不正確")
    with closing(db()) as conn:
        rows = _theme_profile_rows_for_code(conn, norm_code)
        if not rows:
            return _theme_unavailable_payload(norm_code)
        primary = _theme_primary_peer_group(conn, norm_code, rows)
    display_tags = _theme_display_values(rows, priority=THEME_DISPLAY_PRIORITY, limit=5)
    fine_categories = _theme_display_values(rows, priority=THEME_FINE_DISPLAY_PRIORITY, limit=4)
    official_category = _first_theme_display_value(rows, ["official_category"])
    peer_basis = str((primary or {}).get("name") or "").strip() or (display_tags[0] if display_tags else None)
    tags = [
        {
            "tag_type": row.get("tag_type"),
            "tag_name": row.get("tag_name"),
            "source": row.get("source"),
            "source_key": row.get("source_key"),
            "quality": row.get("quality") or "ok",
            "updated_at": row.get("updated_at"),
        }
        for row in rows
    ]
    return {
        "available": True,
        "code": norm_code,
        "tags": tags,
        "display_tags": display_tags,
        "fine_categories_display": fine_categories,
        "official_category_display": official_category,
        "peer_basis_display": peer_basis,
        "classification_display_tags_deduped": display_tags,
        "primary_peer_group": primary,
        "source": sorted({str(row.get("source")) for row in rows if row.get("source")}),
        "updated_at": max((row.get("updated_at") or 0) for row in rows),
        "reason": "",
    }


def _industry_profile_from_db(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            """
            SELECT code, name, market, industry, industry_code, source, quality, updated_at
            FROM stock_industry_profile
            WHERE code=?
            LIMIT 1
            """,
            (str(code).zfill(4)[:4],),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    data = dict(row)
    industry = str(data.get("industry") or "").strip()
    if not industry:
        return None
    return data


def _median(values: list[float]) -> float | None:
    nums = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2


def _average(values: list[float]) -> float | None:
    nums = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _percentile_rank(value: float, values: list[float]) -> float | None:
    nums = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not nums:
        return None
    if len(nums) == 1:
        return 50.0
    lower_or_equal = sum(1 for v in nums if v <= value)
    return max(0.0, min(100.0, lower_or_equal / len(nums) * 100.0))


def _valuation_relative_label(percentile: float | None) -> str:
    if percentile is None:
        return "同業樣本不足"
    if percentile >= 75:
        return "相對同業偏高"
    if percentile >= 50:
        return "相對同業中性偏高"
    if percentile >= 25:
        return "相對同業中性偏低"
    return "相對同業偏低"


def _industry_metric_payload(
    stock_value: Any,
    peer_values: list[Any],
    *,
    allow_zero: bool = False,
) -> dict[str, Any]:
    stock_num = parse_num(stock_value)
    if stock_num is None or (stock_num <= 0 and not allow_zero) or (stock_num < 0 and allow_zero):
        return {
            "available": False,
            "stock": stock_num,
            "industry_median": None,
            "industry_avg": None,
            "percentile": None,
            "label": "無（不適用）",
            "peer_count": 0,
        }
    peers: list[float] = []
    for raw in peer_values:
        val = parse_num(raw)
        if val is None:
            continue
        if allow_zero:
            if val >= 0:
                peers.append(float(val))
        elif val > 0:
            peers.append(float(val))
    if len(peers) < 3:
        return {
            "available": False,
            "stock": stock_num,
            "industry_median": _median(peers),
            "industry_avg": _average(peers),
            "percentile": None,
            "label": "同業樣本不足",
            "peer_count": len(peers),
        }
    pct = _percentile_rank(float(stock_num), peers)
    return {
        "available": True,
        "stock": stock_num,
        "industry_median": _median(peers),
        "industry_avg": _average(peers),
        "percentile": pct,
        "label": _valuation_relative_label(pct),
        "peer_count": len(peers),
    }


def _safe_metric_value(row: Any, metric: str) -> float | None:
    quality = normalize_valuation_row(dict(row) if row is not None else {})
    return valuation_metric_display_value(quality, metric)


def _safe_peer_metric_values(rows: list[Any], metric: str) -> list[float | None]:
    return [_safe_metric_value(row, metric) for row in rows]


def _latest_peer_valuation_row(conn: sqlite3.Connection, code: str) -> tuple[dict[str, Any] | None, str]:
    """Return the latest valuation row normalized for peer comparison.

    Prefer the official daily valuation table. If the stock is OTC or otherwise
    absent there, use the existing local valuation table as a read-only fallback.
    """
    try:
        row = conn.execute(
            """
            SELECT *
            FROM twse_daily_valuation
            WHERE symbol=?
            ORDER BY data_date DESC
            LIMIT 1
            """,
            (code,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        data = dict(row)
        if (data.get("source_status") or "ok") == "ok":
            return data, "twse"

    try:
        legacy_row = conn.execute(
            """
            SELECT *
            FROM valuation
            WHERE code=?
            ORDER BY date DESC
            LIMIT 1
            """,
            (code,),
        ).fetchone()
    except sqlite3.OperationalError:
        legacy_row = None
    if not legacy_row:
        return None, "none"

    legacy = dict(legacy_row)
    return {
        "symbol": legacy.get("code"),
        "data_date": legacy.get("date"),
        "pe_ratio": legacy.get("pe"),
        "pb_ratio": legacy.get("pb"),
        "dividend_yield": legacy.get("dividend_yield"),
        "source": legacy.get("source") or "LOCAL_VALUATION",
        "source_status": "ok",
        "updated_at": legacy.get("updated_at"),
        "timezone": "Asia/Taipei",
    }, "legacy"


def latest_industry_valuation_payload(code: str) -> dict[str, Any]:
    """Read-only peer valuation comparison for the stock detail API."""
    norm_code = str(code or "").strip().zfill(4)[:4]
    if not norm_code.isdigit():
        return _industry_unavailable("股票代號格式不正確")
    with closing(db()) as conn:
        theme_rows = _theme_profile_rows_for_code(conn, norm_code)
        peer_group_without_valuation = _theme_primary_peer_group(conn, norm_code, theme_rows) if theme_rows else None
        profile = _industry_profile_from_db(conn, norm_code)
        if not profile:
            payload = _industry_unavailable("尚未建立可信的產業分類資料；不可用股票名稱猜測同業。")
            payload["code"] = norm_code
            if peer_group_without_valuation:
                payload.update({
                    "peer_count": int(peer_group_without_valuation.get("peer_count") or 0),
                    "peer_group_type": peer_group_without_valuation.get("type"),
                    "peer_group_name": peer_group_without_valuation.get("name"),
                    "fallback_reason": "valuation unavailable; keeping imported taxonomy peer metadata",
                    "peer_refinement_reason": peer_group_without_valuation.get("peer_refinement_reason"),
                    "peer_intersection_logic": peer_group_without_valuation.get("peer_intersection_logic"),
                    "broad_primary_group": peer_group_without_valuation.get("broad_primary_group"),
                })
            return payload

        industry = str(profile.get("industry") or "").strip()
        valuation_row, valuation_source = _latest_peer_valuation_row(conn, norm_code)
        if not valuation_row or (valuation_row.get("source_status") or "ok") != "ok":
            payload = _industry_unavailable("該股票尚無可用 TWSE BWIBBU 估值資料。")
            payload.update({
                "code": norm_code,
                "industry": industry,
                "market": profile.get("market"),
                "source": profile.get("source"),
            })
            if peer_group_without_valuation:
                payload.update({
                    "peer_count": int(peer_group_without_valuation.get("peer_count") or 0),
                    "peer_group_type": peer_group_without_valuation.get("type"),
                    "peer_group_name": peer_group_without_valuation.get("name"),
                    "fallback_reason": "valuation unavailable; technical/chip/support remain nonblocking",
                    "peer_refinement_reason": peer_group_without_valuation.get("peer_refinement_reason"),
                    "peer_intersection_logic": peer_group_without_valuation.get("peer_intersection_logic"),
                    "broad_primary_group": peer_group_without_valuation.get("broad_primary_group"),
                })
            return payload
        data_date = valuation_row.get("data_date")
        valuation_quality = normalize_valuation_row(valuation_row)
        valuation_freshness = valuation_freshness_for_row(conn, norm_code, valuation_row)
        peer_group = _theme_primary_peer_group(conn, norm_code, theme_rows, data_date=data_date, valuation_source=valuation_source) or peer_group_without_valuation
        peer_group_type = THEME_PROFILE_OFFICIAL_FALLBACK
        peer_group_name = industry
        fallback_reason = ""
        try:
            if peer_group and int(peer_group.get("peer_count") or 0) >= 3:
                conditions = peer_group.get("conditions") or [(peer_group.get("type"), peer_group.get("name"))]
                theme_peers = _theme_peer_rows_for_conditions(conn, conditions, data_date, valuation_source)
                if len(theme_peers) >= 3:
                    peers = theme_peers
                    peer_group_type = str(peer_group.get("type") or "theme")
                    peer_group_name = str(peer_group.get("name") or "")
                else:
                    peers = []
                    fallback_reason = "細分類有效樣本不足，改用官方產業大類。"
            else:
                peers = []
                fallback_reason = "細分類有效樣本不足，改用官方產業大類。"
            if not peers:
                if valuation_source == "legacy":
                    peers = conn.execute(
                        """
                        SELECT v.code AS symbol, v.pe AS pe_ratio, v.pb AS pb_ratio, v.dividend_yield AS dividend_yield
                        FROM stock_industry_profile p
                        JOIN valuation v ON v.code = p.code
                        WHERE p.industry = ?
                          AND v.date = (
                            SELECT MAX(v2.date)
                            FROM valuation v2
                            WHERE v2.code = p.code
                          )
                          AND COALESCE(p.quality, 'ok') != 'missing'
                        """,
                        (industry,),
                    ).fetchall()
                else:
                    peers = conn.execute(
                        """
                        SELECT v.symbol, v.pe_ratio, v.pb_ratio, v.dividend_yield
                        FROM stock_industry_profile p
                        JOIN twse_daily_valuation v ON v.symbol = p.code
                        WHERE p.industry = ?
                          AND v.data_date = ?
                          AND COALESCE(p.quality, 'ok') != 'missing'
                        """,
                        (industry, data_date),
                    ).fetchall()
                if industry == "航運業" and peers:
                    fallback_reason = "細分類有效樣本不足，改用官方產業大類；已退回官方大類，可能包含不同商業模式。"
        except sqlite3.OperationalError:
            peers = []
        peer_count = len(peers)
        if peer_count < 3:
            reason = "所有分類有效樣本不足，無法比較。"
            return {
                "available": False,
                "quality": "insufficient_peers",
                "label": "同業估值資料不足",
                "reason": reason,
                "code": norm_code,
                "industry": industry,
                "market": profile.get("market"),
                "peer_count": peer_count,
                "peer_group_type": peer_group_type,
                "peer_group_name": peer_group_name,
                "fallback_reason": fallback_reason or reason,
                "peer_refinement_reason": (peer_group or {}).get("peer_refinement_reason"),
                "peer_intersection_logic": (peer_group or {}).get("peer_intersection_logic"),
                "broad_primary_group": (peer_group or {}).get("broad_primary_group"),
                "source": profile.get("source"),
                "date": data_date,
                "source_status": valuation_freshness.get("source_status") or valuation_row.get("source_status") or "ok",
                "freshness_status": valuation_freshness.get("freshness_status"),
                "market_type": valuation_freshness.get("market_type"),
                "market_latest_date": valuation_freshness.get("market_latest_date"),
                "valuation_trade_day_gap": valuation_freshness.get("valuation_trade_day_gap"),
                "price_date": valuation_freshness.get("price_date"),
                "price_trade_day_gap": valuation_freshness.get("price_trade_day_gap"),
                "valuation_flags": valuation_freshness.get("valuation_flags") or [],
                "stale_reason": valuation_freshness.get("stale_reason"),
                "debug_reason": valuation_freshness.get("debug_reason"),
                "pe": None,
                "pb": None,
                "dividend_yield": None,
                "disclaimer": "同業估值比較僅為同產業相對參考，不代表買賣建議。",
            }
        pe = _industry_metric_payload(
            valuation_metric_display_value(valuation_quality, "pe"),
            _safe_peer_metric_values(peers, "pe"),
        )
        pb = _industry_metric_payload(
            valuation_metric_display_value(valuation_quality, "pb"),
            _safe_peer_metric_values(peers, "pb"),
        )
        dy = _industry_metric_payload(
            valuation_metric_display_value(valuation_quality, "dividend_yield"),
            _safe_peer_metric_values(peers, "dividend_yield"),
            allow_zero=True,
        )
        available = any(metric.get("available") for metric in (pe, pb, dy))
        return {
            "available": bool(available),
            "quality": "ok" if available else "insufficient_metric_data",
            "label": "同業估值比較" if available else "同業估值資料不足",
            "reason": "同業樣本不足或估值欄位缺漏。" if not available else "",
            "code": norm_code,
            "industry": industry,
            "market": profile.get("market"),
            "peer_count": peer_count,
            "peer_group_type": peer_group_type,
            "peer_group_name": peer_group_name,
            "fallback_reason": fallback_reason,
            "peer_refinement_reason": (peer_group or {}).get("peer_refinement_reason"),
            "peer_intersection_logic": (peer_group or {}).get("peer_intersection_logic"),
            "broad_primary_group": (peer_group or {}).get("broad_primary_group"),
            "source": valuation_row.get("source") or profile.get("source") or "stock_industry_profile + valuation",
            "source_status": valuation_freshness.get("source_status") or valuation_row.get("source_status") or "ok",
            "date": data_date,
            "valuation_quality": valuation_quality,
            "freshness_status": valuation_freshness.get("freshness_status"),
            "market_type": valuation_freshness.get("market_type"),
            "market_latest_date": valuation_freshness.get("market_latest_date"),
            "valuation_trade_day_gap": valuation_freshness.get("valuation_trade_day_gap"),
            "price_date": valuation_freshness.get("price_date"),
            "price_trade_day_gap": valuation_freshness.get("price_trade_day_gap"),
            "valuation_flags": sorted(set((valuation_quality.get("suspicious_flags") or []) + (valuation_freshness.get("valuation_flags") or []))),
            "stale_reason": valuation_freshness.get("stale_reason"),
            "debug_reason": valuation_freshness.get("debug_reason"),
            "suspicious_flags": valuation_quality.get("suspicious_flags") or [],
            "parse_warnings": valuation_quality.get("parse_warnings") or [],
            "pe": pe,
            "pb": pb,
            "dividend_yield": dy,
            "disclaimer": "此比較僅為同產業相對估值，不代表買賣建議。估值高低需搭配成長性、獲利品質與景氣循環判斷。",
        }


def _metric_summary_text(label: str, metric: dict[str, Any] | None, suffix: str = "") -> str:
    if not metric or not metric.get("available"):
        return f"{label} 同業樣本不足。"
    stock = metric.get("stock")
    median = metric.get("industry_median")
    rel = metric.get("label") or "相對同業資料不足"

    def _fmt_num(value: Any) -> str:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return "--"
        return f"{n:.2f}{suffix}"

    return f"{label} 個股 {_fmt_num(stock)}，同業中位數 {_fmt_num(median)}，{rel}。"


def build_peer_valuation_summary(payload: dict[str, Any] | None) -> str:
    """Return a plain-language valuation sentence for the detail advice area.

    This only summarizes the existing industry_valuation payload; it does not
    recalculate peer valuation or change the peer selection formula.
    """
    if not payload or not payload.get("available"):
        return "估值比較：同業樣本不足，暫以個股自身估值與基本面變化觀察。"
    peer_name = payload.get("peer_group_name") or payload.get("industry") or "同業"
    peer_count = payload.get("peer_count") or 0
    parts = [
        f"估值比較：目前以「{peer_name}」作為同業參考，共 {peer_count} 檔樣本。",
        _metric_summary_text("PE", payload.get("pe")),
        _metric_summary_text("PB", payload.get("pb")),
        _metric_summary_text("殖利率", payload.get("dividend_yield"), "%"),
    ]
    fallback = payload.get("fallback_reason")
    if fallback:
        parts.append(str(fallback))
    if str(payload.get("source_status") or "ok").lower() in {"stale", "source_delayed", "cannot_verify"}:
        parts.append("估值資料待更新，以上同業比較僅供參考。")
    return " ".join(x for x in parts if x)


def normalize_industry_profile_key(key: Any) -> str:
    text = str(key or "").replace("\ufeff", "").strip().lower()
    text = re.sub(r"[\s　_:\-/%()（）]+", "", text)
    return text


def normalize_industry_profile_code(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    code = digits.zfill(4)[-4:]
    return code if re.fullmatch(r"\d{4}", code) else None


def normalize_industry_official_raw_code(value: Any) -> str | None:
    """Formal import guard: accept only exact 4-digit official raw codes."""
    text = str(value or "").replace("\ufeff", "").strip()
    return text if re.fullmatch(r"\d{4}", text) else None


def normalize_industry_official_value(value: Any) -> str | None:
    text = str(value or "").replace("\ufeff", "").strip()
    return text or None


def normalize_industry_code_key(value: Any) -> str | None:
    text = normalize_industry_official_value(value)
    if not text:
        return None
    if re.fullmatch(r"\d{1,2}", text):
        return text.zfill(2)
    return text


def load_official_industry_code_name_map() -> dict[str, str]:
    return dict(TWSE_ISIN_INDUSTRY_CODE_NAME_MAP)


def resolve_industry_display_name(industry_code: Any) -> tuple[str, bool]:
    text = normalize_industry_official_value(industry_code)
    key = normalize_industry_code_key(text)
    name = load_official_industry_code_name_map().get(key or "")
    if name:
        return name, False
    return f"官方產業代碼 {text or '--'}", True


def _industry_profile_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "rows", "result"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _industry_get_any(row: dict[str, Any], candidates: list[str]) -> Any:
    normalized = {normalize_industry_profile_key(k): v for k, v in row.items()}
    for candidate in candidates:
        key = normalize_industry_profile_key(candidate)
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return None


def _fetch_industry_source_rows(source_info: dict[str, str]) -> dict[str, Any]:
    url = source_info["url"]
    resp = requests.get(
        url,
        headers={**HEADERS, "Referer": "https://www.twse.com.tw/"},
        timeout=20,
    )
    status_code = int(resp.status_code)
    if status_code >= 400:
        raise RuntimeError(f"HTTP {status_code}: {mask_secret_text(resp.text[:240])}")
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    payload = resp.json()
    rows = _industry_profile_rows(payload)
    return {
        "source": source_info["source"],
        "market": source_info["market"],
        "url": url,
        "ok": True,
        "http_status": status_code,
        "encoding": resp.encoding or "utf-8",
        "rows": rows,
        "columns": list(rows[0].keys()) if rows else [],
        "sample": rows[:5],
        "total_rows": len(rows),
    }


def _industry_entry_from_row(row: dict[str, Any], source_info: dict[str, str], row_index: int) -> dict[str, Any]:
    raw_code = _industry_get_any(row, INDUSTRY_CODE_COLUMNS)
    code = normalize_industry_profile_code(raw_code)
    industry = _industry_get_any(row, INDUSTRY_VALUE_COLUMNS)
    industry_code = _industry_get_any(row, INDUSTRY_CODE_VALUE_COLUMNS)
    name = _industry_get_any(row, INDUSTRY_NAME_COLUMNS)
    return {
        "code": code,
        "raw_code": str(raw_code).strip() if raw_code not in (None, "") else None,
        "name": str(name).strip() if name not in (None, "") else None,
        "market": source_info["market"],
        "source": source_info["source"],
        "url": source_info["url"],
        "industry": str(industry).strip() if industry not in (None, "") else None,
        "industry_code": str(industry_code).strip() if industry_code not in (None, "") else None,
        "row_index": row_index,
        "raw_row": row,
    }


def _normalize_industry_source_rows(source_result: dict[str, Any], source_info: dict[str, str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for idx, row in enumerate(source_result.get("rows") or []):
        try:
            entries.append(_industry_entry_from_row(row, source_info, idx))
        except Exception as exc:
            logging.warning("industry profile row parse failed source=%s idx=%s err=%s", source_info.get("source"), idx, safe_error(exc))
    return entries


def _source_infos_for_industry_profile(source_key: str) -> list[dict[str, str]]:
    source_key = (source_key or "auto").strip().lower()
    selected_sources: list[dict[str, str]] = []
    if source_key in {"auto", "twse"}:
        selected_sources.extend(INDUSTRY_PROFILE_SOURCES["twse"])
    if source_key in {"auto", "tpex"}:
        selected_sources.extend(INDUSTRY_PROFILE_SOURCES["tpex"])
    return selected_sources


def _fetch_industry_sources(source_key: str) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[dict[str, Any]]]:
    source_infos = _source_infos_for_industry_profile(source_key)
    source_results: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for source_info in source_infos:
        try:
            result = _fetch_industry_source_rows(source_info)
        except Exception as exc:
            result = {
                "source": source_info["source"],
                "market": source_info["market"],
                "url": source_info["url"],
                "ok": False,
                "error": safe_error(exc),
                "rows": [],
                "columns": [],
                "sample": [],
                "total_rows": 0,
            }
        source_results.append(result)
        entries.extend(_normalize_industry_source_rows(result, source_info))
    return source_infos, source_results, entries


def _usable_industry_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("code") and e.get("industry")]


def _industry_duplicate_codes(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_code: dict[str, list[dict[str, Any]]] = {}
    for entry in _usable_industry_entries(entries):
        by_code.setdefault(str(entry["code"]), []).append(entry)
    return {code: rows for code, rows in by_code.items() if len(rows) > 1}


def _industry_single_from_entries(code: str, entries: list[dict[str, Any]], source_results: list[dict[str, Any]], source_key: str) -> dict[str, Any]:
    matches = [e for e in entries if e.get("code") == code]
    if not matches:
        contains_raw = any(code in json.dumps(row, ensure_ascii=False) for result in source_results for row in (result.get("sample") or []))
        return {
            "ok": False,
            "code": code,
            "source": source_key,
            "reason": "not_found",
            "attempts": [_industry_source_result_public(r) for r in source_results],
            "matched_rows": 0,
            "contains_code_in_sample": contains_raw,
            "writes_db": False,
            "can_update_stock_industry_profile": False,
        }
    usable = [item for item in matches if item.get("industry")]
    industries = {str(item.get("industry")) for item in usable if item.get("industry")}
    sources = {str(item.get("source")) for item in usable if item.get("source")}
    if len(usable) != 1 or len(industries) != 1 or len(sources) != 1:
        return {
            "ok": False,
            "code": code,
            "source": source_key,
            "reason": "ambiguous" if usable else "missing_industry",
            "matched_rows": len(matches),
            "matches": [_industry_entry_public(e) for e in matches[:5]],
            "attempts": [_industry_source_result_public(r) for r in source_results],
            "writes_db": False,
            "can_update_stock_industry_profile": False,
        }
    found = usable[0]
    return {
        "ok": True,
        "source": found.get("source"),
        "market": found.get("market"),
        "url": found.get("url"),
        "code": code,
        "name": found.get("name"),
        "industry": found.get("industry"),
        "industry_code": found.get("industry_code"),
        "columns": next((r.get("columns") or [] for r in source_results if r.get("source") == found.get("source")), []),
        "total_rows": next((r.get("total_rows") for r in source_results if r.get("source") == found.get("source")), None),
        "sample": next((r.get("sample") or [] for r in source_results if r.get("source") == found.get("source")), [])[:5],
        "matched_rows": len(matches),
        "matched_row": found.get("raw_row"),
        "writes_db": False,
        "can_update_stock_industry_profile": True,
    }


def _industry_entry_public(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": entry.get("code"),
        "name": entry.get("name"),
        "market": entry.get("market"),
        "source": entry.get("source"),
        "industry": entry.get("industry"),
        "industry_code": entry.get("industry_code"),
    }


def _industry_raw_summary(row: dict[str, Any], max_items: int = 16) -> dict[str, Any]:
    out: dict[str, Any] = {}
    normalized_map = {normalize_industry_profile_key(k): k for k in row.keys()}
    for candidate in INDUSTRY_PROFILE_RAW_SUMMARY_KEYS:
        key = normalized_map.get(normalize_industry_profile_key(candidate))
        if key is not None and key in row:
            value = row.get(key)
            out[str(key)] = str(value).strip()[:160] if value is not None else None
    if len(out) < max_items:
        for key, value in row.items():
            if str(key) in out:
                continue
            out[str(key)] = str(value).strip()[:160] if value is not None else None
            if len(out) >= max_items:
                break
    return out


def _industry_duplicate_row_public(entry: dict[str, Any]) -> dict[str, Any]:
    raw_row = entry.get("raw_row") if isinstance(entry.get("raw_row"), dict) else {}
    return {
        "source": entry.get("source"),
        "market": entry.get("market"),
        "code": entry.get("code"),
        "name": entry.get("name"),
        "company_name": _industry_get_any(raw_row, ["公司名稱", "CompanyName"]) if raw_row else entry.get("name"),
        "industry_raw": entry.get("industry"),
        "industry_code": entry.get("industry_code"),
        "security_type": _industry_get_any(raw_row, ["有價證券種類", "證券種類", "SecurityType"]) if raw_row else None,
        "market_type": _industry_get_any(raw_row, ["市場別", "MarketType"]) if raw_row else entry.get("market"),
        "listing_category": _industry_get_any(raw_row, ["上市日期", "DateOfListing"]) if raw_row else None,
        "raw_keys": list(raw_row.keys())[:40],
        "raw_summary": _industry_raw_summary(raw_row),
    }


def _industry_source_result_public(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": result.get("source"),
        "market": result.get("market"),
        "url": result.get("url"),
        "ok": bool(result.get("ok")),
        "http_status": result.get("http_status"),
        "encoding": result.get("encoding"),
        "error": result.get("error"),
        "columns": result.get("columns") or [],
        "total_rows": int(result.get("total_rows") or 0),
        "sample": (result.get("sample") or [])[:5],
    }


def inspect_industry_duplicate(code: str, source: str = "auto") -> dict[str, Any]:
    norm_code = normalize_industry_profile_code(code)
    if not norm_code:
        return {
            "ok": False,
            "mode": "inspect_duplicate",
            "reason": "invalid_code",
            "writes_db": False,
        }
    source_key = (source or "auto").strip().lower()
    if source_key not in {"auto", "twse", "tpex"}:
        return {
            "ok": False,
            "mode": "inspect_duplicate",
            "code": norm_code,
            "source": source_key,
            "reason": "unsupported_source",
            "writes_db": False,
        }
    _, source_results, entries = _fetch_industry_sources(source_key)
    matches = [entry for entry in entries if entry.get("code") == norm_code]
    usable = [entry for entry in matches if entry.get("industry")]
    is_ambiguous = len(usable) != 1
    return {
        "ok": bool(matches),
        "mode": "inspect_duplicate",
        "code": norm_code,
        "source": source_key,
        "writes_db": False,
        "duplicate_count": len(matches),
        "usable_count": len(usable),
        "is_ambiguous": is_ambiguous,
        "rows": [_industry_duplicate_row_public(entry) for entry in matches[:10]],
        "sources": [_industry_source_result_public(result) for result in source_results],
        "suggested_policy": "manual_review_required" if is_ambiguous else "single_official_row_usable",
        "note": "若 duplicate_count 不是 1，正式匯入時不可自動挑選，需人工規則或官方欄位排除非普通股/重複資料。",
    }


def inspect_industry_mapping(source: str = "auto") -> dict[str, Any]:
    source_key = (source or "auto").strip().lower()
    if source_key not in {"auto", "twse", "tpex"}:
        return {
            "ok": False,
            "mode": "inspect_industry_mapping",
            "source": source_key,
            "reason": "unsupported_source",
            "writes_db": False,
            "mapping_available": False,
        }
    _, source_results, entries = _fetch_industry_sources(source_key)
    codes = sorted({str(entry.get("industry")) for entry in entries if entry.get("industry")})
    attempts = []
    for result in source_results:
        attempts.append(
            {
                "source": result.get("source"),
                "ok": bool(result.get("ok")),
                "url": result.get("url"),
                "columns": result.get("columns") or [],
                "total_rows": result.get("total_rows") or 0,
                "mapping_available": False,
                "reason": "company_profile_has_industry_code_only",
            }
        )
    return {
        "ok": False,
        "mode": "inspect_industry_mapping",
        "source": source_key,
        "writes_db": False,
        "mapping_available": False,
        "reason": "official_mapping_source_not_found",
        "attempts": attempts,
        "observed_industry_codes": codes[:80],
        "observed_industry_code_count": len(codes),
        "covers_sample_codes": {
            "24": "24" in codes,
            "31": "31" in codes,
            "17": "17" in codes,
        },
        "next_step": "需找到官方產業代碼/中文名稱對照來源；不得硬編 mapping、不得用 LLM 猜產業名稱。",
    }


def _industry_distinct_valuation_codes() -> list[str]:
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM twse_daily_valuation WHERE symbol IS NOT NULL AND symbol<>'' ORDER BY symbol"
        ).fetchall()
    out: list[str] = []
    for row in rows:
        code = normalize_industry_profile_code(row["symbol"])
        if code and code not in out:
            out.append(code)
    return out


def _industry_coverage_for_codes(codes: list[str], usable_codes: set[str], *, sample_limit: int | None = None) -> dict[str, Any]:
    normalized: list[str] = []
    for code in codes:
        norm = normalize_industry_profile_code(code)
        if norm and norm not in normalized:
            normalized.append(norm)
    missing = [code for code in normalized if code not in usable_codes]
    result = {
        "total": len(normalized),
        "matched": len(normalized) - len(missing),
        "missing": missing,
    }
    if sample_limit is not None:
        result["missing_sample"] = missing[:sample_limit]
        result.pop("missing", None)
    return result


def _industry_profile_codes_from_db(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT code FROM stock_industry_profile WHERE code IS NOT NULL AND code<>'' AND quality='ok'"
    ).fetchall()
    return {str(row["code"]).strip() for row in rows if str(row["code"] or "").strip()}


def _industry_profile_coverage(usable_codes: set[str]) -> dict[str, Any]:
    tw50_codes = [r["code"] for r in read_components()]
    watchlist_codes = get_watchlist_codes()
    valuation_codes = _industry_distinct_valuation_codes()
    tw50_cov = _industry_coverage_for_codes(tw50_codes, usable_codes)
    watch_cov = _industry_coverage_for_codes(watchlist_codes, usable_codes)
    valuation_cov = _industry_coverage_for_codes(valuation_codes, usable_codes, sample_limit=30)
    return {
        "tw50_total": tw50_cov["total"],
        "tw50_matched": tw50_cov["matched"],
        "tw50_missing": tw50_cov["missing"],
        "watchlist_total": watch_cov["total"],
        "watchlist_matched": watch_cov["matched"],
        "watchlist_missing": watch_cov["missing"],
        "valuation_codes_total": valuation_cov["total"],
        "valuation_codes_matched": valuation_cov["matched"],
        "valuation_codes_missing_sample": valuation_cov["missing_sample"],
    }


def _industry_update_target_codes(payload: dict[str, Any]) -> tuple[str, set[str] | None]:
    code = normalize_industry_official_raw_code(payload.get("code"))
    if code:
        return "single", {code}
    if str(payload.get("code") or "").strip():
        return "invalid_code", set()
    mode = normalize_list_mode(payload.get("mode", "watchlist"))
    if mode == "all":
        return "all", None
    if mode == "watchlist":
        return "watchlist", {c for c in get_watchlist_codes() if normalize_industry_profile_code(c)}
    if mode == "tw50":
        return "tw50", {r["code"] for r in read_components() if normalize_industry_profile_code(r.get("code"))}
    return mode, set()


def _industry_import_row_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": row.get("code"),
        "raw_code": row.get("raw_code"),
        "name": row.get("name"),
        "market": row.get("market"),
        "source": row.get("source"),
        "industry": row.get("industry"),
        "industry_code": row.get("industry_code"),
        "row_index": row.get("row_index"),
    }


def _industry_import_candidates(source_results: list[dict[str, Any]], target_codes: set[str] | None) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    skipped_long_code: list[dict[str, Any]] = []
    missing_industry: list[dict[str, Any]] = []
    missing_mapping: list[dict[str, Any]] = []
    for result in source_results:
        source_info = {
            "source": str(result.get("source") or ""),
            "market": str(result.get("market") or ""),
            "url": str(result.get("url") or ""),
        }
        for idx, raw_row in enumerate(result.get("rows") or []):
            if not isinstance(raw_row, dict):
                continue
            raw_code_value = _industry_get_any(raw_row, INDUSTRY_CODE_COLUMNS)
            raw_code = str(raw_code_value).replace("\ufeff", "").strip() if raw_code_value not in (None, "") else ""
            strict_code = normalize_industry_official_raw_code(raw_code_value)
            if not strict_code:
                if raw_code:
                    skipped_long_code.append(
                        {
                            "source": source_info["source"],
                            "market": source_info["market"],
                            "raw_code": raw_code,
                            "row_index": idx,
                            "raw_summary": _industry_raw_summary(raw_row, max_items=8),
                        }
                    )
                continue
            industry_code = normalize_industry_official_value(_industry_get_any(raw_row, INDUSTRY_VALUE_COLUMNS))
            name = normalize_industry_official_value(_industry_get_any(raw_row, INDUSTRY_NAME_COLUMNS))
            if target_codes is not None and strict_code not in target_codes:
                continue
            if not industry_code:
                missing_industry.append(
                    {
                        "source": source_info["source"],
                        "market": source_info["market"],
                        "code": strict_code,
                        "raw_code": raw_code,
                        "name": name,
                        "row_index": idx,
                    }
                )
                continue
            industry_name, mapping_missing = resolve_industry_display_name(industry_code)
            if mapping_missing:
                missing_mapping.append(
                    {
                        "source": source_info["source"],
                        "market": source_info["market"],
                        "code": strict_code,
                        "raw_code": raw_code,
                        "name": name,
                        "industry_code": industry_code,
                        "row_index": idx,
                    }
                )
            candidates.append(
                {
                    "code": strict_code,
                    "raw_code": raw_code,
                    "name": name,
                    "market": source_info["market"],
                    "source": source_info["source"],
                    "industry": industry_name,
                    "industry_code": industry_code,
                    "mapping_missing": mapping_missing,
                    "quality": "ok",
                    "row_index": idx,
                }
            )
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_code.setdefault(str(row["code"]), []).append(row)
    ambiguous = {code: rows for code, rows in by_code.items() if len(rows) != 1}
    writable = [rows[0] for code, rows in by_code.items() if code not in ambiguous and len(rows) == 1]
    return {
        "candidates": candidates,
        "writable": writable,
        "skipped_long_code": skipped_long_code,
        "missing_industry": missing_industry,
        "missing_mapping": missing_mapping,
        "ambiguous": ambiguous,
    }


def update_industry_profiles_from_official(payload: dict[str, Any]) -> dict[str, Any]:
    source_key = str(payload.get("source") or "auto").strip().lower()
    if source_key not in {"auto", "twse", "tpex"}:
        return {
            "ok": False,
            "source": source_key,
            "reason": "unsupported_source",
            "message": "source 僅支援 auto / twse / tpex",
            "writes_db": False,
        }
    mode, target_codes = _industry_update_target_codes(payload)
    if mode not in {"all", "watchlist", "tw50", "single"}:
        return {
            "ok": False,
            "mode": mode,
            "source": source_key,
            "reason": "unsupported_mode",
            "writes_db": False,
        }
    dry_run = bool(payload.get("dry_run", True))
    _, source_results, _ = _fetch_industry_sources(source_key)
    twse_result = next((r for r in source_results if r.get("source") == "TWSE_OFFICIAL_COMPANY_PROFILE"), {})
    tpex_result = next((r for r in source_results if r.get("source") == "TPEX_OFFICIAL_COMPANY_PROFILE"), {})
    parsed = _industry_import_candidates(source_results, target_codes)
    writable: list[dict[str, Any]] = parsed["writable"]
    write_count = 0
    now = time.time()
    if not dry_run and writable:
        with _db_lock, closing(db()) as conn:
            for row in writable:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO stock_industry_profile
                    (code, name, market, industry, industry_code, source, quality, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("code"),
                        row.get("name"),
                        row.get("market"),
                        row.get("industry"),
                        row.get("industry_code"),
                        row.get("source"),
                        "ok",
                        now,
                    ),
                )
                write_count += 1
            conn.commit()
    with closing(db()) as conn:
        usable_codes = _industry_profile_codes_from_db(conn)
    if dry_run:
        usable_codes = set(usable_codes) | {str(row["code"]) for row in writable}
    ambiguous_map: dict[str, list[dict[str, Any]]] = parsed["ambiguous"]
    return {
        "ok": bool(source_results) and all(bool(r.get("ok")) for r in source_results),
        "mode": mode,
        "source": source_key,
        "dry_run": dry_run,
        "writes_db": not dry_run,
        "fetched_twse": int(twse_result.get("total_rows") or 0),
        "fetched_tpex": int(tpex_result.get("total_rows") or 0),
        "candidate_rows": len(parsed["candidates"]),
        "would_write_count": len(writable),
        "write_count": write_count,
        "skipped_long_code_count": len(parsed["skipped_long_code"]),
        "skipped_long_code_sample": parsed["skipped_long_code"][:20],
        "ambiguous_count": len(ambiguous_map),
        "ambiguous_sample": [
            {
                "code": code,
                "rows": [_industry_import_row_public(row) for row in rows[:5]],
            }
            for code, rows in sorted(ambiguous_map.items())[:20]
        ],
        "missing_industry_count": len(parsed["missing_industry"]),
        "missing_industry_sample": parsed["missing_industry"][:20],
        "mapping_count": len(load_official_industry_code_name_map()),
        "mapping_source": TWSE_ISIN_INDUSTRY_CODE_NAME_SOURCE,
        "missing_mapping_count": len(parsed["missing_mapping"]),
        "missing_mapping_sample": parsed["missing_mapping"][:20],
        "coverage": _industry_profile_coverage(usable_codes),
        "sources": [_industry_source_result_public(result) for result in source_results],
    }


def debug_industry_profile_all(source: str = "auto") -> dict[str, Any]:
    source_key = (source or "auto").strip().lower()
    if source_key not in {"auto", "twse", "tpex"}:
        return {
            "ok": False,
            "mode": "all",
            "source": source_key,
            "reason": "unsupported_source",
            "writes_db": False,
            "can_update_stock_industry_profile": False,
        }
    _, source_results, entries = _fetch_industry_sources(source_key)
    usable_entries = _usable_industry_entries(entries)
    usable_codes = {str(entry["code"]) for entry in usable_entries}
    valid_code_rows = [entry for entry in entries if entry.get("code")]
    duplicate_map = _industry_duplicate_codes(entries)
    twse_result = next((r for r in source_results if r.get("source") == "TWSE_OFFICIAL_COMPANY_PROFILE"), {})
    tpex_result = next((r for r in source_results if r.get("source") == "TPEX_OFFICIAL_COMPANY_PROFILE"), {})
    tw50_codes = [r["code"] for r in read_components()]
    watchlist_codes = get_watchlist_codes()
    valuation_codes = _industry_distinct_valuation_codes()
    tw50_cov = _industry_coverage_for_codes(tw50_codes, usable_codes)
    watch_cov = _industry_coverage_for_codes(watchlist_codes, usable_codes)
    valuation_cov = _industry_coverage_for_codes(valuation_codes, usable_codes, sample_limit=30)
    return {
        "ok": bool(source_results) and all(bool(r.get("ok")) for r in source_results),
        "mode": "all",
        "source": source_key,
        "writes_db": False,
        "can_update_stock_industry_profile": bool(usable_codes),
        "sources": [_industry_source_result_public(r) for r in source_results],
        "summary": {
            "twse_ok": bool(twse_result.get("ok")),
            "tpex_ok": bool(tpex_result.get("ok")),
            "twse_rows": int(twse_result.get("total_rows") or 0),
            "tpex_rows": int(tpex_result.get("total_rows") or 0),
            "total_rows": len(entries),
            "valid_code_count": len({str(e["code"]) for e in valid_code_rows if e.get("code")}),
            "industry_non_empty_count": len(usable_entries),
            "empty_industry_count": len([e for e in valid_code_rows if not e.get("industry")]),
            "duplicate_code_count": len(duplicate_map),
            "ambiguous_code_sample": sorted(duplicate_map.keys())[:20],
        },
        "coverage": {
            "tw50_total": tw50_cov["total"],
            "tw50_matched": tw50_cov["matched"],
            "tw50_missing": tw50_cov["missing"],
            "watchlist_total": watch_cov["total"],
            "watchlist_matched": watch_cov["matched"],
            "watchlist_missing": watch_cov["missing"],
            "valuation_codes_total": valuation_cov["total"],
            "valuation_codes_matched": valuation_cov["matched"],
            "valuation_codes_missing_sample": valuation_cov["missing_sample"],
        },
        "samples": [_industry_entry_public(entry) for entry in usable_entries[:10]],
    }


def debug_industry_profile_source(code: str, source: str = "auto") -> dict[str, Any]:
    norm_code = normalize_industry_profile_code(code)
    if not norm_code:
        return {"ok": False, "reason": "invalid_code", "writes_db": False}
    source_key = (source or "auto").strip().lower()
    if source_key not in {"auto", "twse", "tpex"}:
        return {
            "ok": False,
            "code": norm_code,
            "source": source_key,
            "reason": "unsupported_source",
            "writes_db": False,
            "can_update_stock_industry_profile": False,
        }
    _, source_results, entries = _fetch_industry_sources(source_key)
    result = _industry_single_from_entries(norm_code, entries, source_results, source_key)
    if not result.get("ok"):
        result.setdefault("next_step", "官方 TWSE/TPEx 來源不可用或查無產業分類；不得猜測或寫入 stock_industry_profile。")
    return result


def local_industry_profile_source_status(code: str) -> dict[str, Any]:
    norm_code = str(code or "").strip().zfill(4)[:4]
    if not norm_code.isdigit():
        return {"ok": False, "reason": "invalid_code", "writes_db": False}
    with closing(db()) as conn:
        profile = _industry_profile_from_db(conn, norm_code)
    if profile:
        return {
            "ok": True,
            "code": norm_code,
            "source": profile.get("source"),
            "industry": profile.get("industry"),
            "market": profile.get("market"),
            "quality": profile.get("quality") or "ok",
            "writes_db": False,
        }
    return {
        "ok": False,
        "code": norm_code,
        "reason": "local_industry_profile_missing",
        "writes_db": False,
    }


def _theme_distinct_valuation_codes() -> list[str]:
    return _industry_distinct_valuation_codes()


def _theme_codes_from_db(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT code
            FROM stock_theme_profile
            WHERE code IS NOT NULL
              AND code <> ''
              AND COALESCE(quality, 'ok') != 'missing'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["code"]).strip() for row in rows if str(row["code"] or "").strip()}


def _theme_coverage(usable_codes: set[str]) -> dict[str, Any]:
    tw50_codes = [r["code"] for r in read_components()]
    watchlist_codes = get_watchlist_codes()
    valuation_codes = _theme_distinct_valuation_codes()
    tw50_cov = _industry_coverage_for_codes(tw50_codes, usable_codes)
    watch_cov = _industry_coverage_for_codes(watchlist_codes, usable_codes)
    valuation_cov = _industry_coverage_for_codes(valuation_codes, usable_codes, sample_limit=30)
    return {
        "tw50_total": tw50_cov["total"],
        "tw50_matched": tw50_cov["matched"],
        "tw50_missing": tw50_cov["missing"],
        "watchlist_total": watch_cov["total"],
        "watchlist_matched": watch_cov["matched"],
        "watchlist_missing": watch_cov["missing"],
        "valuation_codes_total": valuation_cov["total"],
        "valuation_codes_matched": valuation_cov["matched"],
        "valuation_codes_missing_sample": valuation_cov["missing_sample"],
    }


def _theme_update_target_codes(payload: dict[str, Any]) -> tuple[str, set[str] | None]:
    code = normalize_industry_official_raw_code(payload.get("code"))
    if code:
        return "single", {code}
    if str(payload.get("code") or "").strip():
        return "invalid_code", set()
    mode = normalize_list_mode(payload.get("mode", "watchlist"))
    if mode == "all":
        return "all", None
    if mode == "watchlist":
        return "watchlist", {c for c in get_watchlist_codes() if normalize_industry_profile_code(c)}
    if mode == "tw50":
        return "tw50", {r["code"] for r in read_components() if normalize_industry_profile_code(r.get("code"))}
    return mode, set()


def _theme_sample_tags(codes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with closing(db()) as conn:
        for code in codes:
            norm = normalize_industry_profile_code(code)
            if not norm:
                continue
            rows = _theme_profile_rows_for_code(conn, norm)
            out[norm] = {
                "available": bool(rows),
                "tags": [
                    {
                        "tag_type": row.get("tag_type"),
                        "tag_name": row.get("tag_name"),
                        "source": row.get("source"),
                        "quality": row.get("quality") or "ok",
                    }
                    for row in rows
                ],
                "reason": "" if rows else "no_trusted_theme_source_available",
            }
    return out


def _theme_profile_source_payload(mode: str, source: str, target_codes: set[str] | None = None) -> dict[str, Any]:
    with closing(db()) as conn:
        usable_codes = _theme_codes_from_db(conn)
    sample_codes = ["2330", "2317", "2881", "3017", "2376", "2603", "2610", "2618", "2633"]
    return {
        "ok": False,
        "mode": mode,
        "source": source,
        "writes_db": False,
        "can_update_stock_theme_profile": False,
        "available_sources": [],
        "unavailable_sources": THEME_PROFILE_SOURCE_PRECHECKS,
        "candidate_tags": 0,
        "write_count": 0,
        "skipped_count": 0 if target_codes is None else len(target_codes),
        "duplicate_tag_count": 0,
        "coverage": _theme_coverage(usable_codes),
        "sample_tags": _theme_sample_tags(sample_codes),
        "next_step": "需先取得可信、可重跑、全市場覆蓋的細分產業/題材/供應鏈公開資料源；不得用 LLM、股票名稱或大量硬編 mapping 猜測。",
    }


def debug_theme_profile_source(payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or "auto").strip().lower()
    if source not in {"auto"}:
        return {
            "ok": False,
            "source": source,
            "reason": "unsupported_source",
            "writes_db": False,
            "available_sources": [],
            "unavailable_sources": THEME_PROFILE_SOURCE_PRECHECKS,
        }
    mode, target_codes = _theme_update_target_codes(payload)
    if mode not in {"all", "watchlist", "tw50", "single"}:
        return {"ok": False, "mode": mode, "source": source, "reason": "unsupported_mode", "writes_db": False}
    return _theme_profile_source_payload(mode, source, target_codes)


def update_theme_profiles(payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or "auto").strip().lower()
    if source not in {"auto"}:
        return {
            "ok": False,
            "source": source,
            "reason": "unsupported_source",
            "writes_db": False,
        }
    mode, target_codes = _theme_update_target_codes(payload)
    if mode not in {"all", "watchlist", "tw50", "single"}:
        return {"ok": False, "mode": mode, "source": source, "reason": "unsupported_mode", "writes_db": False}
    dry_run = bool(payload.get("dry_run", True))
    result = _theme_profile_source_payload(mode, source, target_codes)
    result["dry_run"] = dry_run
    result["writes_db"] = False
    result["reason"] = "source_unavailable"
    result["message"] = "尚未找到可信、可重跑的全市場細分分類資料源；本次不寫入 stock_theme_profile，也不產生假標籤。"
    return result
