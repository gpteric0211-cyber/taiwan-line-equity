from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.single_track_v3_official_retrieval import (  # noqa: E402
    build_official_company_retrieval_fetcher,
)
from core.news_research_policy import public_news_source_rights  # noqa: E402
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.single_track_v3_research_repository import (  # noqa: E402
    research_news_item,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)
from task.single_track_v3_event_reconciler import (  # noqa: E402
    EVENT_RECONCILIATION_CONTRACT_VERSION,
    reconcile_research_events,
)
from task.single_track_v3_retrieval_worker import (  # noqa: E402
    RetrievalSourceSpec,
    run_retrieval_worker,
)


TPE = ZoneInfo("Asia/Taipei")
TITLE = "董事會決議測試重大訊息"


class IncrementingClock:
    def __init__(self, value: str) -> None:
        self.current = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def _run(run_id: str, code: str = "2454") -> dict:
    return {
        "run_id": run_id,
        "idempotency_key": f"idempotency:{run_id}:{code}",
        "slot_key": "evening_1800",
        "target_trade_date": "2026-09-02",
        "scheduled_for": "2026-09-01T18:00:00+08:00",
        "cutoff_at": "2026-09-01T18:15:00+08:00",
        "started_at": None,
        "completed_at": None,
        "status": "queued",
        "source_policy_version": "SourceAuthorityPolicyV1",
        "calendar_revision": "official-calendar-r1",
        "source_coverage": {},
        "source_failures": [],
        "late_reason": None,
        "created_at": "2026-09-01T17:59:00+08:00",
        "updated_at": "2026-09-01T17:59:00+08:00",
    }


def _official_row(code: str = "2454") -> dict:
    identity = f"listed|{code}|2026-09-01|173005|{TITLE}"
    return {
        "event_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "disclosed_date": "2026-09-01",
        "disclosed_time": "173005",
        "code": code,
        "company_name": "聯發科技股份有限公司",
        "subject": TITLE,
        "explanation": "不得保留的官方說明正文",
        "article_code": "第11款",
        "market": "listed",
        "source": "TWSE_MOPS_DAILY_EVENT",
    }


def _official_result(code: str = "2454") -> dict:
    return {
        "ok": True,
        "status": "ok",
        "items": [_official_row(code)],
        "sources": [
            {"market": "listed", "ok": True, "status": "ok", "timeout_class": None},
            {"market": "otc", "ok": True, "status": "no_results", "timeout_class": None},
        ],
    }


def _radar_event(code: str = "2454") -> dict:
    return {
        "event_key": hashlib.sha256(f"radar:{code}:{TITLE}".encode("utf-8")).hexdigest(),
        "event_date": "2026-09-01",
        "title": TITLE,
        "publisher": "example.com",
        "url": f"https://example.com/news/{code}",
        "publisher_published_at": None,
        "publisher_time_verified": False,
        "index_seen_at": "2026-09-01T17:59:00+08:00",
        "retrieved_at": "2026-09-01T18:00:02+08:00",
        "verification_state": "unverified",
        "untrusted_text": True,
        "rights": public_news_source_rights("GDELT_DOC_INDEX"),
        "citation_required": True,
        "authority_tier": "news_radar",
        "entity_refs": [code],
        "quality": "unverified",
        "can_override_main_status": False,
    }


def _radar_result(code: str = "2454") -> dict:
    return {
        "ok": True,
        "status": "ok",
        "events": [_radar_event(code)],
        "adapter_version": "controlled-radar-fixture-v1",
        "source_policy_version": "news-research-policy-v1",
        "source_id": "GDELT_DOC_INDEX",
        "source_attempts": [
            {
                "source_id": "GDELT_DOC_INDEX",
                "status": "ok",
                "timeout_class": None,
                "item_count": 1,
            }
        ],
        "raw_article_bodies_fetched": 0,
        "raw_body_retention_seconds": 0,
        "canonical_table_writes": 0,
    }


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    return conn


def _execute_retrieval(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    code: str = "2454",
    include_official: bool = True,
) -> None:
    create_news_retrieval_run(conn, _run(run_id, code), ensure_schema=False)
    specs = []
    if include_official:
        specs.append(
            RetrievalSourceSpec(
                source_id="official_company_mops_daily",
                scope_key="official_company",
                fetcher=build_official_company_retrieval_fetcher(
                    [code],
                    fetcher=lambda: _official_result(code),
                    clock=lambda: datetime(2026, 9, 1, 18, 0, 2, tzinfo=TPE),
                ),
                query_mode="once_per_run",
                authority_tier="canonical_official",
            )
        )
    specs.append(
        RetrievalSourceSpec(
            source_id="controlled_news_metadata",
            scope_key="company_industry_news",
            fetcher=lambda _query: _radar_result(code),
            query_mode="per_query",
            authority_tier="news_radar",
        )
    )
    run_retrieval_worker(
        conn,
        run_id=run_id,
        queries=[f"{code} event"],
        entity_refs=[code],
        source_specs=specs,
        query_plan_version="SingleTrackV3EventQueryPlanV1",
        source_plan_version="SingleTrackV3EventSourcePlanV1",
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )


def test_reconciler_requires_primary_evidence_and_links_radar_without_promoting_it() -> None:
    conn = _connection()
    _execute_retrieval(conn, run_id="run-official-radar")

    result = reconcile_research_events(
        conn,
        analysis_cutoff="2026-09-01T18:05:00+08:00",
        sealed_at="2026-09-01T18:06:00+08:00",
        source_coverage_complete=True,
        entity_resolution_verified=True,
    )

    assert result["contract_version"] == EVENT_RECONCILIATION_CONTRACT_VERSION
    assert len(result["linked_news_item_ids"]) == 2
    assert len(result["promoted_event_revision_ids"]) == 1
    assert result["unresolved"] == []
    assert result["canonical_event_evidence_writes"] == 0
    assert result["zero_model_calls"] == 0
    revision = conn.execute("SELECT * FROM event_revision").fetchone()
    assert revision["verification_state"] == "verified"
    assert revision["materiality"] == "unknown"
    assert revision["content_materiality"] == "medium"
    assert revision["materiality_contract_version"] == (
        "MaterialityClassificationContractV1"
    )
    assert revision["short_excerpt"] == ""
    assert json.loads(revision["key_points_json"]) == [TITLE]
    assert "不得保留的官方說明正文" not in str(dict(revision))
    assert conn.execute("SELECT COUNT(*) FROM event_cluster").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0
    assert result["revision_classifications"][0]["revision_class"] == "new_event"
    assert result["revision_classifications"][0]["formal_direction_weight"] == 0.0
    for news_item_id in result["linked_news_item_ids"]:
        item = research_news_item(conn, news_item_id)
        assert item is not None
        assert item["event_revision_id"] == revision["event_revision_id"]

    replay = reconcile_research_events(
        conn,
        analysis_cutoff="2026-09-01T18:05:00+08:00",
        sealed_at="2026-09-01T18:06:00+08:00",
        source_coverage_complete=True,
        entity_resolution_verified=True,
    )
    assert replay["pending_item_count"] == 0
    assert replay["promoted_event_revision_ids"] == []
    assert conn.execute("SELECT COUNT(*) FROM event_revision").fetchone()[0] == 1
    conn.close()


def test_radar_only_remains_unverified_and_unlinked() -> None:
    conn = _connection()
    _execute_retrieval(
        conn,
        run_id="run-radar-only",
        include_official=False,
    )

    result = reconcile_research_events(
        conn,
        analysis_cutoff="2026-09-01T18:05:00+08:00",
        sealed_at="2026-09-01T18:06:00+08:00",
    )

    assert result["promoted_event_revision_ids"] == []
    assert result["linked_news_item_ids"] == []
    assert result["unresolved"][0]["reason_code"] == "unverified_radar"
    assert conn.execute("SELECT COUNT(*) FROM event_cluster").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM event_revision").fetchone()[0] == 0
    conn.close()


def test_same_generic_title_for_different_entities_has_distinct_dedup_clusters() -> None:
    conn = _connection()
    _execute_retrieval(
        conn,
        run_id="run-2454-radar",
        code="2454",
        include_official=False,
    )
    _execute_retrieval(
        conn,
        run_id="run-2330-radar",
        code="2330",
        include_official=False,
    )

    clusters = {
        row[0] for row in conn.execute("SELECT dedup_cluster FROM research_news_item")
    }
    assert len(clusters) == 2
    conn.close()
