# LINE Model V2 operational safety audit

更新時間：2026-08-30 07:19（Asia/Taipei）

後續註記（08:02）：生成 prompt/schema 與 missing_data 驗證邊界已修正，尚未部署。
舊 A/B 與延遲數字保留為歷史證據，不可簽核新生成版本；部署後必須重收。
新修正、全部三十筆離線重驗及資料保留結果見 `docs/LINE_MODEL_PHASE_B_GENERATION_AUDIT.md`。

後續註記（07:36 source hardening）：cold-probe 保護已改寫並通過 610 tests，但未部署或執行新的
冷載入。本頁原本的「當前 hashes／587 tests」均指 07:19 交付快照，不再代表後續完整檔案 hashes。
新依賴分析、原始碼變更與部署前提見 `docs/LINE_MODEL_COLD_LOAD_SAFETY.md`；舊 warm/content 證據可
保留為未變屬性的歷史證據，不能簽核新版 cold lifecycle。

機器可讀總表：`logs/line_model_shadow/operational_safety_audit_current_source_20260830.json`。

## 最終判定

使用者提出的五項疑慮已逐項處理，但結果不是「全部 Release Gate 通過」。目前可簽核的是服務保護、
證據邊界與診斷可重現；candidate canary 仍被高併發延遲、冷載入失敗、五交易日與真實 LINE
telemetry 擋住。

1. 歷史 8020-only 維護窗缺少同步 8010／8021 health 與真實 reply telemetry，使用者影響只能是
   `unknown`，不得補寫為零影響。後續部署一律完整啟動 8010／8020／8021／Tunnel 並保存 health。
2. Phase A 20 筆明列 `valid_for_load_matrix=false`，只證明呼叫、context 與 validator 正確性。
3. Phase B 封包規則定案後已重收 Phase A/B；16 個產品行為 source hashes 相同。其後只修改 evidence
   runner 的 health 首筆 barrier，沒有改 packet、research、validator、renderer、admission 或 LINE 路徑。
4. Phase B 同一 artifact 同時保存每題 attempts/pass/reject/reject-rate 與完整 first pass，不能只秀 pass。
5. active shadow 遇到 interactive 可在數百毫秒讓道；不可搶佔 vision 佔用 GPU 時，正式股票回覆會在
   521 ms 內預測拒絕並走安全 fallback，不再等待約 32 秒才 timeout。

## 1. 維護窗與正式 LINE 使用者影響

### 歷史 8020-only 維護窗

舊 evidence 約建立於 2026-08-29 23:41:52–23:43:37 +08:00。該時段沒有同步保存：

- 8010／8021 health 時序；
- LINE 官方 webhook endpoint 狀態；
- 實際 LINE ingress → reply telemetry。

因此只能判定「資料不足，真實使用者影響未知」。舊 evidence 不可作 release evidence，也不可用事後
回想補成「reply-only 全程在線」。這個歷史事實無法補救，只能防止重演。

### 本輪完整 stack 重啟

兩次均先啟動每 0.5 秒 health sampler，再停止已核對 PID 的完整服務樹；不是只保留 8020。

| Evidence | 8010 gap upper | 8020 gap upper | 8021 gap upper |
| --- | ---: | ---: | ---: |
| `restart_phase_d_vision_admission_20260830.jsonl` | 6,869 ms | 3,981 ms | 7,791 ms |
| `restart_phase_d_probe_error_fix_20260830.jsonl` | 6,847 ms | 3,991 ms | 7,768 ms |

gap upper 是最後一筆 ready 到下一筆 ready 的量測上限；這是短暫中斷，不是零停機。維護窗沒有真實
LINE reply telemetry，因此仍不能宣稱沒有使用者訊息剛好撞到中斷。

2026-08-30 07:19:38 再查 LINE 官方 API：GET 成功、`active=true`、官方 endpoint 與 runtime 相符、
官方 webhook test 成功。只保存 endpoint SHA-256，不保存網址或 token。當下本機 8010／8020／8021
均為 HTTP 200，8021 `ready=true`。

### 冷載入失敗與 orphan runner 清理

為消除 v5 的 collector-only source hash 差異而執行的 v6 冷載入探針失敗，不能當作成功證據：

- `phase_d/special_probes_vision_admission_v6_20260830.json`：文字模型冷載入 120,888 ms 後 HTTP 500；
  Ollama log 記錄 client 在 `llama-server` 完成載入前關閉，該次 Ollama request 為 499。
- 後續 memory compaction 與 vision contention 均因文字模型未常駐而回 HTTP 409；`probe_set_complete=false`。
- health 225 筆中 8021 有一次 0.4 秒 timeout，因此 `all_services_ready=false`。
- 探針後發現 6 個 `llama-server` 的父 PID 已不存在；它們不是目前 8020 Ollama 的子程序，會污染
  GPU residency 與 queue latency 證據。

因果更正（後續 source review）：這 6 個 runner 建立於 02:09–03:26，早於 v6 的 07:11–07:13。
只能確認它們是「v6 失敗後發現的既存 orphan」，不能說由 v6 產生；也尚未證明它們是該次
timeout 的原因。後續 cold-reload 必須先保存完整 runner census，清潔環境下重新量測。

只對已核對的 6 個 orphan PID 執行精確清理，沒有重啟完整 stack，也沒有終止目前的 Ollama PID
36972 或其文字 runner PID 30200。清理後舊內部 listen ports 全部消失，只剩 30200 由 36972 管理並
監聽內部埠 10617；文字 26.9B 模型仍以 context 16,384 常駐。8010／8020／8021 均 HTTP 200、
8021 `ready=true`，07:19:38 的 LINE 官方 webhook test 再次成功。

機器可讀清理證據：`logs/line_model_shadow/runtime_orphan_cleanup_20260830.json`。v6 只能作為
「冷載入 lifecycle gate 失敗」證據；下一次冷載入只能排在明確維護窗，不能在正常正式流量時重跑。

## 2. Phase A 20 筆的證據邊界

有效 artifact：`logs/line_model_shadow/final_phase_a_current_source_v2_20260830.json`。

- 20 attempts：10 pass、10 reject；context HTTP 400=0。
- queue p95=2,013 ms；candidate execution p95=12,664 ms。
- 489 次 health 取樣完整覆蓋執行窗，三服務 not-ready 均為 0。
- `vision_residency_exercised=false`。
- `concurrent_user_traffic_controlled=false`。
- `actual_line_reply_send_exercised=false`。
- `valid_for_load_matrix=false`。

所以這 20 筆只證明最終封包能呼叫本地模型、不再 context 400，以及 validator 會接受或拒絕輸出。
它不替代 concurrency 1/2/4/8、vision、memory compaction 或真實 LINE network send。

Load Matrix 已另外落在 Phase D artifact，不會事後把 Phase A 改標成「已涵蓋」。

## 3. Phase B 修正後 Phase A 是否失效

舊 Phase A 失效，且已重跑。controlled news、packet event preservation、validator event/citation
contract 與 renderer 會改變模型輸入和 pass/reject，因此舊版本不能代表定案後行為。

目前有效兩批：

- Phase A：`final_phase_a_current_source_v2_20260830.json`
- Phase B：`final_phase_b_current_source_v2_20260830.json`

兩批在收集時綁定相同 17 個 hashes；其中 16 個產品行為來源在 07:19 稽核時與磁碟完全相同。收集完成後，
`scripts/run_line_model_live_acceptance.py` 只增加 health monitor「第一筆已落檔才開始 attempt」的 barrier。
Phase A/B 原有 `attempt_window_covered=true`，所以 dependency analysis 判定：保留內容／封包證據，不為
collector-only barrier 重跑 50 次模型；這個單一 collector hash 差異已明示，不能宣稱 17/17 當前相同。

## 4. Phase B 選擇性偏差控制

有效 artifact：`logs/line_model_shadow/final_phase_b_current_source_v2_20260830.json`。

| Scenario | Attempts | Pass | Reject | Reject rate | First pass（全批序號／該題第幾次） |
| --- | ---: | ---: | ---: | ---: | ---: |
| fundamental + chip + technical + news | 10 | 7 | 3 | 30% | 1／1 |
| technical + valuation + support + risk | 10 | 5 | 5 | 50% | 2／1 |
| chip + night + US + events | 10 | 4 | 6 | 60% | 9／3 |

同一 artifact 的 `summary.attempts_by_scenario` 保存 attempts/pass/reject/reject_rate，
`first_pass_by_scenario` 保存完整 first-pass attempt。30 attempts 共 16 pass／14 reject、context 400=0；
723 次 health 取樣完整覆蓋執行窗，三服務全 ready。

第一筆通過樣本摘要：

- fundamental/news：8,904 tokens、40 facts、6 events、4 research events、used E001–E003。
- technical/valuation/support/risk：8,912 tokens、53 facts、0 event。
- chip/night/US/events：9,030 tokens、39 facts、6 events、4 research events、used E001–E003。
- 通過樣本皆 ungrounded=0、referee override=0；article body=0、canonical writes=0。

60% reject 仍偏高，因此只通過「內容存在與契約稽核」，不通過 canary quality gate。

## 5. Shadow 與 vision 對下一位使用者的排隊影響

### Active shadow 可搶佔

有效 artifact：`logs/line_model_shadow/final_preemption_current_source_v3_20260830.json`。

- 30/30 active shadow 被 interactive preempt。
- interactive queue p95/max=289/547 ms。
- submitted-to-complete p95/max=624/822 ms。
- 148 次 health 取樣完整覆蓋執行窗，三服務全 ready。

所以新進 interactive 不會等待完整 7–14 秒 shadow，但仍有數百毫秒取消與 GPU 釋放成本。這是單次
插入探針，不取代完整多使用者 Load Matrix。

### Vision 不可搶佔時的保護

修正前探針顯示：vision 佔用 GPU 時，正式股票分析約等 32,014 ms 才走 TimeoutError fallback。
根因是 vision 與文字共用 15 秒預估值，而且 benchmark vision category 沒有預估值。

修正後：

- vision 使用獨立 `LINE_MODEL_VISION_P95_MS=35000`。
- admission snapshot 顯示 active vision predicted=35,000 ms、remaining≈34,990 ms。
- 正式股票回覆在 521 ms 內走
  `admission_fallback:predicted_deadline_admission_rejected`。
- reply 462 bytes、具免責聲明、candidate submitted=false。
- stable reply 完成時 vision 仍在執行，之後 vision 正常完成。
- 103 次 health 取樣三服務全部 ready。

有效 artifact：`logs/line_model_shadow/phase_d/special_probes_vision_admission_v5_20260830.json`。

v5 驗證的是「模型已常駐且 vision 正在執行時」的 production admission 行為。v6 在進入 vision
contention 前就因文字模型冷載入失敗，所以不推翻 v5 的 warm-state 結果；但它新增一個獨立且未通過的
cold-reload lifecycle gate。v5 收集後只有 probe collector source 改變，產品 admission／renderer／LINE
路徑沒有改變；不得把 v6 的 source-current 標籤誤寫成成功驗證。

## Phase D Load Matrix（診斷完成，Release Gate 未通過）

### 正式 stable reply deadline 保護

`phase_d/stable_admission_final_source_v2_20260830.json` 共 30 筆，涵蓋 focused/comprehensive ×
concurrency 1/2/4/8：

- 30/30 HTTP 200、30/30 在 45 秒內、30/30 具免責聲明。
- c4/c8 觀察到 predicted-deadline early fallback。
- 93 次 health 取樣三服務全 ready。

這證明正式回覆在高負載下會及早降級，不會為了等模型耗盡 reply token。

### 直接候選工作量

`phase_d/2026-08-30-warm-load-final-source-v4` 共 30 筆，實測時 text 26.9B 與 vision 8.8B 同時常駐：

- Driver 610.88、CUDA 13.3、Ollama 0.33.1、context 16,384。
- text VRAM=19,767,796,693 bytes；vision VRAM=7,760,919,920 bytes。
- 515 次 health 取樣三服務全 ready；所有請求 HTTP 200、context overflow=0、ungrounded=0、referee override=0。

| Case | Candidate predicted-work p95 | Work-stop gate |
| --- | ---: | --- |
| focused c1 | 6,244.73 ms | pass |
| focused c2 | 13,816.33 ms | pass |
| focused c4 | 27,272.30 ms | pass |
| focused c8 | 54,529.78 ms | **fail** |
| comprehensive c1 | 11,699.36 ms | pass |
| comprehensive c2 | 21,464.84 ms | pass |
| comprehensive c4 | 50,611.88 ms | **fail** |
| comprehensive c8 | 99,811.70 ms | **fail** |

因此 candidate 路徑不能核准高併發 canary。正式 fallback 保護雖通過，不等於候選品質／延遲 gate 通過。

### News cache

- hit：先清除並 bounded-live prime，再觀察 `cache_only/hit`。
- miss：清除後觀察 `bounded_live/miss`。
- 兩案 validator pass、context 400=0、canonical writes=0、health 全 ready、vision 同時常駐。

## Fire-and-forget 可觀測性

目前 ledger：701 queued/started、535 completed、166 preempted、0 abandoned；23.68% noncompletion 全部
有記錄。這解決「不知道遺失率」的問題，但背景工作仍不具 durable resume，不能宣稱 durable delivery。

## 驗證與保護邊界

- Python compile：通過。
- full suite：587 passed in 31.83s。
- protected analysis hashes：11/11 match。
- candidate replacement=false；model/research rollout 均為 shadow。
- 未修改股票公式、唯一 referee、canonical DB schema 或 canonical DB 資料。

## 尚未完成的 Release Gates

- 2026-08-30 是週日，本輪 Phase D 不計交易日。
- 至少 5 個交易日、每 profile 至少 100 筆尚未完成。
- 真實 LINE ingress → reply telemetry 檔仍不存在。
- focused c8、comprehensive c4/c8 candidate work-stop gate 失敗。
- text + vision residency 下的冷載入探針 120,888 ms 後失敗，事後發現既存 orphan runners；雖已清理並恢復
  正式服務，但 cold-reload lifecycle gate 仍未通過。
- Phase B 最高 reject rate 60%，仍需在不放寬 grounding/referee 的前提下降低。
- canary 與 candidate reply replacement 維持禁止。

## 不可使用的舊證據

以下 artifact 已被 current-source 或成功探針取代，不可拿來簽核：

- `phase_d/2026-08-30-warm-load-news-grounded-v2`
- `phase_d/stable_admission_current_source_20260830.json`
- `phase_d/special_probes_news_grounded_v2_20260830.json`
- `phase_d/special_probes_source_recovered_v3_20260830.json`
- `phase_d/special_probes_vision_admission_v4_20260830.json`
- `phase_d/2026-08-30-warm-load-final-source-v3`

其中一度發現 `line_model_benchmark_service.py` 被 0xFF 覆寫；受影響 evidence 已全部排除，受損檔只保留
為 `line_model_benchmark_service.py.corrupt-20260830-ff` 供事故稽核，目前有效原始碼已重建、compile/test 通過。

`phase_d/special_probes_vision_admission_v6_20260830.json` 不是被刪除的證據：它保留作 cold-load
失敗與不完整 probe 的事故證據，但明確禁止用於宣稱 vision admission 成功。
