from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.single_track_v3_repository import active_alias_candidates  # noqa: E402
from services.stock_entity_registry_materializer import materialize_stock_entity_registry  # noqa: E402
from services.stock_entity_registry_service import resolve_stock_entity_query  # noqa: E402
from evaluation.entity_resolution_gate_v1 import evaluate_entity_resolution_gate  # noqa: E402


ROWS = [
    {"code": "2330", "name": "台灣積體電路製造股份有限公司", "trading_name": "台積電", "market": "listed", "exchange": "TWSE"},
    {"code": "2317", "name": "鴻海精密工業股份有限公司", "trading_name": "鴻海", "market": "listed", "exchange": "TWSE"},
    {"code": "2454", "name": "聯發科技股份有限公司", "trading_name": "聯發科", "market": "listed", "exchange": "TWSE"},
    {"code": "6643", "name": "圓星科技股份有限公司", "trading_name": "M31", "market": "otc", "exchange": "TPEX"},
    {"code": "2603", "name": "長榮海運股份有限公司", "trading_name": "長榮", "market": "listed", "exchange": "TWSE"},
    {"code": "2618", "name": "長榮航空股份有限公司", "trading_name": "長榮航", "market": "listed", "exchange": "TWSE"},
    {"code": "2646", "name": "星宇航空股份有限公司", "trading_name": "星宇航空", "market": "listed", "exchange": "TWSE"},
]


def resolve(query: str, active: str | None = None) -> dict:
    return resolve_stock_entity_query(query, active_rows=ROWS, active_stock_code=active)


def test_required_unique_aliases_switch_directly_without_confirmation() -> None:
    cases = {
        "星宇": "2646",
        "星宇呢": "2646",
        "所以星宇呢": "2646",
        "台積": "2330",
        "聯發科": "2454",
        "M31": "6643",
        "長榮": "2603",
        "長榮航": "2618",
    }
    for query, expected in cases.items():
        result = resolve(query, active="2454")
        assert result["ok"] is True, query
        assert result["stock"]["code"] == expected, query
        assert result["requires_confirmation"] is False, query
    switched = resolve("所以星宇呢", active="2454")
    assert switched["stock"]["code"] == "2646"
    assert switched["stock"]["resolution_source"] == "reviewed_alias"


def test_conflict_typo_and_true_ambiguity_fail_closed() -> None:
    conflict = resolve("2317 台積電")
    assert conflict["ok"] is False and conflict["status"] == "conflict"
    assert {row["code"] for row in conflict["candidates"]} == {"2317", "2330"}
    typo = resolve("星雨")
    assert typo["ok"] is False and typo["status"] == "typo_suggestion"
    assert typo["requires_confirmation"] is True
    assert {row["code"] for row in typo["suggestions"]} == {"2646"}
    ambiguous = resolve("長榮或長榮航")
    assert ambiguous["ok"] is False and ambiguous["status"] == "ambiguous"


def test_comparison_retains_both_entities_and_pronoun_inherits_only_active() -> None:
    comparison = resolve("請比較長榮航和長榮")
    assert comparison["ok"] is True and comparison["status"] == "comparison"
    assert [row["code"] for row in comparison["comparison_stocks"]] == ["2618", "2603"]
    inherited = resolve("它明天呢", active="2454")
    assert inherited["ok"] is True
    assert inherited["status"] == "context_inherited"
    assert inherited["stock"]["code"] == "2454"
    no_active = resolve("它明天呢")
    assert no_active["ok"] is False


def test_registry_materializer_persists_official_and_reviewed_aliases() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    result = materialize_stock_entity_registry(conn, active_rows=ROWS)
    assert result["active_stock_count"] == len(ROWS)
    assert result["rows_written"] == result["alias_count"]
    candidates = active_alias_candidates(
        conn,
        "星宇",
        effective_date="2026-09-01",
        registry_version="StockEntityRegistryV1",
    )
    assert [row["stock_code"] for row in candidates] == ["2646"]
    assert candidates[0]["reviewed"] == 1
    assert materialize_stock_entity_registry(conn, active_rows=ROWS)["alias_count"] == result["alias_count"]


def test_release_entity_gate_has_zero_wrong_entity_switch_or_unnecessary_confirmation() -> None:
    evidence = evaluate_entity_resolution_gate(lambda query, active: resolve(query, active))
    assert evidence["passed"] is True
    assert evidence["unique_alias_unnecessary_confirmation_rate"] == 0
    assert evidence["wrong_entity_resolution_count"] == 0
    assert evidence["active_stock_switch_error_count"] == 0
    assert evidence["clarification_rate"] == 1
