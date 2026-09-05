"""Display-only acceptance of persisted scoped bins; never a scoring gate.

The reconciliation writer stores SCOPED_VALIDATED/scoped when a complete
capture is reconciled within its own scope but fails all-session coverage.
Recheck that evidence here without promoting its quality or modifying the
shared reconciliation/scoring rules.
"""
from __future__ import annotations

import math
from typing import Any

from core.data_quality import assess_post_close_or_delayed_capture


def assess_scoped_price_volume_display(
    snapshot: dict[str, Any], *, trade_date: str, fresh: bool,
    official_trusted: bool, valid_level_count: int,
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    rows = list(snapshot.get('distribution') or [])
    profile = dict(snapshot.get('profile') or {})
    capture = dict(snapshot.get('capture') or {})
    reasons: list[str] = []
    if not fresh or not official_trusted:
        reasons.append('official_close_not_fresh_or_trusted')
    if not rows or len(rows) != valid_level_count:
        reasons.append('missing_or_invalid_price_bins')
    if any(
        str(row.get('source') or '').upper() != 'FUGLE'
        or str(row.get('data_quality') or '').upper() != 'SCOPED_VALIDATED'
        or str(row.get('source_quality') or '').upper() != 'SCOPED_VALIDATED'
        for row in rows
    ):
        reasons.append('bins_not_scoped_validated')
    if str(profile.get('quality') or '').lower() != 'scoped':
        reasons.append('profile_not_scoped')
    if str(profile.get('date') or '') != trade_date:
        reasons.append('profile_trade_date_mismatch')
    # Only these two existing rejections are expected for scoped storage.
    # Any extra reason (source, unsupported scope, missing/mismatched totals,
    # overcount, etc.) fails closed. Never relabel scoped quality as high/ok.
    if set(reconciliation.get('reasons') or []) != {
        'profile_quality_is_not_validated',
        'profile_official_volume_coverage_below_threshold',
    }:
        reasons.append('scoped_reconciliation_failed')

    bins_time = assess_post_close_or_delayed_capture(
        trade_date, [row.get('snapshot_time') for row in rows], capture,
    )
    capture_time = assess_post_close_or_delayed_capture(
        trade_date, [capture.get('snapshot_time')], capture,
    )
    if not (bins_time.get('ready') and capture_time.get('ready')
            and bins_time.get('captured_at') == capture_time.get('captured_at')):
        reasons.append('capture_snapshot_not_bound_to_bins')
    if (str(capture.get('trade_date') or '') != trade_date
            or str(capture.get('code') or '') != str(profile.get('code') or '')
            or str(capture.get('source') or '').upper() != 'FUGLE'
            or str(capture.get('endpoint') or '').lower() != 'trades'):
        reasons.append('capture_identity_mismatch')
    if (str(capture.get('capture_complete') or '').lower() not in {'1', 'true'}
            or str(capture.get('data_quality') or '').upper() != 'SESSION_COMPLETE'):
        reasons.append('capture_not_session_complete')
    try:
        counts = [int(capture.get(key) or 0) for key in
                  ('provider_row_count', 'normalized_row_count', 'stored_row_count')]
        counts_ready = counts[0] > 0 and len(set(counts)) == 1
        cumulative = float(capture.get('latest_cumulative_volume') or 0)
        cumulative_ready = math.isfinite(cumulative) and cumulative > 0
    except (TypeError, ValueError, OverflowError):
        counts_ready = cumulative_ready = False
    if not counts_ready or not cumulative_ready or not capture.get('latest_trade_time'):
        reasons.append('capture_completion_evidence_missing')
    return {
        'available': not reasons,
        'quality': 'scoped' if not reasons else 'unavailable',
        'trade_scope': reconciliation.get('trade_scope'),
        'official_coverage_ratio': reconciliation.get('official_coverage_ratio'),
        'analysis_eligible': False,
        'reason_codes': reasons,
    }
