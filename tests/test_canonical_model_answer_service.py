from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_model_validation import validate_model_analysis_v2  # noqa: E402
from services.canonical_analysis_orchestrator import run_canonical_analysis  # noqa: E402
from services.canonical_model_answer_service import (  # noqa: E402
    CanonicalModelAnswerFinalizationError,
    finalize_authorized_canonical_model_answer,
)
from services.canonical_model_candidate_service import CANONICAL_CANDIDATE_VERSION  # noqa: E402
from services.canonical_model_packet_service import (  # noqa: E402
    build_canonical_model_fact_packet_v2,
)


CUTOFF = "2026-09-01T14:00:00+08:00"
RECEIVED = "2026-09-01T14:00:01+08:00"


def _connection_factory(path: Path):
    return lambda: sqlite3.connect(path)


def _snapshot() -> dict:
    return {
        "ok": True,
        "status": "ready",
        "code": "2330",
        "trade_date": "2026-09-01",
        "stock": {"code": "2330", "name": "台積電"},
        "ohlcv": {
            "date": "2026-09-01",
            "close": 1200.0,
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
            "main_reasons": ["等待條件確認"],
            "version": "practical-status-core-v1",
            "support_zone": {"zone_low": 1180.0, "zone_high": 1190.0},
            "resistance_zone": {"zone_low": 1220.0, "zone_high": 1230.0},
            "can_be_overridden_by_model": False,
        },
        "official_event_context": {"available": False, "events": []},
        "external_event_context": {"available": False, "events": []},
        "news_radar_context": {"available": False, "events": []},
        "global_market_context": {"available": False},
        "taifex_night_context": {"available": False},
    }


def _candidate_submission(packet: dict) -> dict:
    evidence_id = packet["render_contract"]["scope_evidence_ids"]["price"][0]
    output = json.dumps(
        {
            "contract_version": "model-analysis-v2",
            "explanation_blocks": [
                {
                    "block_type": "fact",
                    "text_template": "官方價格資料可供條件式判讀。",
                    "evidence_ids": [evidence_id],
                    "uncertainty": "low",
                    "conditions": [],
                }
            ],
            "missing_data": packet["render_contract"]["output_rules"]["missing_data_exact"],
            "used_event_ids": [],
            "research_limitations": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert validate_model_analysis_v2(output, packet).passed is True
    packet_json = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "candidate_version": CANONICAL_CANDIDATE_VERSION,
        "compacted_packet": packet,
        "validated_model_output": output,
        "model_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "model_id": "taiwan-stock-qwen:latest",
        "model_digest": "d" * 64,
        "generation_schema_version": "model-analysis-generation-shape-v4",
        "generation_schema_sha256": "e" * 64,
        "generation_prompt_sha256": "f" * 64,
        "packet_digest": packet["packet_digest"],
        "compacted_packet_sha256": hashlib.sha256(packet_json.encode("utf-8")).hexdigest(),
    }


def _decision(_cohort_key: str) -> dict:
    return {
        "authorized": True,
        "selected": True,
        "reason_codes": ["selected_for_canary"],
        "authorization_id": "release-auth-1",
        "release_source_digest": "a" * 64,
    }


def test_finalized_answer_is_immutable_idempotent_and_shared_by_web_and_line(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.sqlite3"
    factory = _connection_factory(database)
    common = {
        "code": "2330",
        "analysis_cutoff": CUTOFF,
        "request_received_at": RECEIVED,
        "conversation_context_digest": "same-context",
        "profile": "focused",
        "connection_factory": factory,
        "snapshot_builder": Mock(return_value=_snapshot()),
    }
    base = run_canonical_analysis(delivery_channel="web", **common)
    packet = build_canonical_model_fact_packet_v2(base, requested_scopes=["price"])
    submission = _candidate_submission(packet)

    finalized = finalize_authorized_canonical_model_answer(
        submission,
        cohort_key="event-1",
        decision_provider=_decision,
        connection_factory=factory,
        created_at="2026-09-01T14:00:02+08:00",
    )
    retried = finalize_authorized_canonical_model_answer(
        submission,
        cohort_key="event-1",
        decision_provider=_decision,
        connection_factory=factory,
        created_at="2026-09-01T14:00:03+08:00",
    )
    web = run_canonical_analysis(delivery_channel="web", **common)
    line = run_canonical_analysis(delivery_channel="line", **common)

    assert finalized["reused"] is False
    assert retried["reused"] is True
    assert finalized["canonical_answer_text_hash"] == retried["canonical_answer_text_hash"]
    assert finalized["raw_model_output_persisted"] is False
    assert web["model_answer_finalized"] is True
    assert web["canonical_answer_text"] == line["canonical_answer_text"]
    assert web["canonical_answer_text_hash"] == line["canonical_answer_text_hash"]
    assert web["canonical_answer_text_hash"] != web["base_canonical_answer_text_hash"]
    assert web["analysis"]["canonical_answer_text_hash"] == web["canonical_answer_text_hash"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_model_answer_extension"
        ).fetchone()[0] == 1
        stored = connection.execute(
            "SELECT canonical_answer_text,explanation_blocks_json FROM canonical_model_answer_extension"
        ).fetchone()
        assert "model-analysis-v2" not in stored[0]
        assert "validated_model_output" not in stored[1]


def test_finalizer_rejects_unauthorized_and_tampered_submissions(tmp_path: Path) -> None:
    database = tmp_path / "market.sqlite3"
    factory = _connection_factory(database)
    base = run_canonical_analysis(
        code="2330",
        delivery_channel="web",
        analysis_cutoff=CUTOFF,
        request_received_at=RECEIVED,
        connection_factory=factory,
        snapshot_builder=Mock(return_value=_snapshot()),
    )
    submission = _candidate_submission(
        build_canonical_model_fact_packet_v2(base, requested_scopes=["price"])
    )
    with pytest.raises(CanonicalModelAnswerFinalizationError) as unauthorized:
        finalize_authorized_canonical_model_answer(
            submission,
            cohort_key="event-1",
            decision_provider=lambda _key: {
                "authorized": False,
                "selected": False,
                "reason_codes": ["kill_switch"],
            },
            connection_factory=factory,
        )
    assert unauthorized.value.reason_code == "kill_switch"

    tampered = dict(submission)
    tampered["validated_model_output"] += " "
    with pytest.raises(CanonicalModelAnswerFinalizationError) as mismatch:
        finalize_authorized_canonical_model_answer(
            tampered,
            cohort_key="event-1",
            decision_provider=_decision,
            connection_factory=factory,
        )
    assert mismatch.value.reason_code == "candidate_model_output_digest_mismatch"
