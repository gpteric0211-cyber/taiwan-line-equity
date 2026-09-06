# 會員、註冊與密碼：開發驗證（2026-09-06）

## 手機帳號頁、獨立後臺及社群登入更新

本節取代下方較早的帳號／部署狀態。後臺登入已移除大段介紹，使用獨立 Cookie、
管理 session 與每次請求的角色檢查。使用者已明確要求後臺免手機驗證；
一般新會員仍須手機驗證。新增登入、明確登出與會員修改前後紀錄。
`setup-owner.cmd`／`setup-owner.sh` 提供本機互動建立第一位擁有者；
仍需 SMTP 與 Email 驗證，沒有預設帳密、沒有自動提升現有會員。

客戶端新增 LINE／Apple／Google 綁定後登入，state 單次消耗、nonce、JWT/官方
ID-token 驗證與 Google/LINE PKCE；密碼確認後才綁定或解除，不按相同 Email 自動合併。
資料庫只保留帶金鑰的識別雜湊，不保留社群 token/profile。OAuth 回程 GET 不寫 DB，
轉 POST 完成流程；存取日誌遮除 callback query。修改密碼須舊密碼、確認新密碼及 Email OTP。

手機號碼正規化後，跨帳號共享臺灣日期每天最多 5 次發送預留，第 6 次不呼叫供應商；
維持冷卻與全站預算。使用者只允許免費方案，因此介接僅接受 Twilio Trial 帳戶，
付費 Full 帳戶在發送前拒絕，預設停用。試用僅供預先核准測試號碼；
任意臺灣客戶的永久免費 SMS 方案尚未確認，不能宣稱正式註冊已開放。

驗證：完整隔離回歸 **1,968 passed，1 個既有套件警告，52.34 秒**；
Python 語法與 diff check 通過。320/390/768 px × 4 頁無橫向溢出，
實際瀏覽器登入／角色授權／稽核／登出通過，零頁面 JS 錯誤。
證據 `var/test-results/auth-admin-social.xml`、`var/qa/auth-mobile-results.json`；
手機截圖 `var/qa/admin-login-compact.png`、`var/qa/customer-login-compact.png`。

正式帳號庫已先備份再升級至 v7，共用服務重啟為 Running；
health/account/members/portfolio/social-options 回覆 200，未登入 admin/quotes/detail 回覆 401。
LINE endpoint 同步且官方空事件測試成功，沒有發送真實訊息。
原排程設定為 disabled 但執行中；本次暫時啟用重啟後已還原 disabled 設定，仍在 Running。
此輪未修改分析公式、歷史保留、行情資料库、會員實際角色或金流。
Email／社群／SMS 真實服務尚未設定；擁有者由使用者透過工具自行建立。
風險：高（驗證與授權），已覆蓋跨帳號、重播、簽章、到期、撤銷、目的不符 OTP 與每日限額。
新提交的 GitHub CI 結果記於 PR #2；下方舊 SHA 的 CI 不代表本輪結果。
操作與外部前提見 [帳號設定](project/ACCOUNT_SETUP.md)。

部署與 CI 補充（08:00 後）：正式帳號庫已透過備份後遷移升級至 v5，
共用服務已恢復 Running；`/account`、`/members`、`/portfolio` 公開頁全部 200，LINE endpoint
啟用且官方空事件成功，四種會員提示的官方 validate/reply 全部 200，未發送真實訊息。
證據 `var/qa/member-deployment.json`。Email／SMS 設定目前均未完成，註冊入口保持停用；
擁有者尚待使用者指定 Email。下方開發階段「尚未遷移／未重啟」敘述已由本段取代。

提交 `4a821029428099119abc70a9b4acfbeb6973dfa9` 已推送，草稿 PR #2 的四組 CI 全部成功。
Push run 34000002596 有一項 Windows/Python 3.11 失敗：既有測試
`tests/test_canonical_model_candidate_service.py:227` 比較 `30.00000000000003 <= 30` 不成立，
該工作 1 failed / 1942 passed；其餘三組成功。此測試與對應 service 均未被本次提交修改。
使用者已明確核准固定測試時鐘：僅替換該測試中 service 的 time 綁定，
monotonic 固定 1000.0、deadline 固定 1030.0，保留真實 perf_counter_ns 與原本 5–30 秒斷言。
未修改正式服務邏輯、重啟服務或重寫 main。相關 4 項測試通過；完整隔離回歸
1,943 passed、1 個既有套件警告（41.02 秒），語法檢查通過。
證據 `var/test-results/ci-clock-fix.xml`；新提交的遠端 CI 結果另記於 PR #2，須同時確認 push 與 PR 工作。

- [PR #2](https://github.com/gpteric0211-cyber/taiwan-line-equity/pull/2)
- [失敗工作](https://github.com/gpteric0211-cyber/taiwan-line-equity/actions/runs/34000002596/job/101397039230)
- [通過的 PR workflow](https://github.com/gpteric0211-cyber/taiwan-line-equity/actions/runs/34000039655)
- 已核准並套用提案：`var/qa/proposed-ci-clock.patch`；未放寬斷言、跳過測試或變更正式程式。

- 分支 `feat/member-administration` 基於 `7b718fff906d25ff4662b970870cbb4bc3faf648`；原 main 未重寫。
- 帳號庫新增交易式 v2–v5 升級：會員資格/角色/稽核、憑證版本、手機驗證及 LINE 身分對照。
  `equity.membership_admin migrate` 先建立並檢查 SQLite 備份。正式帳號库尚未執行此命令。
- `/members` 管理員名單、搜尋、分頁、限期/永久招待、角色調整與操作紀錄；版本衝突和重送檢查，
  寫入交易內重新驗證權限。擁有者須指定現有已驗證帳號，本輪未自動提升任一帳號。
- `/account` 註冊雙密碼、Email/手機驗證、登入、修改密碼與忘記密碼。舊憑證在密碼變更後撤銷。
  SMTP 缺設定不假回報成功、不記錄 OTP；Email/簡訊均有重寄與嘗試上限。新會員手機未驗證時阻擋會員 API。
- Twilio Verify 僅模擬測試；尚無真實送達證據。信用卡、LINE Pay、自動月繳、USDT/USDC 結算未串接/啟用。
  使用者目前為無統編個人，需依官方資格申請服務。詳見 [會員操作與申請限制](project/MEMBERSHIP.md)。
- 分級開關 `EQUITY_MEMBERSHIP_ENFORCEMENT` 預設關閉；開啟後 Web AI/圖片與 LINE 分析共用到期狀態，
  免費持股與刪除功能保留。未更動分析公式、行情更新或歷史保留規則。
- 相關 42 項測試通過；完整隔離回歸 **1,943 passed，1 個既有套件警告**，
  報告 `var/test-results/member-account-complete.xml`。新增頁面 JS 語法通過。
- CUA 沙箱失敗後，以內建 Playwright/Edge 在隔離合成帳號預覽完成 390 px 手機檢查：
  註冊雙密碼欄位、招待會員操作、修改密碼均通過，零頁面 JS 錯誤。截圖 `var/qa/account-register-mobile.png`、
  `var/qa/member-admin-mobile.png`；這是測試資料，不是正式會員畫面。
- 正式服務未重啟，公開註冊及收費尚未上線。保留原有 tracked LINE WAL/SHM 刪除狀態，不納入提交。

---

# 共用網頁、LINE 與行情更新修正（2026-09-06）

- 新分支 `fix/shared-web-line-runtime` 自 `4cab294548a226ae644179f88d2654c3b9c444d5` 建立，未重寫舊提交。GitHub main 已重新讀取核對；舊提交的 Portable regression 四個 Windows／Ubuntu、Python 3.11／3.13 工作全部成功，舊「0 個可存取儲存庫」判斷已失效。
- 網頁與 LINE 按鈕改用同一背景 supervisor；新增啟動鎖、專案／市場庫識別及 runtime ID 驗證。正式主機已實測冷啟 LINE 後再按網頁／LINE，均沿用同一 PID；命令結束後服務仍持續運行。
- `core.database_access` 以 Windows LockFileEx／POSIX flock 保護市場連線；API 請求持有共享鎖，候選驗證在鎖外完成、切換時取得排他鎖。切換後清除三種衍生快取並回報 X-Market-Generation。六個既有資料服務補上明確關閉連線，交易語意不變。
- 一鍵與排程更新使用相同市場路徑解析，修正相對路徑受工作目錄影響的問題。Canonical/shadow 產物保存 POST 也共用寫入鎖；更新中回覆 503／Retry-After，普通讀取仍可使用已發布資料。其他未參與的寫入仍會使候選安全拒絕發布，不能宣稱所有寫入皆可無等待並行。
- 修改前 1,915 passed；新增 11 個合成並行測試後 **1,926 passed，1 warning**。全部修改的 Python 檔 py_compile 通過。完整報告：`var/test-results/shared-runtime.xml`。一鍵更新以 plan-only 驗證原九階段，沒有重跑正式行情更新。
- 回歸曾指出啟動旗標遺漏及唯讀缺檔時建立目錄，均已修正並完整重跑。上線查核工具曾因 Secure cookie 在 HTTP 合成客戶端未送出、台灣 50 路由名稱不符而回報 401／404；改用既有登入回傳的 Bearer token 與實際路由，未調弱產品驗證。
- 市場公式、評分、資料品質門檻與歷史保留規則不變；原有帳號、持股、對話庫未搬移或重建。開始工作前既有兩個 tracked LINE WAL／SHM 刪除狀態保留，未把它們當成本次程式碼變更。
- 操作說明及限制：[共用服務](project/SHARED_RUNTIME.md)。本次新提交的遠端 CI 與最後服務查核結果於交付時另行回報；後方各節屬歷史紀錄。
- 上線查核工具已完成：首頁、自選股、台灣 50、2330 明細全部 200；三個網頁資料 API 與正式 Bot daily API 的 generation 相同，行情日期為 2026-09-04。簽章空 webhook 200、錯誤簽章 401；公開頁 200、未登入持股 API 401、遠端初始化關閉，LINE endpoint 啟用且官方空事件成功。報告 `var/qa/shared-runtime-live.json` 不含測試帳密，測試私有庫已自動清除。

---

# Taiwan Line Equity 重建：2026-09-05 歷史審查狀態
本節描述新專案 2026-09-05 最後查核；後方為原專案歷史紀錄，不能當成目前部署的驗證結果或新的操作指示。
- 初始遷移 106 個業務表、19,242,910 筆逐筆摘要相同；原保存期限、分析公式與資料品質門檻不變。
- 四庫備份於另一目錄實際復原，SHA-256、完整性、外鍵及筆數一致；金鑰分開保存。模型與 runtime 共 995 檔約 53.18 GB 的 SHA-256 核對通過。
- 2026-09-04 官方行情已於 22:57:44 安全發布。1,978 檔母體完整分類，原子發布前候選完整性通過，發布後外鍵違規 0。Fugle 歷史逐筆仍缺漏、full_analysis_ready=false；未開放不合格價量評分。
- 新位置乾淨 Git 安裝及 1,913 項回歸通過；最後啟動順序修正後完整離線回歸 **1,915 passed**。一項上游 deprecation warning，無測試失敗。跨 OS CI 尚待 GitHub 遠端。
- API `/`、自選股、台灣 50 與 2330 明細通過；行情頁最新日期 9/4。390 px 手機持股確認／重載、新聞與綁定流程通過。
- 真實 Qwen3.8-27B 已以確認的合成持股走 model 路徑，約 5.45 秒並通過既有財務事實檢查；精確分析 LINE payload 官方驗證 200。Qwen3-VL 8B 合成持股圖片辨識通過，保存前需本人確認。
- Quick Tunnel 權限檢查與 LINE 空事件測試通過，webhook 已切換並啟用；沒有發送任何測試訊息。正式帳號密碼須本人在本機建立。
- Windows 父程序停止測試證實整組所屬子程序回收，其他服務保留。新 OS 排程最後實際查核 Running／Enabled=true；未以真實重新登入或開機測試。
- 舊專案封存複製 246,600 個可讀檔案；77 個舊快取／測試暫存目錄被 Windows 拒讀。原 Desktop 專案保留，沒有宣稱全部移走或刪除。
- 新 Git 工作樹已有提交；GitHub 連線可見儲存庫仍為空，尚未 push。原始碼 ZIP／Git bundle 不含資料庫、模型、金鑰或私有設定。
- 風險與限制：中。外部行情與模型仍可能失敗、Quick Tunnel 不保證穩定網址、LINE webhook 背景處理尚非持久佇列。測試提供可重現證據，不承諾零缺陷。
操作與逐需求結果見 [新專案說明](../README.md)、[遷移紀錄](project/MIGRATION.md)、[可攜部署](project/PORTABILITY.md)。本次交付不變更 V2／V3 權重或原 rollout 授權。

---
# Codex Review Packet — 排程與全市場盤後更新複查（2026-09-05）

## 本次複查結果

- 系統唯讀查驗：原有 6 個股市更新 Windows 工作全部 Disabled；未發現正在執行的更新／scheduler Python 或 cmd 程序。
- 有效設定：AUTO_REFRESH_MARKET_DATA_ON_START=False、AUTO_UPDATE_TW50_ON_START=False。
- 專案 review_src/scripts 未找到 place_order、submit_order、buy_order、sell_order、Shioaji 或 Fubon 下單實作。此结論只涵蓋本專案，不能代表其他券商軟體或遠端帳戶。
- 最新已收盤交易日為 2026-09-04；正式 DB 的當日日線、技術、法人、融資融券借券、估值、分價量、內外盤及每日籌碼筆數全部為 0。
- DB 最新行情仍是 2026-09-03（1,942 檔）；9/4 Fugle trades 原始快取為 0。新驗收報告 daily_update_complete=false、safe_to_publish=false、SQLite quick_check=ok。
- 這次是檢查及修正一鍵程式；沒有執行全市場正式 DB 寫入，也沒有再次宣稱首次 9/3 更新等於最新 9/4 已更新。

## 本次修正

- scripts/run_isolated_manual_daily_analysis_update.py：凍結目標日期並在成功發布後以相同日期、相同 DB 驗收；plan-only/help 不建立快照。
- 一鍵更新今日分析資料.bat：移除會驗錯日期的第二次預設日期驗收；非零結果要求查看發布 receipt，避免把「已發布但不完整」誤說成未發布。
- scripts/run_manual_daily_analysis_update.py：補日游標改從最後完整 publication 起算，重試尾端部分寫入日；跨日跳過 capture 明確標記；新增第 8 階段月營收／政策／已設定授權新聞，完整驗收改第 9 階段。
- scripts/verify_daily_analysis_update.py：用股票代碼集合驗證官方 OHLCV 與各衍生表，額外股票不能抵銷缺股；空母體不能通過。新增漏分類代碼、分價量分數表及 TW50 成分代碼核對；舊日期報告不能讓今日 capture/scoring 變成 ready。美股／ETF、夜盤、公告、交易限制及外部事件納入完整度，partial 不等於 ok；已設定新聞來源失敗不可被忽略。
- tests/test_manual_daily_analysis_update.py：新增異碼等筆數、partial 來源、尾端補日、指定日期與 DB 發布後驗收、失敗候選不驗舊 DB 等回歸案例。
- docs/MANUAL_MARKET_UPDATE_GUIDE.md：更新 9 階段、來源範圍與跨日分價量無法保證回補的限制。
- 未改動評分公式、RSI、裁判層、支撐壓力或 API 行為。

## 驗證及限制

- 4 個修改 Python 檔（含測試）py_compile 通過。
- 手動更新／盤後／隔離發布／全市場順序及 readiness／分價量 lifecycle 共 58 tests passed。
- BAT --plan-only --date 2026-09-04 exit 0，列出 9 階段、writes_db=false。
- 修改後 verifier 實際讀正式 DB 完成，quick_check=ok，9/4 缺口明確報告；未用舊報告聲稱新日期完整。
- 風險：中（更新協調及驗收條件改變；新外部資料階段已接既有來源服務，但本次沒有實際網路抓取或正式發布驗證）。
- 新報告 docs/MANUAL_EXTERNAL_EVENTS_REPORT.json 會在首次執行新增階段後產生；目前缺少 receipt 正確視為尚未驗證。
- 當日分價量在沒有快取／逐筆來源時，現有 Fugle intraday 不保證跨日回補。9/4 的缺口須取得合法歷史逐筆／分價量來源，不能靠無限重跑 BAT 解決。
- 券商分點成本原始資料、人工驗證公司行動及研究／模型產物不是已完成的每日行情抓取項目；不能把既有估算籌碼成本說成分點資料完整。
- 下一步：保持排程停用，往後於交易日當天盤後手動擷取並保存 Fugle 資料；9/4 需歷史來源才能完整補回。現有安全發布 gate 在分價量缺少時仍會拒絕候選，不能宣稱全市場更新已完成。

---

# 歷史紀錄 — 全市場手動每日更新（2026-09-04）

## 任務與結論

- 任務：停止所有既有每日自動更新，改成可顯示進度的一鍵手動更新；範圍為全部有效上市、上櫃股票，不限台灣 50。
- 更新目標交易日：`2026-09-03`。
- 結果：首次隔離更新已安全發布；正式 DB 的完整 `PRAGMA integrity_check` 為 `ok`。
- 完整度：`safe_to_publish=true`、`daily_update_complete=false`。已取得的官方列及衍生資料彼此一致，但上游來源對 2 檔股票尚未提供可核對的 9/3 分類，因此不得宣稱全市場 100% 齊全。

## 已停用的 Windows 排程

以下 6 個工作均於完成後重新查驗為 `Disabled`：

1. `Taiwan Stock External Analysis Context`
2. `Taiwan Stock Fugle Full-Market Capture`
3. `Taiwan Stock Official EOD Reconciliation`
4. `Taiwan50 Fugle Watchlist Price Volume Update`
5. `Taiwan50 TDCC Equity Update`
6. `Watchlist TDCC Equity Update`

App 啟動旗標亦為：`AUTO_REFRESH_MARKET_DATA_ON_START=False`、`AUTO_UPDATE_TW50_ON_START=False`。
Repo 內沒有 `.github` 目錄，因此沒有另外存在的 GitHub Actions cron；系統層面所有指向此 repo 的排程動作就是上述 6 項，均已停用。

## 一鍵更新資料流

根目錄入口：`一鍵更新今日分析資料.bat`。

1. 補齊先前遺漏的官方交易日。
2. 重播同日已保存的 Fugle 原始 JSON。
3. 擷取尚缺的全市場逐筆成交、分價量與內外盤資料，並每 30 秒顯示進度。
4. 寫入官方收盤 OHLCV、估值、技術指標、三大法人、融資、融券、借券及估算籌碼成本。
5. 更新全市場每日籌碼動能。
6. 產生台灣 50 相容批次，不作為全市場母體。
7. 更新 TDCC 週頻股權分散資料。
8. 逐表驗收來源日期、列數、分價量核對狀態與 SQLite 完整性。

所有寫入先在 SQLite 一致性快照的隔離候選 DB 執行。候選通過安全發布 gate 與完整性檢查後才原子替換正式 DB；正式 DB 在更新期間若有其他 writer 變動，發布會中止。

## 首次更新驗收

- 有效上市／上櫃股票：1,978 檔。
- 9/3 官方 OHLCV：1,942 檔。
- 9/3 官方無交易分類：34 檔。
- 尚未被官方 OHLCV 或官方無交易分類涵蓋：2 檔，`1563 巧新`、`6949 沛爾生醫-創`。
- 技術向量／技術快照：各 1,942 檔。
- 經官方量能核對的分價量、分價量輪廓、分價量分數、內外盤、每日籌碼動能：各 1,942 檔。
- 三大法人：1,850 檔。
- 融資／融券／借券餘額：1,874 檔。
- 官方估值：1,967 檔。
- 估算籌碼成本：1,942 檔。
- 台灣 50 相容收盤批次：50/50，0 errors。
- TDCC：最新官方週日期 `2026-08-28`，1,977 檔；相對交易日 6 天，通過 14 天新鮮度門檻。
- Fugle 快取重播：1,437,545 筆逐筆成交，成功涵蓋 1,942 檔。
- 正式 DB：13,238,919,168 bytes；完整 `integrity_check=ok`。
- 發布前舊正式 DB 保留為 `review_src/data/taiwan50.previous.db`。

來源缺口沒有被推定為停牌或無交易，也沒有填入假數字。這是 `daily_update_complete=false` 的主要原因；9/2 亦有相同的點時母體缺口，因此 previous-day continuity gate 未通過。分價量裁判層的 30 日／80% 歷史覆蓋門檻仍未累積完成，與「今日分價量已寫入」分開呈現。

## 建立或修改的檔案

- `一鍵更新今日分析資料.bat`：使用相對路徑選擇 Python、呼叫隔離更新、發布後再次驗收，並顯示報告位置與 exit code。
- `scripts/run_manual_daily_analysis_update.py`：協調 8 階段全市場更新與進度報告。
- `scripts/run_isolated_manual_daily_analysis_update.py`：在隔離候選 DB 執行，沿用一致性快照、發布鎖、正式 DB 指紋檢查與原子發布。
- `scripts/verify_daily_analysis_update.py`：唯讀逐表驗收，分離 `safe_to_publish` 與 `daily_update_complete`。
- `tests/test_manual_daily_analysis_update.py`：驗證全市場母體、fail-closed、來源部分完成可安全發布、retryable capture、plan-only 與點時母體契約。
- `docs/MANUAL_MARKET_UPDATE_GUIDE.md`：說明手動入口、全市場範圍、資料項目、報告及 gate 語意。
- `docs/MANUAL_DAILY_ANALYSIS_UPDATE_REPORT.json`：首次更新分階段結果。
- `docs/MANUAL_DAILY_ANALYSIS_UPDATE_VERIFICATION.json`：首次更新逐表完整度結果。
- `logs/manual_daily_update/isolated_update_latest.json`：隔離候選發布 receipt。
- `docs/CODEX_REVIEW_PACKET.md`：本次最新審查摘要。

## 驗證與風險

- Python `py_compile`：通過。
- 手動更新、盤後更新、全市場順序／readiness、分價量 capture／service 測試：73 passed。
- BAT `--plan-only`：通過，不建立快照、不抓網路、不寫 DB。
- 首次正式執行：候選 pipeline exit 0、`published=true`、正式 DB 完整 integrity check 通過。
- 風險等級：中。原因是官方來源尚缺 2 檔分類且多日分價量評分覆蓋尚未達標；不是 DB 損壞或已取得資料落地失敗。

## 下一步

保持所有排程停用。稍後官方來源補齊時再次雙擊 `一鍵更新今日分析資料.bat`；流程會利用已保存快取，只補仍缺的來源並重新驗收。在 `daily_update_complete=true` 前，不得把今日資料宣稱為全市場完全齊全，也不得讓不合格分價量進入裁判層評分。
