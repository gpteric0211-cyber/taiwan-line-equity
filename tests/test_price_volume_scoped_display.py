"""Read-only canonical pipeline -> web display, without relaxing analysis gates."""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import app
from repository.market_microstructure_repository import read_daily_market_microstructure
from services import bot_market_data_service as service

_spec = importlib.util.spec_from_file_location('scoped_display_fixtures', Path(__file__).with_name('test_bot_market_data_api.py'))
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)


@pytest.fixture
def scoped_snapshot():
    case = fixtures.BotMarketDataServiceTest()
    case.setUp()
    try:
        snapshot = read_daily_market_microstructure(fixtures.CODE)
        snapshot['history']['volume'] = 150000
        snapshot['profile'].update(quality='scoped', trade_scope='regular_intraday',
                                   total_volume_shares=110000, eod_volume_shares=150000)
        for row in snapshot['distribution']:
            row.update(data_quality='SCOPED_VALIDATED', source_quality='SCOPED_VALIDATED')
        snapshot['capture'] = dict(
            code=fixtures.CODE, trade_date=fixtures.TRADE_DATE, endpoint='trades', source='FUGLE',
            snapshot_time='2026-08-21 13:35:00', page_count=2, provider_row_count=700,
            normalized_row_count=700, stored_row_count=700, capture_complete=1,
            data_quality='SESSION_COMPLETE', latest_cumulative_volume=110,
            latest_trade_time='13:30:00', reason='',
        )
        yield snapshot
    finally:
        case.doCleanups()


def render(snapshot, **kwargs):
    with patch.object(service, 'read_daily_market_microstructure', return_value=snapshot):
        canonical = service.build_canonical_close_batch_snapshot(fixtures.CODE, **kwargs)
    return canonical, app._canonical_price_volume_for_web(canonical)


def test_scoped_verified_bins_are_displayed_but_never_promoted(scoped_snapshot):
    original = copy.deepcopy(scoped_snapshot)
    canonical, web = render(scoped_snapshot)
    assert web['available'] is True
    assert web['display_available'] is True
    assert web['quality'] == 'scoped'
    assert web['trade_scope'] == 'regular_intraday'
    assert web['official_coverage_pct'] == pytest.approx(73.3333, abs=.0001)
    assert web['total_volume_lots'] == 110
    assert sum(row['volume_lots'] for row in web['profile_rows']) == 110
    assert max(row['bar_pct'] for row in web['profile_rows']) == 100
    assert web['analysis_eligible'] is False
    assert web['can_override_main_status'] is False
    assert canonical['data_quality']['decision_ready'] is False
    assert canonical['microstructure_status']['status'] == 'volume_mismatch'
    assert canonical['support_pressure']['available'] is False
    assert canonical['multi_day_score']['referee_eligible'] is False
    assert scoped_snapshot == original


@pytest.mark.parametrize('section,key,value', [
    ('profile', 'quality', 'unavailable'),
    ('profile', 'date', '2026-08-20'),
    ('profile', 'source_name', 'unknown'),
    ('profile', 'trade_scope', 'unknown'),
    ('profile', 'total_volume_shares', 50000),
    ('profile', 'eod_volume_shares', 160000),
    ('profile', 'total_volume_shares', float('nan')),
    ('history', 'volume', 100000),
    ('history', 'source_quality', 'SCRAPED'),
    ('capture', 'capture_complete', 0),
    ('capture', 'data_quality', 'PARTIAL'),
    ('capture', 'stored_row_count', 699),
    ('capture', 'provider_row_count', 0),
    ('capture', 'latest_cumulative_volume', 0),
    ('capture', 'latest_cumulative_volume', float('inf')),
    ('capture', 'latest_trade_time', ''),
    ('capture', 'trade_date', '2026-08-20'),
    ('capture', 'snapshot_time', '2026-08-21 13:36:00'),
    ('capture', 'source', 'unknown'),
    ('capture', 'endpoint', 'volumes'),
])
def test_invalid_scope_evidence_stays_hidden(scoped_snapshot, section, key, value):
    scoped_snapshot[section][key] = value
    _, web = render(scoped_snapshot)
    assert web['available'] is False


@pytest.mark.parametrize('key,value', [
    ('data_quality', 'PARTIAL'), ('source_quality', 'VALIDATED'), ('source', 'YAHOO'),
    ('snapshot_time', ''), ('snapshot_time', '2026-08-21 12:00:00'),
    ('snapshot_time', '2026-08-20 13:35:00'), ('volume_lots', 1000),
    ('price', float('nan')),
])
def test_mixed_or_invalid_bins_stay_hidden(scoped_snapshot, key, value):
    scoped_snapshot['distribution'][0][key] = value
    _, web = render(scoped_snapshot)
    assert web['available'] is False


def test_stale_scoped_snapshot_stays_hidden(scoped_snapshot):
    # Freshness is relative to the published close batch, not just calendar day.
    scoped_snapshot['publication_date'] = '2026-08-25'
    with patch.object(service, 'recent_market_date_for_eod', return_value='2026-08-25'):
        _, web = render(scoped_snapshot)
    assert web['available'] is False


def test_scoped_evidence_missing_capture_stays_hidden(scoped_snapshot):
    scoped_snapshot['capture'] = None
    _, web = render(scoped_snapshot)
    assert web['available'] is False


def test_delayed_complete_scoped_capture_can_display(scoped_snapshot):
    for row in scoped_snapshot['distribution']:
        row['snapshot_time'] = '2026-08-22 14:00:00'
    scoped_snapshot['capture']['snapshot_time'] = '2026-08-22 14:00:00'
    canonical, web = render(scoped_snapshot)
    assert web['available'] is True
    assert web['trade_date'] == fixtures.TRADE_DATE
    assert web['snapshot_time'] == '2026-08-22 14:00:00'
    assert canonical['data_quality']['decision_ready'] is False


def test_public_scope_uses_neutral_label(scoped_snapshot):
    scoped_snapshot['profile']['trade_scope'] = 'fugle_captured_session'
    _, web = render(scoped_snapshot)
    assert web['available'] is True
    assert web['trade_scope'] == 'captured_session'
    assert 'fugle' not in json.dumps(web, ensure_ascii=False).lower()


def test_display_permission_does_not_change_referee_or_support(scoped_snapshot):
    before, _ = render(scoped_snapshot)
    scoped_snapshot['capture']['capture_complete'] = 0
    after, _ = render(scoped_snapshot)
    for key in ('referee', 'support_pressure', 'microstructure_status', 'multi_day_score'):
        assert before[key] == after[key]


def test_display_metadata_does_not_change_line_fact_projection(scoped_snapshot):
    from services.line_bot_service import _compact_daily
    canonical, _ = render(scoped_snapshot)
    legacy = {k: v for k, v in canonical.items() if k != 'scoped_price_volume_display'}
    assert _compact_daily(canonical) == _compact_daily(legacy)


def test_existing_validated_display_and_analysis_are_preserved(scoped_snapshot):
    scoped_snapshot['history']['volume'] = 110000
    scoped_snapshot['profile'].update(quality='high', eod_volume_shares=110000)
    for row in scoped_snapshot['distribution']:
        row.update(data_quality='VALIDATED', source_quality='VALIDATED')
    canonical, web = render(scoped_snapshot)
    assert web['available'] is True
    assert web['quality'] == 'ok'
    assert canonical['data_quality']['decision_ready'] is True


def test_no_levels_requested_cannot_display(scoped_snapshot):
    _, web = render(scoped_snapshot, include_levels=False)
    assert web['available'] is False


def test_truncation_is_explicit_not_misreported_as_full_profile(scoped_snapshot):
    _, web = render(scoped_snapshot, level_limit=2)
    assert web['price_levels_truncated'] is True
    assert web['price_level_count'] == 5
    assert len(web['profile_rows']) == 2
    assert web['total_volume_lots'] == 110


def test_browser_renderer_shows_scoped_warning_and_exact_rows(scoped_snapshot):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable; renderer execution not verified')
    _, web = render(scoped_snapshot)
    page = (Path(__file__).resolve().parents[1] / 'review_src/static/detail.html').read_text(encoding='utf-8')
    renderer = page.split('function renderPriceVolume(pv){', 1)[1].split('function fmtKlinePrice', 1)[0]
    script = "const nodes={};const $=id=>nodes[id]||(nodes[id]={style:{}});const esc=x=>String(x);\n"
    script += 'function renderPriceVolume(pv){' + renderer
    script += '\nrenderPriceVolume(' + json.dumps(web) + ');console.log(nodes.priceVolume.innerHTML);'
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
    assert '限定交易範圍' in result.stdout
    assert '不參與評分' in result.stdout
    assert '73.33%' in result.stdout
    assert '110 張' in result.stdout
    assert result.stdout.count('class="volumeBar"') == 5
    assert 'width:100%' in result.stdout
    assert 'Fugle' not in result.stdout
