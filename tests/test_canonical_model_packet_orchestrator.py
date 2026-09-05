from __future__ import annotations

import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.canonical_model_packet_orchestrator import (  # noqa: E402
    build_canonical_question_model_packet,
)


class _Connection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _artifact(**_kwargs) -> dict:
    return {
        "analysis_id": "analysis-2330",
        "snapshot_id": "snapshot-2330",
        "analysis_cutoff": "2026-09-01T13:30:00+08:00",
        "canonical_answer_text": "台積電目前維持觀察。",
        "canonical_answer_text_hash": "a" * 64,
        "validity": "valid",
        "event_ids": ["event-1"],
        "evidence_ids": ["fact-1"],
        "analysis": {
            "analysis_id": "analysis-2330",
            "snapshot_id": "snapshot-2330",
            "analysis_cutoff": "2026-09-01T13:30:00+08:00",
            "trade_date": "2026-09-01",
            "profile": "focused",
            "target_entities": [{"code": "2330", "name": "台積電"}],
            "entity_analyses": [
                {
                    "entity": {"code": "2330", "name": "台積電"},
                    "trade_date": "2026-09-01",
                    "ohlcv": {"close": 1200.0},
                    "technical_ensemble": {"status": "ok", "overall_score": 0.2},
                }
            ],
            "referee": {"decision_ready": True, "main_status": "觀察"},
            "event_scan": {"coverage": {"official": "ok"}},
        },
    }


def test_model_packet_envelope_binds_artifact_and_cutoff_eligible_events() -> None:
    connection = _Connection()
    envelope = build_canonical_question_model_packet(
        query="台積電技術面與事件",
        conversation_context={},
        requested_scopes=["technical", "events"],
        profile="focused",
        artifact_builder=_artifact,
        connection_factory=lambda: connection,
        event_reader=lambda _connection, _cutoff: [
            {
                "event_id": "event-1",
                "untrusted_text": "官方重大訊息",
                "publisher": "MOPS",
                "source_class": "canonical_official",
                "rights": "official_public_metadata",
                "verification_state": "primary_verified",
            },
            {"event_id": "event-after-cutoff", "untrusted_text": "不可進入"},
        ],
    )

    assert connection.closed is True
    assert envelope["packet_ready"] is True
    assert envelope["analysis_id"] == "analysis-2330"
    assert envelope["canonical_answer_text"] == "台積電目前維持觀察。"
    assert envelope["packet"]["artifact_identity"]["analysis_id"] == "analysis-2330"
    assert envelope["packet"]["referee"]["immutable"] is True
    assert len(envelope["packet"]["events"]) == 1
    assert envelope["raw_event_text_outside_packet_returned"] is False
    assert set(envelope["stage_timings_ms"]) == {"event_retrieval", "packet_build"}
    assert all(value >= 0 for value in envelope["stage_timings_ms"].values())


def test_model_packet_envelope_propagates_clarification_without_packet() -> None:
    envelope = build_canonical_question_model_packet(
        query="星雨",
        conversation_context={},
        requested_scopes=["technical"],
        profile="focused",
        artifact_builder=lambda **_kwargs: {
            "requires_clarification": True,
            "resolution": {"reason": "typo"},
        },
        connection_factory=lambda: (_ for _ in ()).throw(AssertionError("must not read DB")),
    )

    assert envelope["packet_ready"] is False
    assert envelope["requires_clarification"] is True
    assert "packet" not in envelope
