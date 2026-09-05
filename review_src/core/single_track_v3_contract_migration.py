from __future__ import annotations

"""Additive V11 contract columns and duplicate guards for the existing V3 schema."""

import sqlite3
from typing import Any


V11_STAGE1_SCHEMA_CONTRACT_VERSION = "SingleTrackV3V11Stage1SchemaContractV1"


_COLUMN_ADDITIONS: dict[str, dict[str, str]] = {
    "canonical_analysis_artifact": {
        "normalized_request_key": "TEXT CHECK(normalized_request_key IS NULL OR length(normalized_request_key)=64)",
        "target_entity_id": "TEXT",
        "target_trade_date": "TEXT",
        "analysis_session": "TEXT",
        "selected_profile": "TEXT",
        "canonical_core_digest": "TEXT CHECK(canonical_core_digest IS NULL OR length(canonical_core_digest)=64)",
        "projection_digest": "TEXT CHECK(projection_digest IS NULL OR length(projection_digest)=64)",
        "render_digest": "TEXT CHECK(render_digest IS NULL OR length(render_digest)=64)",
        "source_revision_ids_json": "TEXT NOT NULL DEFAULT '[]'",
        "artifact_contract_version": "TEXT NOT NULL DEFAULT 'unversioned'",
    },
    "event_revision": {
        "content_materiality": (
            "TEXT NOT NULL DEFAULT 'unknown_pending' "
            "CHECK(content_materiality IN "
            "('critical','high','medium','low','unknown_pending'))"
        ),
        "materiality_contract_version": "TEXT NOT NULL DEFAULT 'unversioned'",
    },
    "target_impact_assessment": {
        "target_materiality": (
            "TEXT NOT NULL DEFAULT 'unknown_pending' "
            "CHECK(target_materiality IN "
            "('critical','high','medium','low','unknown_pending'))"
        ),
        "target_direction": (
            "TEXT NOT NULL DEFAULT 'unknown' "
            "CHECK(target_direction IN ('positive','negative','mixed','neutral','unknown'))"
        ),
        "target_impact_magnitude": (
            "TEXT NOT NULL DEFAULT 'unknown_pending' "
            "CHECK(target_impact_magnitude IN "
            "('negligible','low','medium','high','extreme','unknown_pending'))"
        ),
        "regime_selection": (
            "TEXT NOT NULL DEFAULT 'suppressed_pending' "
            "CHECK(regime_selection IN "
            "('normal','material_event','material_pending','suppressed_pending'))"
        ),
        "target_relationship_type": (
            "TEXT NOT NULL DEFAULT 'unresolved' "
            "CHECK(target_relationship_type IN "
            "('direct_company','parent_subsidiary_group','supply_chain','customer',"
            "'peer','incidental_mention','unresolved'))"
        ),
        "weight_version": "TEXT NOT NULL DEFAULT 'candidate-unreleased'",
    },
    "stock_entity_alias": {
        "approved": "INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1))",
        "approval_id": "TEXT",
        "source_evidence_digest": (
            "TEXT CHECK(source_evidence_digest IS NULL OR length(source_evidence_digest)=64)"
        ),
    },
    "single_track_v3_source_snapshot_receipt": {
        "source_revision_id": "TEXT",
        "publisher_published_at": "TEXT",
        "first_seen_at": "TEXT",
        "usable_from": "TEXT",
        "source_policy_version": "TEXT NOT NULL DEFAULT 'unversioned'",
    },
    "canonical_evidence_fact": {
        "unit": "TEXT",
        "source_revision_id": "TEXT",
        "first_seen_at": "TEXT",
        "usable_from": "TEXT",
    },
    "technical_indicator_component": {
        "available_at": "TEXT",
        "usable_from": "TEXT",
    },
    "technical_indicator_vector_daily": {
        "available_at": "TEXT",
        "usable_from": "TEXT",
    },
    "analysis_target_prediction": {
        "event_revision_id": "TEXT",
        "target_entity_id": "TEXT",
        "target_trade_date": "TEXT",
        "weight_version": "TEXT NOT NULL DEFAULT 'unversioned'",
        "contribution_state": (
            "TEXT NOT NULL DEFAULT 'shadow_zero_weight' "
            "CHECK(contribution_state IN "
            "('shadow_zero_weight','eligible_unreleased','released','suppressed'))"
        ),
    },
    "single_track_v3_schema_state": {
        "previous_schema_version": "TEXT",
        "migration_contract_version": "TEXT NOT NULL DEFAULT 'unversioned'",
        "rollback_strategy": "TEXT NOT NULL DEFAULT 'backup_restore'",
    },
}


_INDEX_STATEMENTS = (
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_artifact_core_selection_v11
    ON canonical_analysis_artifact(
        target_entity_id,target_trade_date,analysis_session,analysis_cutoff DESC,validity
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_artifact_request_v11
    ON canonical_analysis_artifact(normalized_request_key,analysis_cutoff DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_event_revision_materiality_v11
    ON event_revision(content_materiality,verification_state,available_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_target_impact_materiality_v11
    ON target_impact_assessment(
        target_entity_id,target_trade_date,target_materiality,regime_selection
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_stock_entity_alias_approval_v11
    ON stock_entity_alias(
        alias_normalized,registry_version,reviewed,approved,confidence DESC
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_source_snapshot_revision_v11
    ON single_track_v3_source_snapshot_receipt(
        source_revision_id,target_trade_date,usable_from
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_target_impact_active_contribution_v11
    ON target_impact_assessment(
        event_revision_id,target_entity_id,target_trade_date,forecast_target_id,weight_version
    )
    WHERE assessment_status='complete' AND eligible_for_weight=1
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_target_prediction_contribution_v11
    ON analysis_target_prediction(
        event_revision_id,target_entity_id,target_trade_date,target_key,weight_version,model_role
    )
    WHERE eligible=1
      AND event_revision_id IS NOT NULL
      AND target_entity_id IS NOT NULL
      AND target_trade_date IS NOT NULL
    """,
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def apply_v11_stage1_contract_schema(conn: sqlite3.Connection) -> None:
    """Apply only additive columns and indexes to already-created V3 tables."""

    for table, additions in _COLUMN_ADDITIONS.items():
        existing = _columns(conn, table)
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    duplicate_audit = audit_v11_stage1_duplicates(conn)
    if duplicate_audit["duplicate_group_count"]:
        raise RuntimeError(
            "V11 Stage 1 duplicate audit failed before unique-index creation: "
            + ",".join(duplicate_audit["failed_checks"])
        )
    for statement in _INDEX_STATEMENTS:
        conn.execute(statement)


def audit_v11_stage1_duplicates(conn: sqlite3.Connection) -> dict[str, Any]:
    """Report duplicate contribution groups without mutating or deleting rows."""

    checks = {
        "target_impact_active_contribution": """
            SELECT COUNT(*) FROM (
                SELECT event_revision_id,target_entity_id,target_trade_date,
                       forecast_target_id,weight_version
                FROM target_impact_assessment
                WHERE assessment_status='complete' AND eligible_for_weight=1
                GROUP BY event_revision_id,target_entity_id,target_trade_date,
                         forecast_target_id,weight_version
                HAVING COUNT(*) > 1
            )
        """,
        "target_prediction_contribution": """
            SELECT COUNT(*) FROM (
                SELECT event_revision_id,target_entity_id,target_trade_date,
                       target_key,weight_version,model_role
                FROM analysis_target_prediction
                WHERE eligible=1
                  AND event_revision_id IS NOT NULL
                  AND target_entity_id IS NOT NULL
                  AND target_trade_date IS NOT NULL
                GROUP BY event_revision_id,target_entity_id,target_trade_date,
                         target_key,weight_version,model_role
                HAVING COUNT(*) > 1
            )
        """,
    }
    counts = {
        name: int(conn.execute(statement).fetchone()[0] or 0)
        for name, statement in checks.items()
    }
    failed = sorted(name for name, count in counts.items() if count)
    return {
        "contract_version": V11_STAGE1_SCHEMA_CONTRACT_VERSION,
        "checks": counts,
        "duplicate_group_count": sum(counts.values()),
        "failed_checks": failed,
        "passed": not failed,
    }


def v11_stage1_required_columns() -> dict[str, tuple[str, ...]]:
    """Expose the additive contract surface for migration and production probes."""

    return {
        table: tuple(additions)
        for table, additions in _COLUMN_ADDITIONS.items()
    }
