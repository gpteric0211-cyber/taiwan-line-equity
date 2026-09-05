# Stage 8 Final Freeze 與 Release Readiness（2026-09-02）

## 最終判定

`release_ready=false`，`phase_a_authorized=false`。Stage 8 已完成 freeze 與 readiness 判定，
但不能確認「沒有任何待修改 release-bound 項目」。Phase A 為
`not_started_stage8_prerequisite_failed`，B–E 依序等待前一 Phase；本次沒有切 rollout、migration production DB、restart、canary、
部署或發送 candidate reply。

Canonical manifest：`docs/SINGLE_TRACK_V3_STAGE8_FREEZE.json`。

## Freeze 證據

- Freeze contract：`SingleTrackV3Stage8FreezeV1`。
- 94 個 release-bound code/prompt/schema/renderer/contract/evidence files 已封存，aggregate digest：
  `caf7ccc432e73f43c3d88d19faa7ae9da96934d528242ee136f63c7b2f02c56d`。
- Freeze 當下 Ollama gateway `127.0.0.1:8020` 拒絕連線；manifest 因此保存
  `runtime.available=false`、`model_resident=false`、`probe_error_class=ConnectionError`，不沿用舊的
  runtime model digest／quantization／context／CPU offload 結果。
- 獨立硬體 probe 仍確認 Driver 610.88 不等於指定 595.71；CUDA 13.3 符合。
- Final full suite：1,693 passed in 30.50s；freeze Python 86 files 全部 `py_compile` 通過。
- Protected analysis hashes：11/11，mismatch 0。
- Temporary SQLite migration/rollback drill：schema `single-track-v3-stage8.11` migration ready；新增
  scheduler persistence、bounded `research_news_item`、premarket/event-delta artifacts、immutable
  gate/prediction/outcome/evaluation manifests、zero-GPU production receipt/content-terminal artifact、
  event cluster/revision、content/target assessment 與 attempt tables 均存在，
  rollback 後 V3 leftover tables 0；temporary DB 已刪除，production writes 0。
- Final manifest frozen-file hash 重算 mismatch 0；candidate matrix evidence 與 collector hash 都通過。
- Assessment persistence 已依 identity/create、content transition、target transition 分為三個責任模組，
  舊 import surface 保持相容；同一組 dispatch/retry/CAS/cascade race tests 為 28/28。重複 probe 另揭露
  DB clock 毫秒在 retry timestamp 被截秒的既有缺陷；修正為保留非零 fractional seconds 後，固定
  delay + deterministic jitter 精確契約與 50/50 repeat probe 全部通過。
- `research_news_item` 現只保存 bounded noncanonical metadata／key points／短摘錄／hash，不含 raw body；
  `first_retrieved_at` immutable、`available_at` 不可往後、content retention 最長七日且受 source rights
  更短期限約束。GDELT index time 不得冒充 publisher time；prune 只清 hot content，保留 URL/hash/event
  audit metadata。Stage 8.9 worker 已把 prune 納入每個 terminal savepoint，包括全來源失敗或零結果；
  production scheduler／DB 仍未啟用。
- `premarket_intelligence_artifact` 現只接受四個固定 premarket slots，與單一 terminal retrieval run
  的 target/cutoff/policy/calendar/coverage/failures 完整綁定；event revision 需通過 point-in-time gate，
  sealed artifact immutable/idempotent。selector 只在同一 target trade date 內選最近已可見 slot，21:00
  artifact 缺失時可選 18:00，但不借前一交易日。`event_delta_artifact` 以 pending→terminal CAS 保存
  post-base 新事件，PIT reader 可重建 seal 前狀態，且不改寫 premarket 或唯一 canonical artifact。
  新增測試 6/6、Stage 1 合併 39/39；artifact 建立仍未接 retrieval/runtime runner。
- Statistical manifest tranche 先封存 exact gate spec/hash、實際 evaluator/TargetLabel source SHA-256、
  bootstrap 與完整 holdout sample/target identities，再分別複製 stable/candidate prediction snapshots，
  最後才允許開啟 official-adjusted outcome revision。Failure/abstention 保留於 denominator、unavailable
  outcome 明示計數、synthetic 固定為 0；prediction snapshots 必須早於 holdout open。Evaluation repository
  自行執行 frozen evaluator，偽造 `pass_for_canary` 會拒絕。來源 staging rows 後續 upsert 不會改 snapshot。
  新增測試 7/7；Stage 1 persistence 46/46、連同 label/gate/evidence 測試 61/61。Production paired rows
  仍為 0，兩 regime 仍是 insufficient power，未宣稱統計通過。
- Stage 8.7 deterministic zero-GPU producer 只消費 `CONTENT_TERMINAL_CASCADE`／`EVENT_DELTA_SEALED`。
  前者封存唯一 `suppressed_unresolved` artifact；後者只以晚於 analysis cutoff 的 verified/material
  canonical events 更新 invalidation metadata，canonical answer text/hash 逐 byte 不變。Delta seal＋enqueue
  與 output＋receipt＋pending→delivered CAS 各自原子；無 targets 為 explicit no-op，tampered payload/replay
  fail closed。Receipt DB constraint 固定 `zero_gpu_model_calls=0`，模組無 adapter/service/Qwen imports。
  新增測試 7/7；相關 Stage 1／label／gate／evidence 合併 68/68。Stage 8.9 retrieval worker 現可封存
  terminal outbox，但 premarket intelligence producer 與 content/target model worker 仍未接線。
- Stage 8.8 新增 frozen official-session calendar revision、durable scheduler tick、純 TPE planner、
  portable CLI 與 fail-closed Windows installer source。四固定時段為 T-1 18:00／21:00、T 06:00／06:45，
  final scan cutoff 07:00；15 分鐘 sentinel 在週末／非交易日仍運行。晚啟動一律用 actual observed time
  與未來 cutoff，舊固定時段／sentinel interval 明示 skipped，不回填歷史 cutoff。Temporary SQLite
  close/reopen probe 通過 101→101 replay、下一 interval 102，模型呼叫 0；這不是實機 boot/wake 證據。
  Installer what-if 確認 Taipei timezone，但正式 DB 尚未 migration/calendar materialization，故
  `install_ready=false`、Windows task 仍為 0，沒有安裝或 production write。
- Stage 8.9 新增 durable metadata-only retrieval worker：lease/CAS 先持久化，adapter 在 DB transaction
  外執行，terminal attempt、非 canonical research item/link、prune、run 狀態、outbox、receipt 與 lease
  release 在單一 savepoint 內完成。完成後關閉／重開 SQLite 直接 replay，adapter 呼叫 0；live lease
  不偷取，expired running lease 可續跑且不改原 started_at。timeout／429／offline 分類、no-results、late、
  raw-body/canonical-write fail-closed 共 8/8 通過；模型呼叫 0、canonical table writes 0。
- Stage 8.9 official calendar materializer 僅接受 `OfficialTWSEExactSessionSourceV1` 的逐日精確 session set；
  scheduled/cancelled/delayed/special/early-close 5 狀態 5/5 通過。年度休市／closure-only payload 無法證明
  每日開收盤，因此明確拒絕，未硬編碼 09:00/13:30。這些是 offline fixture，production source fetches 0，
  不代表正式 calendar 已 materialize。
- Stage 8.10 凍結七個重大事件 scan scopes、logical source plan 與最多四個 bounded Radar queries；query
  只能由官方 entity packet 產生，query digest、entity refs、query/source plan version 全部寫入 terminal
  receipt。Worker 升為 `SingleTrackV3RetrievalWorkerV2`，source spec 明確綁 scope／query mode／authority，
  canonical source 需 primary verification，dedup 同時綁定 entity refs，避免不同公司相同題名被合併。
- MOPS 上市／上櫃 official adapter 已接入 off-by-default execution plan：每 run 一次、只保留目標股票的
  題名／精確揭露時間／URL／來源權利；「說明」與 raw body 完全不進 research lane。單一市場失敗時保留
  另一市場成功資料並封存 `partial`。timeout/offline 等 failure class 不會被 fallback 隱藏。
- 台灣政策 adapter 已接入同一 execution plan：只允許行政院／行政院所屬機關／經濟部固定官方 RSS
  whitelist，且 relevance 只能使用 entity packet 的官方名稱、稽核 alias 與產業詞。只保留題名、精確
  publisher time、官方 HTTPS link 與 source identity；RSS summary/raw body 不進 research lane。單一 feed
  timeout 不會抹除其他 feed 成功，離線測試 4/4。
- Federal Reserve monetary-policy adapter 已接入同一 execution plan；endpoint 由 Board 官方 RSS directory
  確認。邊界拒絕 redirect、非 Fed host、DTD/entity、超量 response/item 與未來／無效時間，只保留題名、
  精確 publisher time、官方 HTTPS link 與 source identity。對股票的關聯只代表 retrieval scope，不代表
  materiality 或方向；離線測試 4/4。
- `SingleTrackV3EventSourcePlanV2` 將制裁與出口管制拆成兩個獨立 coverage key。U.S. Treasury press
  releases、OFAC Recent Actions、BIS all press releases 已由單一 off-by-default wrapper 每 run 各抓一次，
  只接受各自官方 host 與固定 detail path；date-only 來源不冒充精確 publisher time，page excerpt/raw body
  不保留。OFAC 官方已退休 RSS，因此明確只用 Recent Actions HTML index。三來源 partial failure、redirect／
  非官方 host 與 worker/reconciler boundary 離線測試 4/4。
- Stage 8.11 在 noncanonical `research_news_item` 增加 nullable `publisher_published_date`：Treasury、OFAC、
  BIS 的官方 date-only metadata 可原樣保存，但不能捏造午夜或精確發布時間。Worker/repository 會拒絕
  future date，以及與已驗證 publisher timestamp 不一致的日期；8.10 temporary schema migration regression、
  exact-date persistence 與 invalid/future-date cases 均通過。
- `SingleTrackV3EventReconciliationV1` 已把 retrieval 與 promotion 分開：Radar-only 維持
  `unverified_radar`；官方一手或合格次級證據才可建立 sealed event cluster/revision。Revision 初始
  `materiality=unknown`、無方向、模型呼叫 0，且 `canonical_event_evidence` writes 0；既有 scan persistence
  也修正為不再把 Radar-only event 寫入 canonical table。聚焦離線證據為 retrieval 11、calendar 5、
  MOPS 5、Taiwan policy 4、Fed 4、US Treasury/OFAC/BIS 4、source/query plan 10、reconciliation 3，全部通過；production source
  fetches 0。
- 目前 MOPS、台灣官方政策、Fed monetary-policy、U.S. Treasury、OFAC、BIS 與 controlled Radar 已組成
  execution plan。合格地緣來源、海外價格反應、美股與台灣夜盤、稀釋估值 snapshot 仍未齊，
  因此 full source coverage 明確為 false，不能聲稱 scan complete 或允許高信心。

## Runtime source 與 contention evidence integrity

- Freeze 當下 8021 source fingerprint 與 `/healthz` 都拒絕連線，current workspace digest 為
  `ada33f095b2fe2943b770a88393d255e6f416251ce974b94944461d51fdeafae`；因此 running source 無法
  證明等於 frozen workspace，LINE readiness 也明確為 false。
- 一次非 cold warm contention run 實際觀察到 memory preemption 與 vision completion；stable
  fallback 179 ms、未送 candidate。但 23/23 LINE health samples not ready，且 source/runtime
  不一致，所以該 artifact 明確標為 invalid for current-workspace release evidence。
- 新 collector 先做 authenticated read-only runtime fingerprint；對舊 runtime 的負向驗證為
  exit 2、`model_probe_posts=0`、admission completed tasks 27→27。
- 完整稽核：`docs/STAGE8_RUNTIME_EVIDENCE_AUDIT.md`。Cold 未要求、未執行、未 unload 模型。

## Canonical candidate corrective evidence

初始 rejection 已重現並定位為 candidate boundary 問題：未綁定 literal stock code、event rights 與
display/public URL 不相容、sealed snapshot sections 未投影，以及 exact offset-aware cutoff 與 daily
`as_of` 語義不相容。最小修正沒有改 protected 分析公式、referee 或 stable reply。

- Focused 與 comprehensive 代表題均通過 validator，ungrounded claim 0、referee override 0。
- 本次 comprehensive 實際輸出曾使用無法由 `moving_averages.ma5` 證明的「短均線」標籤；validator
  正確以 `claim_field_label_unverifiable` 拒絕。最小 deterministic repair 只有在同 block 真正引用
  `moving_averages.*` 時才將其保守降格成既有白名單「均線」，沒有放寬 validator 或猜測均線週期。
- 1/2/4/8 controlled replay 共 30 筆 actual executions、0 synthetic、30 pass／0 reject。
- 修正 scheduler 將 2 秒 shadow idle grace 對連續批次每筆重複收取的問題；現在每個連續背景
  batch 只在第一筆前等待一次，interactive priority/preemption 與單 inference slot 不變。
- 最新 focused queue+execution p95：9.201／11.675／23.316／46.679 秒。
- 最新 comprehensive queue+execution p95：10.649／13.096／27.201／53.695 秒。
- 兩個 profile 都在 concurrency 8 超過各自 31／45 秒門檻，故 latency gate 仍 fail。
- 候選均未送 Web/LINE、不可取代 stable，production DB writes 0。LINE shadow 現已從 authenticated
  POST 取得同一 sealed artifact 的 `ModelFactPacketV2`，不再使用 legacy LINE projection。
- HMAC 簽章、time window、5/10/25/50/100 cohort、deadline work-stop、validator/referee 與 kill-switch
  canary gate 已實作。Validated model answer 現由 authenticated backend finalization 重新驗證後，
  以 `analysis_id` 為唯一鍵封存至 immutable `canonical_model_answer_extension`；Web／LINE 讀同一
  persisted text/hash，重試 idempotent、衝突 fail closed，raw model output 不落 DB。實作完成不等於
  canary 獲准；目前 rollout、runtime、schema 與其餘 release gates 仍阻止使用者端替換。
- 正式 Phase-A/D 採證入口改為 canonical packet/candidate，Phase-B 有獨立 fixed-scenario packet audit；
  Phase-D 必要 stage timing 缺任一項即 fail。Final-path instrumentation 現另行量測 canonical artifact
  reuse 與實際 packet `news_radar` event hit/miss，不能沿用 legacy research cache；但尚未累積 Phase D
  的 5 交易日／每 profile 100 筆正式樣本，因此 instrumentation 不冒充通過證據。
- 完整逐筆證據：`docs/STAGE8_CANONICAL_MATRIX.json`；SHA256
  `8ccf4ed2fa9be5bae1cea060854e792c8a5a493e90aecfcb12c42fc9eea1dfef`。

## Privacy/security review 狀態

依 `codex-security:security-diff-scan` 啟動 app-backed working-tree review 時，未追蹤的 1-byte NUL
runtime lock 被誤納入 source diff，scan identity 因而未建立。`.gitignore` 現已排除所有本機 `logs/`
runtime/evidence 輸出，沒有刪除證據且已追蹤 `.gitkeep` 不受影響；但依 workflow 不得為同一次失敗
另建替代 scan，因此：

- 沒有可延續的 scan ID；
- 沒有另開替代 scan、刪除 runtime lock、停止服務或把手動 source scan 冒充正式結果；
- security review 狀態為 `incomplete`，是 release blocker；
- full regression 已覆蓋 LINE signature/replay、image/OCR persistence、remote URL reject、GET
  side-effect、model validator 與 privacy contracts，但 passing tests 不能替代完整 security scan。

## Gate 順序與 blockers

Phase D 在 Phase A shadow 後蒐集；5 交易日／100 筆不可循環地當成 Phase A 前提。Manifest
因此分成：

Pre-Phase-A blockers：

1. 100 題 actual stable/candidate outputs 與 independent human ratings 尚未收集，quality gate
   為 `insufficient_human_evidence`。盲評套件已具備公開/私有分離、逐題與文件 SHA-256 綁定、
   exactly-one-rating、human attestation、拒絕覆寫及 Stage 8 重算契約；這些工具不等於已取得評分。
   Manifest 如實列出 6 個尚不存在的 output/review/key/rating/evaluation evidence files。
2. Cold 與 current-source vision/memory contention evidence 未完成。
3. Codex Security diff scan 未完成；runtime outputs 已排除，但初次失敗沒有 scan ID 可續跑，也沒有
   completed formal report。
4. `SingleTrackV3SchedulerAuditV1` 已能找到四固定 slot、15 分鐘 sentinel、portable runner、
   metadata-only retrieval worker、MOPS execution plan/reconciler、exact-calendar materializer 與 fail-closed installer source；但 matching
   Windows tasks 為 0，正式 DB schema/calendar 尚未 ready，actual restart/wake/catch-up 也未驗證；既有
   市場資料排程不得取代此 gate。
   Stage 1 已有 run/lease/heartbeat/outbox、bounded research item、premarket/event-delta artifact、
   immutable statistical manifests、two-stage assessment/attempt、typed retry、promotion CAS 與 content
   terminal cascade/reconciler persistence、離線 zero-GPU outbox artifact producer 與 temp-only runner
   probe；但尚無 content/target model worker、installed OS task 或 actual wake evidence，也不等同
   production scheduler 已可執行。
5. Production Single-Track V3 schema 尚未 migration；Phase A 不得同時混入 schema 變更。
6. Ollama/LINE gateway 在 freeze 時均拒絕連線；runtime model identity/residency 無法重驗，running LINE
   source 不等於 frozen workspace，readiness 為 false。
7. Canonical concurrency-8 latency diagnostic 未通過，表示仍有 release-bound admission/latency
   工作待決定，不能在 Phase A 後再偷偷修改。重複 idle grace 已排除；最新實測在 text/vision
   runner 同時常駐的真實 contention 下，單 slot 約 5.8–6.8 秒/次，未停掉競爭工作、調高門檻或
   未經證據提高 parallel slots。

Future Phase-D/E blockers（不阻止 Phase A 的順序，但 Phase E 前仍是硬門檻）：

1. Actual driver 610.88 與 expected 595.71 不一致。
2. 尚未累積 5 交易日與每 release profile 100 筆 execution。
3. Production paired outcomes 為 0，normal/material-event 均
   `remain_shadow_insufficient_power`。

## 風險與下一步

風險等級：**高（release）／中（additive persistence implementation）**。Freeze 與 temporary rollback
drill 都是唯讀或 temporary-local；新增 schema/repository 尚未寫入 production，也不改 stable behavior。
真正 release 風險來自上述未完成證據。

下一個合理步驟不是 Phase A。先取得並驗證可合法使用的 TWSE exact-session 官方來源，繼續補齊
合格地緣、海外反應、美股／夜盤與稀釋估值來源，再於 temporary copy
把完整 coverage、content assessment/watchdog 與 premarket artifact runner 接線；之後才由
operator 受控清理目前的 Ollama 孤兒，並完成 100 題人工盲評、runtime/security scan、current-source
部署（先保持 rollout off）、conversation-memory readiness、cold/current-source contention，以及
獨立的 production schema migration；另須安裝並跨 restart/wake 驗證 Single-Track V3 durable
scheduler。由 owner 決定 latency 與 driver contract。只有重新產生
`release_ready=true` 且
`phase_a_authorized=true` 的 Stage 8 manifest，才可開始 Phase A。
