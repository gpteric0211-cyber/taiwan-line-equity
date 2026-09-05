from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import Mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import start_line_bot_stack as launcher  # noqa: E402


def test_existing_stack_requires_all_three_expected_health_payloads(monkeypatch) -> None:
    monkeypatch.delenv("QWEN_VISION_ENABLED", raising=False)
    payloads = {
        "http://127.0.0.1:8020/api/version": {"version": "0.32.15"},
        "http://127.0.0.1:8010/healthz": {"status": "ok", "mode": "read_only"},
        "http://127.0.0.1:8021/healthz": {"status": "ok", "ready": True},
    }
    monkeypatch.setattr(launcher, "_get_json", lambda url: payloads.get(url))

    assert launcher._existing_stack_is_healthy(8020, 8010, 8021) is True

    payloads["http://127.0.0.1:8021/healthz"] = {"status": "ok", "ready": False}
    assert launcher._existing_stack_is_healthy(8020, 8010, 8021) is False


def test_existing_stack_must_report_chart_support_when_vision_is_enabled(monkeypatch) -> None:
    monkeypatch.setenv("QWEN_VISION_ENABLED", "true")
    payloads = {
        "http://127.0.0.1:8020/api/version": {"version": "0.32.15"},
        "http://127.0.0.1:8010/healthz": {"status": "ok", "mode": "read_only"},
        "http://127.0.0.1:8021/healthz": {"status": "ok", "ready": True},
    }
    monkeypatch.setattr(launcher, "_get_json", lambda url: payloads.get(url))
    assert launcher._existing_stack_is_healthy(8020, 8010, 8021) is False

    payloads["http://127.0.0.1:8021/healthz"]["chart_image_analysis_enabled"] = True
    assert launcher._existing_stack_is_healthy(8020, 8010, 8021) is True


def test_instance_lock_rejects_second_launcher(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(launcher, "LOG_DIR", tmp_path)
    monkeypatch.setattr(launcher, "INSTANCE_LOCK", tmp_path / "line_bot_stack.lock")

    first = launcher._acquire_instance_lock()
    assert first is not None
    try:
        assert launcher._acquire_instance_lock() is None
    finally:
        first.close()


def test_descendant_process_ids_follow_only_the_owned_tree() -> None:
    parent_by_pid = {
        101: 100,
        102: 101,
        103: 999,
        104: 102,
        105: 105,
    }

    assert launcher._descendant_process_ids(100, parent_by_pid) == [101, 102, 104]


def test_stop_process_tree_cleans_descendants_after_parent_exit(monkeypatch) -> None:
    process = Mock(pid=7100, returncode=1)
    process.poll.return_value = 1
    taskkill = Mock()
    monkeypatch.setattr(launcher, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        launcher,
        "_windows_descendant_process_ids",
        lambda root_pid: [7200, 7300] if root_pid == 7100 else [],
    )
    monkeypatch.setattr(launcher.subprocess, "run", taskkill)

    launcher._stop_process_tree(process)

    assert [call.args[0][2] for call in taskkill.call_args_list] == ["7300", "7200"]
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_windows_descendant_lookup_fails_closed_when_snapshot_is_unavailable(
    monkeypatch,
) -> None:
    def unavailable() -> dict[int, int]:
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(launcher, "_windows_process_parent_map", unavailable)

    assert launcher._windows_descendant_process_ids(7100) == []


def test_generated_internal_token_is_persisted_without_duplicate_key(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.line_bot"
    env_file.write_text(
        "LINE_CHANNEL_SECRET=secret\nBOT_MARKET_DATA_TOKEN=\nQWEN_ENABLED=true\n",
        encoding="utf-8",
    )
    token = "a" * 43

    launcher._persist_generated_bot_token(env_file, token)

    persisted = env_file.read_text(encoding="utf-8")
    assert persisted.count("BOT_MARKET_DATA_TOKEN=") == 1
    assert f"BOT_MARKET_DATA_TOKEN={token}" in persisted
    assert "LINE_CHANNEL_SECRET=secret" in persisted
    assert "QWEN_ENABLED=true" in persisted


def test_generated_internal_token_rejects_short_secret(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.line_bot"

    try:
        launcher._persist_generated_bot_token(env_file, "too-short")
    except ValueError as exc:
        assert "too short" in str(exc)
    else:
        raise AssertionError("short generated token must be rejected")


def test_configure_line_webhook_sets_and_tests_same_endpoint(monkeypatch) -> None:
    put_response = Mock()
    post_response = Mock()
    post_response.json.return_value = {"success": True}
    put = Mock(return_value=put_response)
    post = Mock(return_value=post_response)
    get_response = Mock()
    get_response.json.return_value = {
        "endpoint": "https://example.trycloudflare.com/line/webhook",
        "active": True,
    }
    get = Mock(return_value=get_response)
    monkeypatch.setattr(launcher.requests, "put", put)
    monkeypatch.setattr(launcher.requests, "post", post)
    monkeypatch.setattr(launcher.requests, "get", get)

    public_url = "https://example.trycloudflare.com/line/webhook"
    launcher._configure_line_webhook(public_url, "secret-token")

    assert put.call_args.kwargs["json"] == {"endpoint": public_url}
    assert post.call_args.kwargs["json"] == {"endpoint": public_url}
    assert put.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-token"
    put_response.raise_for_status.assert_called_once_with()
    post_response.raise_for_status.assert_called_once_with()
    get_response.raise_for_status.assert_called_once_with()


def test_configure_line_webhook_rejects_failed_official_test(monkeypatch) -> None:
    put_response = Mock()
    post_response = Mock()
    post_response.json.return_value = {"success": False, "reason": "connection failed"}
    monkeypatch.setattr(launcher.requests, "put", Mock(return_value=put_response))
    monkeypatch.setattr(launcher.requests, "post", Mock(return_value=post_response))

    try:
        launcher._configure_line_webhook(
            "https://example.trycloudflare.com/line/webhook",
            "secret-token",
            max_attempts=1,
        )
    except RuntimeError as exc:
        assert "未通過" in str(exc)
    else:
        raise AssertionError("failed LINE webhook test must reject startup")


def test_legacy_tunnel_launcher_redirects_to_one_click_launcher() -> None:
    script = (PROJECT_ROOT / "維護工具" / "啟動Cloudflare臨時Tunnel.cmd").read_text(
        encoding="utf-8"
    )

    assert "scripts\\start_line_bot_stack.py" in script
    assert "cloudflared.exe" not in script


def test_line_child_receives_v2_shadow_and_context_configuration() -> None:
    assert "QWEN_CONTEXT_TOKENS" in launcher.LINE_ENV_KEYS
    assert "QWEN_MODEL_DIGEST" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_V2_ROLLOUT" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_V2_PROFILE" in launcher.LINE_ENV_KEYS
    assert "QWEN_TEXT_API_MODE" in launcher.LINE_ENV_KEYS
    assert "QWEN_NATIVE_BASE_URL" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_BACKGROUND_PREWARM" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_COLD_WARMUP_P95_MS" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_STARTUP_LOAD_TIMEOUT_SECONDS" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_COLD_PROBE_ENABLED" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_COLD_PROBE_TIMEOUT_SECONDS" in launcher.LINE_ENV_KEYS


def test_model_prewarm_is_requested_through_live_admission_controller(monkeypatch) -> None:
    response = Mock()
    response.json.return_value = {
        "status": "scheduled",
        "scheduled": True,
        "predicted_duration_ms": 70000,
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(launcher.requests, "post", post)
    log = StringIO()

    result = launcher._request_admission_visible_background_warmup(
        line_port=8021,
        internal_token="internal-secret",
        log_file=log,
    )

    assert result["scheduled"] is True
    assert post.call_args.args[0].endswith("/background-warmup")
    assert post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer internal-secret"
    }
    assert "status=scheduled model_kind=text admission_visible=true" in log.getvalue()
