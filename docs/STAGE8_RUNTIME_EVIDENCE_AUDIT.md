# Stage 8 runtime evidence integrity audit（2026-09-01）

## 判定

Warm memory／vision contention 的核心 probe 行為已實際觀察，但本次 artifact **不得作為
current-workspace release evidence**。原因是 running LINE process 早於目前 release-bound source
變更啟動，且 `/healthz` 持續回 `ready=false`。沒有以 HTTP 200、probe pass 或磁碟上的新 hash
覆蓋這兩個失敗條件。

## 實際觀察

- Evidence：`logs/line_model_shadow/single_track_v3_stage8/contention_warm_20260901_1835.json`。
- Memory compaction contention：HTTP 200；maintenance 在 interactive 到達後被 preempt，interactive
  queue wait 236 ms、total wait 689 ms。
- Vision/stable-reply contention：HTTP 200；vision 最終 completed；stable reply 179 ms，走
  `admission_fallback:predicted_deadline_admission_rejected`，未送 candidate reply。
- Text 與 vision model 均實際 resident；探針未要求 cold、未 unload 任一模型。
- 23 個連續 health samples 中，market 與 Ollama ready；LINE 全部 HTTP 200 但 structured
  `ready=false`。閒置後再次查詢仍為 `conversation_memory_ready=false`，同時 DB 回報
  schema `line-memory-v3`、integrity `ok`。
- Running 8021 process 於 13:25 啟動；目前 `line_memory_schema.py` 於 15:47、benchmark service
  於 17:27 才修改，證明 process 沒有載入目前 source。

## Fail-closed 修正

- 新增 `line-model-runtime-source-fingerprint-v1`：running API 在 import 時封存 relative-path
  hashes；authenticated GET 只回 contract、時間、digest 與 hashes，不回 secret 或本機絕對路徑。
- `run_line_model_phase_d_probes.py` 在任何 benchmark POST 前比較 exact runtime/workspace mapping。
- 對舊 runtime 的負向驗證得到 `runtime_source_fingerprint_mismatch`，collector exit 2，
  `model_probe_posts=0`，admission completed tasks 27→27，沒有建立假 evidence artifact。
- Phase D health preflight 現在同時要求 HTTP 200 與 LINE structured `ready=true`，並保留
  degraded check 名稱。

## 尚未完成

- Current source 尚未部署／重啟，因此新的 fingerprint endpoint 尚未在 8021 出現。
- Conversation memory readiness 必須在 current source 啟動後重新確認。
- Cold probe 需要 operator-confirmed maintenance window；本次未要求、未執行。
- 完整 contention/load matrix、5 交易日、每 profile 100 筆及 LINE ingress telemetry 仍未完成。
