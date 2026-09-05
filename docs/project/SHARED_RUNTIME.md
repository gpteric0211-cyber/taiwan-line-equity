# 網頁、LINE Bot 與更新共用同一台主機

## 日常操作

| 操作 | 根目錄入口 |
| --- | --- |
| 開網頁 | `啟動台股分析系統.bat` |
| 開 LINE Bot | `啟動LINE股票機器人.cmd` |
| 一鍵更新行情 | `一鍵更新今日分析資料.bat` |
| Linux／macOS 開網頁 | `sh start.sh --open-browser` |

網頁與 Bot 兩個按鈕都呼叫 `equity start`。第一次啟動背景 supervisor；已啟動時驗證專案／資料庫身分、runtime ID 與 supervisor 鎖，再沿用原程序。網頁按鈕只多開瀏覽器。關閉瀏覽器或按鈕視窗不會關閉 LINE。首次載入模型及資料庫健檢可能需要數分鐘。

本機網址由 `EQUITY_HOST`／`EQUITY_PORT` 決定，啟動結果列出行情 `/` 與持股 `/portfolio`。預設為 loopback 的 8056。LINE webhook 與內部 Bot 資料 API 由同一 HTTP 程序提供；另有已設定的 HTTPS Tunnel。手機外網仍使用 Tunnel 網址。

`start` 不會變更已運行服務的埠號或參數。若其他服務占用設定的埠、另一份專案占用埠或舊版 runtime 未升級，會明確拒絕啟動。先確認所屬服務再停止／重啟；不要隨意結束所有 Python 程序。`start_dashboard.py` 與 `scripts/start_line_bot_stack.py` 為保留的舊維護實作，日常使用上表按鈕。

## 共用資料庫與更新

- 市場資料庫預設為 `review_src/data/taiwan50.db`。統一服務、Bot repository、手動更新與排程使用 `core.market_database_config` 選擇同一份資料；`TAIWAN50_DB_PATH` 相對於 `review_src/`，與執行命令的工作目錄無關。
- 帳號、加密持股、LINE 對話仍使用原有獨立私有資料庫。它們不會被行情副本發布覆蓋。
- Canonical／shadow 分析會保存產物至市場庫，相關 POST 也共用更新寫入鎖；更新期間暫緩產物保存並回覆 503／稍後重試，避免使整批候選失效。一般查詢不受此寫入鎖限制，LINE 沿用既有 fallback；這不等於保證更新期間所有寫入型操作都立即成功。
- 網頁啟動不進行自動資料修補。每日排程、一鍵更新、新聞、集保更新共用既有寫入鎖。遇到另一個更新工作時依設定等待或回報忙碌，不能同時發布；可查看更新視窗及 `logs/post_close_scheduler/isolated_publish.lock` 對應的工作紀錄。
- 更新先建立一致快照、在候選庫更新，並通過原資料品質與 SQLite 完整性檢查。這段期間仍讀取上一個已發布版本。
- 市場連線持有共享存取鎖；完整 HTTP API 請求也持有共享鎖。最後替換資料檔前，發布者取得排他鎖，等待已開始的查詢結束，然後原子切換已驗證的候選檔。此時新的查詢可能短暫等待；LINE webhook 入口與健康檢查不會因這個鎖停止服務。
- 切換後的首個 API 請求清除行情列、評分與實務分類快取。`X-Market-Generation` 回應標頭可核對兩個 API 讀取的版本。
- 若活躍資料被其他寫入者改動、仍有未處理 WAL、讀者未於期限內釋放或檔案被外部工具占用，候選不覆蓋正式資料。排查實際錯誤後重試；不要刪除 WAL／SHM 或鎖檔來強行解除。鎖檔存在不代表有鎖，作業系統會在程序結束後釋放鎖。

候選已在切換前完成完整性驗證；`os.replace` 只原子移動同一個檔案，不在排他區段重新掃描十多 GB 資料。`.previous.db` 保留上次版本。分析公式、金融數值、品質門檻及歷史保留規則不變。

此協調適用於專案的市場連線、API、一鍵更新與排程。外部 SQLite 管理軟體或自行撰寫的直接連線不會自動參與；維護前請關閉外部連線。支援本機檔案系統，不把 DB 放在雲端同步或共用網路磁碟上進行多主機寫入。

實作依據：[Microsoft 共用／排他檔案鎖](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)、[SQLite 使用中資料庫的更名風險](https://www.sqlite.org/howtocorrupt.html#unlinking_or_renaming_a_database_file_while_in_use)。Windows 使用 LockFileEx，POSIX 使用 flock；兩者都與 SQLite 自己的交易鎖分開。

## 驗證與維護

```sh
uv run --frozen python tools/run_tests.py tests/test_shared_runtime.py -q
uv run --frozen python tools/run_tests.py tests -q
uv run --frozen python tools/check_shared_runtime.py --url http://127.0.0.1:8056 --public
```

離線合成測試涵蓋跨程序讀者、重複／同時啟動、查詢與發布重疊、Bot／網頁切換後快取一致、活躍資料改動保護、發布失敗及相對路徑。離線測試不操作正式庫、不呼叫模型、不向 LINE 使用者發送訊息。第三個命令是選用的上線查核：改成實際本機網址，使用拋棄式帳號測試正式行情讀取；`--public` 另外驗證現有 Tunnel 與 LINE 空事件，不傳送聊天訊息。

`EQUITY_START_TIMEOUT_SECONDS` 預設 1000 秒，供首啟模型與大型資料库健檢；`EQUITY_DB_ACCESS_TIMEOUT_SECONDS` 預設 180 秒，API 等待發布超時會回覆 503 與 Retry-After。發布等待讀者沿用 60 秒上限。正常切換區段只有檔案操作，模型分析耗時不屬於檔案切換時間。

套用新版本須重啟共用服務一次，讓所有連線載入新的鎖協定。Windows 已安裝登入工作時可停止並啟動該工作；日常按鈕隨後會沿用它。重啟 Quick Tunnel 可能更換手機網址，應讀取新的 `var/services/public-endpoint.json` 並實際驗證。不要把舊狀態檔當成服務正在運行的證據。
