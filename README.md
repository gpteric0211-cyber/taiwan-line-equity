# Taiwan Line Equity

台股資料庫、在地 AI 分析、LINE Bot 與手機持股研究介面。由原 Taiwan50 系統遷移；沿用行情來源、資料品質門檻、計算公式及歷史保留規則，新增獨立帳號、加密持股與統一啟動／排程。

## 開始使用

需要 Python 3.11–3.13、uv、足夠的資料儲存空間，以及本機 Ollama 或相容的既有模型設定。安裝會依 `uv.lock` 建立環境；啟動不會自動下載模型。

Windows：

```powershell
.\setup.cmd
.\start.cmd
```

Linux／macOS：

```sh
sh setup.sh
sh start.sh
```

啟動後開啟終端顯示的 `/portfolio` 網址。首次帳號只能在主機本機建立；已有帳號則登入。預設 HTTP 只監聽 loopback，手機外網與 LINE 需要 HTTPS 反向代理。詳見 [操作手冊](docs/project/OPERATIONS.md)。

`start` 會啟動或沿用同一個背景服務，管理網頁、LINE webhook、模型預載與每日排程。關閉瀏覽器或啟動視窗不會停止 Bot；電腦仍需保持開機。需要前景除錯時使用 `python -m equity run`，停止該前景程序會回收它管理的子程序。

| 要做的事 | Windows 按鈕 |
| --- | --- |
| 開啟網頁，同時保持 LINE Bot 運行 | `啟動台股分析系統.bat` |
| 啟動／確認 LINE Bot 與共用服務 | `啟動LINE股票機器人.cmd` |
| 更新共用行情資料庫 | `一鍵更新今日分析資料.bat` |

前兩個按鈕可重複按，使用同一組服務與同一份市場資料庫。更新先在副本完成，發布時才短暫協調查詢；排程與一鍵更新共用寫入鎖。[同機共用與更新說明](docs/project/SHARED_RUNTIME.md)。

## 功能

- 歷史與每日行情：保留原全市場更新流程、Fugle 取樣、官方盤後核對及資料品質門檻。
- 持股：手動輸入或截圖辨識，核對後保存；股／張明確換算，支援成本、資料日期及估算損益。
- 分析：網頁與 LINE 共用原有分析／裁決服務，由本機 Qwen 解讀可用事實與限制。
- LINE：一對一持股管理、一次性帳號綁定、圖片辨識及原有行情／對話功能。
- 新聞：重大事件 metadata 永久保存，一般 radar 新聞依原 30 日規則滾動清理，其他來源沿用各自期限；官方公司行動仍使用原永久事件表。
- 個人資料：帳號資料庫獨立；持股以 Fernet 加密，LINE 身分以 HMAC 隔離。原圖不落盤，辨識草稿一小時到期。
- 維護：鎖定依賴、資料庫健檢、線上備份、離線回歸測試及 Windows／Linux CI 設定。

## 常用命令

以下命令在專案根目錄執行。市場資料庫 `TAIWAN50_DB_PATH` 的相對路徑以 `review_src/` 為基準；新帳號／持股／模型設定則以專案根目錄為基準。

```sh
uv run --frozen python -m equity doctor --full --services
uv run --frozen python -m equity warmup
uv run --frozen python -m equity serve
uv run --frozen python -m equity update --stage finalize
uv run --frozen python -m equity news
uv run --frozen python -m equity backup var/backups/manual
uv run --frozen python tools/run_tests.py tests -q
```

## 設定與目錄

| 位置 | 用途 |
| --- | --- |
| `equity/` | 可攜啟動、健檢、備份、排程與程序管理 |
| `review_src/` | API、分析、來源 adapter、repository 與既有頁面 |
| `review_src/data/taiwan50.db` | 行情與歷史資料 |
| `var/private/` | 帳號、加密持股、私密金鑰 |
| `review_src/.env` | 原有行情／網頁私密設定 |
| `.env.line_bot` | LINE 與模型私密設定 |
| `models/`、`runtime/` | 本機模型與作業系統專用執行檔 |
| `var/jobs/`、`var/services/` | 新排程狀態與執行紀錄 |
| `var/migration/` | 私有遷移證據與比對報告 |
| `legacy_archive/` | 原專案可讀內容封存；讀取例外見遷移紀錄 |

勿把資料庫、金鑰、token、模型、`.venv`、封存或執行紀錄提交至 Git。移到另一台電腦後重新執行 setup；Python 虛擬環境與 Windows 執行檔不可直接當成跨平台套件。

- [架構與維護邊界](docs/project/ARCHITECTURE.md)
- [操作、模型、LINE 與故障排查](docs/project/OPERATIONS.md)
- [資料保存規則](docs/project/DATA_RETENTION.md)
- [遷移與驗證紀錄](docs/project/MIGRATION.md)
- [搬到另一個位置或電腦](docs/project/PORTABILITY.md)

本系統提供資料整理與研究觀察。資料延遲、缺漏與未驗證新聞會保留狀態；不承諾未來報酬，也不把模型輸出當成可直接執行的交易指令。
