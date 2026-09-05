# WORKFLOW.md — Codex 重構流程

## 固定順序

本 repo 的重構與架構整理必須依照以下順序：

1. 建立或確認 .agents/skills/maintainable-refactor/SKILL.md
2. 建立或確認 AGENTS.md
3. 建立 docs/ARCHITECTURE.md
4. 建立 docs/REFACTOR_PLAN.md
5. 使用者明確說「開始第 N 階段」後，才可以開始實作該階段

不得跳過 ARCHITECTURE.md 或 REFACTOR_PLAN.md 直接開始拆程式。

## 目前進度

* SKILL.md：已建立
* AGENTS.md：已建立
* 下一步：建立 docs/ARCHITECTURE.md

## ARCHITECTURE.md 驗收標準

建立 ARCHITECTURE.md 時，必須真實讀取原始碼，不得憑空假設。

所有聲稱存在的內容都必須附上可驗證依據：

* endpoint 路徑必須附上檔案路徑與行號
* function / class 名稱必須附上檔案路徑與行號
* DB table 名稱必須附上檔案路徑與行號，或 migration / schema / SQL 定義位置
* background task / cache / repair job 必須附上檔案路徑與行號
* external data source，例如 TWSE / MIS / FinMind / Fugle / Yahoo / TAIFEX，必須附上實際引用位置

建議使用：

```bash
rg -n "route|@app|APIRouter|CREATE TABLE|sqlite|def |class |FinMind|TWSE|TAIFEX|Fugle|Yahoo|enqueue|background|cache"
```

如果某項無法在原始碼中找到，必須標記為：

```text
Unverified / Not found in current source
```

不得把未驗證內容寫成既定事實。

## REFACTOR_PLAN.md 驗收標準

REFACTOR_PLAN.md 只能規劃，不得執行。

每個階段都必須包含：

* 目標
* 預計修改檔案
* 不會修改的邏輯
* 風險等級
* 測試方式
* 完成標準

每個階段的「開始執行」都需要使用者明確說：

```text
開始第 N 階段
```

才可以動手修改程式。

不得自行從計畫跳到實作。

## 實作階段規則

實作時必須遵守：

* 一次只做一個小階段
* 不做 big-bang refactor
* 不刪除既有業務邏輯
* 不改變 API 主結論來源
* GET route 不得產生副作用
* 金融資料缺值不得假造
* Python 修改後至少執行 py_compile
* 完成後回報檔案、原因、風險、測試結果
# Codex Review Packet Rule

每次完成任何 Phase、bug fix、feature change、refactor 或文件更新後，Codex 必須更新 docs/CODEX_REVIEW_PACKET.md。

每次任務完成後，Codex 必須更新 docs/CODEX_REVIEW_PACKET.md；此檔案只保留最新一次審查包，可覆蓋舊內容。

這份文件必須包含：

* 修改檔案
* 修改原因
* 邊界檢查
* 測試結果
* 風險
* 殘留問題
* 下一步建議

完成後不得自行開始下一個 Phase，必須等待使用者明確批准。
