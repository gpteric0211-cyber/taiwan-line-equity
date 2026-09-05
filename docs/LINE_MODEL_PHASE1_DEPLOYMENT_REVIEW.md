# Phase 1 部署前驗收 — 2026-08-30

時間均為 Asia/Taipei。狀態：**部署前回歸通過；部署未准入、未執行；Phase 1 尚未完成。**

## 1. 本次授權與實際執行範圍

使用者已授權由執行者填入實際維護時段，可接受最長約 15 秒服務中斷。
本窗只允許部署 cold-load 保護、新 Schema（含 Q2 日期修正），以及前置已齊的 vision cold 測試環境。
這不是仍在等使用者提供時段；目前阻擋是**現有重啟路徑無法合理控制正式服務恢復在約 15 秒內**。

本次未停止、重啟或啟動正式 stack，未修改 Python、正式環境設定、模型、DB 或 webhook。
只執行完整離線回歸、唯讀健康／process 查證、來源快照及本交付文件。
沒有模型 POST、LINE 訊息、候選品質樣本、負載測試或 Phase 2 工作。
原先已啟用的 shadow/research 設定保持不變；不能把既有設定算成本次新增功能。

| 使用者要求 | 本次結果 |
| --- | --- |
| 部署前完整 py_compile | 339 個專案 Python 檔成功，exit 0 |
| 部署前 `pytest tests -q` | 688 passed in 13.62s，exit 0 |
| Cold capability 不再 404 | **未達成**；13:48:11 實際 GET 仍 404 |
| 三服務重啟後 ready | **未執行重啟**；只有部署前點狀健康觀測 |
| 實際維護時段／service gap | `null / not_run`；未開窗，不能填虛構時段或稱零停機 |
| 部署後完整 pytest | `not_run_no_deployment`；不能拿部署前結果冒充 |
| Protected hashes | 11/11 與核准 baseline 相符 |
| Vision cold | **跳過**：專用 cold lifecycle／恢復保護未完成，不臨時新增 |

## 2. 為何未停服務：程式原文與恢復限制

### 2.1 停一個 managed child 會結束整組服務

`scripts/start_line_bot_stack.py:826`：

```python
        while all(process.poll() is None for process in managed_processes):
            time.sleep(1)
        failed = next((process for process in managed_processes if process.poll() is not None), None)
        return int(failed.returncode or 1) if failed else 0
```

同檔 `:836`：

```python
    finally:
        for process in reversed(managed_processes):
            _stop_process_tree(process)
        for handle in log_handles:
            handle.close()
        instance_lock.close()
```

同檔 `:344` 的 Windows 分支使用：

```python
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
```

因此不能直接殺 LINE worker，假設 Ollama／market API／公開 tunnel 都會保留。
本次沒有執行上述停止命令，也沒有用終止 parent、留下孤兒 child 的方式繞過管理機制。

### 2.2 重新啟動包含公開路由重建，不只是本機 ready

同檔 `:793`：

```python
        tunnel, public_url = _start_quick_tunnel(
            cloudflared_exe,
            line_port,
            tunnel_log,
            process_env=_child_environment(),
        )
        managed_processes.append(tunnel)
        print("正在自動更新並驗證 LINE Webhook...")
        _configure_line_webhook(public_url, _text("LINE_CHANNEL_ACCESS_TOKEN"))
```

同檔 `:364` 的 tunnel 預設 timeout 為 60 秒；`:426` 的 webhook 設定預設
`max_attempts=24`、`retry_seconds=5`，PUT／POST test／GET 各自 timeout 為 20／30／20 秒。
這些是**程式允許等待的時間，不是實測停機時間，也不表示每次一定超過 15 秒**。
但現有流程沒有 15 秒內可驗證的舊公開路由回復／局部 restart 機制，不能用過往本機 6–10 秒 gap 承諾本次正式可用性。

整組 teardown 也包括 Ollama 與其 runner；`:716` 重新啟動 Ollama，但只等 `/api/version`。
`:807` 的 background prewarm 預設 false，且不是同步模型 ready 關卡。
`review_src/services/line_bot_service.py:4199` 原文：

```python
        "dependency_probe_scope": "configuration_only",
```

所以 HTTP health 恢復不等於 text model 已恢復常駐推理，更不等於真實 LINE 回覆已在時限內成功。
現有「重用健康 stack」分支不重載 Python module，不能用它讓舊 404 端點變成新部署。

### 2.3 直接證據與推論邊界

`pre_processes.json` 顯示現在仍是同一組 06:25 啟動的程序：
launcher 36500；Ollama 36972；market wrapper/worker 31016/38620；LINE wrapper/worker 24536/34196；tunnel 36068。
text runner 30200 的 parent 為 Ollama 36972，啟動於 07:14:34。
runtime state 最後更新 06:26:06。這些是 process／state 時間，不是完整維護 gap。

已確認：上述生命週期與等待設定存在；沒有已驗證的 15 秒正式恢復程序。
尚不能斷言：此次真的重啟必定停多久。因為沒有執行，**沒有本次重啟延遲數字**。
停止條件是沒有充分依據遵守授權的中斷上限，不是宣稱現行服務已故障。

## 3. 原始請求結果

完整證據位於 `logs/line_model_shadow/phase1_20260830/`。含認證的請求只保存結果，沒有保存 token。

### Cold capability：仍未部署

`pre_health.json` 原始該筆：

```json
{
  "probe": "cold_capability",
  "request_method": "GET",
  "request_url": "http://127.0.0.1:8021/internal/line-model-benchmark/cold-load-capability",
  "started_at": "2026-08-30T13:48:11.408958+08:00",
  "http_status": 404,
  "body": {"detail": "Not Found"},
  "elapsed_ms": 4.114,
  "finished_at": "2026-08-30T13:48:11.413082+08:00"
}
```

### 部署前服務狀態，不是部署後驗收

| 請求開始時間 | 服務／請求 | 結果 | 能證明的範圍 |
| --- | --- | --- | --- |
| 13:48:11.308808 | 8010 `/api/bot/market-data/readyz` | 200；`ready=true, mode=read_only` | 該次可讀 official stock master |
| 13:48:11.384270 | 8020 `/api/version` | 200；`version=0.33.1` | 模型 API 正在回應，不是推理測試 |
| 13:48:11.388715 | 8021 `/healthz` | 200；`ready=true` | configuration-only readiness |
| 13:48:11.413105 | 8020 `/api/ps` | 200；text model resident | 當下 context 16384、VRAM bytes 19767796693 |
| 13:49:09.138635 | 現有公開 tunnel `/healthz` | 200；`ready=true`，13:49:10.344701 完成 | 外部 HTTPS health 可達；不是 LINE webhook event／reply 成功 |

公開 URL 已遮蔽；沒有關閉 TLS 驗證。公開 health 成功結果原文：

```json
{
  "started_at": "2026-08-30T13:49:09.1386351+08:00",
  "finished_at": "2026-08-30T13:49:10.3447017+08:00",
  "status": 200,
  "ready": true,
  "dependency_probe_scope": "configuration_only",
  "url": "[current public tunnel]/healthz",
  "tls_verification_disabled": false
}
```

### 失敗不刪除

- 13:48:11 sandbox Python 公開 health 為 `SSLError`（228.589ms），留在 `pre_health.json`。
- 13:48:37 sandbox PowerShell 同請求為 `HttpRequestException`，SSL connection could not be established。
  唯讀的 sandbox 外重試成功；沒有關閉驗證或修改正式服務。較符合執行環境差異，未查明確切憑證根因。
- sandbox WMI process census 回傳拒絕存取；經核准唯讀查詢後成功，保存 `pre_processes.json`。
- 來源 manifest 寫入後的顯示命令 `Get-FileHash` 少用陣列語法而失敗；manifest 已完整寫入，後續另行解析／比對確認。
  這不是產品測試失敗，不改記為 pytest failure，也沒有刪去失敗工具輸出。

沒有真實模型樣本可列拒絕率。本次維護未開始，service gap 為 **not_run／不適用**，不是測得 0 秒。
唯讀 GET 和完整 pytest 會使用少量網路／CPU／磁碟；未量測對正式請求的資源影響，不宣稱零影響。

## 4. 完整回歸與來源快照

本次範圍為 `review_src/`、`scripts/`、`tests/` 所有 339 個 `.py`（排除 `.venv`）。
由 repo root 執行：

```powershell
$phase1Files = @(rg --files review_src scripts tests -g '*.py' -g '!**/.venv/**')
$phase1Files | & .\review_src\.venv\Scripts\python.exe -c "import sys,py_compile; files=sys.stdin.read().splitlines(); [py_compile.compile(p,doraise=True) for p in files]; print('py_compile: %d files passed' % len(files))"
$env:PYTHONPATH='review_src'
& .\review_src\.venv\Scripts\python.exe -m pytest tests -q
```

實際開始 13:47:34.8581916、結束 13:47:50.7588495。原始結果：

```text
py_compile: 339 files passed
........................................................................ [ 10%]
........................................................................ [ 20%]
........................................................................ [ 31%]
........................................................................ [ 41%]
........................................................................ [ 52%]
........................................................................ [ 62%]
........................................................................ [ 73%]
........................................................................ [ 83%]
........................................................................ [ 94%]
........................................                                 [100%]
688 passed in 13.62s
pytest_exit_code=0
```

`pre_protected_hashes.json` 保存核准 baseline 的全部 11 個 expected／actual／match，13:48:36 比對 11/11。
`predeployment_manifest.json` 保存 339 個 Python source hashes、測試與未部署狀態。
這是**磁碟來源快照**，不是運行中 module attestation、部署批准或 git commit；沒有鎖住工作目錄。
之後有行為程式變更，須重凍結快照並重跑完整回歸，不能沿用本次結果當新版本部署前證據。

13:55 再核對快照：339/339 Python 檔一致；本文件引用的四個完整測試函式已逐字比對原檔，4/4 相符。

本次測試沒有重跑舊模型 A20／B30／負載數字：它們只保留為歷史原始紀錄，**不採認為當前 Schema／validator 的正式證據**。
當前新版 Phase A/B 仍待使用者核准 Phase 1 後按 Phase 2 規則重新收集；本輪沒有舊證據豁免。

## 5. 已有測試原文（完整函式，不是新增測試）

下面是此次完整 suite 涵蓋的代表性完整測試。依賴／fixtures 與其餘完整碼保留在所列原檔；
均為 mock／離線 pipeline，**不能證明正式 cold load 成功、新模型拒絕率或 15 秒維護可行性**。

### Cold capability 不應觸發載入

`tests/test_line_model_benchmark_api.py:403`：

```python
def test_cold_capability_is_read_only_and_disabled_by_default(monkeypatch):
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")
    monkeypatch.delenv("LINE_MODEL_COLD_PROBE_ENABLED", raising=False)
    monkeypatch.setattr(benchmark_api, "run_live_cold_load_probe", lambda **k: pytest.fail("GET must not load"))
    result = benchmark_api.cold_load_capability()
    assert result["enabled"] is False
    assert result["exclusive_admission"] is True
    assert result["benchmark_contract"] == "line-model-maintenance-cold-load-v2"
```

### 拒絕必須早於 model residency／卸載

`tests/test_line_model_benchmark_api.py:370`：

```python
@pytest.mark.parametrize("enabled,confirmed,reason", [
    (False, True, "cold_probe_disabled"),
    (True, False, "maintenance_window_required"),
])
def test_cold_probe_gates_reject_before_residency_or_unload(monkeypatch, enabled, confirmed, reason):
    monkeypatch.setenv("LINE_MODEL_COLD_PROBE_ENABLED", str(enabled))
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda: pytest.fail("must not access model"))
    with pytest.raises(benchmark_service.LineModelBenchmarkError) as caught:
        benchmark_service.run_live_cold_load_probe(maintenance_window_confirmed=confirmed)
    assert caught.value.reason_code == reason
```

### Schema 必須計入真正 pipeline 的 token preflight

`tests/test_line_model_v2_contracts.py:72`：

```python
def test_generation_schema_and_guard_are_charged_to_real_pipeline_preflight(monkeypatch, tmp_path):
    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    monkeypatch.setenv("QWEN_CONTEXT_TOKENS", "16384")
    captured = {}

    def completion(system, user, **kwargs):
        captured.update(system=system, user=user, **kwargs)
        return _completion({})  # Deliberately invalid: transport success is not validation success.

    monkeypatch.setattr(line_model_shadow_service, "qwen_chat_detailed", completion)
    result = line_model_shadow_service.execute_line_model_shadow(
        {"request_id": "schema-budget", "question": "台積電基本面分析",
         "model_facts": _facts_with_missing_fundamentals(), "focus": "fundamentals"},
        force=True, evidence_path=tmp_path / "schema.jsonl",
    )
    schema_text = captured["system"].split("\nOUTPUT_JSON_SCHEMA：", 1)[1]
    assert json.loads(schema_text) == captured["response_schema"]
    prompt = f'{captured["system"]}\n{line_model_shadow_service.MODEL_ANALYSIS_FINAL_GUARD}'
    assert result["generation_prompt_sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert result["generation_schema_prompt_tokens"] > 0
    # Preflight concatenates guard before packet; final prompt puts it after.
    # The estimator is character-additive (rounding tolerance: one token).
    actual_estimate = conservative_prompt_token_estimate(captured["system"], captured["user"])
    assert abs(result["estimated_prompt_token_count"] - actual_estimate) <= 1
    assert result["estimated_prompt_token_count"] <= result["effective_prompt_budget"]
    assert result["validator_result"] == "reject"
    assert result["candidate_can_replace_reply"] is False
```

### Q2／日期與誠實缺失解釋完整正負例

`tests/test_line_model_v2_contracts.py:120`：

```python
@pytest.mark.parametrize("field", ["text_template", "conditions", "missing_data", "research_limitations"])
@pytest.mark.parametrize(
    "claim,expected_pass",
    [
        ("缺少2026年Q2的EPS數據", False),
        ("缺少Q1的EPS數據", False),
        ("缺少Q2的EPS數據", False),
        ("缺少q2的EPS數據", False),
        ("缺少Q3的EPS數據", False),
        ("缺少Q4的EPS數據", False),
        ("缺少Ｑ２的EPS數據", False),
        ("缺少第二季的EPS數據", False),
        ("缺少二〇二六 年的EPS數據", False),
        ("缺少八 月的EPS數據", False),
        ("缺少EPS9元的資料", False),
        ("目前缺少可核對的基本面資料，只能說明判讀限制。", True),
    ],
)
def test_unbound_period_and_value_labels_cannot_bypass_pipeline(
    monkeypatch, tmp_path, field, claim, expected_pass,
):
    """Raw text is checked on every surface, before and after bounded repair."""

    monkeypatch.setenv("LINE_MODEL_SHADOW_IDLE_GRACE_MS", "0")
    block = {
        "block_type": "limitation", "text_template": "基本面資料不足。",
        "evidence_ids": ["F003"], "uncertainty": "high", "conditions": [],
    }
    output = {
        "contract_version": "model-analysis-v2", "explanation_blocks": [block],
        "missing_data": [], "used_event_ids": [], "research_limitations": [],
    }
    if field == "text_template":
        block[field] = claim
    elif field == "conditions":
        block[field] = [claim]
    else:
        output[field] = [claim]
    calls = []

    def completion(*_args, **_kwargs):
        calls.append(True)
        return _completion(output)

    monkeypatch.setattr(line_model_shadow_service, "qwen_chat_detailed", completion)
    result = line_model_shadow_service.execute_line_model_shadow(
        {"request_id": "period-boundary", "question": "台積電基本面分析",
         "model_facts": _facts_with_missing_fundamentals(), "focus": "fundamentals"},
        force=True, evidence_path=tmp_path / "period-boundary.jsonl",
    )
    assert calls == [True]
    assert result["compacted_packet"]["contract_version"] == "model-fact-packet-v2"
    assert result["validator_result"] == ("pass" if expected_pass else "reject")
    assert result["candidate_can_replace_reply"] is False
    if expected_pass:
        assert result["rendered_blocks"]
    else:
        assert "ungrounded_numeric_or_date_claim" in result["validator_reason_codes"]
        assert result["rendered_blocks"] == []
```

## 6. 未完成、風險與下一個需核准的決定

- Phase 1 尚未部署；cold capability 404 未消失，新 Schema／日期修正尚未以新 process 驗證。
- 沒有部署後全套 tests、維護 gap 或重啟後 ready 證據；本次不可簽核 Phase 1 通過。
- Vision cold 專用流程／恢復保護未完成，按本次授權跳過，不下載模型、不臨時改設計。
- Typed absence、absence-only 非數值語意漏洞、兩個 harness 仍未完成；完整 pytest 通過不會消除這些已知缺口。
- Phase 2 A20/B30、至少十筆人工閱讀、Phase 3–6 均未開始本輪工作，不提出 canary 放行。

本輪變更風險低（文件／證據）；在目前流程硬做 15 秒部署的營運風險中至高。
maintainable-refactor 的小步驟、live readiness、保留行為及 evidence gate 使本輪停止在停服務之前；
未以「清單已補」或「688 passed」宣稱 LINE bot 升級完成。

建議下一步：**先另行核准離線補上保留公開 tunnel／模型常駐的局部重啟與有時限回復機制**，
驗證後再維持約 15 秒上限排 Phase 1。這是額外生命週期變更，未經核准本輪不實作。
替代方式是使用者另核准較長、包含模型恢復的中斷預算；目前沒有充分證據可承諾一個安全秒數。
無論選哪條，Phase 1 通過仍須使用者明確簽核，才可開始 Phase 2。
