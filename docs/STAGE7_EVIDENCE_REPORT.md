# Stage 7 Qwen candidate、校準與負載證據（2026-09-01）

## 結論

Stage 7 的 additive 實作已完成，但所有 release gate 維持 fail closed。候選仍只能
offline/shadow，不能替換 stable 回答或覆寫 referee。Stage 7 初始樣本的 validator rejection
已在 Stage 8 以最小 projection/generation boundary 修正，新的 30 筆 actual matrix 為 30/30 pass；
但並行 8 延遲、production paired outcomes、獨立人工盲評、正式硬體多日樣本、指定 driver 與
其他硬門檻仍未達標。因此 Stage 8 仍為 `release_ready=false`，Release Phase A 不得啟動。

## 2026-09-01 current refreeze addendum

- Shared finalization 已實作：backend 會獨立驗證 signed release decision、packet/base-answer/model/
  source/version digests，重跑 validator，並把唯一 sanitized answer 封存至 immutable
  `canonical_model_answer_extension`。Web／LINE 讀相同 persisted text/hash；raw output 不保存，
  idempotent retry 不新增列，semantic conflict fail closed。Production schema 尚未 migration。
- Phase-D runner 現可對 final canonical path 明確斷言 artifact reuse hit/miss 與 packet 中實際
  `news_radar` event hit/miss；這是量測能力，不是 5 日／100 筆 evidence。
- 新 30 筆 actual controlled replay 為 validator 30 pass／0 reject、ungrounded 0、referee override 0；
  evidence SHA256 `8ccf4ed2fa9be5bae1cea060854e792c8a5a493e90aecfcb12c42fc9eea1dfef`。
  Focused 1/2/4/8 p95 為 9.201/11.675/23.316/46.679 秒；comprehensive 為
  10.649/13.096/27.201/53.695 秒，兩個 concurrency-8 gate 仍 fail。
- 最新 freeze digest `660a030002d86c9f136624d0970450dbe3ef78d19519a54dd122f2b3d310ffd5`：
  64 files、59 Python files、1,579 tests、protected 11/11、temporary schema stage7.2 rollback
  零殘留。Freeze 當下 Ollama/LINE gateway 拒絕連線，故 runtime unavailable 已成為結構化 blocker；
  沒有沿用舊 runtime model digest 或自行重啟服務。

## 實作與封存契約

- `TargetLabelContractV1` 使用 official-adjusted T/T+1 結果，固定 next-open gap、
  continuation/reversal 與 next-close direction 邊界；suspension/no-trade/quality failure
  一律 unavailable，sample ID 不含 delivery channel。
- `StatisticalReleaseGateV1` spec hash 為
  `59958b9838f3944ea4f0d360031578334ad63275b728320f4ff8acf527333b89`。它固定 10,000 次
  bootstrap、seed 20260901、normal 連續 5 交易日 moving blocks、material-event 的 date block
  與 independent event-cluster 較不利 bound、predeclared metrics/margins/Holm 順序及六種合法
  結果。Production evaluator 拒絕 synthetic rows，也不允許更改 bootstrap 次數。
- `CanonicalModelFactPacketV2ProjectionV1` 從同一 sealed artifact 投影 exact cutoff、
  target/comparison entities、immutable referee、official OHLCV、technical ensemble、event、
  factor coverage、bounded conversation、image typed estimates、omissions/conflicts 與 source/evidence
  binding。無證據的 requested scope 會形成 explicit unavailable limitation fact，不能讓模型自行補齊。
- `canonical-model-candidate-v1` 只接受 `offline` 或 `shadow`，沿用既有 LINE shadow 的 system
  prompt、generation guard、JSON schema、token preflight、compaction、validator 與 deterministic
  repair。評估 decoding 固定 `temperature=0`、seed `20260901`，16K 為唯一已選 profile；32K
  未設定、未驗證、未放行。
- `ExpertResponseQualityGateV1` 將 100 題 stable/candidate 以 deterministic blind assignment
  產生 reviewer manifest，role key 分離。`ExpertResponseQualityReviewKitV1` 另將完整 output
  provenance、公開 blind manifest、私有 key、ratings 與 evaluation 以 SHA-256 綁定，Stage 8
  會重算結果而不信任已存的 pass 欄位。只有每題恰一筆含 human/independence/no-role-key
  attestation 的 `independent_human` rating 可進 gate；缺回答或 ratings 時只能得到
  `insufficient_human_evidence`，synthetic rating 明確禁止。Attestation 是必填來源聲明，並非
  程式自動證明真人身分。
- Stage 7 需要的 prediction event cluster/type/large-safety/synthetic 欄位已加入 additive schema
  與 repository，含既有 table 的 `ALTER ADD COLUMN` 測試；正式 DB 未 migration。

## 真實本機模型與硬體證據

實際 runtime 探測：

- GPU：NVIDIA GeForce RTX 5090，VRAM 32,607 MiB。
- Driver：610.88；objective 指定 595.71，**不一致，為 blocker**。
- CUDA：13.3，符合；Ollama：0.33.1。
- Model：`taiwan-stock-qwen:latest`，digest
  `64285f05652890940d23e0120a4a687b9b8e57deb0c450aa52aef48567f276cf`，Qwen 26.9B、Q4_K_M。
- Resident context：16,384；model bytes 與 VRAM bytes 同為 19,767,796,693，探測值顯示
  CPU offload 0 bytes。

1/2/4/8 concurrency matrix 共 15 筆 actual executions、0 synthetic latency rows：

| concurrency | completed | validator pass | end-to-end p95 | queue p95 | 判定 |
|---:|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 10,312 ms | 2,010 ms | reject |
| 2 | 2 | 0 | 17,103 ms | 9,509 ms | reject |
| 4 | 4 | 0 | 37,185 ms | 29,015 ms | focused 31s 超標 |
| 8 | 8 | 0 | 81,231 ms | 73,379 ms | focused 31s 超標 |

這是小樣本診斷，不是 Phase D p95 證據；尚未達每 profile 100 executions、5 交易日。另有兩筆
focused preview/raw 與兩筆 comprehensive cache miss/hit 實跑，四筆均被 validator 拒絕。
Comprehensive cache miss 的 bounded research 約 4,452 ms，repeat 為 cache hit 0 ms；兩次皆無
canonical research write、無 raw body 保存。

新的 canonical packet 路徑實際執行兩次。第一次推論完成後，Windows 因測試 harness 未明確
關閉 SQLite handle 而拒絕刪除 temp DB，結果 JSON 未輸出；程式已改成 `finally: close()`。
第二次留下完整 sanitized evidence：

- artifact cutoff `2026-09-01T17:40:44+08:00`，partial，1 evidence、1 event、6 omissions；
- temp SQLite 已刪除，production DB writes = 0，未送 Web/LINE；
- 16K preflight ready，estimated prompt 5,525，actual prompt 3,285，completion 376；
- queue 2,001 ms，model 7,458 ms，fixed seed/temperature；
- validator reject：`ungrounded_numeric_or_date_claim`、`citation_not_renderable`；
- ungrounded count 1、referee override count 0。

連同上述 15+4 筆，Stage 7 有 20 筆保留結構化結果的 actual local-model executions；另有 1 筆
因 harness cleanup failure 未保留結果。這些不能轉列 Phase A，因 rollout 在本工作開始前已是
shadow，且 Stage 8 prerequisites 尚未通過。

## Stage 8 corrective evidence（supersedes candidate-rejection blocker only）

Stage 8 以相同 canonical candidate/validator 路徑重現後，確認初始 rejection 有四個邊界根因：

- generation 可輸出未綁定的 literal 股票代碼；
- attribution-required event 被強制加入，但 rights 不允許 display，形成 non-renderable citation；
- sealed snapshot 已有 valuation/institutional/global/support 等 section，artifact projection 卻丟棄；
- validator cutoff parser 只接受 date，而 canonical artifact 使用 offset-aware exact timestamp；daily
  fact 的 `as_of` 也不應被 artifact cutoff 取代。

修正只收緊 candidate generation guard、event rights/citation eligibility、既有 snapshot section
projection 與 point-in-time parser；沒有修改 protected 分析公式、referee、stable reply 或 production
DB。Focused 與 comprehensive 代表題各自實跑通過，接著在同一 local hardware 做 1/2/4/8 matrix：

| profile | actual executions | validator pass/reject | p95（1/2/4/8） | profile gate |
|---|---:|---:|---|---|
| focused | 15 | 15 / 0 | 9.201 / 11.675 / 23.316 / 46.679 s | fail（8 concurrency >31s） |
| comprehensive | 15 | 15 / 0 | 10.649 / 13.096 / 27.201 / 53.695 s | fail（8 concurrency >45s） |

30 筆均為 actual、synthetic=0、ungrounded claim=0、referee override=0、candidate reply sent=false、
production DB writes=0。完整逐筆證據為 `docs/STAGE8_CANONICAL_MATRIX.json`，evidence SHA256
`8ccf4ed2fa9be5bae1cea060854e792c8a5a493e90aecfcb12c42fc9eea1dfef`。這只解除
`actual_candidate_validator_rejections` blocker；它不是 5 交易日／每 profile 100 筆的 Phase D
證據，且新的 `canonical_candidate_latency_gate_failed` blocker 仍成立。

## Warm/cold、vision 與 memory contention

- Warm text path 已實測。Cold capability endpoint 顯示 disabled / maintenance-only；未經授權不
  unload resident production text model，因此 cold gate 未評估。
- Memory contention probe 在 vision warmup 階段約 132 秒後 HTTP 500。根因已核對為設定的
  `QWEN_MEMORY_MODEL_ID=qwen3-vl:8b-instruct` 不在 resident set，Ollama `/api/chat` warmup 於
  120 秒 timeout 後取消 runner load；沒有證據顯示 OOM。Text model 事後仍 resident。
- 同一 vision model 未 resident 且剛 timeout，沒有重複執行 vision contention。錯誤分類已修正：
  新程式會回 `memory_model_warmup_failed`／`vision_model_warmup_failed` 的 structured 409，而非
  未分類 500；live process 未 restart，所以這項修正尚未載入 running service。
- 因 cold、vision、memory contention 未完成，這些 gate 都是 blocker，不得以 warm text 結果代替。

## Statistical 與人工品質 gate

正式 DB 只讀檢查結果：`analysis_target_prediction` 與 `analysis_target_outcome` 尚未 migration，
paired rows = 0，normal 與 material-event 都只能得到 `remain_shadow_insufficient_power`。沒有用
synthetic rows 補足，也沒有執行 production migration。

100 題 corpus mix 與非覆寫盲評工具已封存，但本階段沒有 100 組完整、逐題綁定來源的
stable/candidate outputs，也沒有任何獨立人工 rating。故 factual/entity critical error、overall
candidate preference >=70%、各 scenario >=60%、
dimension median >=4/5 等門檻全部是「未評估」，不是零錯誤或通過。Quality gate 的實際狀態是
`insufficient_human_evidence`。

## 驗證與遺留風險

- 最新 Stage 8 freeze 全量 `tests/` regression：1,579 passed in 30.48s；59 個 freeze Python files
  通過 `py_compile`，protected hashes 11/11，temporary migration/rollback 通過。
- 風險等級：**高（release）／中（additive code）**。原因是 driver mismatch、8 concurrency
  延遲超標、production evidence 0、人工 ratings 0、cold/vision/contention、shared canonical
  runtime unavailable；shared model-answer persistence 與 canonical news hit/miss instrumentation
  已完成，但尚未 production migration 或取得 Phase-D 多日 evidence。程式未改 protected formulas、
  未部署。
- 首次 harness failure 留下
  `%LOCALAPPDATA%\Temp\stage7-canonical-dvsl0wk9`。已核對為本次建立的單一 temp
  SQLite 目錄並嘗試安全刪除，但 OS 回 access denied；不含 secret 或原始模型 prompt/image，屬
  低風險清理遺留。

## 下一步

維持 shadow-only，不開始 Phase A。下一步應在維護時段完成同一 working-tree security scan，
由 owner 解決 driver contract，改善並行 8 admission/latency，並累積 5 交易日／每 profile 100 筆
正式實機證據、production paired outcomes、vision/cold contention 與獨立人工盲評。全部硬門檻
通過並重新 freeze 為 `release_ready=true` 前，不得開始 Release Phase A。
