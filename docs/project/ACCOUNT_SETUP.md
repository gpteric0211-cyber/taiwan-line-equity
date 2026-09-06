# 帳號與後臺設定

## 第一位後臺管理員

先在主機的 `review_src/.env` 設定 SMTP 寄信服務。從專案根目錄執行
`./.venv/Scripts/python.exe -m equity.membership_admin migrate`，再雙擊
`setup-owner.cmd`。macOS/Linux 使用 `sh setup-owner.sh`。
工具會要求 Email、密碼、再次輸入密碼及 Email 驗證碼；密碼輸入不顯示，
不存入命令列、文件或日誌。沒有預設帳密，也沒有公開網頁的擁有者建立入口。

帳號已存在時，先於帳號頁完成 Email 驗證，再執行：

```text
./.venv/Scripts/python.exe -m equity.membership_admin owner --email 你的已驗證Email
```

只有第一位擁有者可用此初始化方式。登入 `/members` 後，找到會員、
選「調整資格」→「管理權限」→「會員管理員」，填原因後儲存。
只有擁有者可授予／移除管理權限。免費、月費、永久招待資格不會自動給予管理權限。
需要專用管理帳號時，第一位擁有者可使用與日常會員不同的 Email 建立。

後臺使用獨立 Cookie 與一小時登入憑證，與客戶端登入分離；後臺免手機驗證，
一般新會員仍須手機驗證。每次請求重新檢查啟用狀態、角色與密碼版本。
明確登出會撤銷該後臺憑證；修改密碼後舊前後臺憑證皆失效。
修改密碼要求目前密碼、新密碼、確認新密碼與寄至目前帳號 Email 的單次驗證碼。

擁有者在「管理紀錄」可查看登入、明確登出、操作者、被修改會員、
修改前後內容與原因。關閉瀏覽器無法可靠視為登出，不能把關閉時間當登出時間；
未按登出者於憑證到期後失去存取權。修改紀錄與會員變更在同一交易保存。
紀錄只對授權管理者顯示，不包含密碼、OTP、第三方 token 或原始社群識別碼。

## LINE、Google、Apple 登入與綁定

先完成 Email／手機註冊驗證，以 Email 登入，再到「帳號安全」輸入目前密碼，
選擇綁定第三方帳號；之後可使用該平台登入同一會員。不同會員不可綁定同一平台帳號，
不依相同 Email 自動合併。解除綁定也需目前密碼，Email 密碼登入仍可使用。
此功能是網站登入；LINE Bot 對話與持股的既有綁定流程仍在研究空間操作。

在 `review_src/.env` 設定（不要提交真實密鑰）：

- `EQUITY_AUTH_PUBLIC_URL`：固定 HTTPS 網站 origin，不含路徑。
- `EQUITY_OAUTH_GOOGLE_CLIENT_ID`、`EQUITY_OAUTH_GOOGLE_CLIENT_SECRET`。
- `EQUITY_OAUTH_LINE_CLIENT_ID`、`EQUITY_OAUTH_LINE_CLIENT_SECRET`：LINE Login channel，
  不是 Messaging API channel。
- `EQUITY_OAUTH_APPLE_CLIENT_ID`：Apple Services ID；
  `EQUITY_OAUTH_APPLE_CLIENT_SECRET`：依 Apple 規範簽發的 client-secret JWT，須於到期前更新。

以你的固定網站 origin 加上下列路徑作為平台允許的 redirect URI：

| 平台 | Callback 路徑 |
| --- | --- |
| Google | `/api/auth/social/google/return` |
| LINE | `/api/auth/social/line/return` |
| Apple | `/api/auth/social/apple/callback` |

Google／LINE 回程 GET 只提供轉送頁，不寫資料庫；瀏覽器轉成 POST 後才交換授權碼。
Apple 使用 form_post。流程驗證 browser Cookie、state、nonce、單次消耗、平台及客戶端識別；
Google／Apple 驗证 JWT 簽章、發行者、audience 與有效期；LINE 使用官方 ID token 驗證。
Google／LINE 使用 PKCE。帳號只保存具金鑰雜湊的識別對照，不保存社群 token、Email 或個人檔案。
Cookie 僅 HTTPS 且 HttpOnly，回程頁不傳 Referer；Uvicorn 日誌遮除 OAuth callback 查詢字串。
若另有代理伺服器，也須關閉該 callback 的查詢字串記錄。
單次狀態十分鐘到期，在下一次發起流程時清理；解除綁定會刪除對照。

未填設定的按鈕顯示「尚未開通」。臨時 Quick Tunnel 網址變動時 callback 設定失效，
正式使用第三方登入前須有穩定 HTTPS 網域、平台核准與真實登入實測。
Apple 網站登入須 Services ID 與已啟用 Sign in with Apple 的主要 App ID，
並非僅提供 Apple 個人帳號即可串接。

## 手機驗證與免費限制

同一正規化臺灣手機號碼，依 Asia/Taipei 日期每天最多預留 5 次發送；
跨會員帳號共用計數。第 6 次在呼叫簡訊商前回覆 429。
同時保留 60 秒冷卻、每小時帳號／號碼防濫用與全站每日預算。
發送失敗也占一次額度，避免不確定送達時重送造成額外發送。
號碼加密保存；雜湊用於額度與唯一性檢查。

目前只允許 Twilio 免費試用：設定 `PHONE_VERIFICATION_MODE=trial` 與既有三個
`TWILIO_*` 項目。每次發送前確認帳戶類型為 Trial，Full 帳戶會被拒絕；
預設 disabled。試用只支援平台事先驗證的測試號碼，並受其額度及區域限制。
沒有設定或試用額度用完時，不假裝發送成功，也不自動切換付費。

截至 2026-09-06，Firebase 正式 SMS 驗證需 Blaze 計費；Twilio 試用不是
任意臺灣客戶可長期免費使用的服務。因此「正式客戶、任意臺灣手機、永久免費 SMS」
尚未有可交付方案。正式開放註冊前仍需解決此限制。

官方設定與限制：

- [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect)
- [LINE Login](https://developers.line.biz/en/docs/line-login/integrate-line-login/)
- [Apple 網站登入設定](https://developer.apple.com/help/account/capabilities/configure-sign-in-with-apple-for-the-web/)
- [Firebase SMS 限制](https://firebase.google.com/docs/auth/limits)
- [Twilio Verify 試用限制](https://www.twilio.com/docs/verify/api/verification)
