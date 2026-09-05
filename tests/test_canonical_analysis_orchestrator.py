from __future__ import annotations

import inspect
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.single_track_v3_repository import canonical_analysis_artifact  # noqa: E402
from api.bot_market_data import api_bot_daily_market_data  # noqa: E402
import app  # noqa: E402
from services.canonical_analysis_orchestrator import run_canonical_analysis  # noqa: E402
from services.canonical_model_packet_service import (  # noqa: E402
    build_canonical_model_fact_packet_v2,
)


CUTOFF = "2026-09-01T14:00:00+08:00"
RECEIVED = "2026-09-01T14:00:01+08:00"


def _connection_factory(path: Path):
    return lambda: sqlite3.connect(path)


def _snapshot(*, event: bool = True) -> dict:
    official_events = []
    if event:
        official_events.append(
            {
                "available_at": "2026-09-01T13:45:00+08:00",
                "retrieved_at": "2026-09-01T13:45:00+08:00",
                "effective_tw_trade_date": "2026-09-02",
                "event_type": "material_information",
                "source_id": "MOPS",
                "source_quality": "official",
                "subject": "可轉換公司債定價",
                "attention_level": "attention",
                "direction": "mixed",
                "confidence": "high",
            }
        )
    return {
        "ok": True,
        "status": "ready",
        "code": "2454",
        "trade_date": "2026-09-01",
        "stock": {"code": "2454", "name": "聯發科"},
        "ohlcv": {
            "date": "2026-09-01",
            "close": 1520.0,
            "source": "TWSE",
            "source_quality": "official",
            "official_trusted": True,
        },
        "analysis_status": {
            "status": "ready",
            "complete": True,
            "decision_ready": True,
            "main_status": "可觀察",
        },
        "referee": {
            "decision_ready": True,
            "main_status": "可觀察",
            "main_reasons": ["價格與量能條件通過"],
            "version": "practical-status-core-v1",
            "support_zone": {"zone_low": 1490.0, "zone_high": 1500.0},
            "resistance_zone": {"zone_low": 1540.0, "zone_high": 1560.0},
            "can_be_overridden_by_model": False,
        },
        "official_event_context": {
            "available": bool(official_events),
            "status": "ok" if official_events else "unavailable",
            "events": official_events,
        },
        "external_event_context": {"available": False, "events": []},
        "news_radar_context": {"available": False, "events": []},
        "global_market_context": {"available": False},
        "taifex_night_context": {"available": False},
    }


def _snapshot_for(code: str) -> dict:
    payload = _snapshot(event=False)
    names = {"2454": "聯發科", "2646": "星宇航空"}
    closes = {"2454": 1520.0, "2646": 24.5}
    payload["code"] = code
    payload["stock"] = {"code": code, "name": names[code]}
    payload["ohlcv"] = {**payload["ohlcv"], "close": closes[code]}
    return payload


def test_web_and_line_reuse_one_immutable_artifact_and_text(tmp_path: Path) -> None:
    database = tmp_path / "market.sqlite3"
    builder = Mock(return_value=_snapshot())
    common = {
        "code": "2454",
        "analysis_cutoff": CUTOFF,
        "request_received_at": RECEIVED,
        "conversation_context_digest": "same-context",
        "profile": "focused",
        "connection_factory": _connection_factory(database),
        "snapshot_builder": builder,
    }

    web = run_canonical_analysis(delivery_channel="web", **common)
    line = run_canonical_analysis(delivery_channel="line", **common)

    assert builder.call_count == 1
    assert web["reused"] is False and line["reused"] is True
    for key in (
        "analysis_id",
        "snapshot_id",
        "analysis_cutoff",
        "validity",
        "canonical_answer_text",
        "canonical_answer_text_hash",
        "event_ids",
        "evidence_ids",
        "omissions",
        "conflicts",
        "analysis",
    ):
        assert web[key] == line[key]
    assert web["analysis"]["main_conclusion"] == "可觀察"
    assert web["analysis"]["referee"]["can_be_overridden_by_model"] is False
    assert web["analysis"]["event_ids"] == web["event_ids"]
    assert web["analysis"]["evidence_ids"] == web["evidence_ids"]
    assert web["canonical_answer_text"].count("不保證報酬") == 1

    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        saved = canonical_analysis_artifact(conn, web["analysis_id"])
        assert saved is not None
        assert saved["canonical_payload"] == web["analysis"]
        assert conn.execute("SELECT COUNT(*) FROM canonical_analysis_artifact").fetchone()[0] == 1


def test_later_verified_material_event_supersedes_prior_artifact(tmp_path: Path) -> None:
    database = tmp_path / "market.sqlite3"
    factory = _connection_factory(database)
    first = run_canonical_analysis(
        code="2454",
        delivery_channel="web",
        analysis_cutoff="2026-09-01T13:30:00+08:00",
        request_received_at="2026-09-01T13:30:01+08:00",
        connection_factory=factory,
        snapshot_builder=Mock(return_value=_snapshot(event=False)),
    )
    second = run_canonical_analysis(
        code="2454",
        delivery_channel="line",
        analysis_cutoff=CUTOFF,
        request_received_at=RECEIVED,
        connection_factory=factory,
        snapshot_builder=Mock(return_value=_snapshot(event=True)),
    )

    assert second["analysis_id"] != first["analysis_id"]
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        prior = canonical_analysis_artifact(conn, first["analysis_id"])
        assert prior is not None
        assert prior["validity"] == "superseded"
        assert prior["superseded_by_event_ids"] == second["event_ids"]


def test_future_cutoff_is_rejected_before_any_database_write(tmp_path: Path) -> None:
    database = tmp_path / "market.sqlite3"
    with pytest.raises(ValueError, match="cannot be later"):
        run_canonical_analysis(
            code="2454",
            delivery_channel="web",
            analysis_cutoff="2026-09-01T14:00:02+08:00",
            request_received_at=RECEIVED,
            connection_factory=_connection_factory(database),
            snapshot_builder=Mock(return_value=_snapshot()),
        )
    assert not database.exists()


def test_existing_get_transports_do_not_invoke_the_write_orchestrator() -> None:
    for endpoint in (
        api_bot_daily_market_data,
        app.api_quotes,
        app.api_stock_detail,
    ):
        source = inspect.getsource(endpoint)
        assert "run_canonical_analysis" not in source
        assert "seal_canonical_analysis_artifact" not in source
        assert "enqueue_missing=True" not in source


def test_comparison_artifact_retains_both_entities_and_two_close_evidence(tmp_path: Path) -> None:
    database = tmp_path / "market.sqlite3"
    builder = Mock(side_effect=lambda code, **_kwargs: _snapshot_for(code))
    resolution = {
        "ok": True,
        "status": "comparison",
        "stock": {"code": "2646", "name": "星宇航空"},
        "entities": [
            {"code": "2646", "name": "星宇航空"},
            {"code": "2454", "name": "聯發科"},
        ],
        "comparison_stocks": [
            {"code": "2646", "name": "星宇航空"},
            {"code": "2454", "name": "聯發科"},
        ],
        "resolution_digest": "comparison-resolution",
    }
    result = run_canonical_analysis(
        code="2646",
        delivery_channel="web",
        analysis_cutoff=CUTOFF,
        request_received_at=RECEIVED,
        conversation_context_digest="comparison-context",
        entity_resolution=resolution,
        conversation_projection={"active_stock": {"code": "2646"}},
        connection_factory=_connection_factory(database),
        snapshot_builder=builder,
    )

    assert [call.args[0] for call in builder.call_args_list] == ["2646", "2454"]
    assert [row["code"] for row in result["analysis"]["comparison_set"]] == ["2646", "2454"]
    assert [row["entity"]["code"] for row in result["analysis"]["entity_analyses"]] == ["2646", "2454"]
    assert len(result["evidence_ids"]) == 2
    assert "星宇航空（2646）" in result["canonical_answer_text"]
    assert "聯發科（2454）" in result["canonical_answer_text"]


def test_comprehensive_artifact_retains_existing_canonical_sections_for_one_packet(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.sqlite3"
    snapshot = _snapshot(event=False)
    snapshot.update(
        {
            "technical": {
                "available": True,
                "status": "ok",
                "decision_ready": True,
                "rsi": {"value": 55.0},
            },
            "valuation": {
                "available": True,
                "status": "ok",
                "pe_ratio": 18.5,
                "trade_date": "2026-09-01",
            },
            "institutional_context": {
                "available": True,
                "status": "ok",
                "flow": {"foreign_net_lots": 1200},
            },
            "global_market_context": {
                "available": True,
                "status": "ok",
                "score": 0.2,
                "market_date": "2026-09-01",
            },
            "support_pressure": {
                "available": True,
                "status": "ok",
                "support_zone": {"low": 1490.0, "high": 1500.0},
            },
        }
    )
    result = run_canonical_analysis(
        code="2454",
        delivery_channel="web",
        analysis_cutoff=CUTOFF,
        request_received_at=RECEIVED,
        profile="comprehensive",
        connection_factory=_connection_factory(database),
        snapshot_builder=Mock(return_value=snapshot),
    )
    entity_analysis = result["analysis"]["entity_analyses"][0]
    assert entity_analysis["canonical_sections"]["valuation"]["pe_ratio"] == 18.5
    assert entity_analysis["canonical_sections"]["institutional_context"]["flow"] == {
        "foreign_net_lots": 1200
    }

    scopes = [
        "fundamentals",
        "institutional",
        "technical",
        "global_market",
        "support_resistance",
        "risk",
    ]
    packet = build_canonical_model_fact_packet_v2(result, requested_scopes=scopes)
    domains = {str(item["domain"]) for item in packet["facts"]}

    assert {
        "valuation",
        "institutional_context",
        "technical",
        "global_market_context",
        "support_resistance",
    } <= domains
    assert all(packet["render_contract"]["scope_evidence_ids"][scope] for scope in scopes)
    assert all(str(item["as_of"]) in {CUTOFF, "2026-09-01"} for item in packet["facts"])
    assert any(
        str(source).startswith("entity_analyses[2454].canonical_sections")
        for item in packet["facts"]
        for source in item.get("source_fields") or []
    )
