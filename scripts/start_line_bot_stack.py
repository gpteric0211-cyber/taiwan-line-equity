from __future__ import annotations

import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import BinaryIO, TextIO
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

try:
    import truststore  # type: ignore
except Exception:
    truststore = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
PRIVATE_ENV = PROJECT_ROOT / ".env.line_bot"
LEGACY_PRIVATE_ENV = REVIEW_SRC / ".env.line_bot"
LOG_DIR = PROJECT_ROOT / "logs" / "line_bot"
RUNTIME_STATE = LOG_DIR / "runtime_state.json"
INSTANCE_LOCK = LOG_DIR / "line_bot_stack.lock"
TRY_CLOUDFLARE_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
LINE_WEBHOOK_ENDPOINT_API = "https://api.line.me/v2/bot/channel/webhook/endpoint"
LINE_WEBHOOK_TEST_API = "https://api.line.me/v2/bot/channel/webhook/test"
_IS_WINDOWS = os.name == "nt"
_WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BASE_CHILD_ENV_KEYS = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMSPEC",
    "CUDA_VISIBLE_DEVICES",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "NVIDIA_VISIBLE_DEVICES",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}

LINE_ENV_KEYS = {
    "LINE_CHANNEL_SECRET",
    "LINE_CHANNEL_ACCESS_TOKEN",
    "LINE_VERIFY_SIGNATURE",
    "LINE_ALLOWED_USER_IDS",
    "LINE_REPLY_TIMEOUT_SECONDS",
    "LINE_CONTENT_TIMEOUT_SECONDS",
    "LINE_MAX_IMAGE_BYTES",
    "LINE_TOTAL_REPLY_BUDGET_SECONDS",
    "LINE_CONVERSATION_TTL_SECONDS",
    "LINE_CONVERSATION_MAX_SESSIONS",
    "LINE_CONVERSATION_MAX_TURNS",
    "LINE_CONVERSATION_MAX_USER_CHARS",
    "LINE_MEMORY_STORAGE",
    "LINE_MEMORY_DB_PATH",
    "LINE_MEMORY_KEY_FILE",
    "LINE_MEMORY_CHANNEL_NAMESPACE",
    "LINE_MEMORY_RECENT_EXCHANGES",
    "LINE_MEMORY_PROMPT_CHARACTER_BUDGET",
    "LINE_MEMORY_COMPACTION_TRIGGER",
    "LINE_MEMORY_COMPACTION_BATCH_SIZE",
    "LINE_MEMORY_COMPACTION_TIMEOUT_SECONDS",
    "LINE_MEMORY_RAW_RETENTION_SECONDS",
    "LINE_MEMORY_SUMMARY_RETENTION_SECONDS",
    "LINE_MEMORY_PRIVACY_NOTICE_VERSION",
    "LINE_MEMORY_LONG_TERM_APPROVED",
    "BOT_MARKET_DATA_BASE_URL",
    "BOT_MARKET_DATA_TOKEN",
    "BOT_MARKET_DATA_TIMEOUT_SECONDS",
    "QWEN_ENABLED",
    "QWEN_BASE_URL",
    "QWEN_NATIVE_BASE_URL",
    "QWEN_API_KEY",
    "QWEN_MODEL_ID",
    "QWEN_MODEL_DIGEST",
    "QWEN_MEMORY_MODEL_ID",
    "QWEN_MODEL_FILE",
    "QWEN_CONTEXT_TOKENS",
    "QWEN_MAX_OUTPUT_TOKENS",
    "QWEN_TEXT_API_MODE",
    "QWEN_NUM_BATCH",
    "QWEN_KEEP_ALIVE",
    "LINE_MODEL_V2_ROLLOUT",
    "LINE_MODEL_V2_PROFILE",
    "LINE_MODEL_BENCHMARK_ENABLED",
    "LINE_MODEL_BENCHMARK_TIMEOUT_SECONDS",
    "LINE_MODEL_BACKGROUND_PREWARM",
    "LINE_MODEL_COLD_WARMUP_P95_MS",
    "LINE_MODEL_STARTUP_LOAD_TIMEOUT_SECONDS",
    "LINE_MODEL_COLD_PROBE_ENABLED",
    "LINE_MODEL_COLD_PROBE_TIMEOUT_SECONDS",
    "LINE_MODEL_INTERACTIVE_P95_MS",
    "LINE_MODEL_PREEMPTION_P95_MS",
    "LINE_REPLY_TELEMETRY_ENABLED",
    "LINE_REPLY_TELEMETRY_PATH",
    "QWEN_ENABLE_THINKING",
    "QWEN_TEMPERATURE",
    "QWEN_TOP_P",
    "QWEN_TIMEOUT_SECONDS",
    "QWEN_VISION_ENABLED",
    "QWEN_VISION_BASE_URL",
    "QWEN_VISION_MODEL_ID",
    "QWEN_VISION_TIMEOUT_SECONDS",
    "QWEN_VISION_MAX_OUTPUT_TOKENS",
    "QWEN_VISION_TEMPERATURE",
    "QWEN_VISION_KEEP_ALIVE",
    "LINE_BOT_READ_ONLY",
    "LINE_BOT_ALLOW_TRADING",
    "LINE_BOT_REQUIRE_DECISION_READY",
    "LINE_BOT_ALLOW_MODEL_RECALCULATION",
    "LINE_BOT_LOG_RAW_MARKET_PAYLOAD",
    "LINE_BOT_LOG_SECRETS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
}

MARKET_ENV_KEYS = {
    "BOT_MARKET_DATA_TOKEN",
    "BOT_ENABLE_UNVERIFIED_TRADES",
    "TAIWAN50_DB_PATH",
    "APP_ENV",
}


def _text(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _private_env_file() -> Path:
    if PRIVATE_ENV.is_file() or not LEGACY_PRIVATE_ENV.is_file():
        return PRIVATE_ENV
    return LEGACY_PRIVATE_ENV


def _persist_generated_bot_token(env_file: Path, token: str) -> None:
    """Persist the generated internal token without ever printing it."""

    normalized = str(token or "").strip()
    if len(normalized) < 32:
        raise ValueError("generated internal token is too short")
    original = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
    lines = original.splitlines()
    replacement = f"BOT_MARKET_DATA_TOKEN={normalized}"
    replaced = False
    updated: list[str] = []
    for line in lines:
        if line.startswith("BOT_MARKET_DATA_TOKEN="):
            updated.append(replacement)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        if updated and updated[-1]:
            updated.append("")
        updated.append(replacement)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{env_file.name}.",
            suffix=".tmp",
            dir=env_file.parent,
            delete=False,
        ) as handle:
            handle.write("\n".join(updated) + "\n")
            temporary_path = Path(handle.name)
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        temporary_path.replace(env_file)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _port(name: str, default: int) -> int:
    try:
        value = int(_text(name, str(default)))
    except ValueError:
        value = default
    return max(1024, min(value, 65535))


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _child_environment(*allowed_names: str) -> dict[str, str]:
    names = {name.upper() for name in BASE_CHILD_ENV_KEYS | set(allowed_names)}
    child = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in names
    }
    child["PYTHONUTF8"] = "1"
    child["PYTHONIOENCODING"] = "utf-8"
    return child


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _acquire_instance_lock() -> BinaryIO | None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handle = INSTANCE_LOCK.open("a+b")
    if handle.tell() == 0 and handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - Windows deployment is primary
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return None
    return handle


def _get_json(url: str, *, timeout: int = 5) -> dict[str, object] | None:
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (requests.RequestException, ValueError):
        return None


def _existing_stack_is_healthy(qwen_port: int, market_port: int, line_port: int) -> bool:
    qwen = _get_json(f"http://127.0.0.1:{qwen_port}/api/version")
    market = _get_json(f"http://127.0.0.1:{market_port}/healthz")
    line = _get_json(f"http://127.0.0.1:{line_port}/healthz")
    vision_expected = _text("QWEN_VISION_ENABLED", "false").lower() in {
        "1", "true", "yes", "on"
    }
    return bool(
        qwen
        and qwen.get("version")
        and market
        and market.get("status") == "ok"
        and market.get("mode") == "read_only"
        and line
        and line.get("status") == "ok"
        and line.get("ready") is True
        and (not vision_expected or line.get("chart_image_analysis_enabled") is True)
    )


def _wait_health(
    url: str,
    process: subprocess.Popen[bytes],
    timeout: int,
    *,
    headers: dict[str, str] | None = None,
    required_json: dict[str, object] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"服務提前結束，exit_code={process.returncode}")
        try:
            response = requests.get(url, headers=headers, timeout=2)
            if response.status_code == 200:
                if required_json:
                    payload = response.json()
                    if not all(payload.get(key) == value for key, value in required_json.items()):
                        time.sleep(1)
                        continue
                time.sleep(0.2)
                if process.poll() is None:
                    return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError(f"服務在 {timeout} 秒內未就緒：{url}")


def _start(
    name: str,
    command: list[str],
    log_file: TextIO,
    *,
    process_env: dict[str, str],
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    print(f"啟動 {name}...")
    creationflags = _WINDOWS_NO_WINDOW if os.name == "nt" else 0
    process_env = dict(process_env)
    if extra_env:
        process_env.update(extra_env)
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=process_env,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def _descendant_process_ids(
    root_pid: int,
    parent_by_pid: dict[int, int],
) -> list[int]:
    """Return the known descendants of ``root_pid`` in parent-first order."""

    children_by_parent: dict[int, list[int]] = {}
    for pid, parent_pid in parent_by_pid.items():
        if pid <= 0 or pid == root_pid:
            continue
        children_by_parent.setdefault(parent_pid, []).append(pid)

    descendants: list[int] = []
    seen = {root_pid}
    pending = list(children_by_parent.get(root_pid, ()))
    while pending:
        pid = pending.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        descendants.append(pid)
        pending.extend(children_by_parent.get(pid, ()))
    return descendants


def _windows_process_parent_map() -> dict[int, int]:
    """Read the Windows process parent map without PowerShell or WMI."""

    if not _IS_WINDOWS:
        return {}

    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot in {None, invalid_handle}:
        return {}

    parent_by_pid: dict[int, int] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        has_entry = bool(process_first(snapshot, ctypes.byref(entry)))
        while has_entry:
            parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            has_entry = bool(process_next(snapshot, ctypes.byref(entry)))
    finally:
        close_handle(snapshot)
    return parent_by_pid


def _windows_descendant_process_ids(root_pid: int) -> list[int]:
    try:
        parent_by_pid = _windows_process_parent_map()
    except (AttributeError, OSError, ValueError):
        # Cleanup remains best-effort if Windows process enumeration is unavailable.
        parent_by_pid = {}
    return _descendant_process_ids(root_pid, parent_by_pid)


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    is_running = process.poll() is None
    if _IS_WINDOWS:
        descendant_pids = _windows_descendant_process_ids(process.pid)
        target_pids = ([process.pid] if is_running else []) + list(
            reversed(descendant_pids)
        )
        for target_pid in target_pids:
            subprocess.run(
                ["taskkill", "/PID", str(target_pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_WINDOWS_NO_WINDOW,
                check=False,
            )
        if not is_running:
            return
    else:
        if not is_running:
            return
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _start_quick_tunnel(
    cloudflared_exe: Path,
    line_port: int,
    log_file: TextIO,
    *,
    process_env: dict[str, str],
    timeout: int = 60,
) -> tuple[subprocess.Popen[str], str]:
    print("啟動 Cloudflare 臨時 Tunnel...")
    creationflags = _WINDOWS_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [
            str(cloudflared_exe),
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"http://127.0.0.1:{line_port}",
        ],
        cwd=PROJECT_ROOT,
        env=process_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    output: queue.Queue[str | None] = queue.Queue()

    def consume_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            log_file.write(line)
            log_file.flush()
            output.put(line)
        output.put(None)

    threading.Thread(target=consume_output, daemon=True).start()
    deadline = time.monotonic() + timeout
    public_url = ""
    connection_registered = False
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Cloudflare Tunnel 提前結束，exit_code={process.returncode}")
        try:
            line = output.get(timeout=1)
        except queue.Empty:
            continue
        if line is None:
            continue
        match = TRY_CLOUDFLARE_URL.search(line)
        if match:
            public_url = f"{match.group(0).rstrip('/')}/line/webhook"
        if "Registered tunnel connection" in line:
            connection_registered = True
        if public_url and connection_registered:
            return process, public_url
    _stop_process_tree(process)
    raise RuntimeError(f"Cloudflare Tunnel 在 {timeout} 秒內未產生公開網址")


def _configure_line_webhook(
    public_url: str,
    channel_access_token: str,
    *,
    max_attempts: int = 24,
    retry_seconds: int = 5,
) -> None:
    headers = {
        "Authorization": f"Bearer {channel_access_token}",
        "Content-Type": "application/json",
    }
    last_error = ""
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            response = requests.put(
                LINE_WEBHOOK_ENDPOINT_API,
                headers=headers,
                json={"endpoint": public_url},
                timeout=20,
            )
            response.raise_for_status()
            test_response = requests.post(
                LINE_WEBHOOK_TEST_API,
                headers=headers,
                json={"endpoint": public_url},
                timeout=30,
            )
            test_response.raise_for_status()
            payload = test_response.json()
            if not isinstance(payload, dict) or payload.get("success") is not True:
                last_error = "LINE 官方測試尚未成功"
            else:
                info_response = requests.get(
                    LINE_WEBHOOK_ENDPOINT_API,
                    headers=headers,
                    timeout=20,
                )
                info_response.raise_for_status()
                info = info_response.json()
                if (
                    isinstance(info, dict)
                    and info.get("endpoint") == public_url
                    and info.get("active") is True
                ):
                    return
                last_error = "Webhook 尚未啟用或 LINE 仍在套用新網址"
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in {401, 403}:
                raise RuntimeError("LINE Channel access token 無效或沒有 Webhook 設定權限") from exc
            last_error = f"LINE API HTTP {status or 'error'}"
        except (requests.RequestException, ValueError) as exc:
            last_error = type(exc).__name__
        if attempt < max_attempts:
            time.sleep(max(1, retry_seconds))
    raise RuntimeError(
        f"LINE 官方 Webhook 在 {max_attempts} 次嘗試後仍未通過（{last_error}）"
    )


def _write_runtime_state(
    *,
    public_url: str,
    reused_local_stack: bool,
    processes: list[subprocess.Popen[object]],
) -> None:
    state = {
        "public_webhook_url": public_url,
        "reused_local_stack": reused_local_stack,
        "process_ids": [process.pid for process in processes],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    RUNTIME_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ollama_has_model(base_url: str, model_id: str) -> bool:
    response = requests.get(f"{base_url}/api/tags", timeout=10)
    response.raise_for_status()
    requested = model_id.removesuffix(":latest")
    for item in response.json().get("models", []):
        name = str(item.get("name") or item.get("model") or "")
        if name == model_id or name.removesuffix(":latest") == requested:
            return True
    return False


def _request_admission_visible_background_warmup(
    *,
    line_port: int,
    internal_token: str,
    log_file: TextIO,
) -> dict[str, object]:
    """Queue cold text loading inside the live admission controller."""

    response = requests.post(
        f"http://127.0.0.1:{line_port}/internal/line-model-benchmark/background-warmup",
        headers={"Authorization": f"Bearer {internal_token}"},
        json={},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("background warmup endpoint returned invalid JSON")
    log_file.write(
        "startup_prewarm status="
        f"{payload.get('status') or 'unknown'} model_kind=text admission_visible=true\n"
    )
    log_file.flush()
    return payload


def _missing_configuration() -> list[str]:
    missing: list[str] = []
    if not _text("LINE_CHANNEL_SECRET"):
        missing.append("LINE_CHANNEL_SECRET")
    if not _text("LINE_CHANNEL_ACCESS_TOKEN"):
        missing.append("LINE_CHANNEL_ACCESS_TOKEN")
    return missing


def _configuration_errors() -> list[str]:
    errors: list[str] = []
    public_url = _text("LINE_WEBHOOK_PUBLIC_URL")
    if public_url:
        parsed = urlparse(public_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not parsed.path.rstrip("/").endswith("/line/webhook")
        ):
            errors.append("LINE_WEBHOOK_PUBLIC_URL 必須是以 /line/webhook 結尾的公開 HTTPS URL")
    required_true = {
        "LINE_VERIFY_SIGNATURE": "LINE 簽章驗證必須啟用",
        "LINE_BOT_READ_ONLY": "LINE Bot 必須維持唯讀模式",
        "LINE_BOT_REQUIRE_DECISION_READY": "資料品質 decision-ready 門檻必須啟用",
    }
    required_false = {
        "LINE_BOT_ALLOW_TRADING": "LINE Bot 不得啟用交易功能",
        "LINE_BOT_ALLOW_MODEL_RECALCULATION": "Qwen 不得自行重算金融數值",
        "LINE_BOT_LOG_RAW_MARKET_PAYLOAD": "不得記錄原始市場 payload",
        "LINE_BOT_LOG_SECRETS": "不得記錄密鑰",
    }
    for name, message in required_true.items():
        if _text(name, "true").lower() not in {"1", "true", "yes", "on"}:
            errors.append(f"{message}（{name}=true）")
    for name, message in required_false.items():
        if _text(name, "false").lower() in {"1", "true", "yes", "on"}:
            errors.append(f"{message}（{name}=false）")
    return errors


def _load_configuration() -> None:
    # Import only the side-effect-free resolver, never dashboard DB bootstrap.
    if str(REVIEW_SRC) not in sys.path:
        sys.path.insert(0, str(REVIEW_SRC))
    from core.market_database_config import MARKET_DB_ENV, resolve_market_db_path

    market_db = resolve_market_db_path(base_dir=REVIEW_SRC, private_env_file=_private_env_file())
    explicit_environment = dict(os.environ)
    load_dotenv(REVIEW_SRC / ".env", override=False)
    load_dotenv(_private_env_file(), override=True)
    os.environ.update(explicit_environment)
    os.environ[MARKET_DB_ENV] = str(market_db)


def _enable_system_truststore() -> None:
    if truststore is None:
        raise RuntimeError("缺少 truststore，無法使用 Windows 系統憑證安全連線 LINE API")
    truststore.inject_into_ssl()


def main() -> int:
    _load_configuration()
    _enable_system_truststore()
    private_env = _private_env_file()
    missing = _missing_configuration()
    if missing:
        print(f"請先填寫：{private_env}")
        for item in missing:
            print(f"- {item}")
        return 2
    errors = _configuration_errors()
    if errors:
        print(f"設定錯誤：{private_env}")
        for item in errors:
            print(f"- {item}")
        return 2
    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        print("LINE 股票機器人一鍵啟動器已在執行；不會重複建立服務或 Tunnel。")
        return 0
    python = REVIEW_SRC / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)
    ollama_exe = _project_path(
        _text("OLLAMA_EXE_PATH", "runtime/ollama/ollama.exe")
    )
    ollama_models = _project_path(
        _text("OLLAMA_MODELS_PATH", "models/ollama")
    )
    if not ollama_exe.is_file():
        print(f"找不到 Ollama：{ollama_exe}")
        return 3
    ollama_models.mkdir(parents=True, exist_ok=True)

    qwen_port = _port("QWEN_SERVER_PORT", 8020)
    market_port = _port("BOT_MARKET_DATA_PORT", 8010)
    line_port = _port("LINE_GATEWAY_PORT", 8021)
    configured_ports = {
        "Qwen": qwen_port,
        "唯讀股票 API": market_port,
        "LINE Gateway": line_port,
    }
    if len(set(configured_ports.values())) != len(configured_ports):
        print("啟動失敗：QWEN_SERVER_PORT、BOT_MARKET_DATA_PORT、LINE_GATEWAY_PORT 必須互不相同。")
        return 3
    occupied_names = {
        name for name, port in configured_ports.items() if not _port_is_available(port)
    }
    reused_local_stack = False
    if occupied_names:
        all_occupied = len(occupied_names) == len(configured_ports)
        if all_occupied and _existing_stack_is_healthy(qwen_port, market_port, line_port):
            reused_local_stack = True
            print("偵測到上一組 LINE 股票機器人本機服務仍健康，將直接沿用。")
        else:
            print("啟動失敗：下列本機連接埠被占用，但不是完整且健康的 LINE 股票機器人：")
            for name, port in configured_ports.items():
                if name in occupied_names:
                    print(f"- {name}={port}")
            print("為避免誤關其他程式，啟動器不會自動終止未知程序。")
            return 3
    if not reused_local_stack and len(_text("BOT_MARKET_DATA_TOKEN")) < 32:
        generated_token = secrets.token_urlsafe(32)
        _persist_generated_bot_token(private_env, generated_token)
        os.environ["BOT_MARKET_DATA_TOKEN"] = generated_token
        print("已為本次服務自動產生並保存內部唯讀 API token（不寫入 log）。")
    try:
        context_size = int(_text("QWEN_CONTEXT_TOKENS", "16384"))
    except ValueError:
        context_size = 16384
    context_size = max(4096, min(context_size, 32768))
    try:
        parallel_slots = int(_text("QWEN_PARALLEL", "1"))
    except ValueError:
        parallel_slots = 1
    parallel_slots = max(1, min(parallel_slots, 2))
    model_id = _text("QWEN_MODEL_ID", "taiwan-stock-qwen")
    keep_alive = _text("QWEN_KEEP_ALIVE", "-1")
    vision_enabled = _text("QWEN_VISION_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    vision_model_id = _text("QWEN_VISION_MODEL_ID", "qwen3-vl:8b-instruct")
    vision_keep_alive = _text("QWEN_VISION_KEEP_ALIVE", "-1")
    ollama_base_url = f"http://127.0.0.1:{qwen_port}"
    os.environ["QWEN_BASE_URL"] = f"{ollama_base_url}/v1"
    os.environ["QWEN_VISION_BASE_URL"] = ollama_base_url
    os.environ["BOT_MARKET_DATA_BASE_URL"] = f"http://127.0.0.1:{market_port}"
    ollama_env = {
        "OLLAMA_HOST": f"127.0.0.1:{qwen_port}",
        "OLLAMA_MODELS": str(ollama_models),
        "OLLAMA_CONTEXT_LENGTH": str(context_size),
        "OLLAMA_NUM_PARALLEL": str(parallel_slots),
        "OLLAMA_MAX_LOADED_MODELS": _text(
            "OLLAMA_MAX_LOADED_MODELS", "2" if vision_enabled else "1"
        ),
        "OLLAMA_KEEP_ALIVE": keep_alive,
        "OLLAMA_LOAD_TIMEOUT": "20m",
        "OLLAMA_NO_CLOUD": "true",
        "OLLAMA_NOHISTORY": "true",
    }
    vulkan_setting = _text("OLLAMA_VULKAN").lower()
    if vulkan_setting in {"true", "false"}:
        ollama_env["OLLAMA_VULKAN"] = vulkan_setting
    ollama_process_env = _child_environment()
    market_process_env = _child_environment(*MARKET_ENV_KEYS)
    line_process_env = _child_environment(*LINE_ENV_KEYS)

    cloudflared_exe = _project_path(
        _text("CLOUDFLARED_EXE_PATH", "runtime/cloudflared/cloudflared.exe")
    )
    if not cloudflared_exe.is_file():
        print(f"找不到 Cloudflare Tunnel：{cloudflared_exe}")
        return 3

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_handles: list[TextIO] = []
    managed_processes: list[subprocess.Popen[object]] = []
    try:
        qwen_log = (LOG_DIR / "qwen_server.log").open("a", encoding="utf-8")
        market_log = (LOG_DIR / "market_data_api.log").open("a", encoding="utf-8")
        line_log = (LOG_DIR / "line_gateway.log").open("a", encoding="utf-8")
        tunnel_log = (LOG_DIR / "cloudflared.log").open("a", encoding="utf-8")
        log_handles.extend((qwen_log, market_log, line_log, tunnel_log))

        if not reused_local_stack:
            qwen = _start(
                "Qwen（Ollama）",
                [str(ollama_exe), "serve"],
                qwen_log,
                process_env=ollama_process_env,
                extra_env=ollama_env,
            )
            managed_processes.append(qwen)
            _wait_health(f"{ollama_base_url}/api/version", qwen, 240)
            if not _ollama_has_model(ollama_base_url, model_id):
                raise RuntimeError(
                    f"Ollama 尚未註冊模型 {model_id}；請依部署文件完成一次模型匯入"
                )
            if vision_enabled:
                if not vision_model_id:
                    raise RuntimeError("已啟用圖片辨識，但 QWEN_VISION_MODEL_ID 未設定")
                if not _ollama_has_model(ollama_base_url, vision_model_id):
                    raise RuntimeError(
                        f"Ollama 尚未註冊視覺模型 {vision_model_id}；請先完成模型下載"
                    )

            market = _start(
                "唯讀股票 API",
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "bot_app:app",
                    "--app-dir",
                    str(REVIEW_SRC),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(market_port),
                    "--workers",
                    "1",
                ],
                market_log,
                process_env=market_process_env,
            )
            managed_processes.append(market)
            _wait_health(
                f"http://127.0.0.1:{market_port}/api/bot/market-data/readyz",
                market,
                45,
                headers={"Authorization": f"Bearer {_text('BOT_MARKET_DATA_TOKEN')}"},
                required_json={"status": "ok", "ready": True, "mode": "read_only"},
            )

            line = _start(
                "LINE Webhook Gateway",
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "line_bot_app:app",
                    "--app-dir",
                    str(REVIEW_SRC),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(line_port),
                    "--workers",
                    "1",
                ],
                line_log,
                process_env=line_process_env,
            )
            managed_processes.append(line)
            _wait_health(
                f"http://127.0.0.1:{line_port}/healthz",
                line,
                45,
                required_json={"status": "ok", "ready": True},
            )

        tunnel, public_url = _start_quick_tunnel(
            cloudflared_exe,
            line_port,
            tunnel_log,
            process_env=_child_environment(),
        )
        managed_processes.append(tunnel)
        print("正在自動更新並驗證 LINE Webhook...")
        _configure_line_webhook(public_url, _text("LINE_CHANNEL_ACCESS_TOKEN"))
        _write_runtime_state(
            public_url=public_url,
            reused_local_stack=reused_local_stack,
            processes=managed_processes,
        )
        background_prewarm_enabled = _text(
            "LINE_MODEL_BACKGROUND_PREWARM", "false"
        ).lower() in {"1", "true", "yes", "on"}
        if not reused_local_stack and background_prewarm_enabled:
            _request_admission_visible_background_warmup(
                line_port=line_port,
                internal_token=_text("BOT_MARKET_DATA_TOKEN"),
                log_file=qwen_log,
            )

        print("LINE 股票機器人本機服務已就緒：")
        print(f"- Qwen API：http://127.0.0.1:{qwen_port}/v1")
        print(f"- 唯讀股票 API：http://127.0.0.1:{market_port}/healthz（啟動時已通過 DB/token readiness）")
        print(f"- LINE Webhook：http://127.0.0.1:{line_port}/line/webhook")
        print(f"- 公開 Webhook：{public_url}（LINE 官方驗證通過）")
        if reused_local_stack:
            print("本次沿用既有本機服務；關閉此視窗只會停止新建的 Tunnel。")
        else:
            print("按 Ctrl+C 可停止本次啟動的全部服務。")
        while all(process.poll() is None for process in managed_processes):
            time.sleep(1)
        failed = next((process for process in managed_processes if process.poll() is not None), None)
        return int(failed.returncode or 1) if failed else 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"啟動失敗：{exc}")
        print(f"請查看：{LOG_DIR}")
        return 4
    finally:
        for process in reversed(managed_processes):
            _stop_process_tree(process)
        for handle in log_handles:
            handle.close()
        instance_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
