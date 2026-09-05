# TDCC_EQUITY_UPDATE.md — TDCC 股權集中度週更新

## 用途

TDCC 股權集中度使用集保股權分散週資料，主要用來觀察中期持股結構，例如大戶持股、小股東持股與股東人數變化。

這不是券商分點資料，也不是短線買賣流向。它不代表特定券商、分點或當日主力進出。

## 手動更新指令

在 repo 根目錄執行：

```powershell
Set-Location <repo-root>
```

單一股票：

```powershell
python scripts\update_tdcc_equity_concentration.py --code 2317 --source tdcc
```

自選股：

```powershell
python scripts\update_tdcc_equity_concentration.py --mode watchlist --source tdcc
```

台灣 50：

```powershell
python scripts\update_tdcc_equity_concentration.py --mode tw50 --source tdcc
```

Dry-run，不寫入 DB：

```powershell
python scripts\update_tdcc_equity_concentration.py --code 2317 --source tdcc --dry-run --verbose
python scripts\update_tdcc_equity_concentration.py --mode watchlist --source tdcc --dry-run
```

預設值：

```text
mode = tw50
source = tdcc
days = 180
```

`--mode all` 目前只代表「自選股 + 台灣 50」聯集，不代表全市場掃描。

## 建議排程

TDCC 是週資料，不需要盤中頻繁更新。

建議排程時間：

```text
每週五 18:30 後，或週六任意時間
```

Windows Task Scheduler 可設定：

```text
Program/script:
python

Arguments:
scripts\update_tdcc_equity_concentration.py --mode tw50 --source tdcc

Start in:
<repo-root>
```

本文件只提供排程範例，不會自動建立 Windows 排程。

## 驗證方式

先 dry-run：

```powershell
python scripts\update_tdcc_equity_concentration.py --code 2317 --source tdcc --dry-run --verbose
```

確認輸出：

```text
dry_run = true
writes_db = false
distribution_rows >= 15
success >= 1
```

實際更新：

```powershell
python scripts\update_tdcc_equity_concentration.py --code 2317 --source tdcc
```

啟動網站後查 detail API：

```powershell
curl http://127.0.0.1:8078/api/stock/2317/detail
```

確認 response 有：

```text
equity_concentration.available = true
```

## 資料與顯示限制

4 週變化需要至少 5 個不同 TDCC 週資料日期。

如果目前只有最新一週資料，4 週變化應顯示為：

```text
null / --
```

不可硬算成 0。

## 安全規則

- TDCC 更新只能由手動 script、明確 POST update endpoint 或排程觸發。
- GET detail API 只能讀取已存在的 SQLite 摘要。
- GET API 不得下載 TDCC、不寫 DB、不啟動 background thread。
- TDCC 股權集中度不等於券商分點集中度。
- 本功能不新增 broker flow、不新增分點資料表。
