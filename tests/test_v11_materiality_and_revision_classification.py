from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.event_revision_classification_v1 import (  # noqa: E402
    classify_event_revision,
)
from analysis.materiality_classification_v1 import (  # noqa: E402
    classify_content_materiality,
    classify_target_regime,
)


def _verified(**overrides):
    return {
        "verification_state": "verified",
        "source_coverage_complete": True,
        "entity_resolution_state": "resolved",
        "revision_complete": True,
        "target_relationship_type": "direct_company",
        "event_category": "company_disclosure",
        "fact_predicates": [],
        **overrides,
    }


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            _verified(fact_predicates=["bankruptcy_or_dissolution"]),
            "critical",
        ),
        (_verified(event_category="earnings"), "high"),
        (_verified(), "medium"),
        (_verified(event_category="routine_disclosure"), "low"),
        (
            _verified(source_coverage_complete=False, event_category="earnings"),
            "unknown_pending",
        ),
    ],
)
def test_materiality_five_levels_are_structured_and_always_zero_weight(
    evidence, expected
) -> None:
    result = classify_content_materiality(evidence)
    assert result["content_materiality"] == expected
    assert result["formal_direction_weight"] == 0.0
    assert result["eligible_for_weight"] is False


def test_material_event_regime_remains_unreleased_even_when_candidate_is_complete() -> None:
    result = classify_target_regime(
        {
            "verification_state": "verified",
            "content_materiality": "high",
            "target_relationship_type": "direct_company",
            "target_direction": "negative",
            "target_impact_magnitude": "high",
            "target_impact_eligible": True,
        }
    )
    assert result["regime_selection"] == "material_event"
    assert result["candidate_contribution"] == 0.0
    assert result["eligible_for_weight"] is False
    assert result["released"] is False


def test_high_content_with_pending_direction_cannot_switch_regime() -> None:
    result = classify_target_regime(
        {
            "verification_state": "verified",
            "content_materiality": "high",
            "target_relationship_type": "direct_company",
            "target_direction": "unknown",
            "target_impact_magnitude": "unknown_pending",
            "target_impact_eligible": False,
        }
    )
    assert result["regime_selection"] == "material_pending"
    assert result["eligible_for_weight"] is False


def _revision(**overrides):
    return {
        "key_points": ["fact-a"],
        "source_refs": [
            {
                "source_id": "MOPS",
                "publisher": "MOPS",
                "content_hash": "hash-a",
            }
        ],
        "core_fact_digest": "core-a",
        "headline": "headline-a",
        "price_reaction": {},
        "target_direction": "unknown",
        "verification_state": "verified",
        **overrides,
    }


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (None, _revision(), "new_event"),
        (_revision(), _revision(), "exact_duplicate"),
        (_revision(), _revision(headline="headline-b"), "headline_rewrite"),
        (
            _revision(),
            _revision(
                source_refs=[
                    {
                        "source_id": "MOPS",
                        "publisher": "MOPS",
                        "content_hash": "hash-a",
                    },
                    {
                        "source_id": "LICENSED",
                        "publisher": "wire",
                        "content_hash": "hash-a",
                    },
                ]
            ),
            "syndicated_copy",
        ),
        (
            _revision(),
            _revision(
                source_refs=[
                    {
                        "source_id": "MOPS",
                        "publisher": "MOPS",
                        "content_hash": "hash-a",
                    },
                    {
                        "source_id": "TWSE",
                        "publisher": "TWSE",
                        "content_hash": "hash-b",
                    },
                ]
            ),
            "new_source_confirmation",
        ),
        (
            _revision(),
            _revision(key_points=["fact-b"], core_fact_digest="core-b"),
            "substantive_revision",
        ),
        (
            _revision(),
            _revision(price_reaction={"change_pct": 3.0}),
            "market_reaction_update",
        ),
        (
            _revision(target_direction="positive"),
            _revision(target_direction="negative"),
            "direction_reversal",
        ),
        (
            _revision(),
            _revision(priced_in_state="already_priced"),
            "already_priced",
        ),
        (
            _revision(),
            _revision(priced_in_state="still_developing"),
            "still_developing",
        ),
    ],
)
def test_revision_categories_are_explicit_and_zero_weight(
    previous, current, expected
) -> None:
    result = classify_event_revision(previous, current)
    assert result["revision_class"] == expected
    assert result["formal_direction_weight"] == 0.0
