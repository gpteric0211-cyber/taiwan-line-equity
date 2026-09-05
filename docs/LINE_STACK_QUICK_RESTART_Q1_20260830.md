# Q1 舊版完整重啟快速確認 — 2026-08-30

## 1. 結論、適用範圍與停止點

Q1 完成一次完整 launcher 重啟；**不是 Phase 1 部署，也不是完整變異證據**。
台北時間 17:31:27.1386138 停止，17:31:52.558 首次觀測完整恢復，QPC 計算 **25.419355 秒**。
只有一次 restart intent、一次 stop、一次 start；沒有追加嘗試或第二次重啟。
17:34:52.467 完成還原後 hash 核對；17:34:52.528 observer 結束。
`line` 已於 17:39:05.428 暫停且讀回 PAUSED；`line-phase-d` 維持 PAUSED。
R1／R2／R3 保留為暫停的歷史計畫，不繼續自動執行。回到正常開發狀態，不再等待量測。

**這是快速確認樣本，不是完整變異證據；正式上線前仍需再補測多次以涵蓋不同暖／冷狀態。**
不計算 P95、不核定正式中斷預算、不宣布 LINE 模型升級完成。
M0 的 127.055702 秒仍保留為歷史樣本，不因 Q1 較快而刪除或失效；兩次不足以推廣至冷開機。

本報告中的 `Q1/` 一律指
`logs/line_model_shadow/restart_quick_20260830/Q1/`；
原始行號皆為檔案一開始起算，JSONL 每一行是一筆完整原始事件。

## 2. 九項准入與版本隔離

| 原計畫准入項目 | 當次結果與原始證據 |
| --- | --- |
| 1 規則與授權 | 依當前單次 Q1 計畫；沒有 Phase 1／Phase 2+ 授權 |
| 2 時間／間隔／去重 | M0 14:23:41.9307356 → Q1 17:31:27.1386138 = 11265.2078782 秒，即 3:07:45.2078782；大於10800秒。停止前掃描既有事件及現場 PID，未發現較晚實際重啟。17:49前開始；intent 唯一 |
| 3 來源與環境 | `pre_source_checks.json:1`：339/339 frozen source、11/11 protected、六組 legacy/pending、私有設定；`pre_runtime_identity.json:1`、`pre_probes.json:1` 綁硬體／模型 |
| 4 流量／GPU／compaction | `immediate_pre_stop_activity.json:1`，17:31:26.326462：queue=0、active=null、最近900秒receipt=0／exchange=0、有效compaction lease=0 |
| 5 排程競爭 | Windows 市場排程非 Running；下一個18:00，在本次恢復後。沒有停止背景工作製造空載；Codex後續品質排程維持暫停。原始 Windows 記錄在 `pre_runtime_identity.json` |
| 6 權限／程序／官方LINE／observer | 停止前成功查 WMI身分、官方endpoint、observer baseline；相同已獲准執行環境一次完成stop+start。新查PID/parent/creation/executable/command存 `processes_immediately_before.json:1` |
| 7 每次完整編譯／測試 | 當次 pending 全339檔編譯exit0；當次完整688 passed。不是沿用上一輪結果 |
| 8 精確快照與回復準備 | 六檔 byte-exact備份 `pending_backup_manifest.json:1`；用apply_patch切legacy後6/6 hash一致及編譯exit0，見 `legacy_at_launch.json:1` |
| 9 健康baseline／最後重查／intent | observer先取得七類健康baseline；停止前再查GPU／流量／程序／來源並 exclusive-create `restart_intent.json`。原始 stop/start 見 `operator_events.jsonl:1` |

原始停止事件（`Q1/operator_events.jsonl:1`）：

```json
{"kind":"full_restart_requested","at":"2026-08-30T17:31:27.1386138+08:00","qpc_ticks":931966134174,"qpc_frequency":10000000,"details":{"phase1_deployment":false,"launcher_pid":27168,"maximum_interruption_seconds":300}}
```

`operator_events.jsonl:2`：17:31:27.2595814，已驗證舊 launcher 樹停止，exit_code=0。
`operator_events.jsonl:3`：17:31:27.2869525，新舊版launcher PID=35716；Hidden啟動。
新程序鏈：launcher 35716/39620、Ollama40760、market7520/33636、
LINE40316/25712、cloudflared40084、runner37592。
PID與creation time完整保留在 `processes_after_recovery.json:1`，不是用健康的舊服務冒充。
新 runtime state 的 `reused_local_stack=false`，更新時間17:31:45。

六檔只做暫時還原／恢復，最終沒有淨原始碼修改：

```text
scripts/start_line_bot_stack.py
review_src/adapter/qwen_local.py
review_src/api/line_model_benchmark.py
review_src/core/line_model_validation.py
review_src/services/line_model_benchmark_service.py
review_src/services/line_model_shadow_service.py
```

完整diff與回復diff：`legacy_switch.patch:1`、`pending_restore.patch:1`。
還原pending前再核對現場仍為自己切入的legacy，見 `pre_pending_restore_identity.json:1`；
還原後339 frozen source全部一致。**pending回到磁碟，不代表服務已載入pending。**
既有常駐程序沒有自動reload；本次沒有再次重啟或新增局部重啟。
依 maintainable-refactor 的行為隔離要求，公式／referee未更改，Phase1修正沒有混入啟動來源。

## 3. 真實恢復時間線與取樣誤差

以 stop_requested 的 QPC tick為零點；不是用事後人工查看時間當完成時間。
既有 observer 0.5秒輪詢（一次請求結束後再等待），official約5秒；
請求本身延遲也在原始起訖時間內。QPC/monotonic為同機單調時間。
`measurement.jsonl` 共3641行，其中3639筆probe；全部保留，包括失敗。

| 條件 | 第一次有效的新代恢復（台北時間） | 距停止秒數 | measurement.jsonl 行 |
| --- | --- | ---: | ---: |
| 8020 /api/version 200，0.33.1 | 17:31:29.813 | 2.674672 | 1404 |
| 8010 /readyz 200，ready=true | 17:31:31.296 | 4.157058 | 1416 |
| 8021 /health 200，ready=true | 17:31:32.870 | 5.731614 | 1430 |
| 新runtime state，不復用stack | 17:31:45.619 | 18.480517 | 1572 |
| 官方新endpoint active=true且match | 17:31:46.303 | 19.163821 | 1580 |
| 新公開 /health 200 | 17:31:47.013 | 19.874085 | 1587 |
| 新runner原digest/context常駐 | 17:31:51.940 | 24.801017 | 1641 |
| warmup完成、completed_tasks=1、queue=0、active=null | 17:31:52.558 | **25.419355** | 1650 |

停止樹尚未結束時，8020在17:31:27.252及residency在17:31:27.205有舊代成功回應，
其後又有失敗。**這兩筆保留但不能當恢復**；表格採後續失敗後的新代成功。
8021新程序早期 completed_tasks=0，見1430行；最後才到1，沒有使用M0累計值冒充。

所有條件同時滿足的最近七筆原始probe是1645–1650及1638行（詳
`measurement_summary.json` 的 recovery.supporting_probes）。
official取樣較疏，完整恢復時其最新成功probe結束於17:31:51.611，
而非宣稱七項在同一奈秒測得。
原launcher已先做官方空事件webhook測試，再請求background warmup；
`logs/line_bot/line_gateway.log:14918`及14920行有相應200原文，
啟动版本的順序見舊snapshot launcher:799與809行。

### Service gap：輪詢觀測區間，不冒充精確物理停機

以下為「一次連續不可用區段」假設下的保守觀測夾限：
下界=max(0, 最後失敗請求開始−首次失敗請求结束)，
上界=首次恢復請求结束−最後健康請求開始。
完整每一筆起訖與失敗後再成功／成功後再失敗的清單在
`measurement_summary.json` 的 service_gap_observation_brackets。
輪詢無法排除取樣間隙的短暫變化；單次timeout也不等於整段持續不可用。

| 服務 | 下界（秒） | 上界（秒） |
| --- | ---: | ---: |
| 8010 local readyz | 1.832627 | 4.517530 |
| 8021 local health | 3.158984 | 5.840932 |
| 8020 version | 0 | 2.566675 |
| 公開LINE health | **14.744123** | **20.610308** |

完整文字分析可用性另須等到25.419355秒的模型與warmup條件；
不能拿5.73秒的本地HTTP恢復宣稱模型已可服務。
這次沒有零影響：服務確實完整重啟，公開health曾timeout／502／530，
新公開URL與LINE endpoint亦由既有launcher重新建立／更新。
使用者表示開發階段無使用者；持久化事件0只證明未保存訊息事件，
不能證明停機時沒有未送達的嘗試，也不是正式用戶體驗測試。

## 4. Warmup、runner與硬體原文

觀測到warmup排隊：17:31:46.662–46.683（1584行，queue=1、completed=0）。
首次active：17:31:47.191–47.210（1590行）。
最後warming：17:31:51.439–51.501（1637行）。
首次完成：17:31:52.541–52.558（1650行）。
首次active结束到首次完成结束 **5.348250秒**；
依同樣取樣邊界，active期約 **4.229223–5.896386秒**，不是精確模型推理耗時。

Ollama原文（`logs/line_bot/qwen_server.log:113724`、113728、113760，
已固化到 `restart_log_excerpts.json`；僅socket遮罩）：

```text
time=2026-08-30T17:31:51.905+08:00 level=INFO source=llama_server.go:1362 msg="llama-server started in 4.07 seconds"
time=2026-08-30T17:31:51.935+08:00 level=INFO source=llama_server.go:1362 msg="llama-server started in 4.10 seconds"
[GIN] 2026/08/30 - 17:31:52 | 200 |    4.9643197s |       [ip_redacted] | POST     "/api/chat"
```

同一新runner的两条启动等待日志，**不是兩次重啟或只挑4.07秒**。
api/chat=4.9643197秒包含載入／warmup請求處理，不等於長篇股市回答延遲。
只有原launcher的一次startup text warmup；沒有追加品質題、卸載、vision cold或GPU競爭工作。

原文（qwen_server.log:113667）：

```text
llama_kv_cache: size = 1024.00 MiB ( 16384 cells,  16 layers,  1/1 seqs), K (f16):  512.00 MiB, V (f16):  512.00 MiB
```

環境綁定（僅代表本機此版本）：

- RTX5090，VRAM32607MiB；NVIDIA driver610.88，CUDA UMD13.3。
- Ollama0.33.1；context16384，parallel1，Q4_K_M／26.9B；65/65層GPU。
- 模型digest `64285f05652890940d23e0120a4a687b9b8e57deb0c450aa52aef48567f276cf`。
- runner二進位SHA-256 `be075122836c62cb41dc8fb42345c8fa3cedcbf2b63930767cdc5a66c8580b99`。
- 私有設定SHA-256 `001a13f6f9d0ead805be3e1c3e0463f8f8a498dd4a1c6b0289b9e7f849a1ac47`，前後一致；不公開設定內容。
- OS boot=2026-08-29 15:38:07.5；准入uptime=92876.0263449秒，`long_uptime`。
- `os_boot_kind=unknown`、OS cache／power_cycle=unknown；`text_resident_before=true`。
- 本次為 `model_cold_after_stack_restart`，**不是PC冷開機**；vision未常駐。
- GPU有既有桌面圖形工作，瞬時利用率12%，沒有關掉screensaver等背景程序。
  queue=0／active=null僅是模型工作閒置，不能宣稱整張GPU完全空載。
- Q1比M0快的原因尚無隔離實驗；可能的快取效應不能寫成已確認根因。

## 5. 全部失敗、端點請求與未修風險

停止後共71筆not-ready，全部在原始JSONL及摘要失敗陣列，不篩除：

| 類別 | 次數 |
| --- | ---: |
| 8010 ConnectTimeout | 3 |
| 8021 ConnectTimeout | 4 |
| 8020 ReadTimeout | 1 |
| residency ReadTimeout | 1 |
| residency HTTP200但models=[] | 43 |
| 公開health ReadTimeout | 1 |
| 公開health HTTP502 | 1 |
| 公開health HTTP530 | 17 |

residency空陣列是「未常駐」，不能因HTTP200當成功。
第一次有效新代恢復後，至observer17:34:52.528結束，沒有再次not-ready；
這只涵蓋本次觀測區間，不能宣稱後續永不失敗。
無300秒超時、無追加恢復重啟；完整新PID鏈及observer終了已保存。

cloudflared原文（UTC時間，`logs/line_bot/cloudflared.log:3650`起）：

```text
TCP Connectivity  region1.v2.argotunnel.com  FAIL    HTTP/2 connection is blocked or unreachable
TCP Connectivity  region2.v2.argotunnel.com  FAIL    HTTP/2 connection is blocked or unreachable
SUMMARY: Environment ready with degraded transport. cloudflared will proceed using 'quic'.
```

QUIC兩項PASS，HTTP/2兩項FAIL仍在。未修改Norton、防火牆、憑證或網路設定；
本次沒有重新證明Norton根因、也沒修好備援路徑。此風險獨立保留。
Ollama尚記錄AMD driver警告及啟動過渡的 `llm server error`，
後續確認實際以RTX5090 CUDA成功load；不刪除警告，也不把它改稱不存在。
launcher stdout原始亂碼保留、stderr為空；未為修顯示而改程式。

直接端點證據（`Q1/recovery_direct_requests.json:1`），不是摘要：

```json
{
  "cold_capability": {
    "method": "GET",
    "path": "internal/line-model-benchmark/cold-load-capability",
    "started_at": "2026-08-30T17:34:50.446254+08:00",
    "http_status": 404,
    "response": {"detail": "Not Found"}
  },
  "official_webhook_test": {
    "method": "POST",
    "path": "v2/bot/channel/webhook/test",
    "started_at": "2026-08-30T17:34:50.453257+08:00",
    "http_status": 200,
    "response": {
      "success": true,
      "timestamp": "2026-08-30T09:34:52.225261Z",
      "statusCode": 200,
      "reason": "OK",
      "detail": "200"
    }
  }
}
```

以上按端點重排原始欄位，完整起訖／耗時／response保留於原JSON。
**404在本次是未部署的預期狀態，不是cold-load保護驗收通過。**
官方回應timestamp是LINE伺服器時鐘；總恢復計時使用本機QPC，沒有混算兩個時鐘。
webhook測的是官方空事件連通性，不是實際股市題或LINE reply品質。

收尾派生證據時曾遇Windows GBK無法輸出tokenizer的U+0120，改用ASCII escaped JSON讀回；
另一次初稿聚合命令回傳traceback而非JSON，未採用失敗派生結果。
最後完整性核對首次亦因預設GBK讀取UTF-8而失敗，明定encoding='utf-8'後重查通過。
這些只發生在離線報表處理，不影響已完成重啟；保存於
`postprocessing_diagnostics.json:1`。原始probe不改寫。

## 6. 完整回歸、hash與證據效力

執行器：專案 `review_src/.venv/Scripts/python.exe`。
編譯範圍：`review_src/`、`scripts/`、`tests/`全部339個專案Python檔，
排除虛擬環境；命令等價於對該清單執行 `python -m py_compile <全部339檔>`。
完整測試命令：

```text
review_src/.venv/Scripts/python.exe -m pytest tests -q
```

前測編譯17:25:57.5379043–17:25:58.5012135，339檔、exit0；
pytest17:25:58.5012220–17:26:12.9587466，exit0：

```text
688 passed in 13.77s
```

還原pending後編譯17:33:33.7160354–17:33:34.6864168，339檔、exit0；
pytest17:33:34.6864168–17:33:48.5911566，exit0：

```text
688 passed in 13.19s
```

原始完整stdout及時間／exit code在
`pre_compile.txt`、`pre_pytest.txt`、`pre_test_metadata.json`、
`post_compile.txt`、`post_pytest.txt`、`post_test_metadata.json`；
編譯stdout為空是成功命令的原始輸出，不補造pass文字。
切入legacy另編譯六檔exit0，見 `legacy_at_launch.json`。

**完整pytest前後跑的是pending磁碟來源；重啟實際跑的是精確legacy六檔。**
因此688 passed不能冒稱legacy現場所有行為皆由這份pytest驗證；
legacy本次的啟動／恢復另由原始live probes、綁新PID／原hash驗證。

`post_hashes.json:1` 當次結果（17:34:52.4671968）：

```json
{
  "source_total": 339,
  "source_matched": 339,
  "protected_total": 11,
  "protected_matched": 11,
  "private_config_sha256": "001a13f6f9d0ead805be3e1c3e0463f8f8a498dd4a1c6b0289b9e7f849a1ac47"
}
```

每個檔案完整expected／actual／match在同一JSON，非僅計數。
測試碼沒有新增或修改。下節附完整launcher測試原文，SHA-256仍為
`72d8edd3f96ab135977f4c8d638091599d761d8ea58c78d691615214c1785e18`。
M0及排程前的舊回歸沒有當成本輪測試；本輪全部重跑。
由於六檔與339來源最終hash一致，既有M0作為舊版歷史樣本仍可保留，
但不能作pending修正部署／模型品質有效的證據。

## 7. 交付內容、風險與尚未完成

此次新增Q1獨立原始證據與本報告；更新
`docs/LINE_STACK_RESTART_VARIANCE_PLAN.md`（狀態，不改第3–6節方法）、
`logs/line_model_shadow/restart_variance_20260830/plan.json`（Q1結果及paused）、
`docs/CODEX_REVIEW_PACKET.md`（最新驗收）。
唯一排程更動是將既有line設PAUSED；未新建排程。
六檔源碼暫時切换而最終byte-exact還原，沒有淨修改，不刪除用戶檔案。
既有launcher完成的程序重建、tunnel／官方endpoint更新是本次授權的實際副作用。

風險：**中**（真實完整停止／啟動），不是只有改文件的低風險；
恢復在300秒內完成，沒有據此宣稱無正式服务影響。

尚未完成／本階段不能證明：

- 多次暖／冷狀態變異、PC冷開機、P95、正式中斷預算。
- HTTP/2可用備援、Norton例外設定。
- Phase1 cold-load／新Schema／Q2部署與其現場驗證。
- Phase2模型品質、vision cold、併發Load Matrix、後续classifier/research/canary驗收。
- 真實LINE使用者分析回答、吞吐量或投資分析品質。

下一步：可回到正常開發；若要正式部署，仍由使用者另行核定窗口和中斷預算。
在使用者核准前不自動重啟、不開始Phase1或Phase2+。
未來正式上線前再补多次不同暖／冷狀態量測，不用單次25.42秒排窄窗口。

## 8. 完整既有 launcher 測試原文

來源：`tests/test_start_line_bot_stack.py:1`；本輪未修改，非只貼assert片段。

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
