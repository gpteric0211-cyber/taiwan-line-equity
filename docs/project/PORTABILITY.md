# 搬到另一個資料夾或電腦
原始碼從模組位置解析專案根目錄；下列命令刻意在專案根目錄執行。不要搬用 .venv，請依 uv.lock 重建。OS 排程需要從新位置重新註冊。
## 同一部電腦換位置
1. 停止本專案服務及更新工作，確認沒有寫入中的 SQLite。先建立並驗證備份。
2. 搬移專案、models、私有設定及資料庫／金鑰。legacy_archive 是舊專案封存，不是執行相依；可另外存放，但應保留讀取例外紀錄。
3. 不帶 .venv；執行 setup.cmd 或 setup.sh。相對路徑設定保持不變。
4. 執行 doctor --full --services；確認服務、資料日期及已確認持股可以讀取，再以新位置更新 OS 排程。
## 從原始碼與備份建立新環境
原始碼 ZIP／Git bundle 不含資料庫、模型、金鑰、憑證與舊專案封存。這些項目必須由擁有者另外保存及搬移。
```sh
git clone taiwan-line-equity.bundle taiwan-line-equity
cd taiwan-line-equity
uv sync --frozen
```
先將已驗證的備份放到正確相對位置，再執行 init。預設對應如下；若原部署使用其他路徑，依私有環境設定為準。
| 備份檔 | 新環境預設位置 |
| --- | --- |
| market.sqlite3 | review_src/data/taiwan50.db |
| accounts.sqlite3 | var/private/accounts.sqlite3 |
| portfolio.sqlite3 | var/private/portfolio.sqlite3 |
| line-memory.sqlite3 | 依 LINE_MEMORY_DB_PATH 設定 |
另行搬移 review_src/.env、.env.line_bot、var/private/portfolio.key、var/private/jwt.key、LINE 記憶加密金鑰及內部 API token。不要把它們加入 Git 或公開壓縮檔。密碼及持股加密金鑰不能靠重新初始化找回。
複製 models/ollama，或依已記錄的模型版本重新準備模型。runtime 需符合目標作業系統；Windows exe 不能當成 Linux/macOS 執行檔。GPU 驅動、作業系統憑證信任與 Python 安裝屬部署前提，不嵌入專案路徑。
```sh
uv run --frozen python -m equity init
uv run --frozen python -m equity doctor --full
uv run --frozen python -m equity run
```
移動或重啟 Quick Tunnel 後，網址會改變。啟用 webhook 同步時，程式先驗證新入口再更新 LINE；固定網域則由原代理設定管理。
## 這次已驗證的範圍
Git 副本放在不同、包含空白的路徑，重新建立依賴與空資料庫，編譯與 1,913 項離線回歸通過。另有原四庫備份於另一個目錄恢復，SHA-256、完整性、外鍵及逐表筆數一致。這不等同於已在另一台作業系統執行；Windows／Ubuntu 與 Python 3.11／3.13 的 CI 已配置，遠端執行結果仍待 GitHub 儲存庫可用後確認。
