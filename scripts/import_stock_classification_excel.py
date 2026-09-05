from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH  # noqa: E402


SOURCE = "MANUAL_CURATED_EXCEL"
NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

SAMPLE_CODES = ["2330", "2454", "2317", "6669", "6757", "2603", "2610"]
VALIDATION_CODES = ["2330", "2454", "2317", "6669", "2603", "2610", "3491", "5425", "8261", "2481", "6435"]

ROLE_ALIASES = {
    "code": ["股票代號", "代號"],
    "name": ["公司名稱", "股票名"],
    "official_industry": ["官方大分類"],
    "primary": ["主要分類(主要營收高的項目)", "主要分類 (主要營收高的項目)", "細分類"],
    "secondary1": ["次分類1(次要營收高的項目)", "次分類1 (次要營收高的項目)", "細分類2"],
    "secondary2": ["次分類2(占比更少的營收項目)", "細分類3"],
    "secondary3": ["次分類3(比次分類2佔比更少營收的公司經營項目)"],
    "legacy_sector_group": ["大分類"],
}

NEW_REQUIRED_ROLES = ["code", "name", "official_industry", "primary", "secondary1", "secondary2", "secondary3"]
OLD_REQUIRED_ROLES = ["code", "name", "official_industry", "legacy_sector_group", "primary", "secondary1", "secondary2"]

BROAD_PRIMARY_NAMES = {
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

EXPECTED_KEYWORDS = {
    "2330": ["晶圓代工", "半導體製造", "IC生產製造", "先進製程"],
    "2454": ["IC設計", "晶片設計", "半導體"],
    "2317": ["電子代工", "EMS", "ODM", "伺服器", "AI伺服器"],
    "6669": ["伺服器", "ODM", "AI伺服器"],
    "2603": ["貨櫃航運", "航運", "海運"],
    "2610": ["航空", "空運"],
}


def _column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in str(cell_ref or "") if ch.isalpha())
    out = 0
    for ch in letters.upper():
        out = out * 26 + ord(ch) - 64
    return max(out - 1, 0)


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("a:si", NS):
        values.append("".join(text.text or "" for text in item.findall(".//a:t", NS)))
    return values


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//a:t", NS)).strip()
    node = cell.find("a:v", NS)
    raw = "" if node is None or node.text is None else node.text
    if cell_type == "s" and raw:
        try:
            return shared[int(raw)].strip()
        except (ValueError, IndexError):
            return raw.strip()
    return raw.strip()


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    if "xl/worksheets/sheet1.xml" in zf.namelist():
        return "xl/worksheets/sheet1.xml"
    candidates = sorted(name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
    if not candidates:
        raise ValueError("xlsx has no worksheet xml")
    return candidates[0]


def normalize_header(value: Any) -> str:
    text = str(value or "").replace("\ufeff", "").replace("\u3000", " ").strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\(", "(", text)
    text = re.sub(r"\)\s+", ")", text)
    return text


def normalize_text(value: Any) -> str:
    text = str(value or "").replace("\ufeff", "").replace("\u3000", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def normalize_code(value: Any) -> str | None:
    text = normalize_text(value)
    text = re.sub(r"\.(TW|TWO)$", "", text, flags=re.IGNORECASE)
    if re.fullmatch(r"\d+\.0", text):
        text = text.split(".", 1)[0]
    if not re.fullmatch(r"\d{4}", text):
        return None
    return text


def split_secondary_tags(value: Any) -> list[str]:
    text = normalize_text(value)
    if not text:
        return []
    out: list[str] = []
    for part in re.split(r"[/／]", text):
        tag = normalize_text(part)
        if tag and tag not in out:
            out.append(tag)
    return out


def read_xlsx_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        sheet_path = _first_sheet_path(zf)
        root = ET.fromstring(zf.read(sheet_path))
        raw_rows: list[list[str]] = []
        for row in root.findall(".//a:sheetData/a:row", NS):
            values: list[str] = []
            for cell in row.findall("a:c", NS):
                idx = _column_index(cell.attrib.get("r", "A1"))
                while len(values) <= idx:
                    values.append("")
                values[idx] = _cell_value(cell, shared)
            raw_rows.append(values)
    if not raw_rows:
        return [], []
    headers = [normalize_header(x) for x in raw_rows[0]]
    rows: list[dict[str, str]] = []
    for raw in raw_rows[1:]:
        item = {headers[i]: (raw[i].strip() if i < len(raw) and raw[i] is not None else "") for i in range(len(headers))}
        if any(normalize_text(v) for v in item.values()):
            rows.append(item)
    return rows, headers


def _alias_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for role, aliases in ROLE_ALIASES.items():
        for alias in aliases:
            out[normalize_header(alias)] = role
    return out


def detect_columns(headers: list[str]) -> dict[str, Any]:
    alias_to_role = _alias_map()
    found: dict[str, str] = {}
    used_headers: set[str] = set()
    for header in headers:
        role = alias_to_role.get(normalize_header(header))
        if role and role not in found:
            found[role] = header
            used_headers.add(header)
    new_missing = [role for role in NEW_REQUIRED_ROLES if role not in found]
    old_missing = [role for role in OLD_REQUIRED_ROLES if role not in found]
    if not new_missing:
        schema = "new_full_market"
        missing = []
        required = NEW_REQUIRED_ROLES
    elif not old_missing:
        schema = "legacy_1084"
        missing = []
        required = OLD_REQUIRED_ROLES
    else:
        schema = "unknown"
        missing = new_missing
        required = NEW_REQUIRED_ROLES
    return {
        "schema": schema,
        "columns": found,
        "missing_required_columns": [role for role in required if role not in found],
        "ignored_columns": [header for header in headers if header and header not in used_headers],
    }


def _tag_plan(schema: str, mapping: str) -> list[tuple[str, str]]:
    if schema == "legacy_1084":
        return [
            ("legacy_sector_group", "sector_group"),
            ("primary", "subindustry"),
            ("secondary1", "theme"),
            ("secondary2", "theme"),
        ]
    if mapping in {"user-authoritative", "hybrid"}:
        return [
            ("official_industry", "official_category"),
            ("official_industry", "sector_group"),
            ("primary", "primary_revenue"),
            ("primary", "subindustry"),
            ("secondary1", "secondary_revenue_1"),
            ("secondary1", "theme"),
            ("secondary2", "secondary_revenue_2"),
            ("secondary2", "theme"),
            ("secondary3", "secondary_revenue_3"),
            ("secondary3", "theme"),
        ]
    if mapping == "b-confirmed":
        return [
            ("primary", "sector_group"),
            ("secondary1", "subindustry"),
            ("secondary2", "theme"),
            ("secondary3", "theme"),
        ]
    return [
        ("primary", "subindustry"),
        ("secondary1", "theme"),
        ("secondary2", "theme"),
        ("secondary3", "theme"),
    ]


def _header_for(columns: dict[str, str], role: str) -> str:
    return columns.get(role, role)


def _role_value(row: dict[str, str], columns: dict[str, str], role: str) -> str:
    col = columns.get(role)
    return normalize_text(row.get(col, "")) if col else ""


def _keyword_hit(text: str, keywords: list[str]) -> bool:
    haystack = normalize_text(text).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _sample_keyword_check(rows_by_code: dict[str, dict[str, str]], columns: dict[str, str]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    mismatch_count = 0
    checked_count = 0
    for code, keywords in EXPECTED_KEYWORDS.items():
        row = rows_by_code.get(code)
        if not row:
            checks[code] = {"present": False, "expected_keywords": keywords}
            continue
        primary = _role_value(row, columns, "primary")
        secondary1 = _role_value(row, columns, "secondary1")
        primary_match = _keyword_hit(primary, keywords)
        secondary1_match = _keyword_hit(secondary1, keywords)
        mismatch = bool((not primary_match) and secondary1_match)
        checked_count += 1
        mismatch_count += 1 if mismatch else 0
        checks[code] = {
            "present": True,
            "expected_keywords": keywords,
            "primary": primary,
            "secondary1": secondary1,
            "primary_match": primary_match,
            "secondary1_match": secondary1_match,
            "secondary1_better_than_primary": mismatch,
        }
    majority_threshold = max(2, math.ceil(max(checked_count, 1) / 2))
    return {
        "checks": checks,
        "checked_count": checked_count,
        "secondary1_better_count": mismatch_count,
        "secondary1_better_majority": bool(checked_count and mismatch_count >= majority_threshold),
    }


def analyze_mapping(rows: list[dict[str, str]], columns: dict[str, str], schema: str) -> dict[str, Any]:
    rows_by_code: dict[str, dict[str, str]] = {}
    primary_values: list[str] = []
    secondary1_values: list[str] = []
    primary_slash_samples: list[dict[str, str]] = []
    duplicate_codes: list[str] = []
    seen_codes: set[str] = set()
    for row in rows:
        code = normalize_code(_role_value(row, columns, "code"))
        if not code:
            continue
        if code in seen_codes and code not in duplicate_codes:
            duplicate_codes.append(code)
        seen_codes.add(code)
        rows_by_code.setdefault(code, row)
        primary = _role_value(row, columns, "primary")
        secondary1 = _role_value(row, columns, "secondary1")
        if primary:
            primary_values.append(primary)
            if "/" in primary or "／" in primary:
                primary_slash_samples.append({"code": code, "primary": primary})
        if secondary1:
            secondary1_values.append(secondary1)
    primary_counter = Counter(primary_values)
    secondary1_counter = Counter(secondary1_values)
    primary_sizes = list(primary_counter.values())
    top_name, top_size = ("", 0)
    if primary_counter:
        top_name, top_size = primary_counter.most_common(1)[0]
    primary_median = float(median(primary_sizes)) if primary_sizes else 0.0
    keyword_check = _sample_keyword_check(rows_by_code, columns)
    reasons: list[str] = []
    if schema == "new_full_market":
        if len(primary_counter) < len(secondary1_counter) * 0.6 and primary_median > 30:
            reasons.append("primary_unique_count_too_low_and_primary_groups_too_broad")
        if top_size > 80 and top_name in BROAD_PRIMARY_NAMES:
            reasons.append("primary_top_group_is_broad")
        if keyword_check["secondary1_better_majority"]:
            reasons.append("representative_samples_match_secondary1_better_than_primary")
        if primary_slash_samples:
            reasons.append("primary_contains_slash")
    broad_primary_groups = {
        name: count
        for name, count in primary_counter.items()
        if count > 80 or name in BROAD_PRIMARY_NAMES
    }
    broad_sample_by_group: dict[str, list[str]] = {}
    broad_sample_codes: list[str] = []
    for group_name in sorted(broad_primary_groups):
        samples: list[str] = []
        for code, row in rows_by_code.items():
            if _role_value(row, columns, "primary") == group_name:
                samples.append(code)
        highlighted = [code for code in VALIDATION_CODES if code in samples]
        combined = []
        for code in [*highlighted, *samples]:
            if code not in combined:
                combined.append(code)
            if len(combined) >= 10:
                break
        broad_sample_by_group[group_name] = combined
        for code in combined:
            if code not in broad_sample_codes:
                broad_sample_codes.append(code)
    return {
        "duplicate_count": len(duplicate_codes),
        "duplicate_codes": duplicate_codes[:50],
        "primary_unique_count": len(primary_counter),
        "secondary1_unique_count": len(secondary1_counter),
        "primary_median_group_size": primary_median,
        "primary_top_group_name": top_name,
        "primary_top_group_size": top_size,
        "primary_slash_samples": primary_slash_samples[:20],
        "sample_business_keyword_check": keyword_check,
        "mapping_ambiguous": bool(reasons),
        "mapping_ambiguous_reasons": reasons,
        "broad_primary_groups": broad_primary_groups,
        "broad_primary_count": len(broad_primary_groups),
        "broad_primary_sample_codes": broad_sample_codes,
        "broad_primary_sample_codes_by_group": broad_sample_by_group,
    }


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_theme_profile (
            code TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            source TEXT NOT NULL,
            source_key TEXT,
            quality TEXT,
            updated_at REAL,
            PRIMARY KEY (code, tag_type, tag_name, source)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_theme_profile_code ON stock_theme_profile(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_theme_profile_tag ON stock_theme_profile(tag_type, tag_name)")


def backup_counts() -> dict[str, int]:
    if not DB_PATH.exists():
        return {"backup_unique_codes": 0, "backup_total_tags": 0}
    with sqlite3.connect(DB_PATH) as conn:
        ensure_table(conn)
        total = conn.execute("SELECT COUNT(*) FROM stock_theme_profile WHERE source=?", (SOURCE,)).fetchone()
        unique = conn.execute("SELECT COUNT(DISTINCT code) FROM stock_theme_profile WHERE source=?", (SOURCE,)).fetchone()
    return {
        "backup_unique_codes": int(unique[0] or 0),
        "backup_total_tags": int(total[0] or 0),
    }


def build_tags(
    rows: list[dict[str, str]],
    columns: dict[str, str],
    schema: str,
    mapping: str,
    broad_primary_groups: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tag_plan = _tag_plan(schema, mapping)
    broad_names = set((broad_primary_groups or {}).keys())
    tags: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    skipped_rows = 0
    missing_required_rows = 0
    valid_codes: set[str] = set()
    bad_tags: list[dict[str, str]] = []
    slash_residue: list[dict[str, str]] = []
    sample_tags: dict[str, Any] = {code: {"present": False, "tags": []} for code in VALIDATION_CODES}
    sample_rows: dict[str, Any] = {code: {"present": False} for code in VALIDATION_CODES}
    semantic_tags_written = 0
    backward_tags_written = 0
    primary_groups_written_as_subindustry: set[str] = set()
    primary_groups_not_written_as_subindustry: set[str] = set()
    for row in rows:
        code = normalize_code(_role_value(row, columns, "code"))
        if not code:
            skipped_rows += 1
            continue
        valid_codes.add(code)
        required_values = [_role_value(row, columns, role) for role in ("code", "name", "official_industry", "primary")]
        if any(not value for value in required_values):
            missing_required_rows += 1
            continue
        row_tags: list[dict[str, str]] = []
        for role, tag_type in tag_plan:
            source_key = _header_for(columns, role)
            primary_value = normalize_text(_role_value(row, columns, "primary"))
            if mapping in {"user-authoritative", "hybrid"} and role == "primary" and tag_type == "subindustry":
                if primary_value in broad_names:
                    primary_groups_not_written_as_subindustry.add(primary_value)
                    continue
                primary_groups_written_as_subindustry.add(primary_value)
            if role == "primary":
                values = [normalize_text(_role_value(row, columns, role))]
            elif role == "official_industry":
                values = [normalize_text(_role_value(row, columns, role))]
            else:
                values = split_secondary_tags(_role_value(row, columns, role))
            for tag_name in values:
                if not tag_name:
                    continue
                if "/" in tag_name or "／" in tag_name:
                    slash_residue.append({"code": code, "column": source_key, "tag": tag_name})
                if tag_name.lower() in {"nan", "none", "null"}:
                    bad_tags.append({"code": code, "column": source_key, "tag": tag_name})
                    continue
                key = (code, tag_type, tag_name)
                if key in seen:
                    continue
                seen.add(key)
                tags.append(
                    {
                        "code": code,
                        "name": _role_value(row, columns, "name"),
                        "official_industry": _role_value(row, columns, "official_industry"),
                        "tag_type": tag_type,
                        "tag_name": tag_name,
                        "source_key": source_key,
                    }
                )
                if tag_type in {"official_category", "primary_revenue", "secondary_revenue_1", "secondary_revenue_2", "secondary_revenue_3"}:
                    semantic_tags_written += 1
                else:
                    backward_tags_written += 1
                row_tags.append({"tag_type": tag_type, "tag_name": tag_name, "source_key": source_key})
        if code in sample_tags:
            sample_tags[code] = {
                "present": True,
                "name": _role_value(row, columns, "name"),
                "official_industry": _role_value(row, columns, "official_industry"),
                "tags": row_tags,
            }
            sample_rows[code] = {
                "present": True,
                "code": code,
                "name": _role_value(row, columns, "name"),
                "official_industry": _role_value(row, columns, "official_industry"),
                "primary": _role_value(row, columns, "primary"),
                "secondary1": _role_value(row, columns, "secondary1"),
                "secondary2": _role_value(row, columns, "secondary2"),
                "secondary3": _role_value(row, columns, "secondary3"),
            }
    return tags, {
        "valid_4digit_rows": len(valid_codes),
        "skipped_rows": skipped_rows,
        "missing_required_rows": missing_required_rows,
        "unique_codes_written": len({tag["code"] for tag in tags}),
        "bad_tag_count": len(bad_tags),
        "bad_tag_sample": bad_tags[:20],
        "slash_residue_count": len(slash_residue),
        "slash_residue_sample": slash_residue[:20],
        "has_slash_residue": bool(slash_residue),
        "sample_tags": sample_tags,
        "sample_rows": sample_rows,
        "semantic_tags_written": semantic_tags_written,
        "backward_compatible_tags_written": backward_tags_written,
        "primary_groups_written_as_subindustry": sorted(primary_groups_written_as_subindustry),
        "primary_groups_not_written_as_subindustry": sorted(primary_groups_not_written_as_subindustry),
        "hybrid_peer_refinement_enabled": bool(mapping in {"user-authoritative", "hybrid"}),
    }


def validate_db_import(conn: sqlite3.Connection, expected_codes: int, expected_tags: int) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM stock_theme_profile WHERE source=?", (SOURCE,)).fetchone()
    unique = conn.execute("SELECT COUNT(DISTINCT code) FROM stock_theme_profile WHERE source=?", (SOURCE,)).fetchone()
    bad = conn.execute(
        """
        SELECT COUNT(*) FROM stock_theme_profile
        WHERE source=? AND (tag_name IS NULL OR TRIM(tag_name)='' OR LOWER(tag_name) IN ('nan','none','null'))
        """,
        (SOURCE,),
    ).fetchone()
    slash = conn.execute(
        "SELECT COUNT(*) FROM stock_theme_profile WHERE source=? AND (tag_name LIKE '%/%' OR tag_name LIKE '%／%')",
        (SOURCE,),
    ).fetchone()
    return {
        "db_unique_codes": int(unique[0] or 0),
        "db_total_tags": int(total[0] or 0),
        "db_bad_tag_count": int(bad[0] or 0),
        "db_slash_residue_count": int(slash[0] or 0),
        "valid": bool(
            int(unique[0] or 0) == expected_codes
            and int(total[0] or 0) == expected_tags
            and int(bad[0] or 0) == 0
            and int(slash[0] or 0) == 0
        ),
    }


def parse_workbook(file_path: Path, *, mapping: str) -> dict[str, Any]:
    rows, headers = read_xlsx_rows(file_path)
    detected = detect_columns(headers)
    schema = detected["schema"]
    if detected["missing_required_columns"]:
        return {
            "ok": False,
            "error": "missing_required_columns",
            "schema": schema,
            "excel_rows": len(rows),
            **detected,
        }
    mapping_used = (
        "legacy"
        if schema == "legacy_1084"
        else (
            "mapping_b_confirmed"
            if mapping == "b-confirmed"
            else ("user_authoritative_hybrid" if mapping in {"user-authoritative", "hybrid"} else "mapping_a")
        )
    )
    mapping_report = analyze_mapping(rows, detected["columns"], schema)
    if mapping in {"user-authoritative", "hybrid"}:
        non_blocking_reasons = {"primary_top_group_is_broad"}
        blocking_reasons = [
            reason for reason in mapping_report.get("mapping_ambiguous_reasons", []) if reason not in non_blocking_reasons
        ]
        mapping_report["conservative_mapping_ambiguous"] = mapping_report.get("mapping_ambiguous")
        mapping_report["conservative_mapping_ambiguous_reasons"] = mapping_report.get("mapping_ambiguous_reasons", [])
        mapping_report["mapping_ambiguous"] = bool(blocking_reasons)
        mapping_report["mapping_ambiguous_reasons"] = blocking_reasons
    tags, tag_stats = build_tags(
        rows,
        detected["columns"],
        schema,
        mapping,
        mapping_report.get("broad_primary_groups") or {},
    )
    return {
        "ok": True,
        "schema": schema,
        "excel_rows": len(rows),
        "headers": headers,
        "detected_columns": detected["columns"],
        "ignored_columns": detected["ignored_columns"],
        "mapping_requested": mapping,
        "mapping_used": mapping_used,
        "user_classification_is_authoritative": bool(mapping in {"user-authoritative", "hybrid"}),
        "proposed_mapping": _tag_plan(schema, mapping),
        **mapping_report,
        **tag_stats,
        "candidate_tags": len(tags),
        "tags": tags,
    }


def import_tags(file_path: Path, *, apply: bool, mapping: str, force_first_import: bool) -> dict[str, Any]:
    parsed = parse_workbook(file_path, mapping=mapping)
    backups = backup_counts()
    base_result: dict[str, Any] = {
        "file": str(file_path),
        "source": SOURCE,
        "writes_db": False,
        "dry_run": not apply,
        "force_first_import": force_first_import,
        **backups,
        **{k: v for k, v in parsed.items() if k != "tags"},
    }
    if not parsed.get("ok"):
        return base_result
    stop_reasons: list[str] = []
    if parsed.get("mapping_ambiguous"):
        stop_reasons.append("mapping_ambiguous")
    if int(parsed.get("duplicate_count") or 0) > 0:
        stop_reasons.append("duplicate_codes")
    if int(parsed.get("missing_required_rows") or 0) > 0:
        stop_reasons.append("missing_required_rows")
    if int(parsed.get("bad_tag_count") or 0) > 0:
        stop_reasons.append("bad_tags")
    if int(parsed.get("slash_residue_count") or 0) > 0:
        stop_reasons.append("slash_residue")
    if apply and int(backups["backup_total_tags"]) == 0 and not force_first_import:
        stop_reasons.append("no_existing_manual_curated_rows")
    if stop_reasons:
        base_result["ok"] = False
        base_result["stop_reasons"] = stop_reasons
        base_result["stop_gates_triggered"] = stop_reasons
        base_result["next_step"] = "Fix ambiguity/data issues or rerun with explicit confirmation before applying."
        return base_result
    if not apply:
        base_result["ok"] = True
        base_result["inserted_theme_tags"] = parsed["candidate_tags"]
        base_result["stop_gates_triggered"] = []
        return base_result
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        try:
            conn.execute("BEGIN")
            ensure_table(conn)
            conn.execute("DELETE FROM stock_theme_profile WHERE source=?", (SOURCE,))
            now = time.time()
            for tag in parsed["tags"]:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO stock_theme_profile
                    (code, tag_type, tag_name, source, source_key, quality, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tag["code"],
                        tag["tag_type"],
                        tag["tag_name"],
                        SOURCE,
                        tag["source_key"],
                        "ok",
                        now,
                    ),
                )
            validation = validate_db_import(conn, parsed["unique_codes_written"], parsed["candidate_tags"])
            if not validation["valid"]:
                conn.rollback()
                base_result["ok"] = False
                base_result["writes_db"] = False
                base_result["stop_reasons"] = ["post_import_validation_failed"]
                base_result["validation"] = validation
                return base_result
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    base_result["ok"] = True
    base_result["writes_db"] = True
    base_result["inserted_theme_tags"] = parsed["candidate_tags"]
    base_result["validation"] = validation
    base_result["stop_gates_triggered"] = []
    return base_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Import curated stock classification Excel into stock_theme_profile.")
    parser.add_argument("--file", required=True, help="Path to curated .xlsx file.")
    parser.add_argument(
        "--mapping",
        choices=["auto", "b-confirmed", "user-authoritative", "hybrid"],
        default="auto",
        help="Column-to-tag mapping strategy.",
    )
    parser.add_argument("--force-first-import", action="store_true", help="Allow apply when no previous MANUAL_CURATED_EXCEL rows exist.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Parse and report only; do not write DB.")
    mode.add_argument("--apply", action="store_true", help="Replace MANUAL_CURATED_EXCEL rows and write DB.")
    args = parser.parse_args()
    path = Path(args.file)
    if not path.exists():
        print(json.dumps({"ok": False, "error": "file_not_found", "file": str(path)}, ensure_ascii=False, indent=2))
        return 2
    try:
        result = import_tags(
            path,
            apply=bool(args.apply),
            mapping=args.mapping,
            force_first_import=bool(args.force_first_import),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
