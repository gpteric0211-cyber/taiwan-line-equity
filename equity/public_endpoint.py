"""Optional Quick Tunnel with tested LINE webhook configuration (no messages)."""

from __future__ import annotations
import ipaddress
import ssl
import urllib3
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
import requests
from equity import ROOT
from equity.processes import spawn, stop

LINE_API = "https://api.line.me/v2/bot/channel/webhook"
QUICK_URL = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?![a-z0-9.-])")


def public_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Public URL must be an HTTPS origin without credentials or a path")
    return value.rstrip("/")


def _public_response(url: str) -> tuple[int, bytes]:
    resolver = os.getenv("EQUITY_DOH_URL", "").strip()
    if not resolver:
        response = requests.get(url, timeout=12, allow_redirects=False)
        return response.status_code, response.content
    if urlsplit(resolver).scheme != "https":
        raise ValueError("EQUITY_DOH_URL must use HTTPS")
    parsed = urlsplit(url)
    answer = requests.get(
        resolver,
        params={"name": parsed.hostname, "type": "A"},
        headers={"Accept": "application/dns-json"},
        timeout=12,
    )
    answer.raise_for_status()
    addresses = [row.get("data", "") for row in answer.json().get("Answer", []) if row.get("type") == 1]
    addresses = [value for value in addresses if ipaddress.ip_address(value).is_global]
    if not addresses:
        raise RuntimeError("Configured DNS resolver returned no public IPv4 address")
    # Resolve only this probe. Keep TLS SNI, certificate hostname verification and
    # the HTTP Host header tied to the original domain; never change system DNS.
    with urllib3.HTTPSConnectionPool(
        addresses[0],
        port=parsed.port or 443,
        server_hostname=parsed.hostname,
        assert_hostname=parsed.hostname,
        ssl_context=ssl.create_default_context(),
        timeout=12,
    ) as pool:
        response = pool.request("GET", parsed.path or "/", headers={"Host": parsed.netloc}, redirect=False)
        return response.status, response.data


def verify_public_access(origin: str) -> dict:
    origin = public_origin(origin)
    status = {}
    for path, expected in (("/portfolio", 200), ("/api/portfolio", 401)):
        code, _ = _public_response(origin + path)
        status[path] = code
        if code != expected:
            raise RuntimeError(f"Public access check failed for {path}: {code}")
    code, body = _public_response(origin + "/api/setup")
    if code != 200 or json.loads(body).get("setup_required") is not False:
        raise RuntimeError("Public first-account setup must be disabled")
    status["remote_setup_allowed"] = False
    return status


def sync_line_webhook(origin: str, token: str, evidence: Path) -> dict:
    if not token:
        raise ValueError("LINE channel access token is missing")
    endpoint = public_origin(origin) + "/line/webhook"
    headers = {"Authorization": "Bearer " + token}
    previous = requests.get(LINE_API + "/endpoint", headers=headers, timeout=20)
    previous.raise_for_status()
    state = previous.json()
    # Save the previous URL before mutation so an operator can restore it.
    evidence.parent.mkdir(parents=True, exist_ok=True)
    if not evidence.exists():
        evidence.write_text(
            json.dumps({"endpoint": state.get("endpoint"), "active": state.get("active")}, indent=2),
            encoding="utf-8",
        )
    tested = requests.post(LINE_API + "/test", headers=headers, json={"endpoint": endpoint}, timeout=30)
    tested.raise_for_status()
    if tested.json().get("success") is not True:
        raise RuntimeError("LINE empty-webhook test failed; endpoint was not changed")
    if state.get("endpoint") != endpoint:
        changed = requests.put(
            LINE_API + "/endpoint", headers=headers, json={"endpoint": endpoint}, timeout=20
        )
        changed.raise_for_status()
    current = requests.get(LINE_API + "/endpoint", headers=headers, timeout=20)
    current.raise_for_status()
    current = current.json()
    if current.get("endpoint") != endpoint:
        raise RuntimeError("LINE endpoint read-back did not match")
    return {
        "endpoint": endpoint,
        "active": bool(current.get("active")),
        "empty_webhook_test": True,
        "messages_sent": 0,
    }


def cloudflared_executable() -> str:
    configured = os.getenv("CLOUDFLARED_EXE_PATH", "").strip()
    name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    if configured:
        candidate = Path(configured).expanduser()
        candidate = candidate if candidate.is_absolute() else ROOT / candidate
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(configured)
    else:
        candidate = ROOT / "runtime" / "cloudflared" / name
        found = str(candidate) if candidate.is_file() else shutil.which(name)
    if not found:
        raise RuntimeError("Install cloudflared for this platform or configure CLOUDFLARED_EXE_PATH")
    return found


def main(port: int) -> int:
    location = ROOT / "var/services"
    location.mkdir(parents=True, exist_ok=True)
    report_path = location / "public-endpoint.json"
    state = {"status": "starting", "mode": "quick", "messages_sent": 0}

    def save():
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(report_path)

    save()
    log_path = location / "cloudflared.log"
    with log_path.open("w", encoding="utf-8") as log:
        child = spawn(
            [cloudflared_executable(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("Quick Tunnel exited; inspect var/services/cloudflared.log")
                matches = QUICK_URL.findall(log_path.read_text(encoding="utf-8", errors="replace"))
                if matches:
                    state["origin"] = matches[-1]
                    try:
                        state["access_checks"] = verify_public_access(state["origin"])
                        break
                    except (
                        requests.RequestException,
                        urllib3.exceptions.HTTPError,
                        RuntimeError,
                        ValueError,
                    ):
                        pass
                time.sleep(2)
            else:
                raise RuntimeError("Quick Tunnel did not pass public access checks within 180 seconds")
            if os.getenv("EQUITY_LINE_WEBHOOK_SYNC", "0").lower() in {"1", "true", "on"}:
                state["line"] = sync_line_webhook(
                    state["origin"],
                    os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""),
                    location / "line-webhook-before.json",
                )
            state.update(status="ready", mobile_url=state["origin"] + "/portfolio")
            save()
            print(json.dumps(state, ensure_ascii=False), flush=True)
            while child.poll() is None:
                time.sleep(2)
            raise RuntimeError("Quick Tunnel stopped; restart the service")
        except KeyboardInterrupt:
            return 0
        finally:
            stop(child)
            state["status"] = "stopped"
            save()
