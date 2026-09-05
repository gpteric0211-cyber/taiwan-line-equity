# LINE 模型共用核心執行契約

版本：設計基準 v1，2026-08-30。

狀態：使用者接受七條契約作為設計基準，僅核准文件與現有 controller 的紅燈測試。
本文件不是 runtime 實作、部署或重構授權。讓 V2 接管使用者回覆完全不在本次討論範圍。
本文件以責任、條件及可驗收結果描述設計，不以示意程式碼取代契約。

## 1. 現況、目的與證據範圍

現有 controller 是單程序、單 worker 的共用模型工作佇列。一般工作由呼叫端排入，
worker 執行 callback，呼叫端等待結果。若 callback 又同步等待排入同一 controller 的工作，
唯一 worker 便可能等待一個必須由自己執行的內層工作。

來源依據：

- [model_admission_service.py 第70行](../review_src/services/model_admission_service.py)：建立單一 worker。
- 同檔第213行：worker 同步執行工作 callback。
- 同檔第310–329行：interactive 入口排隊後等待結果。
- 同檔第356–366行：shadow 入口排隊後無期限等待結果。
- [line_model_shadow_service.py 第361行](../review_src/services/line_model_shadow_service.py)：現有已准入執行函式。
- 同檔第631–655行：shadow 外層入口另含 controller 准入，不能在已准入的 worker 裡再次使用。

目的在於消除「同一工作重複取得准入並自我等待」，不是繞過 GPU 排程，也不是增加 worker 數。
目前已有已准入函式作為拆分起點，但其中仍混有 shadow 開關、背景 timeout、證據寫入；
尚未符合以下共用核心契約，不得僅靠改名或強制開啟旗標宣稱完成。

本轮紅燈僅重現 shadow 類別同步重入自身 controller。它能證明這個排程原語缺乏防誤接保護，
不能證明目前 LINE 呼叫链已採用此錯誤接法，也不是正式服務事故、GPU benchmark 或品質證據。

## 2. 契約一：單一准入責任與核心邊界

外層協調者負責準備請求、既有 DB facts、封包版本、token preflight 與剩餘預算。
每個模型生成工作只能由外層協調者申請一次 controller 准入。
interactive 與 shadow 可使用同一生成核心，但兩者各自保留外層責任，不能彼此呼叫另一個排隊入口。

生成核心只接收已準備好的資料及 controller 核發的執行憑據；不得直接讀 DB、執行研究搜尋、
暖機、分類 pre-pass、記憶壓縮或另外建立模型工作。不得藉由額外 thread 或 callback 繞過這項限制。

執行憑據至少綁定 controller 身分、工作身分、執行緒身分及該次准入的有效狀態。
只能由 controller 在真正開始執行時建立，結束時失效；呼叫者不能任意宣告「已經准入」。
缺失、過期、跨 controller 或跨工作使用憑據都必須拒絕，模型呼叫數為零。

生成後的 validation 與 rendering 共用相同既有邏輯，仍受總預算限制，但不必占用 GPU slot。
shadow 的證據與 ledger 記錄由非 controller worker 的協調端承接；不得把重工作放進
Future 完成 callback，又讓唯一 worker 等待其他排隊結果。
具體承接方式、模組白名單與移動範圍須在未來實作提案列明；本文件不批准新增執行基礎設施。

驗收要求：正常工作恰好一次准入；核心內任何再排隊嘗試被攔下；controller 與外部 I/O 邊界可分別觀測。

## 3. 契約二：重入偵測與立即拒絕

偵測點為 controller 的共同提交邊界，不應散落在每一種模型 wrapper。
若提交來自該 controller 自己的 worker，必須在入列、取消其他工作或改動排程狀態之前拒絕。
規則涵蓋同類重入、跨類重入、on-start callback 與完成 callback；不能只看 category 名稱是否相同。

拒絕應同步產生具名的 ModelAdmissionError，reason code 為 admission_reentry_rejected。
既有外層 wrapper 必須保留此原因，不可轉成無法辨識的通用模型錯誤。
「立即」的機制定義是：不入列、不等待內層 Future、不執行內層 callback，拒絕只做有界身分檢查。

離線測試以 250 毫秒作為拒絕的觀測上限，以 5 秒父程序 watchdog 保護測試程序。
這兩個數字是測試監督常數，不是產品 SLA、GPU P95 或 LINE 平台規定。
只有超时不夠：必須同時確認錯誤原因、內層未執行，且下一筆合法工作仍可完成。

禁止以 inline 執行內層工作、增加 worker、另開 controller 或直接呼叫 GPU 來躲過死結。
guard 是程序內的誤用防護，不是多程序或分散式 GPU 排程保證。

驗收要求：同 controller 的重入先拒絕，佇列不增加內層工作；外層可退出，後續 sentinel 正常完成。

## 4. 契約三：絕對期限與取得 slot 後的預算重算

總回覆期限以既有 webhook ingress 的 monotonic 時間為基準，只計算一次。
扣除既有傳送保留時間及安全餘裕後形成工作停止時間；不得在 handler、入列或 worker 開始時重新給完整預算。

入列前，協調者與 controller 檢查估計排隊時間及工作時間能否符合剩餘期限。
取得 slot 後，再以當下 monotonic 時間重新計算生成可用時間，扣除尚未執行的 validation、rendering 與安全保留。
已消耗的檢索／封包時間不得漏算，也不得再重複扣除同一段歷史等待。

若剩餘生成時間不足、所需預測值未經核對或 category 找不到有效 duration 估計，應拒絕生成並保留原因。
不能因新 category 默認估計為零而自動取得准入。shadow 的背景 timeout 不得當成 interactive 的預算。

模型 adapter 的整體執行上限不能超過重算後的絕對生成期限；HTTP read timeout 或呼叫者等待上限，
不能單獨被當成所有工作均會停止的證明。到期後的行為必須與下一節的取消狀態一致。

驗收要求：同一請求不同排隊長度不重置期限；預算不足時模型呼叫為零；晚到結果不進入成功輸出。

## 5. 契約四：取消、終態與 slot 釋放

工作必須有可區分的狀態：準備中、排隊中、執行中、取消請求中、已確認停止，以及完成／拒絕／失敗終態。
取消請求不等於取消成功；呼叫者 Future 逾時不等於 worker 已停止。

排隊中到期或取消的工作，須在同一排程同步邊界內決定是否還能開始，避免「取消與出列」競態。
取消成功後 callback 永遠不得晚些執行；工作只有一個終態，Future 只能完成一次。

執行中的工作採合作取消。收到取消後，adapter 停止接收結果並終止受控 I/O，
只有 callback 已退出、且模型資源停止条件得到核對後，才能宣告 slot 可用。
若只能證明客戶端串流關閉，不能直接聲稱伺服器或 GPU 工作已停止。

無法在有界時間確認停止時，必須記錄取消未確認，保持容量不可用並讓新請求有界拒絕／回退；
不得假造釋放、增加並行度或開旁路模型。實際資源停止的可觀測訊號與恢復操作仍須後續單獨驗證，
未驗證前不能將這條契約標成已實作或已通過。

清理責任必須唯一：正常完成、exception、取消與逾時相交都不應重複釋放或重複完成 Future。
要保留取消請求、確認退出、資源可用、終態各自的時間與原因，不能只記「cancelled」。

驗收要求：合作取消能釋放；不合作 stub 不能被誤判已釋放；任何失敗後均以後續工作驗證可用性，
或明確回報容量仍不可用。不得在正式服務中用強殺 thread 充當產品取消機制。

## 6. 契約五：單次生成與失敗回退

本設計每個 interactive 請求至多一次模型生成，失敗後不再同步呼叫其他 LLM 補答。
回退由 controller 外的協調者使用既有 DB／referee 結果，保留缺資料、縮減範圍及失敗原因。
不能保證補答時限時，不承諾之後會自動送達。

至少區分重入拒絕、預測 deadline 拒絕、已過期、佇列滿、取消未確認、模型 I/O 失敗及驗證拒絕。
原因不得因 renderer 或通用 exception handling 而遺失，也不得把拒絕計成模型成功。

同一工作不能因呼叫端逾時而重新生成；取消中的結果不得再寫成功紀錄。
不能額外產生同請求的重複 shadow synthesis 來掩蓋失敗。
既有非模型的 deterministic repair 如未改動，只能在剩餘時間內處理，不能藉此擴大模型呼叫數。

驗收要求：每次請求模型呼叫上限可查核；失敗原因保留；不重複送出或錯寫成功記憶。

## 7. 契約六：結果型別、驗證與副作用隔離

模型原始輸出、驗證結果、可呈現文字與稽核資料必須有明確邊界，不能用同一個混合字典任由下游取用。
公開回答邊界只接受通過既有驗證與 renderer 的內容；不得直接顯示 raw JSON、未解析 placeholder 或內部錯誤。

數值、日期、單位、品質、引用及不可變 referee 均沿用既有 contract。
不因抽離核心放寬 validator，也不以此設計宣稱其他已知語意缺口已修好。
缺資料的說明不可補造數值，正常输出仍受既有 sanitization 與 disclaimer 規則約束。

共用核心不寫 canonical DB、不傳 LINE、不決定對話記憶已送達狀態，也不寫 shadow ledger。
這些副作用由對應外層負責，只有實際符合既有成功條件才可記成功。
本輪不討論或實作對外回答 selector 的切換。

驗收要求：raw 輸出不越界；拒絕內容不進成功出口；shadow 記錄不污染使用者已送達對話。

## 8. 契約七：範圍、權限與停點

禁止修改股票公式、裁判層、資料品質規則、DB、模型、env、launcher 或網路設定。
禁止夾帶新聞、vision、分類器接線、局部重啟、熱重載、部署或新的自動排程。
本輪唯一 Python 變更為新增測試檔，現有測試檔及 production 程式維持原樣。

交付紅燈後停止，等待使用者核對。不得將紅燈自動視為修正 controller 或抽離核心的授權。
測試必須保留失敗狀態，不用 skip、xfail、改 assertion 或改 production 程式使報表變綠。

## 9. 測試範圍與驗收狀態

本次依最新收斂要求，只實作「同類重入必須立即拒絕且後續工作仍可執行」：

1. 同一隔離程序先以真實 shadow wrapper 完成普通工作，確認 import、worker 與排程可用。
2. 外層 shadow callback 使用真實同步 shadow wrapper，再提交同類內層工作。
3. 分別觀測外層等待、內層是否執行、佇列深度、實際 worker stack。
4. 再送高優先權 sentinel，檢查是否能繼續處理工作。
5. 父程序最多等五秒，只終止自身建立且 PID 已綁定的測試程序，等待回收並保存 stdout、stderr。
6. 使用「必須立即拒絕並可繼續工作」作為真正 assertion，現行版本應以實測結果決定紅／綠。

測試不 mock controller、Future、worker 或時鐘；沒有模型、HTTP、DB 或正式 webhook 呼叫。
Windows 啟動器與實際 Python PID 必須相同，否則測試監督不合格；以目前 venv 的底層 interpreter
配合同一 venv 依賴目錄解決 redirector 身分差異，路徑均由當前環境解析，未硬編碼。

完整測試碼：[test_model_admission_reentry_contract.py](../tests/test_model_admission_reentry_contract.py)。
紅燈原始資料位於 logs/line_model_shadow/controller_reentry_red_20260830_2148/，
實際結果、hash、失敗與邊界見同輪 [CODEX_REVIEW_PACKET.md](CODEX_REVIEW_PACKET.md)。

仍未新增／未驗收：跨類重入、排隊逾期後不得偷跑、合作與不合作取消競態、1／2／4／8混合併發、
exception／deadline相交。現有相關測試不能自動替代這些新契約案例。
本輪不證明實機 P95、GPU 釋放時間、多程序協調或現行 LINE 呼叫鏈一定會觸發重入。

## 10. 後續決策

唯一下一步是由使用者檢閱紅燈證據及本設計基準。
在收到新的明確核准前，不修改 controller、不抽離共用核心，也不將此文件當成升級完成。
