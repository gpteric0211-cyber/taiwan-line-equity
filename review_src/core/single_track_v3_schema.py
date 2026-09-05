from __future__ import annotations

import sqlite3
from core.sqlite_schema import schema_transaction, execute_schema_script
from datetime import datetime
from zoneinfo import ZoneInfo

from core.single_track_v3_contract_migration import (
    V11_STAGE1_SCHEMA_CONTRACT_VERSION,
    apply_v11_stage1_contract_schema,
)


SINGLE_TRACK_V3_SCHEMA_VERSION = "single-track-v3-v11-stage1.1"
TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS = 600
_TPE = ZoneInfo("Asia/Taipei")

SINGLE_TRACK_V3_TABLES = (
    "single_track_v3_scheduler_tick",
    "single_track_v3_calendar_session",
    "single_track_v3_calendar_revision",
    "single_track_v3_artifact_production_receipt",
    "content_terminal_artifact",
    "target_impact_assessment_attempt",
    "target_impact_assessment",
    "content_assessment_attempt",
    "content_assessment",
    "event_delta_artifact",
    "premarket_intelligence_artifact",
    "single_track_v3_source_snapshot_receipt",
    "single_track_v3_retrieval_worker_receipt",
    "single_track_v3_research_run_item",
    "single_track_v3_retrieval_source_attempt",
    "research_news_item",
    "event_revision",
    "event_cluster",
    "news_run_event",
    "single_track_v3_worker_heartbeat",
    "single_track_v3_outbox",
    "single_track_v3_job_lease",
    "news_retrieval_run",
    "canonical_model_answer_extension",
    "canonical_analysis_event",
    "canonical_analysis_fact",
    "statistical_evaluation_manifest",
    "outcome_snapshot_member",
    "outcome_snapshot_manifest",
    "prediction_snapshot_member",
    "prediction_snapshot_manifest",
    "statistical_gate_holdout_member",
    "statistical_gate_manifest",
    "analysis_target_prediction",
    "analysis_target_outcome",
    "canonical_analysis_artifact",
    "canonical_evidence_fact",
    "canonical_event_evidence",
    "event_scan_record",
    "technical_indicator_state",
    "technical_indicator_vector_daily",
    "technical_indicator_component",
    "stock_entity_alias",
    "single_track_v3_schema_state",
)


def ensure_single_track_v3_schema(conn: sqlite3.Connection) -> None:
    with schema_transaction(conn):
        _apply_single_track_v3_schema(conn)
def _apply_single_track_v3_schema(conn: sqlite3.Connection) -> None:
    """Create additive Single-Track V3 tables in the existing market database."""

    state_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='single_track_v3_schema_state'"
    ).fetchone()
    previous_schema_version = None
    if state_exists:
        previous = conn.execute(
            "SELECT schema_version FROM single_track_v3_schema_state WHERE singleton_id=1"
        ).fetchone()
        previous_schema_version = str(previous[0]) if previous else None

    execute_schema_script(conn,
        """
        CREATE TABLE IF NOT EXISTS technical_indicator_component (
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            indicator_key TEXT NOT NULL,
            component_key TEXT NOT NULL,
            value REAL,
            value_text TEXT,
            unit TEXT NOT NULL,
            parameters_json TEXT NOT NULL DEFAULT '{}',
            formula_version TEXT NOT NULL,
            input_snapshot_digest TEXT NOT NULL,
            input_start_date TEXT,
            input_end_date TEXT NOT NULL,
            input_row_count INTEGER NOT NULL,
            adjustment_basis TEXT NOT NULL,
            source_quality TEXT NOT NULL,
            data_quality TEXT NOT NULL,
            availability_reason TEXT,
            decision_ready INTEGER NOT NULL DEFAULT 0,
            quality_reason TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            PRIMARY KEY(
                trade_date, stock_code, indicator_key, component_key, formula_version
            ),
            CHECK(decision_ready IN (0, 1)),
            CHECK(input_row_count >= 0)
        );
        CREATE INDEX IF NOT EXISTS idx_technical_component_code_date
            ON technical_indicator_component(stock_code, trade_date DESC, formula_version);
        CREATE INDEX IF NOT EXISTS idx_technical_component_date_ready
            ON technical_indicator_component(trade_date DESC, decision_ready, data_quality);

        CREATE TABLE IF NOT EXISTS technical_indicator_vector_daily (
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            formula_version TEXT NOT NULL,
            ensemble_version TEXT NOT NULL,
            values_json TEXT NOT NULL,
            unavailable_json TEXT NOT NULL DEFAULT '{}',
            component_count INTEGER NOT NULL,
            decision_ready_count INTEGER NOT NULL,
            input_snapshot_digest TEXT NOT NULL,
            input_start_date TEXT,
            input_end_date TEXT NOT NULL,
            input_row_count INTEGER NOT NULL,
            adjustment_basis TEXT NOT NULL,
            source_quality TEXT NOT NULL,
            data_quality TEXT NOT NULL,
            quality_reason TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, stock_code, formula_version),
            CHECK(component_count >= 0),
            CHECK(decision_ready_count >= 0),
            CHECK(decision_ready_count <= component_count),
            CHECK(input_row_count >= 0)
        );
        CREATE INDEX IF NOT EXISTS idx_technical_vector_code_date
            ON technical_indicator_vector_daily(stock_code, trade_date DESC, formula_version);
        CREATE INDEX IF NOT EXISTS idx_technical_vector_date_quality
            ON technical_indicator_vector_daily(trade_date DESC, data_quality, stock_code);

        CREATE TABLE IF NOT EXISTS technical_indicator_state (
            stock_code TEXT NOT NULL,
            indicator_key TEXT NOT NULL,
            state_key TEXT NOT NULL,
            formula_version TEXT NOT NULL,
            state_value REAL,
            state_json TEXT NOT NULL DEFAULT '{}',
            last_trade_date TEXT NOT NULL,
            input_snapshot_digest TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(stock_code, indicator_key, state_key, formula_version)
        );

        CREATE TABLE IF NOT EXISTS event_cluster (
            event_cluster_id TEXT PRIMARY KEY,
            dedup_key TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            entity_refs_json TEXT NOT NULL DEFAULT '[]',
            cluster_state TEXT NOT NULL,
            first_available_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(cluster_state IN ('active','superseded','invalidated'))
        );
        CREATE INDEX IF NOT EXISTS idx_event_cluster_seen
            ON event_cluster(cluster_state, last_seen_at DESC);

        CREATE TABLE IF NOT EXISTS event_revision (
            event_revision_id TEXT PRIMARY KEY,
            event_cluster_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            revision_digest TEXT NOT NULL,
            content_evidence_digest TEXT NOT NULL,
            content_cutoff TEXT NOT NULL,
            event_type TEXT NOT NULL,
            verification_state TEXT NOT NULL,
            materiality TEXT NOT NULL,
            key_points_json TEXT NOT NULL DEFAULT '[]',
            short_excerpt TEXT NOT NULL DEFAULT '',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            price_reaction_json TEXT NOT NULL DEFAULT '{}',
            supersedes_revision_id TEXT,
            available_at TEXT NOT NULL,
            hot_content_expires_at TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(event_cluster_id, revision_no),
            UNIQUE(event_cluster_id, revision_digest),
            CHECK(revision_no >= 1),
            CHECK(length(revision_digest)=64),
            CHECK(length(content_evidence_digest)=64),
            CHECK(length(short_excerpt) <= 600),
            CHECK(verification_state IN ('unverified','verified','conflicted','rejected')),
            CHECK(materiality IN ('material','non_material','unknown')),
            FOREIGN KEY(event_cluster_id) REFERENCES event_cluster(event_cluster_id)
                ON DELETE CASCADE,
            FOREIGN KEY(supersedes_revision_id) REFERENCES event_revision(event_revision_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_event_revision_cluster
            ON event_revision(event_cluster_id, revision_no DESC, available_at DESC);

        CREATE TABLE IF NOT EXISTS content_assessment (
            content_assessment_id TEXT PRIMARY KEY,
            content_assessment_key TEXT NOT NULL UNIQUE,
            event_revision_id TEXT NOT NULL,
            content_evidence_digest TEXT NOT NULL,
            content_cutoff TEXT NOT NULL,
            model_digest TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            content_generation INTEGER NOT NULL DEFAULT 1,
            content_eligible_at TEXT NOT NULL,
            content_recovery_deadline_at TEXT NOT NULL,
            assessment_status TEXT NOT NULL,
            state_version INTEGER NOT NULL DEFAULT 0,
            promotion_epoch INTEGER NOT NULL DEFAULT 0,
            active_attempt_id TEXT,
            next_attempt_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            model_repair_count INTEGER NOT NULL DEFAULT 0,
            validation_state TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT,
            result_digest TEXT,
            terminal_transition_id TEXT UNIQUE,
            terminal_reason_code TEXT,
            last_failure_code TEXT,
            terminal_at TEXT,
            sealed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(length(content_evidence_digest)=64),
            CHECK(length(model_digest)=64),
            CHECK(content_generation >= 1),
            CHECK(state_version >= 0),
            CHECK(promotion_epoch >= 0),
            CHECK(attempt_count >= 0 AND attempt_count <= 4),
            CHECK(model_repair_count >= 0 AND model_repair_count <= 1),
            CHECK(assessment_status IN (
                'queued','dispatching','retry_wait','complete',
                'failed_terminal','superseded'
            )),
            CHECK(validation_state IN ('pending','pass','reject')),
            CHECK(result_digest IS NULL OR length(result_digest)=64),
            FOREIGN KEY(event_revision_id) REFERENCES event_revision(event_revision_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_content_assessment_revision
            ON content_assessment(event_revision_id, content_generation, assessment_status);
        CREATE INDEX IF NOT EXISTS idx_content_assessment_deadline
            ON content_assessment(assessment_status, content_recovery_deadline_at);

        CREATE TABLE IF NOT EXISTS content_assessment_attempt (
            attempt_id TEXT PRIMARY KEY,
            content_assessment_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            attempt_kind TEXT NOT NULL,
            attempt_status TEXT NOT NULL,
            dispatch_started_at TEXT NOT NULL,
            attempt_hard_deadline_at TEXT NOT NULL,
            completed_at TEXT,
            result_digest TEXT,
            validation_state TEXT NOT NULL DEFAULT 'pending',
            failure_class TEXT,
            failure_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(content_assessment_id, attempt_no),
            CHECK(attempt_no >= 1 AND attempt_no <= 4),
            CHECK(attempt_kind IN ('initial','retry','contract_repair')),
            CHECK(attempt_status IN (
                'dispatching','running','succeeded','retryable_failure',
                'permanent_failure','timed_out','stale_completion'
            )),
            CHECK(validation_state IN ('pending','pass','reject')),
            CHECK(result_digest IS NULL OR length(result_digest)=64),
            FOREIGN KEY(content_assessment_id) REFERENCES content_assessment(content_assessment_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_content_attempt_assessment
            ON content_assessment_attempt(content_assessment_id, attempt_no DESC);

        CREATE TABLE IF NOT EXISTS target_impact_assessment (
            target_impact_assessment_id TEXT PRIMARY KEY,
            target_impact_key TEXT NOT NULL UNIQUE,
            content_assessment_id TEXT NOT NULL,
            content_result_digest TEXT,
            event_revision_id TEXT NOT NULL,
            target_entity_id TEXT NOT NULL,
            target_trade_date TEXT NOT NULL,
            forecast_target_id TEXT NOT NULL,
            target_fact_snapshot_id TEXT NOT NULL,
            target_fact_snapshot_digest TEXT NOT NULL,
            target_context_digest TEXT NOT NULL,
            model_digest TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            calibrator_version TEXT NOT NULL,
            target_generation INTEGER NOT NULL DEFAULT 1,
            resolution_eligible_at TEXT NOT NULL,
            resolution_start_deadline_at TEXT NOT NULL,
            resolution_deadline_at TEXT NOT NULL,
            target_recovery_deadline_at TEXT NOT NULL,
            prediction_issue_deadline_at TEXT NOT NULL,
            assessment_status TEXT NOT NULL,
            decision_status TEXT NOT NULL DEFAULT 'pending',
            state_version INTEGER NOT NULL DEFAULT 0,
            promotion_epoch INTEGER NOT NULL DEFAULT 0,
            active_attempt_id TEXT,
            next_attempt_at TEXT,
            target_attempt_count INTEGER NOT NULL DEFAULT 0,
            model_repair_count INTEGER NOT NULL DEFAULT 0,
            validation_state TEXT NOT NULL DEFAULT 'pending',
            direction_probabilities_json TEXT,
            magnitude_probabilities_json TEXT,
            drivers_json TEXT NOT NULL DEFAULT '[]',
            counterevidence_json TEXT NOT NULL DEFAULT '[]',
            uncertainty_json TEXT NOT NULL DEFAULT '[]',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            priced_in_state TEXT NOT NULL DEFAULT 'unknown',
            eligible_for_explanation INTEGER NOT NULL DEFAULT 0,
            eligible_for_weight INTEGER NOT NULL DEFAULT 0,
            calibration_state TEXT NOT NULL DEFAULT 'unavailable',
            result_digest TEXT,
            terminal_reason_code TEXT,
            upstream_terminal_reason_code TEXT,
            terminal_at TEXT,
            sealed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(
                content_assessment_id,target_entity_id,target_trade_date,
                forecast_target_id,target_fact_snapshot_digest,target_generation
            ),
            CHECK(content_result_digest IS NULL OR length(content_result_digest)=64),
            CHECK(length(target_fact_snapshot_digest)=64),
            CHECK(length(target_context_digest)=64),
            CHECK(length(model_digest)=64),
            CHECK(target_generation >= 1),
            CHECK(state_version >= 0),
            CHECK(promotion_epoch >= 0),
            CHECK(target_attempt_count >= 0 AND target_attempt_count <= 4),
            CHECK(model_repair_count >= 0 AND model_repair_count <= 1),
            CHECK(forecast_target_id IN (
                'NEXT_SESSION_OPEN_GAP',
                'NEXT_SESSION_OPEN_TO_CLOSE_CONTINUATION',
                'NEXT_SESSION_CLOSE_DIRECTION'
            )),
            CHECK(assessment_status IN (
                'waiting_for_content','queued','dispatching','retry_wait','complete',
                'failed_terminal','blocked_dependency_terminal','superseded'
            )),
            CHECK(
                content_result_digest IS NOT NULL
                OR assessment_status IN ('waiting_for_content','blocked_dependency_terminal')
            ),
            CHECK(decision_status IN (
                'pending','validated','explanation_only','predictive',
                'suppressed_unresolved','superseded'
            )),
            CHECK(validation_state IN ('pending','pass','reject')),
            CHECK(priced_in_state IN ('already_priced','still_developing','mixed','unknown')),
            CHECK(eligible_for_explanation IN (0,1)),
            CHECK(eligible_for_weight IN (0,1)),
            CHECK(calibration_state IN ('unavailable','shadow','calibrated','rejected')),
            CHECK(result_digest IS NULL OR length(result_digest)=64),
            FOREIGN KEY(content_assessment_id) REFERENCES content_assessment(content_assessment_id)
                ON DELETE CASCADE,
            FOREIGN KEY(event_revision_id) REFERENCES event_revision(event_revision_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_target_impact_deadline
            ON target_impact_assessment(assessment_status, target_recovery_deadline_at);
        CREATE INDEX IF NOT EXISTS idx_target_impact_target
            ON target_impact_assessment(
                target_entity_id,target_trade_date,forecast_target_id,assessment_status
            );

        CREATE TABLE IF NOT EXISTS target_impact_assessment_attempt (
            attempt_id TEXT PRIMARY KEY,
            target_impact_assessment_id TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            attempt_kind TEXT NOT NULL,
            attempt_status TEXT NOT NULL,
            dispatch_started_at TEXT NOT NULL,
            attempt_hard_deadline_at TEXT NOT NULL,
            completed_at TEXT,
            result_digest TEXT,
            validation_state TEXT NOT NULL DEFAULT 'pending',
            failure_class TEXT,
            failure_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(target_impact_assessment_id, attempt_no),
            CHECK(attempt_no >= 1 AND attempt_no <= 4),
            CHECK(attempt_kind IN ('initial','retry','contract_repair')),
            CHECK(attempt_status IN (
                'dispatching','running','succeeded','retryable_failure',
                'permanent_failure','timed_out','stale_completion'
            )),
            CHECK(validation_state IN ('pending','pass','reject')),
            CHECK(result_digest IS NULL OR length(result_digest)=64),
            FOREIGN KEY(target_impact_assessment_id)
                REFERENCES target_impact_assessment(target_impact_assessment_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_target_attempt_assessment
            ON target_impact_assessment_attempt(target_impact_assessment_id, attempt_no DESC);

        CREATE TABLE IF NOT EXISTS single_track_v3_calendar_revision (
            calendar_revision TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_digest TEXT NOT NULL,
            session_policy_version TEXT NOT NULL,
            revision_published_at TEXT NOT NULL,
            revision_available_at TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Asia/Taipei',
            revision_digest TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(timezone='Asia/Taipei'),
            CHECK(length(source_digest)=64),
            CHECK(length(revision_digest)=64)
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_calendar_revision_available
            ON single_track_v3_calendar_revision(revision_available_at DESC, sealed_at DESC);

        CREATE TABLE IF NOT EXISTS single_track_v3_calendar_session (
            session_id TEXT PRIMARY KEY,
            calendar_revision TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            session_state TEXT NOT NULL,
            scheduled_open_at TEXT,
            scheduled_close_at TEXT,
            cancellation_reason TEXT,
            source_evidence_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(calendar_revision, trade_date),
            CHECK(session_state IN (
                'scheduled','cancelled','delayed','special_session','early_close'
            )),
            CHECK(length(source_evidence_digest)=64),
            CHECK(
                (session_state='cancelled' AND scheduled_open_at IS NULL AND scheduled_close_at IS NULL)
                OR
                (session_state!='cancelled' AND scheduled_open_at IS NOT NULL AND scheduled_close_at IS NOT NULL)
            ),
            FOREIGN KEY(calendar_revision)
                REFERENCES single_track_v3_calendar_revision(calendar_revision)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_calendar_session_open
            ON single_track_v3_calendar_session(calendar_revision, scheduled_open_at, trade_date);

        CREATE TABLE IF NOT EXISTS news_retrieval_run (
            run_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            slot_key TEXT NOT NULL,
            target_trade_date TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            cutoff_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            status TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            calendar_revision TEXT NOT NULL,
            source_coverage_json TEXT NOT NULL DEFAULT '{}',
            source_failures_json TEXT NOT NULL DEFAULT '[]',
            late_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(slot_key IN (
                'evening_1800','evening_2100','preopen_0600',
                'preopen_final_scan','high_signal_sentinel_15m'
            )),
            CHECK(status IN (
                'queued','running','success','partial','failed','late','skipped'
            ))
        );
        CREATE INDEX IF NOT EXISTS idx_news_retrieval_run_target_slot
            ON news_retrieval_run(target_trade_date, slot_key, scheduled_for DESC);
        CREATE INDEX IF NOT EXISTS idx_news_retrieval_run_status
            ON news_retrieval_run(status, scheduled_for, updated_at);

        CREATE TABLE IF NOT EXISTS single_track_v3_retrieval_source_attempt (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            query_digest TEXT NOT NULL,
            source_id TEXT NOT NULL,
            scope_key TEXT NOT NULL DEFAULT 'unclassified',
            attempt_ordinal INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            source_status TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            adapter_version TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            result_digest TEXT NOT NULL,
            raw_article_bodies_fetched INTEGER NOT NULL DEFAULT 0,
            raw_body_retention_seconds INTEGER NOT NULL DEFAULT 0,
            canonical_table_writes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, source_id, query_digest, attempt_ordinal),
            CHECK(length(query_digest)=64),
            CHECK(length(result_digest)=64),
            CHECK(attempt_ordinal >= 1),
            CHECK(outcome IN ('success','no_results','failed')),
            CHECK(failure_class IN (
                'none','timeout','rate_limited','offline','source_error',
                'policy_disabled','invalid_response'
            )),
            CHECK(length(source_status) BETWEEN 1 AND 64),
            CHECK(item_count >= 0),
            CHECK(raw_article_bodies_fetched=0),
            CHECK(raw_body_retention_seconds=0),
            CHECK(canonical_table_writes=0),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_retrieval_attempt_failure
            ON single_track_v3_retrieval_source_attempt(
                failure_class, completed_at DESC, source_id
            );

        CREATE TABLE IF NOT EXISTS single_track_v3_source_snapshot_receipt (
            snapshot_id TEXT PRIMARY KEY,
            snapshot_key TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            source_key TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            target_entity_id TEXT NOT NULL,
            target_trade_date TEXT NOT NULL,
            cutoff_at TEXT NOT NULL,
            source_as_of_date TEXT,
            available_at TEXT,
            source_id TEXT NOT NULL,
            authority_tier TEXT NOT NULL,
            source_quality TEXT NOT NULL,
            snapshot_status TEXT NOT NULL,
            availability_reason TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            payload_digest TEXT NOT NULL,
            snapshot_digest TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, source_key, target_entity_id),
            CHECK(source_key IN (
                'related_overseas_price_snapshot','us_market_snapshot',
                'taifex_night_snapshot','dilution_valuation_snapshot'
            )),
            CHECK(scope_key IN (
                'related_overseas_price_reaction','us_market_taiwan_night',
                'dilution_valuation_risk'
            )),
            CHECK(authority_tier IN (
                'canonical_official','canonical_normalized_supplemental'
            )),
            CHECK(snapshot_status IN (
                'ok','partial','unavailable','stale','source_delayed',
                'invalid_response'
            )),
            CHECK(row_count >= 0 AND row_count <= 256),
            CHECK(length(payload_digest)=64),
            CHECK(length(snapshot_digest)=64),
            CHECK(
                (snapshot_status='ok' AND row_count > 0 AND available_at IS NOT NULL)
                OR snapshot_status!='ok'
            ),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_source_snapshot_available
            ON single_track_v3_source_snapshot_receipt(
                target_trade_date, available_at, sealed_at
            );

        CREATE TABLE IF NOT EXISTS single_track_v3_scheduler_tick (
            tick_id TEXT PRIMARY KEY,
            tick_key TEXT NOT NULL UNIQUE,
            observed_at TEXT NOT NULL,
            calendar_revision TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            schedule_contract_version TEXT NOT NULL,
            planned_run_ids_json TEXT NOT NULL DEFAULT '[]',
            materialized_run_ids_json TEXT NOT NULL DEFAULT '[]',
            catch_up_run_ids_json TEXT NOT NULL DEFAULT '[]',
            skipped_run_ids_json TEXT NOT NULL DEFAULT '[]',
            tick_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(length(tick_key)=64),
            CHECK(length(tick_digest)=64),
            FOREIGN KEY(calendar_revision)
                REFERENCES single_track_v3_calendar_revision(calendar_revision)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_scheduler_tick_observed
            ON single_track_v3_scheduler_tick(observed_at DESC, calendar_revision);

        CREATE TABLE IF NOT EXISTS research_news_item (
            news_item_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_class TEXT NOT NULL,
            publisher TEXT NOT NULL,
            source_url TEXT NOT NULL,
            publisher_published_at TEXT,
            publisher_published_date TEXT,
            publisher_time_verified INTEGER NOT NULL DEFAULT 0,
            index_seen_at TEXT,
            first_retrieved_at TEXT NOT NULL,
            last_retrieved_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            effective_tw_trade_date TEXT,
            verification_state TEXT NOT NULL,
            title TEXT NOT NULL,
            key_points_json TEXT NOT NULL DEFAULT '[]',
            short_excerpt TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            event_fingerprint TEXT NOT NULL,
            dedup_cluster TEXT NOT NULL,
            entity_refs_json TEXT NOT NULL DEFAULT '[]',
            event_cluster_id TEXT,
            event_revision_id TEXT,
            source_rights_json TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            retention_class TEXT NOT NULL,
            content_expires_at TEXT NOT NULL,
            content_pruned_at TEXT,
            untrusted_text INTEGER NOT NULL DEFAULT 1,
            raw_body_retained INTEGER NOT NULL DEFAULT 0,
            first_run_id TEXT NOT NULL,
            last_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_id, content_hash),
            CHECK(source_class IN (
                'canonical_official','canonical_normalized_supplemental',
                'licensed_secondary','news_radar','community_claim'
            )),
            CHECK(verification_state IN (
                'discovered','unverified','primary_verified',
                'secondary_corroborated','contradicted',
                'pending_reconciliation','expired'
            )),
            CHECK(retention_class IN (
                'hot_news','canonical_disclosure','community_claim'
            )),
            CHECK(length(title) <= 300),
            CHECK(length(short_excerpt) <= 600),
            CHECK(length(content_hash)=64),
            CHECK(length(event_fingerprint)=64),
            CHECK(publisher_published_date IS NULL OR length(publisher_published_date)=10),
            CHECK(publisher_time_verified IN (0,1)),
            CHECK(untrusted_text=1),
            CHECK(raw_body_retained=0),
            FOREIGN KEY(event_cluster_id) REFERENCES event_cluster(event_cluster_id)
                ON DELETE SET NULL,
            FOREIGN KEY(event_revision_id) REFERENCES event_revision(event_revision_id)
                ON DELETE SET NULL,
            FOREIGN KEY(first_run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(last_run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_research_news_available
            ON research_news_item(available_at DESC, verification_state, source_class);
        CREATE INDEX IF NOT EXISTS idx_research_news_expiry
            ON research_news_item(content_pruned_at, content_expires_at);
        CREATE INDEX IF NOT EXISTS idx_research_news_cluster
            ON research_news_item(dedup_cluster, event_revision_id, available_at DESC);

        CREATE TABLE IF NOT EXISTS single_track_v3_research_run_item (
            run_id TEXT NOT NULL,
            news_item_id TEXT NOT NULL,
            event_action TEXT NOT NULL,
            linked_at TEXT NOT NULL,
            PRIMARY KEY(run_id, news_item_id),
            CHECK(event_action IN ('discovered','updated','unchanged')),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE CASCADE,
            FOREIGN KEY(news_item_id) REFERENCES research_news_item(news_item_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_research_run_item_news
            ON single_track_v3_research_run_item(news_item_id, linked_at DESC);

        CREATE TABLE IF NOT EXISTS premarket_intelligence_artifact (
            artifact_id TEXT PRIMARY KEY,
            artifact_key TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL UNIQUE,
            target_trade_date TEXT NOT NULL,
            slot_key TEXT NOT NULL,
            cutoff_at TEXT NOT NULL,
            event_watermark TEXT,
            event_revision_ids_json TEXT NOT NULL DEFAULT '[]',
            source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            source_coverage_json TEXT NOT NULL DEFAULT '{}',
            source_failures_json TEXT NOT NULL DEFAULT '[]',
            artifact_status TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            calendar_revision TEXT NOT NULL,
            artifact_digest TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(target_trade_date, slot_key, cutoff_at),
            CHECK(slot_key IN (
                'evening_1800','evening_2100','preopen_0600',
                'preopen_final_scan'
            )),
            CHECK(artifact_status IN ('sealed','partial','incomplete','late')),
            CHECK(length(artifact_key)=64),
            CHECK(length(artifact_digest)=64),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_premarket_artifact_target_cutoff
            ON premarket_intelligence_artifact(
                target_trade_date, cutoff_at DESC, sealed_at DESC
            );

        CREATE TABLE IF NOT EXISTS event_delta_artifact (
            delta_artifact_id TEXT PRIMARY KEY,
            delta_key TEXT NOT NULL UNIQUE,
            run_id TEXT,
            target_trade_date TEXT NOT NULL,
            base_premarket_artifact_id TEXT,
            cutoff_at TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            event_revision_ids_json TEXT NOT NULL DEFAULT '[]',
            invalidated_analysis_ids_json TEXT NOT NULL DEFAULT '[]',
            source_coverage_json TEXT NOT NULL DEFAULT '{}',
            delta_status TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            calendar_revision TEXT NOT NULL,
            artifact_digest TEXT,
            sealed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(delta_status IN ('pending','sealed','partial','incomplete')),
            CHECK(length(delta_key)=64),
            CHECK(
                (
                    delta_status='pending'
                    AND artifact_digest IS NULL
                    AND sealed_at IS NULL
                ) OR (
                    delta_status IN ('sealed','partial','incomplete')
                    AND length(artifact_digest)=64
                    AND sealed_at IS NOT NULL
                )
            ),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(base_premarket_artifact_id)
                REFERENCES premarket_intelligence_artifact(artifact_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_event_delta_target_cutoff
            ON event_delta_artifact(target_trade_date, cutoff_at DESC, detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_event_delta_status
            ON event_delta_artifact(delta_status, detected_at, updated_at);

        CREATE TABLE IF NOT EXISTS single_track_v3_job_lease (
            lease_key TEXT PRIMARY KEY,
            run_id TEXT,
            owner_id TEXT NOT NULL,
            lease_token TEXT NOT NULL UNIQUE,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            state TEXT NOT NULL,
            released_at TEXT,
            CHECK(state IN ('active','released','expired')),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_job_lease_expiry
            ON single_track_v3_job_lease(state, expires_at);

        CREATE TABLE IF NOT EXISTS single_track_v3_worker_heartbeat (
            worker_id TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            lease_key TEXT NOT NULL,
            run_id TEXT,
            worker_identity_digest TEXT NOT NULL,
            state TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(worker_id, heartbeat_at),
            CHECK(length(worker_identity_digest)=64),
            CHECK(state IN ('starting','running','idle','stopping','stopped','failed')),
            FOREIGN KEY(lease_key) REFERENCES single_track_v3_job_lease(lease_key)
                ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_heartbeat_lease
            ON single_track_v3_worker_heartbeat(lease_key, heartbeat_at DESC);

        CREATE TABLE IF NOT EXISTS single_track_v3_outbox (
            outbox_id TEXT PRIMARY KEY,
            run_id TEXT,
            event_type TEXT NOT NULL,
            aggregate_key TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            available_at TEXT NOT NULL,
            claimed_at TEXT,
            delivered_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(status IN ('pending','processing','delivered','failed')),
            CHECK(attempt_count >= 0),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_outbox_available
            ON single_track_v3_outbox(status, available_at, created_at);

        CREATE TABLE IF NOT EXISTS single_track_v3_retrieval_worker_receipt (
            receipt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            terminal_status TEXT NOT NULL,
            source_attempt_ids_json TEXT NOT NULL DEFAULT '[]',
            news_item_ids_json TEXT NOT NULL DEFAULT '[]',
            pruned_item_count INTEGER NOT NULL DEFAULT 0,
            terminal_outbox_id TEXT NOT NULL UNIQUE,
            worker_contract_version TEXT NOT NULL,
            entity_refs_json TEXT NOT NULL DEFAULT '[]',
            query_digests_json TEXT NOT NULL DEFAULT '[]',
            query_plan_version TEXT NOT NULL DEFAULT 'unversioned',
            source_plan_version TEXT NOT NULL DEFAULT 'unversioned',
            result_digest TEXT NOT NULL,
            zero_model_calls INTEGER NOT NULL DEFAULT 0,
            raw_article_bodies_retained INTEGER NOT NULL DEFAULT 0,
            canonical_table_writes INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(terminal_status IN ('success','partial','failed','late')),
            CHECK(length(result_digest)=64),
            CHECK(pruned_item_count >= 0),
            CHECK(zero_model_calls=0),
            CHECK(raw_article_bodies_retained=0),
            CHECK(canonical_table_writes=0),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(terminal_outbox_id) REFERENCES single_track_v3_outbox(outbox_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_single_track_retrieval_receipt_time
            ON single_track_v3_retrieval_worker_receipt(completed_at DESC, terminal_status);

        CREATE TABLE IF NOT EXISTS content_terminal_artifact (
            artifact_id TEXT PRIMARY KEY,
            terminal_transition_id TEXT NOT NULL UNIQUE,
            content_assessment_id TEXT NOT NULL,
            content_assessment_key TEXT NOT NULL,
            content_generation INTEGER NOT NULL,
            terminal_reason_code TEXT NOT NULL,
            user_visible_state TEXT NOT NULL,
            source_outbox_id TEXT NOT NULL UNIQUE,
            artifact_digest TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(content_generation >= 1),
            CHECK(user_visible_state='suppressed_unresolved'),
            CHECK(length(terminal_transition_id)=64),
            CHECK(length(artifact_digest)=64),
            FOREIGN KEY(content_assessment_id) REFERENCES content_assessment(content_assessment_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(source_outbox_id) REFERENCES single_track_v3_outbox(outbox_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_content_terminal_artifact_content
            ON content_terminal_artifact(content_assessment_id, sealed_at DESC);

        CREATE TABLE IF NOT EXISTS single_track_v3_artifact_production_receipt (
            receipt_id TEXT PRIMARY KEY,
            outbox_id TEXT NOT NULL UNIQUE,
            input_event_type TEXT NOT NULL,
            input_payload_digest TEXT NOT NULL,
            producer_version TEXT NOT NULL,
            production_status TEXT NOT NULL,
            output_artifact_type TEXT NOT NULL,
            output_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
            output_digest TEXT NOT NULL,
            zero_gpu_model_calls INTEGER NOT NULL DEFAULT 0,
            produced_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(length(input_payload_digest)=64),
            CHECK(length(output_digest)=64),
            CHECK(production_status IN ('applied','no_op')),
            CHECK(output_artifact_type IN (
                'content_terminal_state','canonical_analysis_invalidation','none'
            )),
            CHECK(zero_gpu_model_calls=0),
            FOREIGN KEY(outbox_id) REFERENCES single_track_v3_outbox(outbox_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_production_receipt_time
            ON single_track_v3_artifact_production_receipt(produced_at DESC, input_event_type);

        CREATE TABLE IF NOT EXISTS event_scan_record (
            scan_id TEXT PRIMARY KEY,
            entity_refs_json TEXT NOT NULL DEFAULT '[]',
            request_received_at TEXT NOT NULL,
            analysis_cutoff TEXT NOT NULL,
            event_watermark TEXT,
            scan_state TEXT NOT NULL,
            source_policy_version TEXT NOT NULL,
            coverage_json TEXT NOT NULL DEFAULT '{}',
            omissions_json TEXT NOT NULL DEFAULT '[]',
            conflicts_json TEXT NOT NULL DEFAULT '[]',
            completed_at TEXT NOT NULL,
            CHECK(scan_state IN (
                'verified_none','verified_material',
                'pending_reconciliation','scan_incomplete'
            ))
        );
        CREATE INDEX IF NOT EXISTS idx_event_scan_cutoff
            ON event_scan_record(analysis_cutoff DESC, scan_state);

        CREATE TABLE IF NOT EXISTS canonical_event_evidence (
            event_id TEXT PRIMARY KEY,
            entity_refs_json TEXT NOT NULL DEFAULT '[]',
            event_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_class TEXT NOT NULL,
            publisher TEXT NOT NULL,
            source_url TEXT NOT NULL,
            publisher_published_at TEXT,
            index_seen_at TEXT,
            retrieved_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            effective_tw_trade_date TEXT,
            verification_state TEXT NOT NULL,
            rights TEXT NOT NULL,
            untrusted_text TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL,
            dedup_cluster TEXT NOT NULL,
            relevance REAL NOT NULL DEFAULT 0,
            directness REAL NOT NULL DEFAULT 0,
            materiality TEXT NOT NULL DEFAULT 'unknown',
            magnitude REAL,
            surprise REAL,
            direction TEXT NOT NULL DEFAULT 'unknown',
            confidence REAL NOT NULL DEFAULT 0,
            horizon TEXT NOT NULL DEFAULT 'unknown',
            affected_claim_ids_json TEXT NOT NULL DEFAULT '[]',
            invalidation_scope TEXT NOT NULL DEFAULT 'none',
            corroborating_event_ids_json TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL,
            CHECK(source_class IN (
                'canonical_official','canonical_normalized_supplemental',
                'licensed_secondary','news_radar','model_inference'
            )),
            CHECK(verification_state IN ('unverified','verified','conflicted','rejected')),
            CHECK(materiality IN ('material','non_material','unknown')),
            CHECK(direction IN ('positive','negative','mixed','neutral','unknown')),
            CHECK(relevance >= 0 AND relevance <= 1),
            CHECK(directness >= 0 AND directness <= 1),
            CHECK(confidence >= 0 AND confidence <= 1),
            CHECK(length(untrusted_text) <= 2000)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_event_fingerprint
            ON canonical_event_evidence(fingerprint, source_id);
        CREATE INDEX IF NOT EXISTS idx_canonical_event_available
            ON canonical_event_evidence(available_at DESC, verification_state, materiality);
        CREATE INDEX IF NOT EXISTS idx_canonical_event_cluster
            ON canonical_event_evidence(dedup_cluster, available_at DESC);

        CREATE TABLE IF NOT EXISTS news_run_event (
            run_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_revision_id TEXT NOT NULL,
            event_action TEXT NOT NULL,
            linked_at TEXT NOT NULL,
            PRIMARY KEY(run_id, event_revision_id),
            CHECK(event_action IN (
                'discovered','updated','unchanged','contradicted','invalidated'
            )),
            FOREIGN KEY(run_id) REFERENCES news_retrieval_run(run_id)
                ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES canonical_event_evidence(event_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_news_run_event_event
            ON news_run_event(event_id, event_revision_id, linked_at);

        CREATE TABLE IF NOT EXISTS canonical_evidence_fact (
            fact_id TEXT PRIMARY KEY,
            entity TEXT NOT NULL,
            field TEXT NOT NULL,
            value_json TEXT NOT NULL,
            unit_currency TEXT,
            period TEXT,
            trade_date TEXT,
            as_of TEXT NOT NULL,
            available_at TEXT NOT NULL,
            source_market_timestamp TEXT,
            session TEXT NOT NULL DEFAULT 'unknown',
            adjustment_basis TEXT,
            authority_tier TEXT NOT NULL,
            quality TEXT NOT NULL,
            availability_reason TEXT,
            use_scope TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            formula_source_version TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            CHECK(authority_tier IN (
                'canonical_official','canonical_normalized_supplemental',
                'licensed_secondary','news_radar','model_inference'
            ))
        );
        CREATE INDEX IF NOT EXISTS idx_canonical_fact_snapshot
            ON canonical_evidence_fact(snapshot_id, entity, field);
        CREATE INDEX IF NOT EXISTS idx_canonical_fact_available
            ON canonical_evidence_fact(available_at DESC, authority_tier);

        CREATE TABLE IF NOT EXISTS canonical_analysis_artifact (
            analysis_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            snapshot_digest TEXT NOT NULL,
            request_received_at TEXT NOT NULL,
            analysis_cutoff TEXT NOT NULL,
            snapshot_sealed_at TEXT NOT NULL,
            context_digest TEXT NOT NULL,
            component_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            event_watermark TEXT,
            source_policy_version TEXT NOT NULL,
            weight_version TEXT NOT NULL,
            formula_version TEXT NOT NULL,
            referee_version TEXT NOT NULL,
            model_digest TEXT,
            prompt_version TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            renderer_version TEXT NOT NULL,
            entity_registry_version TEXT NOT NULL,
            conversation_projection_version TEXT NOT NULL,
            response_style_version TEXT NOT NULL,
            coverage_json TEXT NOT NULL DEFAULT '{}',
            omissions_json TEXT NOT NULL DEFAULT '[]',
            conflicts_json TEXT NOT NULL DEFAULT '[]',
            validity TEXT NOT NULL,
            superseded_by_event_ids_json TEXT NOT NULL DEFAULT '[]',
            superseded_reason TEXT,
            canonical_payload_json TEXT NOT NULL DEFAULT '{}',
            canonical_answer_text TEXT NOT NULL,
            canonical_answer_text_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(validity IN ('valid','partial','invalid','superseded'))
        );
        CREATE INDEX IF NOT EXISTS idx_canonical_artifact_snapshot
            ON canonical_analysis_artifact(snapshot_id, analysis_cutoff DESC);
        CREATE INDEX IF NOT EXISTS idx_canonical_artifact_cutoff
            ON canonical_analysis_artifact(analysis_cutoff DESC, validity);

        CREATE TABLE IF NOT EXISTS canonical_model_answer_extension (
            analysis_id TEXT PRIMARY KEY,
            base_answer_text_hash TEXT NOT NULL,
            packet_digest TEXT NOT NULL,
            compacted_packet_sha256 TEXT NOT NULL,
            model_id TEXT NOT NULL,
            model_digest TEXT NOT NULL,
            candidate_version TEXT NOT NULL,
            generation_schema_version TEXT NOT NULL,
            generation_schema_sha256 TEXT NOT NULL,
            generation_prompt_sha256 TEXT NOT NULL,
            validator_version TEXT NOT NULL,
            renderer_version TEXT NOT NULL,
            explanation_blocks_json TEXT NOT NULL DEFAULT '[]',
            used_event_ids_json TEXT NOT NULL DEFAULT '[]',
            research_limitations_json TEXT NOT NULL DEFAULT '[]',
            canonical_answer_text TEXT NOT NULL,
            canonical_answer_text_hash TEXT NOT NULL,
            authorization_id TEXT NOT NULL,
            release_source_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(analysis_id) REFERENCES canonical_analysis_artifact(analysis_id)
                ON DELETE CASCADE,
            CHECK(length(base_answer_text_hash)=64),
            CHECK(length(packet_digest)=64),
            CHECK(length(compacted_packet_sha256)=64),
            CHECK(length(model_digest)=64),
            CHECK(length(generation_schema_sha256)=64),
            CHECK(length(generation_prompt_sha256)=64),
            CHECK(length(canonical_answer_text_hash)=64),
            CHECK(length(release_source_digest)=64)
        );
        CREATE INDEX IF NOT EXISTS idx_canonical_model_answer_created
            ON canonical_model_answer_extension(created_at DESC, model_digest);

        CREATE TABLE IF NOT EXISTS canonical_analysis_fact (
            analysis_id TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            claim_id TEXT,
            PRIMARY KEY(analysis_id, fact_id),
            FOREIGN KEY(analysis_id) REFERENCES canonical_analysis_artifact(analysis_id)
                ON DELETE CASCADE,
            FOREIGN KEY(fact_id) REFERENCES canonical_evidence_fact(fact_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_fact_fact
            ON canonical_analysis_fact(fact_id, analysis_id);

        CREATE TABLE IF NOT EXISTS canonical_analysis_event (
            analysis_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            use_scope TEXT NOT NULL,
            PRIMARY KEY(analysis_id, event_id),
            FOREIGN KEY(analysis_id) REFERENCES canonical_analysis_artifact(analysis_id)
                ON DELETE CASCADE,
            FOREIGN KEY(event_id) REFERENCES canonical_event_evidence(event_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_event_event
            ON canonical_analysis_event(event_id, analysis_id);

        CREATE TABLE IF NOT EXISTS stock_entity_alias (
            alias TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            trading_name TEXT NOT NULL,
            source TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            confidence REAL NOT NULL,
            ambiguity_set_json TEXT NOT NULL DEFAULT '[]',
            registry_version TEXT NOT NULL,
            reviewed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(alias_normalized, stock_code, effective_from, registry_version),
            CHECK(length(stock_code)=4 AND stock_code GLOB '[0-9][0-9][0-9][0-9]'),
            CHECK(confidence >= 0 AND confidence <= 1),
            CHECK(reviewed IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS idx_stock_entity_alias_lookup
            ON stock_entity_alias(alias_normalized, effective_from, effective_to, confidence DESC);
        CREATE INDEX IF NOT EXISTS idx_stock_entity_alias_code
            ON stock_entity_alias(stock_code, registry_version, effective_from DESC);

        CREATE TABLE IF NOT EXISTS analysis_target_prediction (
            sample_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            model_role TEXT NOT NULL,
            target_key TEXT NOT NULL,
            regime TEXT NOT NULL,
            probability_json TEXT NOT NULL,
            eligible INTEGER NOT NULL,
            completion_state TEXT NOT NULL,
            omission_reason TEXT,
            analysis_cutoff TEXT NOT NULL,
            event_cluster_id TEXT,
            event_type TEXT,
            large_safety_slice INTEGER NOT NULL DEFAULT 0,
            synthetic INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY(sample_id, model_role, target_key),
            FOREIGN KEY(analysis_id) REFERENCES canonical_analysis_artifact(analysis_id)
                ON DELETE CASCADE,
            CHECK(model_role IN ('stable','candidate')),
            CHECK(target_key IN ('next_open_gap','continuation_reversal','next_close_direction')),
            CHECK(regime IN ('normal','material_event')),
            CHECK(eligible IN (0, 1)),
            CHECK(large_safety_slice IN (0, 1)),
            CHECK(synthetic IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS idx_target_prediction_analysis
            ON analysis_target_prediction(analysis_id, model_role, target_key);
        CREATE INDEX IF NOT EXISTS idx_target_prediction_regime
            ON analysis_target_prediction(regime, target_key, created_at);

        CREATE TABLE IF NOT EXISTS analysis_target_outcome (
            sample_id TEXT NOT NULL,
            target_key TEXT NOT NULL,
            outcome_revision TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            prediction_trade_date TEXT NOT NULL,
            outcome_trade_date TEXT,
            label TEXT,
            t_close REAL,
            next_open REAL,
            next_close REAL,
            adjustment_basis TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            availability_reason TEXT,
            available_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(sample_id, target_key, outcome_revision),
            CHECK(target_key IN ('next_open_gap','continuation_reversal','next_close_direction')),
            CHECK(quality_status IN ('ok','unavailable'))
        );
        CREATE INDEX IF NOT EXISTS idx_target_outcome_date
            ON analysis_target_outcome(prediction_trade_date, target_key, outcome_revision);
        CREATE INDEX IF NOT EXISTS idx_target_outcome_available
            ON analysis_target_outcome(available_at, quality_status);

        CREATE TABLE IF NOT EXISTS statistical_gate_manifest (
            gate_manifest_id TEXT PRIMARY KEY,
            manifest_key TEXT NOT NULL UNIQUE,
            gate_version TEXT NOT NULL,
            gate_spec_json TEXT NOT NULL,
            gate_spec_hash TEXT NOT NULL,
            evaluator_source_digest TEXT NOT NULL,
            target_label_contract_version TEXT NOT NULL,
            target_label_source_digest TEXT NOT NULL,
            holdout_partition_id TEXT NOT NULL,
            holdout_partition_digest TEXT NOT NULL,
            holdout_member_count INTEGER NOT NULL,
            bootstrap_iterations INTEGER NOT NULL,
            bootstrap_seed INTEGER NOT NULL,
            bootstrap_block_days INTEGER NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(length(manifest_key)=64),
            CHECK(length(gate_spec_hash)=64),
            CHECK(length(evaluator_source_digest)=64),
            CHECK(length(target_label_source_digest)=64),
            CHECK(length(holdout_partition_digest)=64),
            CHECK(holdout_member_count >= 1),
            CHECK(bootstrap_iterations >= 1),
            CHECK(bootstrap_block_days >= 1)
        );

        CREATE TABLE IF NOT EXISTS statistical_gate_holdout_member (
            gate_manifest_id TEXT NOT NULL,
            sample_id TEXT NOT NULL,
            target_key TEXT NOT NULL,
            PRIMARY KEY(gate_manifest_id, sample_id, target_key),
            CHECK(target_key IN (
                'next_open_gap','continuation_reversal','next_close_direction'
            )),
            FOREIGN KEY(gate_manifest_id) REFERENCES statistical_gate_manifest(gate_manifest_id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS prediction_snapshot_manifest (
            prediction_manifest_id TEXT PRIMARY KEY,
            manifest_key TEXT NOT NULL UNIQUE,
            gate_manifest_id TEXT NOT NULL,
            model_role TEXT NOT NULL,
            prediction_contract_version TEXT NOT NULL,
            member_count INTEGER NOT NULL,
            member_digest TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(gate_manifest_id, model_role, prediction_contract_version),
            CHECK(length(manifest_key)=64),
            CHECK(length(member_digest)=64),
            CHECK(model_role IN ('stable','candidate')),
            CHECK(member_count >= 1),
            FOREIGN KEY(gate_manifest_id) REFERENCES statistical_gate_manifest(gate_manifest_id)
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS prediction_snapshot_member (
            prediction_manifest_id TEXT NOT NULL,
            sample_id TEXT NOT NULL,
            target_key TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            regime TEXT NOT NULL,
            analysis_cutoff TEXT NOT NULL,
            probability_json TEXT NOT NULL,
            eligible INTEGER NOT NULL,
            completion_state TEXT NOT NULL,
            omission_reason TEXT,
            event_cluster_id TEXT,
            event_type TEXT,
            large_safety_slice INTEGER NOT NULL DEFAULT 0,
            synthetic INTEGER NOT NULL DEFAULT 0,
            prediction_created_at TEXT NOT NULL,
            row_digest TEXT NOT NULL,
            PRIMARY KEY(prediction_manifest_id, sample_id, target_key),
            CHECK(target_key IN (
                'next_open_gap','continuation_reversal','next_close_direction'
            )),
            CHECK(regime IN ('normal','material_event')),
            CHECK(eligible IN (0,1)),
            CHECK(completion_state IN (
                'completed','abstained','omitted','failed','unavailable'
            )),
            CHECK(large_safety_slice IN (0,1)),
            CHECK(synthetic=0),
            CHECK(length(row_digest)=64),
            FOREIGN KEY(prediction_manifest_id)
                REFERENCES prediction_snapshot_manifest(prediction_manifest_id)
                ON DELETE CASCADE,
            FOREIGN KEY(analysis_id) REFERENCES canonical_analysis_artifact(analysis_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_prediction_snapshot_analysis
            ON prediction_snapshot_member(analysis_id, target_key, regime);

        CREATE TABLE IF NOT EXISTS outcome_snapshot_manifest (
            outcome_manifest_id TEXT PRIMARY KEY,
            manifest_key TEXT NOT NULL UNIQUE,
            gate_manifest_id TEXT NOT NULL,
            outcome_revision TEXT NOT NULL,
            target_label_contract_version TEXT NOT NULL,
            target_label_source_digest TEXT NOT NULL,
            adjustment_basis TEXT NOT NULL,
            holdout_opened_at TEXT NOT NULL,
            first_outcome_available_at TEXT NOT NULL,
            last_outcome_available_at TEXT NOT NULL,
            member_count INTEGER NOT NULL,
            member_digest TEXT NOT NULL,
            sealed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(gate_manifest_id, outcome_revision),
            CHECK(length(manifest_key)=64),
            CHECK(length(member_digest)=64),
            CHECK(length(target_label_source_digest)=64),
            CHECK(member_count >= 1),
            FOREIGN KEY(gate_manifest_id) REFERENCES statistical_gate_manifest(gate_manifest_id)
                ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS outcome_snapshot_member (
            outcome_manifest_id TEXT NOT NULL,
            sample_id TEXT NOT NULL,
            target_key TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            prediction_trade_date TEXT NOT NULL,
            outcome_trade_date TEXT,
            label TEXT,
            t_close REAL,
            next_open REAL,
            next_close REAL,
            adjustment_basis TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            availability_reason TEXT,
            available_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            row_digest TEXT NOT NULL,
            PRIMARY KEY(outcome_manifest_id, sample_id, target_key),
            CHECK(target_key IN (
                'next_open_gap','continuation_reversal','next_close_direction'
            )),
            CHECK(length(stock_code)=4 AND stock_code GLOB '[0-9][0-9][0-9][0-9]'),
            CHECK(quality_status IN ('ok','unavailable')),
            CHECK(length(row_digest)=64),
            FOREIGN KEY(outcome_manifest_id)
                REFERENCES outcome_snapshot_manifest(outcome_manifest_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_outcome_snapshot_trade_date
            ON outcome_snapshot_member(prediction_trade_date, target_key, quality_status);

        CREATE TABLE IF NOT EXISTS statistical_evaluation_manifest (
            evaluation_manifest_id TEXT PRIMARY KEY,
            evaluation_key TEXT NOT NULL UNIQUE,
            gate_manifest_id TEXT NOT NULL,
            stable_prediction_manifest_id TEXT NOT NULL,
            candidate_prediction_manifest_id TEXT NOT NULL,
            outcome_manifest_id TEXT NOT NULL,
            gate_version TEXT NOT NULL,
            gate_spec_hash TEXT NOT NULL,
            evaluator_source_digest TEXT NOT NULL,
            target_label_contract_version TEXT NOT NULL,
            target_label_source_digest TEXT NOT NULL,
            bootstrap_iterations INTEGER NOT NULL,
            bootstrap_seed INTEGER NOT NULL,
            holdout_member_count INTEGER NOT NULL,
            eligible_outcome_count INTEGER NOT NULL,
            outcome_unavailable_count INTEGER NOT NULL,
            synthetic_row_count INTEGER NOT NULL DEFAULT 0,
            evaluation_result TEXT NOT NULL,
            evaluation_json TEXT NOT NULL,
            evaluation_digest TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(
                gate_manifest_id, stable_prediction_manifest_id,
                candidate_prediction_manifest_id, outcome_manifest_id
            ),
            CHECK(length(evaluation_key)=64),
            CHECK(length(gate_spec_hash)=64),
            CHECK(length(evaluator_source_digest)=64),
            CHECK(length(target_label_source_digest)=64),
            CHECK(length(evaluation_digest)=64),
            CHECK(holdout_member_count >= 1),
            CHECK(eligible_outcome_count >= 0),
            CHECK(outcome_unavailable_count >= 0),
            CHECK(synthetic_row_count=0),
            CHECK(evaluation_result IN (
                'pass_for_canary',
                'remain_shadow_noninferior_but_not_superior',
                'remain_shadow_insufficient_power',
                'reject_safety_regression',
                'reject_statistical_regression',
                'invalid_evidence'
            )),
            FOREIGN KEY(gate_manifest_id) REFERENCES statistical_gate_manifest(gate_manifest_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(stable_prediction_manifest_id)
                REFERENCES prediction_snapshot_manifest(prediction_manifest_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(candidate_prediction_manifest_id)
                REFERENCES prediction_snapshot_manifest(prediction_manifest_id)
                ON DELETE RESTRICT,
            FOREIGN KEY(outcome_manifest_id)
                REFERENCES outcome_snapshot_manifest(outcome_manifest_id)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_statistical_evaluation_created
            ON statistical_evaluation_manifest(evaluated_at DESC, evaluation_result);

        CREATE TABLE IF NOT EXISTS single_track_v3_schema_state (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
            schema_version TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        """
    )
    artifact_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(canonical_analysis_artifact)").fetchall()
    }
    if "canonical_payload_json" not in artifact_columns:
        conn.execute(
            "ALTER TABLE canonical_analysis_artifact "
            "ADD COLUMN canonical_payload_json TEXT NOT NULL DEFAULT '{}'"
        )
    prediction_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(analysis_target_prediction)").fetchall()
    }
    prediction_additions = {
        "event_cluster_id": "TEXT",
        "event_type": "TEXT",
        "large_safety_slice": "INTEGER NOT NULL DEFAULT 0 CHECK(large_safety_slice IN (0,1))",
        "synthetic": "INTEGER NOT NULL DEFAULT 0 CHECK(synthetic IN (0,1))",
    }
    for column, definition in prediction_additions.items():
        if column not in prediction_columns:
            conn.execute(
                f"ALTER TABLE analysis_target_prediction ADD COLUMN {column} {definition}"
            )
    retry_column_additions = {
        "content_assessment": {
            "next_attempt_at": "TEXT",
        },
        "target_impact_assessment": {
            "next_attempt_at": "TEXT",
        },
        "single_track_v3_retrieval_source_attempt": {
            "scope_key": "TEXT NOT NULL DEFAULT 'unclassified'",
        },
        "single_track_v3_retrieval_worker_receipt": {
            "entity_refs_json": "TEXT NOT NULL DEFAULT '[]'",
            "query_digests_json": "TEXT NOT NULL DEFAULT '[]'",
            "query_plan_version": "TEXT NOT NULL DEFAULT 'unversioned'",
            "source_plan_version": "TEXT NOT NULL DEFAULT 'unversioned'",
        },
        "research_news_item": {
            "publisher_published_date": "TEXT",
        },
        "premarket_intelligence_artifact": {
            "source_snapshot_ids_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    }
    for table, additions in retry_column_additions.items():
        existing_columns = {
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        for column, definition in additions.items():
            if column not in existing_columns:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    # This index must be created after the Stage 8.9 -> 8.10 additive column
    # migration.  Creating it in the initial executescript would fail on an
    # existing 8.9 attempt table that does not yet have scope_key.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_single_track_retrieval_attempt_scope
        ON single_track_v3_retrieval_source_attempt(
            run_id, scope_key, outcome, completed_at
        )
        """
    )
    apply_v11_stage1_contract_schema(conn)
    applied_at = datetime.now(_TPE).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO single_track_v3_schema_state(
            singleton_id,schema_version,applied_at,previous_schema_version,
            migration_contract_version,rollback_strategy
        )
        VALUES(1,?,?,?,?,?)
        ON CONFLICT(singleton_id) DO UPDATE SET
            schema_version=excluded.schema_version,
            applied_at=excluded.applied_at,
            previous_schema_version=single_track_v3_schema_state.schema_version,
            migration_contract_version=excluded.migration_contract_version,
            rollback_strategy=excluded.rollback_strategy
        WHERE single_track_v3_schema_state.schema_version IS NOT excluded.schema_version
           OR single_track_v3_schema_state.migration_contract_version
              IS NOT excluded.migration_contract_version
        """,
        (
            SINGLE_TRACK_V3_SCHEMA_VERSION,
            applied_at,
            previous_schema_version,
            V11_STAGE1_SCHEMA_CONTRACT_VERSION,
            "verified_backup_restore",
        ),
    )


def rollback_single_track_v3_schema(
    conn: sqlite3.Connection,
    *,
    allow_destructive: bool = False,
) -> None:
    """Drop only additive V3 tables for an operator-controlled rollback drill."""

    if not allow_destructive:
        raise PermissionError("single-track V3 rollback requires allow_destructive=True")
    for table in SINGLE_TRACK_V3_TABLES:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
