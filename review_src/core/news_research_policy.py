from __future__ import annotations

"""Versioned policy for noncanonical news-research metadata."""

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any


NEWS_RESEARCH_POLICY_VERSION = "news-research-policy-v1"
OFFICIAL_RESEARCH_POLICY_VERSION = "official-research-policy-v1"


@dataclass(frozen=True)
class NewsSourceRights:
    source_id: str
    allow_fetch: bool
    allow_model: bool
    allow_display: bool
    allow_store_excerpt: bool
    metadata_retention_days: int
    raw_body_retention_seconds: int
    attribution_required: bool
    last_terms_review: str
    policy_note: str
    policy_version: str = NEWS_RESEARCH_POLICY_VERSION


_SOURCE_RIGHTS = MappingProxyType(
    {
        "GDELT_DOC_INDEX": NewsSourceRights(
            source_id="GDELT_DOC_INDEX",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=30,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-08-29",
            policy_note=(
                "Engineering default for title/domain/link metadata only; "
                "not legal clearance for publisher article content."
            ),
        ),
        "GOOGLE_NEWS_RSS_INDEX": NewsSourceRights(
            source_id="GOOGLE_NEWS_RSS_INDEX",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=30,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-08-30",
            policy_note=(
                "Experimental shadow-only engineering fallback for RSS title/source/link "
                "metadata when GDELT is unavailable. The search RSS endpoint is not a "
                "documented public API; re-review source terms and operational stability "
                "before canary or user-visible citations."
            ),
        ),
        "TWSE_MOPS_DAILY_EVENT": NewsSourceRights(
            source_id="TWSE_MOPS_DAILY_EVENT",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=730,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note=(
                "Official MOPS listed-company disclosure metadata; this policy stores "
                "only bounded subject/timestamps/source identity in the hot research lane."
            ),
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "TPEX_MOPS_DAILY_EVENT": NewsSourceRights(
            source_id="TPEX_MOPS_DAILY_EVENT",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=730,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note=(
                "Official MOPS OTC disclosure metadata; this policy stores only "
                "bounded subject/timestamps/source identity in the hot research lane."
            ),
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "EXECUTIVE_YUAN_NEWS": NewsSourceRights(
            source_id="EXECUTIVE_YUAN_NEWS",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=365,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note="Official Executive Yuan RSS title/timestamp/link metadata only.",
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "EXECUTIVE_YUAN_MINISTRY_NEWS": NewsSourceRights(
            source_id="EXECUTIVE_YUAN_MINISTRY_NEWS",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=365,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note="Official Executive Yuan ministry RSS title/timestamp/link metadata only.",
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "MOEA_NEWS": NewsSourceRights(
            source_id="MOEA_NEWS",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=365,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note="Official Ministry of Economic Affairs RSS title/timestamp/link metadata only.",
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "FEDERAL_RESERVE_MONETARY_POLICY_RSS": NewsSourceRights(
            source_id="FEDERAL_RESERVE_MONETARY_POLICY_RSS",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=365,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note=(
                "Official Federal Reserve monetary-policy RSS title/timestamp/link "
                "metadata only; endpoint confirmed from the Board RSS directory."
            ),
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "US_TREASURY_PRESS_RELEASE_INDEX": NewsSourceRights(
            source_id="US_TREASURY_PRESS_RELEASE_INDEX",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=365,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note=(
                "Official U.S. Treasury press-release index title/date/link metadata only; "
                "publisher time remains unverified because the index exposes a date only."
            ),
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "OFAC_RECENT_ACTIONS_INDEX": NewsSourceRights(
            source_id="OFAC_RECENT_ACTIONS_INDEX",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=365,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note=(
                "Official OFAC Recent Actions index title/date/link metadata only; "
                "the retired OFAC RSS feed is not used."
            ),
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
        "BIS_PRESS_RELEASE_INDEX": NewsSourceRights(
            source_id="BIS_PRESS_RELEASE_INDEX",
            allow_fetch=True,
            allow_model=True,
            allow_display=True,
            allow_store_excerpt=False,
            metadata_retention_days=365,
            raw_body_retention_seconds=0,
            attribution_required=True,
            last_terms_review="2026-09-02",
            policy_note=(
                "Official U.S. Bureau of Industry and Security press-release index "
                "title/date/link metadata only; article and index excerpts are excluded."
            ),
            policy_version=OFFICIAL_RESEARCH_POLICY_VERSION,
        ),
    }
)


def news_source_rights(source_id: str) -> NewsSourceRights | None:
    return _SOURCE_RIGHTS.get(str(source_id or "").strip())


def public_news_source_rights(source_id: str) -> dict[str, Any] | None:
    policy = news_source_rights(source_id)
    if policy is None:
        return None
    return {
        **asdict(policy),
        "policy_version": policy.policy_version,
    }
