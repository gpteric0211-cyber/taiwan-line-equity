from __future__ import annotations
import json
import pytest
from equity import public_endpoint as endpoint


class Response:
    def __init__(self, data=None, status=200):
        self.data = data or {}
        self.status_code = status
        self.content = json.dumps(self.data).encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def json(self):
        return self.data


@pytest.mark.parametrize(
    "value",
    [
        "http://host.example",
        "https://user:pass@host.example",
        "https://host.example/path",
        "https://host.example?x=1",
    ],
)
def test_public_origin_rejects_unsafe_or_ambiguous_values(value):
    with pytest.raises(ValueError):
        endpoint.public_origin(value)


def test_public_setup_and_private_api_must_be_blocked(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("/api/setup"):
            return Response({"setup_required": True})
        return Response(status=401 if url.endswith("/api/portfolio") else 200)

    monkeypatch.setattr(endpoint.requests, "get", get)
    with pytest.raises(RuntimeError, match="setup must be disabled"):
        endpoint.verify_public_access("https://example.invalid")


def test_failed_empty_webhook_test_does_not_replace_existing_url(monkeypatch, tmp_path):
    writes = []
    monkeypatch.setattr(
        endpoint.requests,
        "get",
        lambda *a, **k: Response({"endpoint": "https://old.example/line/webhook", "active": True}),
    )
    monkeypatch.setattr(endpoint.requests, "post", lambda *a, **k: Response({"success": False}))
    monkeypatch.setattr(endpoint.requests, "put", lambda *a, **k: writes.append(k))
    evidence = tmp_path / "before.json"
    with pytest.raises(RuntimeError, match="endpoint was not changed"):
        endpoint.sync_line_webhook("https://new.example", "synthetic-token", evidence)
    assert writes == []
    assert "synthetic-token" not in evidence.read_text()
    assert json.loads(evidence.read_text())["endpoint"].startswith("https://old.example")


def test_successful_webhook_is_read_back_and_never_sends_messages(monkeypatch, tmp_path):
    actions = []
    state = {"endpoint": "https://old.example/line/webhook", "active": True}

    def get(url, **kwargs):
        actions.append(("GET", url))
        return Response(dict(state))

    def post(url, **kwargs):
        actions.append(("POST", url))
        assert kwargs["json"] == {"endpoint": "https://new.example/line/webhook"}
        return Response({"success": True})

    def put(url, **kwargs):
        actions.append(("PUT", url))
        state.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(endpoint.requests, "get", get)
    monkeypatch.setattr(endpoint.requests, "post", post)
    monkeypatch.setattr(endpoint.requests, "put", put)
    result = endpoint.sync_line_webhook("https://new.example", "synthetic-token", tmp_path / "before.json")
    assert result["active"] and result["empty_webhook_test"]
    assert result["messages_sent"] == 0
    assert [a[0] for a in actions] == ["GET", "POST", "PUT", "GET"]
    assert all("/message/" not in a[1] for a in actions)


def test_quick_tunnel_url_cannot_match_attacker_suffix():
    assert endpoint.QUICK_URL.findall("https://nice-demo.trycloudflare.com/ ") == [
        "https://nice-demo.trycloudflare.com"
    ]
    assert not endpoint.QUICK_URL.findall("https://nice-demo.trycloudflare.com.attacker.example")


def test_optional_dns_probe_preserves_tls_hostname(monkeypatch):
    monkeypatch.setenv("EQUITY_DOH_URL", "https://resolver.example/dns-query")
    monkeypatch.setattr(
        endpoint.requests, "get", lambda *a, **k: Response({"Answer": [{"type": 1, "data": "1.1.1.1"}]})
    )
    seen = {}

    class Pool:
        def __init__(self, address, **kwargs):
            seen.update(address=address, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, path, **kwargs):
            seen.update(method=method, path=path, **kwargs)
            return type("Result", (), {"status": 200, "data": b"ok"})()

    monkeypatch.setattr(endpoint.urllib3, "HTTPSConnectionPool", Pool)
    assert endpoint._public_response("https://new.example/portfolio") == (200, b"ok")
    assert seen["server_hostname"] == seen["assert_hostname"] == "new.example"
    assert seen["headers"]["Host"] == "new.example"
    assert seen["ssl_context"].check_hostname
    assert seen["ssl_context"].verify_mode == endpoint.ssl.CERT_REQUIRED
