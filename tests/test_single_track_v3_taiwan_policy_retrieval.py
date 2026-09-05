from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.official_external_events import (  # noqa: E402
    TAIWAN_POLICY_RSS_SOURCE_IDS,
    fetch_official_policy_feeds,
)
from adapter.single_track_v3_taiwan_policy_retrieval import (  # noqa: E402
    TAIWAN_POLICY_RETRIEVAL_ADAPTER_VERSION,
    build_taiwan_policy_retrieval_fetcher,
)
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.single_track_v3_reconciliation_repository import (  # noqa: E402
    latest_event_revision_for_cluster,
)
from repository.single_track_v3_research_repository import (  # noqa: E402
    research_news_item,
)
from repository.single_track_v3_retrieval_repository import (  # noqa: E402
    retrieval_worker_receipt,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)
from task.single_track_v3_event_reconciler import reconcile_research_events  # noqa: E402
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


def _item(
    source_id: str,
    title: str,
    *,
    source_url: str | None = None,
) -> dict:
    published = "2026-09-01T17:45:00+08:00"
    return {
        "event_key": hashlib.sha256(
            f"{source_id}|{published}|{title}".encode("utf-8")
        ).hexdigest(),
        "source_id": source_id,
        "publisher": "經濟部",
        "source_url": source_url or f"https://www.moea.gov.tw/news/{source_id.lower()}",
        "published_at": published,
        "title": title,
        "summary_excerpt": "RSS 摘要正文不得進入 Single-Track research lane",
    }


def _fetched(*items: dict, failed_source: str | None = None) -> dict:
    sources = []
    for source_id in TAIWAN_POLICY_RSS_SOURCE_IDS:
        failed = source_id == failed_source
        rows = sum(1 for item in items if item["source_id"] == source_id)
        sources.append(
            {
                "source": source_id,
                "ok": not failed,
                "status": "timeout" if failed else "ok" if rows else "no_results",
                "timeout_class": "source_timeout" if failed else None,
                "rows": 0 if failed else rows,
            }
        )
    return {
        "ok": failed_source is None,
        "status": "ok" if failed_source is None else "partial",
        "items": list(items),
        "sources": sources,
    }


def _run() -> dict:
    return {
        "run_id": "taiwan-policy-run",
        "idempotency_key": "idempotency:taiwan-policy-run",
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


def test_policy_wrapper_filters_by_audited_terms_and_drops_rss_summary() -> None:
    relevant = _item("MOEA_NEWS", "半導體產業新政策正式公告")
    unrelated = _item("EXECUTIVE_YUAN_NEWS", "觀光活動新聞")
    fetcher = build_taiwan_policy_retrieval_fetcher(
        ["2454"],
        ["聯發科", "半導體"],
        fetcher=lambda: _fetched(relevant, unrelated),
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )

    result = fetcher("")

    assert result["adapter_version"] == TAIWAN_POLICY_RETRIEVAL_ADAPTER_VERSION
    assert result["status"] == "ok"
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["title"] == "半導體產業新政策正式公告"
    assert event["entity_refs"] == ["2454"]
    assert event["publisher_published_at"] == "2026-09-01T17:45:00+08:00"
    assert "summary_excerpt" not in event
    assert "RSS 摘要正文" not in str(event)
    assert result["raw_article_bodies_fetched"] == 0
    assert result["canonical_table_writes"] == 0


def test_policy_wrapper_preserves_success_when_another_official_feed_times_out() -> None:
    relevant = _item("MOEA_NEWS", "半導體產業新政策正式公告")
    fetcher = build_taiwan_policy_retrieval_fetcher(
        ["2454"],
        ["半導體"],
        fetcher=lambda: _fetched(relevant, failed_source="EXECUTIVE_YUAN_NEWS"),
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )

    result = fetcher("")

    assert result["status"] == "partial"
    assert len(result["events"]) == 1
    assert result["source_attempts"][0]["status"] == "timeout"
    assert result["source_attempts"][0]["timeout_class"] == "source_timeout"


def test_policy_base_adapter_allows_only_declared_sources_and_types_timeout() -> None:
    requested_urls = []

    def http_get(url: str, **_kwargs):
        requested_urls.append(url)
        raise requests.Timeout("controlled timeout")

    result = fetch_official_policy_feeds(
        source_ids=["MOEA_NEWS"],
        http_get=http_get,
    )

    assert len(requested_urls) == 1
    assert result["sources"][0]["source"] == "MOEA_NEWS"
    assert result["sources"][0]["status"] == "timeout"
    assert result["sources"][0]["timeout_class"] == "source_timeout"
    with pytest.raises(ValueError, match="unknown official policy"):
        fetch_official_policy_feeds(source_ids=["UNKNOWN_POLICY_SOURCE"])


def test_policy_worker_and_reconciler_keep_canonical_promotion_separate() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    create_news_retrieval_run(conn, _run(), ensure_schema=False)
    policy_fetcher = build_taiwan_policy_retrieval_fetcher(
        ["2454"],
        ["半導體"],
        fetcher=lambda: _fetched(
            _item("MOEA_NEWS", "半導體產業新政策正式公告")
        ),
        clock=lambda: datetime(2026, 9, 1, 18, 0, 2, tzinfo=TPE),
    )

    worker = run_retrieval_worker(
        conn,
        run_id="taiwan-policy-run",
        queries=["2454 policy"],
        entity_refs=["2454"],
        source_specs=[
            RetrievalSourceSpec(
                source_id="taiwan_government_policy_official",
                scope_key="taiwan_policy",
                fetcher=policy_fetcher,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            )
        ],
        query_plan_version="SingleTrackV3EventQueryPlanV1",
        source_plan_version="SingleTrackV3EventSourcePlanV1",
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )
    assert worker["status"] == "success"
    receipt = retrieval_worker_receipt(conn, "taiwan-policy-run")
    assert receipt is not None
    item = research_news_item(conn, receipt["news_item_ids"][0])
    assert item is not None
    assert item["source_id"] == "MOEA_NEWS"
    assert item["source_class"] == "canonical_official"
    assert item["verification_state"] == "primary_verified"
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0

    reconciled = reconcile_research_events(
        conn,
        analysis_cutoff="2026-09-01T18:05:00+08:00",
        sealed_at="2026-09-01T18:06:00+08:00",
    )
    revision_id = reconciled["promoted_event_revision_ids"][0]
    revision = latest_event_revision_for_cluster(
        conn,
        conn.execute(
            "SELECT event_cluster_id FROM event_revision WHERE event_revision_id=?",
            (revision_id,),
        ).fetchone()[0],
    )
    assert revision is not None
    assert revision["event_type"] == "government_policy"
    assert revision["materiality"] == "unknown"
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0
    conn.close()
