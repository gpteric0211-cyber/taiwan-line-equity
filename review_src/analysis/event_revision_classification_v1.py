from __future__ import annotations

"""Classify how a same-cluster event observation relates to its prior revision."""

from collections.abc import Mapping
from typing import Any


EVENT_REVISION_CLASSIFICATION_VERSION = "EventRevisionClassificationV1"
EVENT_REVISION_CLASSES = {
    "new_event",
    "exact_duplicate",
    "headline_rewrite",
    "syndicated_copy",
    "new_source_confirmation",
    "substantive_revision",
    "market_reaction_update",
    "already_priced",
    "still_developing",
    "direction_reversal",
}


def _refs(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("source_refs must be a list")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError("source_refs entries must be objects")
    return [dict(item) for item in value]


def _set(refs: list[Mapping[str, Any]], field: str) -> set[str]:
    return {str(item.get(field) or "").strip() for item in refs if item.get(field)}


def classify_event_revision(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one conservative relation; free-form similarity is never guessed."""

    if not isinstance(current, Mapping):
        raise ValueError("current revision must be an object")
    if previous is None:
        relation = "new_event"
        reason = "no_prior_revision"
    elif not isinstance(previous, Mapping):
        raise ValueError("previous revision must be an object or null")
    else:
        prior_refs = _refs(previous.get("source_refs"))
        current_refs = _refs(current.get("source_refs"))
        prior_hashes = _set(prior_refs, "content_hash")
        current_hashes = _set(current_refs, "content_hash")
        prior_publishers = _set(prior_refs, "publisher")
        current_publishers = _set(current_refs, "publisher")
        prior_sources = _set(prior_refs, "source_id")
        current_sources = _set(current_refs, "source_id")
        prior_ref_keys = {
            (
                str(item.get("source_id") or ""),
                str(item.get("publisher") or ""),
                str(item.get("content_hash") or ""),
            )
            for item in prior_refs
        }
        added_refs = [
            item
            for item in current_refs
            if (
                str(item.get("source_id") or ""),
                str(item.get("publisher") or ""),
                str(item.get("content_hash") or ""),
            )
            not in prior_ref_keys
        ]
        added_hashes = _set(added_refs, "content_hash")
        same_core = bool(
            previous.get("core_fact_digest")
            and previous.get("core_fact_digest") == current.get("core_fact_digest")
        )
        same_points = list(previous.get("key_points") or []) == list(
            current.get("key_points") or []
        )
        prior_direction = str(previous.get("target_direction") or "unknown")
        current_direction = str(current.get("target_direction") or "unknown")
        direction_reversed = {
            prior_direction,
            current_direction,
        } == {"positive", "negative"} and current.get("verification_state") == "verified"

        if (
            same_points
            and prior_hashes == current_hashes
            and prior_publishers == current_publishers
            and prior_sources == current_sources
            and previous.get("headline") == current.get("headline")
            and previous.get("price_reaction") == current.get("price_reaction")
            and previous.get("target_direction") == current.get("target_direction")
            and previous.get("priced_in_state") == current.get("priced_in_state")
        ):
            relation = "exact_duplicate"
            reason = "same_points_content_sources_and_publishers"
        elif direction_reversed:
            relation = "direction_reversal"
            reason = "verified_target_direction_flipped"
        elif str(current.get("priced_in_state") or "") == "already_priced":
            relation = "already_priced"
            reason = "target_assessment_marked_already_priced"
        elif str(current.get("priced_in_state") or "") == "still_developing":
            relation = "still_developing"
            reason = "target_assessment_marked_still_developing"
        elif same_core and previous.get("price_reaction") != current.get("price_reaction"):
            relation = "market_reaction_update"
            reason = "same_core_fact_new_market_reaction"
        elif same_core and previous.get("headline") != current.get("headline"):
            relation = "headline_rewrite"
            reason = "same_core_fact_headline_changed"
        elif (
            same_points
            and bool(added_hashes)
            and added_hashes <= prior_hashes
            and bool(current_publishers - prior_publishers)
        ):
            relation = "syndicated_copy"
            reason = "same_content_new_publisher"
        elif same_points and (
            bool(current_sources - prior_sources)
            or bool(current_publishers - prior_publishers)
        ):
            relation = "new_source_confirmation"
            reason = "same_points_new_source_or_publisher"
        else:
            relation = "substantive_revision"
            reason = "core_evidence_changed_or_could_not_be_proven_equivalent"
    return {
        "classification_version": EVENT_REVISION_CLASSIFICATION_VERSION,
        "revision_class": relation,
        "reason_code": reason,
        "creates_new_revision": relation not in {
            "exact_duplicate",
            "headline_rewrite",
            "syndicated_copy",
        },
        "formal_direction_weight": 0.0,
    }
