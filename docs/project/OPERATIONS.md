# 操作手冊

本文命令預設在專案根目錄執行；程式內資源以模組推導的專案根目錄定位，不依賴使用者帳號或磁碟代號。

## 設定與啟動

首次執行 setup 會建立虛擬環境、必要 schema 與隨機金鑰。既有私密設定不會被範例覆寫。主要設定為 `review_src/.env` 與 `.env.line_bot`；值不可提交至 Git。

```sh
uv sync --frozen
uv run --frozen python -m equity init
uv run --frozen python -m equity run
```

使用 start.cmd／start.sh 可執行最後一個命令。只啟動 HTTP 可用 `serve`；只跑排程可用 `schedule`。同一專案不要開兩個 supervisor。未啟用 OS 開機服務時，登出或關機會停止工作。

| 環境變數 | 預設／用途 |
| --- | --- |
| EQUITY_HOST / EQUITY_PORT | loopback 與既有服務預設埠；可由 CLI 覆寫 |
| EQUITY_AUTH_DB | var/private/accounts.sqlite3 |
| EQUITY_USER_DB / EQUITY_USER_KEY_FILE | var/private/portfolio.sqlite3 / portfolio.key |
| EQUITY_JWT_KEY_FILE | var/private/jwt.key |
| EQUITY_MODELS_DIR | models/ollama |
| OLLAMA_EXE_PATH | 可配置執行檔；否則使用 bundled runtime 或 PATH |
| EQUITY_CAPTURE_TIME / EQUITY_FINALIZE_TIME | 15:05 / 18:30，Asia/Taipei |
| EQUITY_TDCC_TW50_TIME / EQUITY_TDCC_WATCHLIST_TIME | 每週五 18:30 / 18:35；重啟補跑最新未完成週次 |
| EQUITY_NEWS_TIMES | 06:15,09:00,11:30,14:00,18:00 |
| EQUITY_MODEL_WARMUP | 預設開啟；0 可交由外部服務自行預載 |
| EQUITY_MODEL_LOAD_TIMEOUT_SECONDS | 每個模型預載 180 秒 |
| QWEN_VISION_CONTEXT_TOKENS | 4096，可依模型需求調整 |
| EQUITY_RETRY_SECONDS | 1800 秒 |
| EQUITY_JOB_TIMEOUT_SECONDS / EQUITY_NEWS_TIMEOUT_SECONDS | 7200 / 900 秒 |

路徑設定可使用相對路徑。若需要外接磁碟，絕對路徑只放在部署環境變數，勿寫入 source。跨機器搬移後重建 .venv，並提供該平台的 Ollama；Windows bundled exe 不能在 Linux/macOS 執行。

## 模型

保留原 `taiwan-stock-qwen`（Qwen3.8-27B）文字模型及 `qwen3-vl:8b-instruct` 圖片模型。這次在 32 GB GPU 主機上，預載使用 16K／4K context，合成兩檔持股圖片辨識約 5.7 秒且數值符合預期。這是合成圖片測試，不代表所有券商版面都有相同準確率。

```sh
uv run --frozen python -m equity model-server
uv run --frozen python -m equity warmup
uv run --frozen python -m equity doctor --services
```

文字、視覺與背景模型工作沿用既有 admission／deadline。預載可避免首次圖片辨識把短回覆時間消耗在載入權重；同時執行其他 GPU 工作仍可能增加延遲。缺少模型時依 Ollama 官方流程自行準備，程式不會在請求中下載幾十 GB 權重。

[Qwen3.8-27B 官方模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)。目前先使用已實測配置；較小模型應以實際台股問題、事實一致性與圖片錯誤率比較後再替換。

## 手機與 LINE

1. 先在主機本機 `/portfolio` 建立首個帳號並測試登入。
2. 提供 HTTPS Tunnel／反向代理，連向同一個 HTTP 服務。
3. LINE Developers webhook 指向該 HTTPS 網址加 `/line/webhook`。
4. 設定 LINE channel secret、access token，啟用簽章驗證及 webhook；外網網址改變時同步更新。
5. 登入手機頁面，同意保存方式，於「帳號與 LINE」產生一次性綁定指令，再由本人私訊 Bot。

原 Cloudflare Quick Tunnel 可供臨時連線；重啟後網址可能改變。固定使用應配置具穩定網址的 Tunnel。不能只把 loopback 網址填進 LINE Developers。

一對一持股指令：

```text
持股說明
同意持股保存
新增持股 2330 1張 950
上傳持股
我的持股
確認持股 <草稿代碼>
刪除持股 2330
刪除全部持股
綁定 <一次性代碼>
解除綁定
```

「上傳持股」之後 10 分鐘內的下一張圖片作持股辨識；其他圖片保留原圖表分析流程。先確認股數與成本，再保存。群組不提供個人持股資訊。

## 備份與復原

```sh
uv run --frozen python -m equity backup var/backups/manual
```

目的檔案已存在時拒絕覆寫，請指定另一個版本目錄。備份逐庫使用 SQLite online backup 並檢查完整性；不是跨庫的單一交易。備份包含行情、帳號、持股及存在的 LINE 對話庫；**不含加密／簽章金鑰**。

復原前停止網頁、模型相關寫入及排程，先備份現況。使用 manifest 對應各庫檔案；恢復原持股／LINE 加密金鑰，否則無法解密。帳號庫與 JWT 金鑰也應配套處理；更換 JWT 金鑰會使既有登入失效。用唯讀 SQLite integrity_check 驗證，再發布到各設定路徑，最後執行 doctor。不要把標記為 corrupt 的舊備份發布成正式庫。

```sh
uv run --frozen python -m equity doctor --full
uv run --frozen python tools/run_tests.py tests -q
```

## 常見故障

| 現象 | 原因／處理與驗證 |
| --- | --- |
| Address already in use | 該埠已有程序。核對所屬程序後關閉重複服務，或設定 EQUITY_PORT；以 /healthz 驗證。 |
| 首次圖片逾時 | 權重尚未載入、GPU 資源不足或 request deadline 到期。先 warmup，檢查其紀錄，再使用合成圖片探針；不要直接無上限延長 LINE 回覆時間。 |
| 行情 source_delayed／缺資料 | 檢查來源與目標交易日期。保留缺漏狀態，待官方資料可用後重試；不要填補成假數值。 |
| SQLite .partial 尚存 | 前次遷移／備份未完成。保留該檔檢查原因，勿直接覆寫正式庫；修復後使用新的目的名稱重試。 |
| PyPI UnknownIssuer | 憑證信任環境不符。本次使用 uv --native-tls 成功；可設定 UV_NATIVE_TLS=1 後 sync，勿關閉 TLS 驗證。 |
| 舊快取 Access denied | 屬舊目錄 ACL／擁有權問題。先核對是否只影響可重建快取，不能據此宣稱完整封存已成功。 |

排程狀態在 `var/jobs/daily-state.json`，服務紀錄在 `var/services/`。任何測試或部署故障都應保存具體 exit code、階段與去敏感化紀錄。

## HTTPS 憑證信任
應用入口預設 `EQUITY_TLS_TRUST=system`，透過 truststore 使用作業系統信任的憑證；行情子程序入口也作相同設定。若部署刻意使用 Python 的獨立 CA 配置，可選 `python`。兩者都保留憑證驗證，不提供跳過驗證選項。
本次曾遇到 LINE API 的 CERTIFICATE_VERIFY_FAILED；切換系統信任後，同一個訊息格式驗證 API 回傳 200。新 TLS 設定 helper 由應用與 CLI 入口呼叫；相容層少數既有 adapter 仍保留原 truststore 初始化。
參考：[truststore 官方說明](https://truststore.readthedocs.io/en/latest/)、[LINE 訊息格式驗證](https://developers.line.biz/en/reference/messaging-api/#validate-reply-message)。

每週集保手動補跑：`uv run --frozen python -m equity tdcc --mode tw50`，自選股改用 `--mode watchlist`。更新沿用原官方 TDCC 來源、欄位及保存規則，與每日更新共用寫入鎖。

## Windows 登入啟動與搬移
先預覽：`powershell -NoProfile -File tools/install_windows_task.ps1`。
安裝並啟動：`powershell -NoProfile -File tools/install_windows_task.ps1 -Install -Start`。
排程以目前使用者登入身分執行，不要求管理員權限。登出後不保證持續運作；需要全天服務時，依部署平台配置服務帳號及常駐服務。
專案搬到另一個位置後先重建 .venv，再從新位置重新執行安裝命令。若確認同名工作是原專案，可加 `-ReplaceExisting`；工具會先匯出原排程 XML 至 var/migration。其他專案請使用不同 `-TaskName`。OS 排程必須儲存解析後的執行位置，這是部署設定；原始碼沒有固定使用者或磁碟路徑。
## 臨時 HTTPS 與 LINE endpoint
在私有設定加入 `EQUITY_TUNNEL_MODE=quick`，整合啟動就會管理 cloudflared。預設 off；已有外部固定反向代理時設 external。執行檔從 runtime/cloudflared 或 PATH 找尋，也可設定 CLOUDFLARED_EXE_PATH。
`EQUITY_LINE_WEBHOOK_SYNC=1` 可在通過公開頁面、未登入持股 API 及本機初始化隔離檢查後，先執行 LINE 空事件測試，再更新 webhook URL 並讀回核對。舊 URL 存在 var/services/line-webhook-before.json。此步驟不發送使用者訊息；LINE Developers 的 Use webhook 仍需啟用。
目前網址與驗證狀態位於 var/services/public-endpoint.json。Quick Tunnel 重新啟動會改變網址，不保證可用性，適合搬移驗收。固定使用應換成有穩定網址的部署。
若預設 DNS 對剛建立的臨時網址回覆 NXDOMAIN，但公共 DNS 可解析，可設定 `EQUITY_DOH_URL=https://cloudflare-dns.com/dns-query`。這只影響程式自己的公開入口探針，維持原網域的 TLS SNI、憑證與 Host 驗證，不改 OS DNS；手機瀏覽器仍使用手機自己的 DNS。未設定時使用正常 DNS。
參考：[Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)、[LINE webhook 設定 API](https://developers.line.biz/en/reference/messaging-api/#set-webhook-endpoint-url)。
## 驗證備份可復原
```sh
uv run --frozen python tools/verify_backup.py var/backups/manual --restore-to var/recovery/rehearsal
```
目的目錄必須不存在。工具複製到新位置後核對 SHA-256、完整 SQLite 結構、外鍵及逐表筆數；不覆寫正式資料庫，不代替金鑰復原。正式復原仍依前述停機、保留現況、還原金鑰及 doctor 流程進行。
## 官方行情與逐筆補充資料分開發布
整合 CLI 的 finalize 使用 `--publish-official-core`：只有全部必要官方來源更新成功、官方驗證日期一致，且資料庫完整性及適用的畫面一致性檢查通過，才發布候選資料庫。Fugle 缺漏仍標記 supplemental_pending，完整價量評分及 full_analysis_ready 不會因官方行情成功而變成可用。相容層 pipeline CLI 的預設嚴格行為不變。
減資與面額變更停牌從 TWSE 官方預告／恢復公告及明確停止日期取得；恢復買賣當天不算停牌，不把查詢索引日期當停止日期，不生成假 OHLCV。重大公告 metadata 永久保留。
