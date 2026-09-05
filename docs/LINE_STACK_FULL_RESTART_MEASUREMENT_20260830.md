# 完整 launcher 重啟量測 — 2026-08-30

## 1. 結論與授權邊界

這次只執行一次「既有版本完整重啟量測」，**不是 Phase 1 部署**。
使用者拒絕臨時新增局部重啟／熱重載，本輪沒有新增這類機制。

- 選定中斷預算：300 秒。執行日期／時區：2026-08-30，Asia/Taipei。
- 重啟指令開始：14:23:41.9307356；完全恢復條件首次全部被觀測滿足：14:25:48.988。
- **重啟指令 → 全部恢復：127.055702 秒（約 2 分 7 秒）。**
- 獨立觀測持續至 14:27:18.739；其後沒有第二次重啟。
- 結果在 300 秒預算內，但超過使用者提出的 120 秒参考門檻，不能稱「1–2 分鐘內已驗證」。
- 樣本數只有 1；不是 P95、Load Matrix、Phase D 或發布核准證據。
- Phase 1、Phase 2 及後續階段未開始；等待使用者依實測數字決定部署方式。

「全部恢復」要求：三服務健康、新 tunnel 的公開 health 成功、官方 endpoint
active 且與新 runtime_state 相符、原文字模型 digest/context 恢復常駐，
並且啟動預載工作完成（completed_tasks=1、active_category=null、queue_depth=0）。
僅配置 health=ready 不滿足此條件。

## 2. 真實時間線與 service gap

原始證據：`logs/line_model_shadow/restart_measurement_20260830/measurement.jsonl`。
下表行號指這個不刪樣本的 2,947 行原始紀錄。秒數以獨立單調時鐘計算。

| 階段 | 首次恢復觀測，+08:00 | 自重啟指令起秒數 | 原始行 |
| --- | --- | ---: | ---: |
| 8020 /api/version HTTP 200 | 14:23:58.670 | 16.738 | 851 |
| 8010 authenticated readyz HTTP 200、ready=true | 14:24:00.144 | 18.212 | 864 |
| 8021 health HTTP 200、ready=true | 14:24:01.485 | 19.553 | 880 |
| 新 runtime_state（launcher 已通過官方測試後才寫入） | 14:24:17.671 | 35.739 | 1060 |
| 新 tunnel 公開 health 首次 HTTP 200 | 14:24:18.706 | 36.774 | 1071 |
| 獨立官方 GET：active=true、endpoint match | 14:24:22.396 | 40.464 | 1111 |
| /api/ps 原文字模型重新常駐 | 14:25:48.096 | 126.164 | 1956 |
| 預載完成、GPU 無執行中／排隊工作，全部條件满足 | 14:25:48.988 | **127.056** | 1966 |

不是把 19.553 秒當成完整 LINE 能力恢復。公開入口在第一次成功後仍有一次逾時：
14:24:24.567 開始的請求，14:24:34.800 回報 ReadTimeout，實際請求耗時
10.233 秒；14:24:35.622 下一次成功（原始第 1157、1169 行）。
沒有把這次失败刪除，也沒有把第一次成功誤稱之後都不中斷。

### 不可用觀測區間，不冒充無誤差的物理停機秒數

| 探測 | 第一筆不可用回應完成 | 第一筆恢復回應完成 | 兩次觀測差值 |
| --- | --- | --- | ---: |
| 8010 | 14:23:43.259 | 14:24:00.144 | 16.884 秒 |
| 8021 | 14:23:43.306 | 14:24:01.485 | 18.179 秒 |
| 8020 API | 14:23:43.259 | 14:23:58.670 | 15.411 秒 |
| 公開 health 第一段 | 14:23:45.726 | 14:24:18.706 | 32.980 秒 |
| 文字模型常駐 | 14:23:43.227 | 14:25:48.096 | 124.870 秒 |

上表差值不包含第一個失敗探測送出至逾時的時間，也不含取樣間隙，**不可當作停機上限**。
舊程序樹停止命令於 14:23:41.9307356 發出，14:23:42.0519774 返回；
實際停止位於這段操作期間，服務／模型恢復又有取樣誤差。
因此部署規劃使用完整保守操作值 **127.06 秒**，不是挑較小的 observed-gap 數字。

觀測器各端點獨立執行；local/public 每次完成後等待 0.5 秒，官方 GET 每次完成後等待
5 秒。local timeout=0.8、public/official timeout=3 是 requests 的網路逾時參數，
**不是整次請求的硬牆鐘上限**，第 1157 行已示範可能超過。所有請求都有各自起訖時間。
HTTP 探測與模型 registry 都是觀測，不是實際每一位 LINE 使用者的等待時間。

原始操作紀錄 `operator_events.jsonl:1`：

```json
{"kind":"full_restart_requested","at":"2026-08-30T14:23:41.9307356+08:00","details":{"maximum_interruption_seconds":300,"launcher_pid":36500,"phase1_deployment":false}}
```

上面為原始紀錄的欄位摘錄；完整 qpc_ticks/qpc_frequency 與 taskkill 每個 PID 結果均在原檔。
公式：`elapsed_seconds = probe.ended_ns / 1e9 - operator.qpc_ticks / operator.qpc_frequency`。
機器可讀彙整：`measurement_summary.json`，明訂 `is_release_benchmark_evidence=false`。

## 3. 原始請求／模型恢復證據

### 官方空事件 webhook 驗證（不是股市品質測試）

`recovery_direct_requests.json` 保留這次 POST 的完整回應；未發送使用者訊息：

```json
{
  "name": "official_webhook_test",
  "method": "POST",
  "at": "2026-08-30T14:26:54.122075+08:00",
  "http_status": 200,
  "elapsed_ms": 1019.277,
  "response": {
    "success": true,
    "timestamp": "2026-08-30T06:26:54.537044Z",
    "statusCode": 200,
    "reason": "OK",
    "detail": "200"
  }
}
```

服務端 `restart_log_excerpts.json` 保留：

```text
INFO:     Started server process [31280]
INFO:     Application startup complete.
INFO:     <client-socket-redacted> - "POST /line/webhook HTTP/1.1" 200 OK
INFO:     <client-socket-redacted> - "POST /internal/line-model-benchmark/background-warmup HTTP/1.1" 200 OK
INFO:     <client-socket-redacted> - "POST /line/webhook HTTP/1.1" 200 OK
```

客戶端 socket／公開網址去識別化；HTTP status、路徑與失敗不改寫。
官方回應時間戳由 LINE 主機產生，不拿它計算本機中斷秒數。

### 模型恢復原文

`restart_log_excerpts.json` 中的原始模型紀錄：

```text
time=2026-08-30T14:25:47.992+08:00 level=INFO source=llama_server.go:1362 msg="llama-server started in 88.03 seconds"
[GIN] 2026/08/30 - 14:25:48 | 200 |         1m29s |       127.0.0.1 | POST     "/api/chat"
```

`measurement.jsonl:1966` 原始回應中的相關欄位摘錄：

```json
{
  "text_model_warmup": {"status": "resident", "resident": true},
  "gpu_admission": {
    "queue_depth": 0,
    "active_category": null,
    "completed_tasks": 1
  }
}
```

gpu_admission 在完整原文實際位於 `response.line_model_v2_shadow.gpu_admission`，
不是 response 頂層。完整 health 與 /api/ps JSON 均保留，未以摘要取代原始記錄。
既有預載函式不保存 READY 的實際輸出文字；本輪不捏造那段模型輸出，也不以此宣稱分析品質提升。
只證明一次原有文字冷載入／預載呼叫成功；不證明待部署的 cold-load 新保護已生效。

### Cold capability 必須仍未部署

正確路徑的重新確認，`restored_pending_and_correct_capability.json`：

```json
{
  "at": "2026-08-30T14:28:08.660018+08:00",
  "method": "GET",
  "path": "/internal/line-model-benchmark/cold-load-capability",
  "http_status": 404,
  "response": {"detail": "Not Found"}
}
```

本輪第一次收尾探測誤用了 `/cold-capabilities`，也是 404；那筆保留在
`recovery_direct_requests.json`，**無法證明真正 capability 狀態**。已明確另測正確路徑，
不覆蓋舊紀錄、不把錯誤路徑的 404 當作部署證據。Phase 1 要求的 404→200 本輪沒有達成，
因為使用者只核准舊版重啟量測。

## 4. 版本隔離與恢復：沒有順便部署 Phase 1

磁碟上已有尚未部署修改，直接啟動會違反本次授權。本輪採用以下可核對流程：

1. 從本工作既有 patch 紀錄反向恢復六個舊來源；每個 SHA-256 都必須與歷史紀錄完全一致。
2. 保存 `legacy/<原路徑>.snapshot` 與 `pending/<原路徑>.snapshot`，不丟棄待部署工作。
3. 暫時將六個正式路徑設回舊來源、雜湊核對 6/6、編譯 6/6；才停止舊 launcher PID 36500 的程序樹。
4. 用同一個既有 launcher 原始碼完整啟動。新 parent PID 11460；不是沿用舊服務，
   新 runtime_state 的 `reused_local_stack=false`。
5. 全部恢復後，用補丁還原 pending 六檔，**不再重啟**。
   新服務載入的仍是量測舊版；磁碟恢復為待部署狀態，與量測前的狀態分離相同。
6. 所有 339 個既有 Python 來源雜湊回到量測前值；private config 雜湊不變；protected 11/11。

這是短暫來源還原／恢復，不應描述成「來源完全沒有寫入」。沒有改股票公式，
沒有加入熱重載／局部重啟功能，沒有永久回退或刪除 pending 工作。
下一次重啟會載入 pending，仍須經 Phase 1 部署授權，不可自行再重啟。

六個路徑及舊／pending 完整雜湊在 `legacy_launch_manifest.json`、
`restored_pending_and_correct_capability.json`：

- `scripts/start_line_bot_stack.py`
- `review_src/adapter/qwen_local.py`
- `review_src/api/line_model_benchmark.py`
- `review_src/core/line_model_validation.py`
- `review_src/services/line_model_benchmark_service.py`
- `review_src/services/line_model_shadow_service.py`

來源依據是精確雜湊比對，不是 Git commit 部署身分：這些來源原先即未追蹤，
不能聲稱已知 runtime 的全部記憶體模組都經簽章 attestation。六個變更來源有歷史雜湊，
其餘 runtime 路徑核對未在現行程序启动後修改；新 generation schema 檔留在磁碟，
但舊 shadow 路徑不 import 它。此限制與原始 manifest 一併交付。

新服務 PID，完整建立時間見 `processes_after.json`：

| 服務 | wrapper / worker / runner PID |
| --- | --- |
| launcher | 27168 / 11460 |
| Ollama 8020 | 31236 |
| market 8010 | 34420 / 4844 |
| LINE 8021 | 36216 / 31280 |
| tunnel | 5792 |
| text llama-server | 37240（parent 31236） |

只停止已驗證的 launcher 程序樹；taskkill 的已終止子程序原文全數保留。
收尾程序普查只見一個該模型 runner，沒有看到先前六個孤兒程序那種狀態；
這是當時快照，不是未來不會再發生的保證。

### 舊版 launcher 的實際順序原文

原始檔 `logs/line_model_shadow/restart_measurement_20260830/legacy/scripts/start_line_bot_stack.py.snapshot:797`：

```python
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
```

官方確認函式原文（同一舊版快照第 424 行起）：

```python
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
```

因此 runtime_state 新時間戳可以佐證 launcher 已通過官方測試，但其本身
不保證模型已常駐；這是本輪另外量測 /api/ps 與 admission completion 的原因。

## 5. 失敗與風險全部保留

重啟開始後的探測統計（不是模型分析通過率，也不是使用者成功率）：

| 探測 | 成功 / 總數 | 不可用／失敗分類 |
| --- | --- | --- |
| 8010 | 353 / 366 | ConnectTimeout 13 |
| 8021 | 351 / 365 | ConnectTimeout 14 |
| 8020 API | 368 / 380 | ReadTimeout 12 |
| text residency | 176 / 379 | ReadTimeout 12；HTTP 200 但模型未常駐 191 |
| 公開 health | 208 / 250 | ReadTimeout 2；502 1；530 39 |
| 官方 endpoint GET | 38 / 39 | HTTP 200 但新 endpoint 與尚未更新的舊 state 不同 1 |

最後一列不是 LINE API 失敗：原始第 1052 行為 14:24:17.022，
active=true、endpoint_matches_state=false；啟動器正在切換網址。
`measurement_summary.json` 的原因標籤 `http_200` 應與此詳細原文一起讀，
不可誤報成 HTTP error，也不可刪除以提高成功率。

Cloudflared 的 TCP/7844 預檢兩項 FAIL（HTTP/2 blocked/unreachable）均保留；
QUIC 的 UDP 測試 PASS，日誌說明以 degraded transport/QUIC 繼續。
這次實際成功不代表 HTTP/2 備援連線正常，沒有擅改防火牆。

另保留一次讀取 launcher stdout 的主控台 UnicodeEncodeError：
是診斷輸出遇到 gbk 無法輸出 U+FFFD，不是服務啟動失敗。
改用 JSON ASCII escape 讀取；沒有重跑重啟去掩蓋錯誤。
launcher stdout 的中文亦有重導向編碼失真，故時間結論依 JSON 觀測與原始服務 log，
不依失真的中文輸出。沒有修 encoding 基礎設施。

既有啟動過程包括模型 residency 重建、LINE webhook URL 更新、記憶體／ledger 正常初始化。
本輪沒有執行 canonical market table 寫入任務，但沒有逐表前後 DB 快照，
不把本輪冒充「DB 寫入零次」的資料庫層級證明。

維護窗內持久化 event_receipt=0、exchange=0；量測前一小時亦為 0。
沒有真實 reply telemetry 能覆盖停機時丟失／被拒收的請求，
**不能宣稱零使用者影響、零漏訊息、零 reply-token 失效**。
公開入口實際中斷，模型預載也確實佔用 GPU 約 89 秒。

## 6. 環境綁定與證據適用範圍

完整身分：`hardware_identity.json`；模型 JSON 在 `measurement.jsonl`。
這份量測僅適用於以下環境／舊版來源組合：

- GPU：RTX 5090，nvidia-smi VRAM 32607 MiB；driver 610.88。
- CUDA UMD version：13.3（nvidia-smi 的實際值，不宣稱已驗證另裝的 CUDA toolkit）。
- CPU：AMD Ryzen 9 9950X3D；RAM 66155397120 bytes；Windows 11 build 26200。
- Ollama 0.33.1；Ollama 與 llama-server executable SHA-256 均在 hardware_identity。
- 模型 taiwan-stock-qwen:latest，實際 metadata 26.9B、Q4_K_M（俗稱 27B/28B）。
- model digest：`64285f05652890940d23e0120a4a687b9b8e57deb0c450aa52aef48567f276cf`。
- num_ctx=16384；parallel slots=1；實際 KV K=f16/V=f16，buffer=1024 MiB。
- 一次完整重啟、一個 startup text warmup、未注入 interactive 負載。
- vision 設定仍開啟，但前後 /api/ps 都只有文字模型；**未測 vision 冷載入／共存**。
- 未跑 memory compaction contention、並發 1/2/4/8、分類器、多輪對話或網路研究工作。
- 同時存在 read-only HTTP 觀測開銷；不等同無探測的理想空載。
- driver/model/runtime/預載或 launcher 邏輯改變後，不可拿 127 秒當作新版部署保證。

可證明：這組舊版／硬體在本次條件下完整啟動一次成功，各恢復階段可追溯。
不能證明：新 cold 保護、新 Schema 品質、semantic/typed 修正、vision cold、P95、
併發公平性、穩定可達 120 秒上限、Phase 1 或全部 LINE bot 升級完成。
舊 Phase A/B/D 證據未升格、未沿用為本輪品質證據；新版本部署後仍須重新收集。

## 7. 完整回歸與完整測試原文

```text
量測前：python -m py_compile <rg 選出的 review_src/scripts/tests 全部 339 個 Python 檔>
py_compile: 339 Python files passed
python -m pytest tests -q
688 passed in 18.40s

pending 六檔恢復後：同一組完整命令
py_compile: 339 Python files passed
python -m pytest tests -q
688 passed in 19.23s
```

兩次皆 688 通過、0 失敗，非 targeted 子集；完整 console output 在
`pre_compile.txt`、`pre_pytest.txt`、`post_compile.txt`、`post_pytest.txt`。
新增／修改測試案例=0。另將一次性 observer 自身 py_compile 通過並先做 6 秒 live smoke，
完整 smoke 原始 probe 保留於 `observer_smoke.jsonl`。

完整 pytest 測的是待部署磁碟版本；量測時的舊版六檔另外編譯 6/6。
不宣稱 688 是對舊版正在執行的 Python 模組做整合測試，更不是新功能部署成功。
單元測試不能取代本文件的 live JSON；live JSON 也不能取代全套回歸。

protected hashes：11/11 一致，期望值／實際值完整表在
`restored_pending_and_correct_capability.json`；全 339 源碼比對在
`post_regression_and_traffic.json`。本輪沒有 bug 修正／新增金融規則，
因此沒有虛构 red→green 案例；停機的 fail→ready 只算重啟測量。

以下附目前 `tests/test_start_line_bot_stack.py:1` 全檔原文（10 個完整測試函式，包含
同 endpoint 的 PUT/POST/GET、官方測試失敗時拒絕啟動、預載經 admission controller）。
這份 pending-tree 測試內容包含尚未部署的 cold config assertions，不能偷換為 legacy runtime 證據。

```python
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
    assert "LINE_MODEL_V2_ROLLOUT" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_V2_PROFILE" in launcher.LINE_ENV_KEYS
    assert "QWEN_TEXT_API_MODE" in launcher.LINE_ENV_KEYS
    assert "QWEN_NATIVE_BASE_URL" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_BACKGROUND_PREWARM" in launcher.LINE_ENV_KEYS
    assert "LINE_MODEL_COLD_WARMUP_P95_MS" in launcher.LINE_ENV_KEYS
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
```

## 8. 交付檔案與尚未完成

本輪文件：本檔、`docs/CODEX_REVIEW_PACKET.md`、
`docs/LINE_MODEL_MAINTENANCE_CHECKLIST.md`。一次性量測程式：
`logs/line_model_shadow/restart_measurement_20260830/observe_restart.py`，
完整碼可直接逐行核對；它沒有 stop/start/model POST，也不是新的重啟機制。

整體風險：**中**（實際中斷公開正式服務、更新 tunnel/webhook、文字模型 cold load）。
源码最终差异为零不代表服务影响为零。

尚未完成／尚未驗證：

- Phase 1 cold-load 保護、新 Schema／Q2 修改：仍未部署；正確 capability 仍 404。
- typed 缺失期間、absence-only 語意漏洞及兩個 harness：本輪沒有繼續實作或宣稱完成。
- vision cold-load 成功案例：仍無本輪證據，獨立未完成項。
- Phase 2 A20／B30、人工品質重讀、paired replay：未執行。
- Phase 3–6／Phase D 的發布與負載 gates：沒有因此通過。
- 新版 Phase 1 的完整重啟時間：尚未量測；本輪只證明舊版一次 127 秒。

**下一步由使用者決定，先不執行。** 127 秒超過其 120 秒參考值；
可以討論是否接受保留 3–5 分鐘預算的完整重啟部署，或另立獨立的重啟架構設計／
測試／rollback 階段。單一樣本不足以保證下一次 300 秒內必定恢復。
不因這次量測成功自行推進 Phase 1，更不越級 Phase 2。

