from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "review_src"))
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
import start_dashboard  # noqa: E402


def test_windows_launcher_forces_existing_data_safe_mode():
    launcher = (ROOT / "啟動台股分析系統.bat").read_text(encoding="utf-8")
    assert 'set "AUTO_REFRESH_MARKET_DATA_ON_START=0"' in launcher


def test_launcher_skips_preload_and_repair_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["start_dashboard.py"])
    args = start_dashboard.parse_args()
    assert args.preload is False
    assert args.no_preload is False


def test_startup_core_preserves_market_data_when_auto_refresh_is_disabled(monkeypatch, tmp_path):
    calls = []
    existing_db = tmp_path / "existing.db"
    existing_db.write_bytes(b"existing database placeholder")
    monkeypatch.setattr(app, "AUTO_REFRESH_MARKET_DATA_ON_START", False)
    monkeypatch.setattr(app, "DB_PATH", existing_db)
    monkeypatch.setattr(app, "assert_db_integrity", lambda: calls.append("integrity"))

    def unexpected(*args, **kwargs):
        raise AssertionError("automatic market-data work must stay disabled")

    monkeypatch.setattr(app, "init_db", unexpected)
    monkeypatch.setattr(app, "start_mis_quote_daemon", unexpected)
    monkeypatch.setattr(app, "set_status", unexpected)
    monkeypatch.setattr(app.threading, "Thread", unexpected)
    monkeypatch.setattr(app, "get_watchlist_codes", unexpected)

    app.startup_core()

    assert calls == ["integrity"]


def _readiness():
    return {
        "ready": False, "checked": 2, "pass_count": 1, "fail_count": 1,
        "failed_codes": ["6669"], "global_issues": [],
        "failed": [{"code": "6669", "name": "緯穎", "issues": ["K線資料過期"],
                    "analysis_status": {"reason_code": "official_daily_not_ready"}}],
    }


def test_6669_one_sided_support_resistance_disables_only_subitem_not_stock():
    code = "6669"
    result = app._support_resistance_readiness(
        {
            "decision_ready": False,
            "main_status": "資料部分可用",
            "reason_code": "partial_support_resistance",
            "support_zone": {"zone_low": 1800.0, "zone_high": 1850.0},
            "resistance_zone": None,
        },
        {"status": "insufficient_data", "complete": False},
    )
    assert result["ready_for_display"] is True
    assert result["partial"] is True
    assert result["detail"]["status"] == "partial"
    assert result["detail"]["component_coverage"] == {
        "support": True,
        "resistance": False,
    }
    hidden_codes: list[str] = []
    if not result["ready_for_display"]:
        hidden_codes.append(code)
    assert result["detail"]["reason_code"] == "partial_support_resistance"
    assert hidden_codes == []
    assert "不產生主結論" in result["warning"]["reason"]


@pytest.fixture
def quotes_boundary(monkeypatch):
    built = []
    items = [{"code": "2330", "name": "台積電"}, {"code": "6669", "name": "緯穎"}]
    monkeypatch.setattr(app, "read_components", lambda: deepcopy(items))
    monkeypatch.setattr(app, "list_watchlist_code_name_items", lambda: deepcopy(items[:1]))
    monkeypatch.setattr(app, "db", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(app, "latest_taiwan50_close_batch", lambda conn: None)
    monkeypatch.setattr(app, "latest_completed_tw50_close_date", lambda conn, items: "2026-08-28")
    monkeypatch.setattr(app, "get_status", lambda: {})

    def build(item, mode, *, persist_state, source_type):
        assert persist_state is False
        assert source_type == ("taiwan50_batch" if mode == "tw50" else "watchlist_realtime")
        built.append(item["code"])
        return {**item, "rsi5": 40, "rsi10": 45, "rsi14": 50,
                "rsi_data_quality": {"ready": True}}

    monkeypatch.setattr(app, "build_row", build)
    return built


def test_one_unavailable_analysis_cannot_hide_other_complete_rows(monkeypatch, quotes_boundary):
    readiness = _readiness()
    before = deepcopy(readiness)

    def audit(items, mode, *, enqueue_missing):
        assert enqueue_missing is False
        return readiness

    monkeypatch.setattr(app, "data_readiness_for_items", audit)
    result = app.api_quotes(mode="tw50")
    assert [row["code"] for row in result["rows"]] == ["2330"]
    assert quotes_boundary == ["2330"]
    assert result["display_ready"] is True
    assert result["ready"] is False  # Do not lie about whole-universe completeness.
    assert result["partial_ready"] is True
    assert result["hidden_codes"] == ["6669"]
    assert result["readiness"]["fail_count"] == 1
    assert readiness == before
    assert result["update_mode"] == "close_batch" and result["is_realtime"] is False
    assert result["data_date"] == "2026-08-28"


@pytest.mark.parametrize("failure", ["global", "all_failed", "unknown_code", "count_mismatch"])
def test_partial_display_cannot_bypass_global_or_inconsistent_checks(monkeypatch, quotes_boundary, failure):
    readiness = _readiness()
    if failure == "global":
        readiness["global_issues"] = ["volume unit not verified"]
    elif failure == "all_failed":
        readiness.update(pass_count=0, fail_count=2, failed_codes=["2330", "6669"])
    elif failure == "unknown_code":
        readiness["failed_codes"] = ["9999"]
    else:
        readiness["checked"] = 50
    monkeypatch.setattr(app, "data_readiness_for_items", lambda *args, **kwargs: readiness)
    result = app.api_quotes(mode="tw50")
    assert result["rows"] == []
    assert result["display_ready"] is False
    assert result["partial_ready"] is False
    assert quotes_boundary == []


def test_complete_tw50_and_watchlist_keep_original_rows(monkeypatch, quotes_boundary):
    readiness = _readiness()
    readiness.update(ready=True, pass_count=2, fail_count=0, failed_codes=[], failed=[])
    monkeypatch.setattr(app, "data_readiness_for_items", lambda *args, **kwargs: readiness)
    complete = app.api_quotes(mode="tw50")
    assert complete["ready"] is True and complete["display_ready"] is True
    assert len(complete["rows"]) == 2 and complete["hidden_codes"] == []
    watchlist = app.api_quotes(mode="watchlist")
    assert [row["code"] for row in watchlist["rows"]] == ["2330"]
    assert watchlist["readiness"] is None


def _partial_payload():
    return {"ready": False, "display_ready": True, "partial_ready": True,
            "rows": [{"code": "2330"}], "hidden_codes": ["6669"],
            "readiness": _readiness()}


def test_launcher_opens_valid_partial_list_without_starting_repair(monkeypatch):
    payload = _partial_payload()
    assert start_dashboard.dashboard_payload_ready(payload) is True
    calls = []

    def request(url, *, payload=None, timeout=120):
        calls.append((url, payload))
        assert payload is None, "A renderable partial list must not start data-repair POSTs"
        return {"statuses": {}} if url.endswith("/api/status") else _partial_payload()

    monkeypatch.setattr(start_dashboard, "_request_json", request)
    ready, summary = start_dashboard.wait_until_dashboard_data_ready("http://test.invalid", timeout_seconds=1)
    assert ready is True and summary["partial_ready"] is True
    assert len(calls) == 2
    assert summary["pass_count"] == 1 and summary["fail_count"] == 1


@pytest.mark.parametrize("failure", ["global", "unfiltered", "missing_flag", "empty", "count_mismatch"])
def test_launcher_does_not_accept_unsafe_partial_payload(failure):
    payload = _partial_payload()
    if failure == "global":
        payload["readiness"]["global_issues"] = ["no publication"]
    elif failure == "unfiltered":
        payload["rows"] = [{"code": "6669"}]
    elif failure == "missing_flag":
        payload.pop("display_ready")
    elif failure == "empty":
        payload["rows"] = []
    else:
        payload["readiness"]["checked"] = 50
    assert start_dashboard.dashboard_payload_ready(payload) is False


def test_browser_partial_branch_renders_rows_and_safe_notice():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the actual JavaScript branch test")
    html = (ROOT / "review_src/static/index.html").read_text(encoding="utf-8")
    function = html.split("function renderPayload(d, cachedAt){", 1)[1].split("\nasync function load(", 1)[0]
    source = "function renderPayload(d, cachedAt){" + function
    payload = _partial_payload()
    payload["message"] = "已顯示 1 檔可用分析；另 1 檔暫不顯示。"
    payload["readiness"]["failed"][0]["name"] = '<img src=x onerror="alert(1)">'
    driver = r'''
const vm = require('node:vm');
const input = JSON.parse(process.argv[1]);
function run(payload) {
  const nodes = {};
  const state = {rendered: null, blocked: false};
  const escape = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  const sandbox = {$: id => nodes[id] ??= {}, esc: escape, latestQuotesPayload:null,
    render: rows => state.rendered = rows, renderReadiness: () => state.blocked = true,
    updateMarketOverview:()=>{}, startPreloadPolling:()=>{}, stopPreloadPolling:()=>{},
    setTab:()=>{}, marketLabel:()=>'', Date};
  vm.runInNewContext(input.source, sandbox);
  sandbox.renderPayload(payload, null);
  return {...state, nodes};
}
console.log(JSON.stringify({partial:run(input.payload), blocked:run({...input.payload,display_ready:false,rows:[]})}));
'''
    result = subprocess.run([node, "-e", driver, json.dumps({"source": source, "payload": payload})],
                            capture_output=True, text=True, encoding="utf-8", check=True, timeout=15)
    evidence = json.loads(result.stdout)
    assert evidence["partial"]["rendered"] == [{"code": "2330"}]
    assert evidence["partial"]["blocked"] is False
    notice = evidence["partial"]["nodes"]["readinessNotice"]
    assert notice["hidden"] is False and "6669" in notice["innerHTML"]
    assert "&lt;img" in notice["innerHTML"]
    assert "<img" not in notice["innerHTML"]
    assert evidence["blocked"]["blocked"] is True
    assert 'id="readinessNotice"' in html
