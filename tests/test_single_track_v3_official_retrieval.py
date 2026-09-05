from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.official_company_events import (  # noqa: E402
    LISTED_EVENTS_URL,
    fetch_official_company_events,
)
from adapter.single_track_v3_official_retrieval import (  # noqa: E402
    OFFICIAL_COMPANY_RETRIEVAL_ADAPTER_VERSION,
    build_official_company_retrieval_fetcher,
)
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.single_track_v3_research_repository import (  # noqa: E402
    research_news_item,
)
from repository.single_track_v3_retrieval_repository import (  # noqa: E402
    retrieval_worker_receipt,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)
from task.single_track_v3_retrieval_worker import (  # noqa: E402
    RetrievalSourceSpec,
    run_retrieval_worker,
)


TPE = ZoneInfo("Asia/Taipei")


class IncrementingClock:
    def __init__(self, value: str) -> None:
        self.current = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def _row(
    code: str,
    *,
    market: str = "listed",
    disclosed_time: str = "173005",
    subject: str = "董事會決議測試重大訊息",
) -> dict:
    identity = f"{market}|{code}|2026-09-01|{disclosed_time}|{subject}"
    return {
        "event_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "disclosed_date": "2026-09-01",
        "disclosed_time": disclosed_time,
        "code": code,
        "company_name": "聯發科技股份有限公司" if code == "2454" else "其他公司",
        "subject": subject,
        "explanation": "這段官方說明正文不得進入研究 metadata lane",
        "article_code": "第11款",
        "market": market,
        "source": "TWSE_MOPS_DAILY_EVENT" if market == "listed" else "TPEX_MOPS_DAILY_EVENT",
    }


def _fetched(*rows: dict, otc_status: str = "no_results") -> dict:
    return {
        "ok": otc_status in {"ok", "no_results"},
        "status": "ok" if otc_status in {"ok", "no_results"} else "partial",
        "items": list(rows),
        "sources": [
            {
                "market": "listed",
                "ok": True,
                "status": "ok" if any(row["market"] == "listed" for row in rows) else "no_results",
                "timeout_class": None,
            },
            {
                "market": "otc",
                "ok": otc_status in {"ok", "no_results"},
                "status": otc_status,
                "timeout_class": "source_offline" if otc_status == "offline" else None,
            },
        ],
    }


def _run() -> dict:
    return {
        "run_id": "official-retrieval-run",
        "idempotency_key": "idempotency:official-retrieval-run",
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


def test_official_wrapper_filters_entities_and_never_copies_explanation() -> None:
    fetcher = build_official_company_retrieval_fetcher(
        ["2454"],
        fetcher=lambda: _fetched(_row("2454"), _row("2330")),
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )

    result = fetcher("")

    assert result["adapter_version"] == OFFICIAL_COMPANY_RETRIEVAL_ADAPTER_VERSION
    assert result["status"] == "ok"
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["entity_refs"] == ["2454"]
    assert event["title"] == "董事會決議測試重大訊息"
    assert "explanation" not in event
    assert "說明正文" not in str(event)
    assert event["publisher_published_at"] == "2026-09-01T17:30:05+08:00"
    assert event["publisher_time_verified"] is True
    assert event["authority_tier"] == "canonical_official"
    assert result["raw_article_bodies_fetched"] == 0
    assert result["canonical_table_writes"] == 0


def test_official_wrapper_preserves_partial_source_failure_and_successful_rows() -> None:
    fetcher = build_official_company_retrieval_fetcher(
        ["2454"],
        fetcher=lambda: _fetched(_row("2454"), otc_status="offline"),
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )

    result = fetcher("")

    assert result["ok"] is False
    assert result["status"] == "partial"
    assert len(result["events"]) == 1
    assert result["source_attempts"] == [
        {
            "source_id": "mops_listed_disclosures",
            "status": "ok",
            "timeout_class": None,
            "item_count": 1,
        },
        {
            "source_id": "mops_otc_disclosures",
            "status": "offline",
            "timeout_class": "source_offline",
            "item_count": 0,
        },
    ]


def test_official_transport_classifies_timeout_without_hiding_other_market() -> None:
    class EmptyResponse:
        text = "[]"
        encoding = "utf-8"

        def raise_for_status(self) -> None:
            return None

    def http_get(url: str, **_kwargs):
        if url == LISTED_EVENTS_URL:
            raise requests.Timeout("controlled timeout")
        return EmptyResponse()

    result = fetch_official_company_events(http_get=http_get)

    assert result["ok"] is False
    assert result["status"] == "partial"
    assert result["sources"][0]["status"] == "timeout"
    assert result["sources"][0]["timeout_class"] == "source_timeout"
    assert result["sources"][1]["status"] == "no_results"


def test_worker_persists_official_metadata_as_noncanonical_research_only() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    create_news_retrieval_run(conn, _run(), ensure_schema=False)
    conn.commit()
    fetcher = build_official_company_retrieval_fetcher(
        ["2454"],
        fetcher=lambda: _fetched(_row("2454"), otc_status="offline"),
        clock=lambda: datetime(2026, 9, 1, 18, 0, 2, tzinfo=TPE),
    )

    result = run_retrieval_worker(
        conn,
        run_id="official-retrieval-run",
        queries=["2454 official disclosure"],
        entity_refs=["2454"],
        source_specs=[
            RetrievalSourceSpec(
                source_id="official_company_mops_daily",
                scope_key="official_company",
                fetcher=fetcher,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            )
        ],
        query_plan_version="SingleTrackV3EventQueryPlanV1",
        source_plan_version="SingleTrackV3EventSourcePlanV1",
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )

    assert result["status"] == "partial"
    receipt = retrieval_worker_receipt(conn, "official-retrieval-run")
    assert receipt is not None
    assert len(receipt["news_item_ids"]) == 1
    item = research_news_item(conn, receipt["news_item_ids"][0])
    assert item is not None
    assert item["source_id"] == "TWSE_MOPS_DAILY_EVENT"
    assert item["source_class"] == "canonical_official"
    assert item["verification_state"] == "primary_verified"
    assert item["entity_refs"] == ["2454"]
    assert item["publisher_published_at"] == "2026-09-01T17:30:05+08:00"
    assert item["raw_body_retained"] is False
    assert item["event_cluster_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM canonical_analysis_artifact").fetchone()[0] == 0
    conn.close()


def test_official_wrapper_rejects_unofficial_entity_identity() -> None:
    try:
        build_official_company_retrieval_fetcher(["MediaTek"])
    except ValueError as exc:
        assert "four-digit entity refs" in str(exc)
    else:
        raise AssertionError("unofficial entity identity must be rejected")
