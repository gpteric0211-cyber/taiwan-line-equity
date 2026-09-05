# AGENTS.md — 台灣股票分析系統工作規則

本檔是本 repo 的最高層工作規則。所有 AI agent 與開發者在修改本專案前都必須遵守。

本專案是台灣股票分析系統，金融資料正確性、來源可追溯性、資料可信度與使用者端穩定性，永遠優先於開發速度。

涉及架構、重構、除 bug、資料流調整時，應搭配 `$maintainable-refactor` 規則執行。

## 修改前必做

修改任何檔案前，必須先確認：

- 閱讀專案目錄結構。
- 確認資料流：來源 → DB → service → API → 前端。
- 確認涉及的 DB table、API endpoint、cache、background task、資料來源。
- 確認是否影響評分、籌碼、支撐壓力、分價量、隔日展望等分析邏輯。

## 禁止大規模重構

- 每次只做一個小步驟。
- 每個步驟後 app 必須仍可啟動。
- 不允許一次重寫多個模組。
- 移動程式碼時先保留原行為。
- 行為改變必須另外提出、另外說明，不得混在搬檔或整理中。

## 禁止刪除或改寫既有分析邏輯

除非使用者明確要求，不得刪除、簡化、重寫或重新詮釋既有分析邏輯，尤其是：

- 股票評分與主狀態分類
- RSI 與技術指標計算
- 支撐 / 賣壓計算
- 籌碼成本估算
- 分價量評分
- 隔日展望加權
- 資料新鮮度與信心判斷

若需要修正邏輯，必須先說明原因、影響範圍、風險與驗證方式。

## 後端分層規則

後端優先遵守以下分層：

- `api/`：HTTP route、request/response shaping，不放重業務邏輯。
- `service/`：協調 use case，組裝資料與呼叫分析。
- `repository/`：DB 查詢與寫入。
- `adapter/`：外部資料來源，例如 TWSE、MIS、FinMind、Fugle、Yahoo、TAIFEX。
- `analysis/`：純分析與計算邏輯。
- `model/`：內部 domain model / dataclass。
- `schema/`：API request / response schema。
- `task/`：背景任務、排程、修復工作。
- `core/`：設定、DB bootstrap、共用工具、共用資料品質規則。

依賴方向：

```text
api → service → repository / adapter / analysis → core
```

下層不得 import 上層。避免 circular import。

## 資料品質集中管理

資料可信度判斷必須集中在 `core/data_quality.py` 或等價單一位置。

不得在各 `analysis/` 模組內各自散落判斷：

- 資料來源日期
- T / T-1
- `source_delayed`
- `confidence`
- `estimated`
- `stale`
- `unavailable`

資料進入 `analysis/` 前，應盡量先被正規化並附帶明確品質 metadata。

## 裁判層唯一主結論

API response 的頂層主結論，例如 `main_status`、`practical_status`、`signal`、`recommendation`，只能由單一 referee / practical-status 層產生。

`scoring.py` 的 `eligible_for_watchlist` / `signal` 為 legacy，只允許在 `analysis/` 內部使用。

Legacy scoring 不得直接輸出成與裁判層競爭的頂層 API 結論。

夜盤期貨訊號 `can_override_main_status` 永遠為 `False`，只能對裁判層加減權或提供背景資訊。

## GET 路由無副作用

GET route 必須是 read-only。

GET route 不得：

- 觸發 DB 寫入
- 觸發 repair task
- 觸發 background scheduling
- 使用 `enqueue_missing=True` 這類寫入或排程行為

這些行為只能出現在：

- `task/`
- 明確 POST endpoint
- 明確 repair/update endpoint
- scheduled background jobs

GET 遇到缺資料時，應回傳明確狀態，不得默默啟動修復或排程。

## 缺資料回傳規則

缺資料或資料不可信時，必須回傳明確狀態：

- `unavailable`：完全無資料
- `stale`：資料過期
- `source_delayed`：來源尚未更新
- `estimated`：推估值，必須明確標示

禁止為了讓畫面好看而填假數字。

## 分價量品質門檻

分價量不得只因 `available=True` 就視為可評分。

以下情況不得輸出分價量分數給裁判層：

- `coverage_days < required_days * 0.8`
- `status != ok`

不合格時，必須回傳明確 metadata，說明為何排除於裁判層評分之外。

## Python 修改後最低驗證

修改 Python 檔後，至少執行：

```text
python -m py_compile <修改的檔案>
```

若影響 API，需檢查：

- `GET /`
- `GET /api/quotes?mode=watchlist`
- `GET /api/quotes?mode=tw50`
- `GET /api/stock/{code}/detail`

若測試無法執行，必須明確說明原因。

## 完成後必須回報

每次完成後必須回報：

- 建立或修改的檔案
- 每個修改的原因
- 風險等級：低 / 中 / 高
- 測試執行結果
- 已知遺留風險或後續建議
After each completed task, update docs/CODEX_REVIEW_PACKET.md with the latest review summary; this file may be overwritten each time.
