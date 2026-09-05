台灣50 v2.32 - FinMind Token 防呆修正版

修正內容：
1. 若 .env 的 FINMIND_TOKEN 無效或過期，FinMind 回傳「Token is illegal」時，程式會自動停用該 token。
2. 同一輪執行期間會改用免 token 模式重試，避免 50 檔 × 3 種資料一直刷 HTTP 400。
3. /api/config 會回傳 finmind_token_disabled_reason，前端/除錯可看到 token 已被停用原因。
4. 不更動成交量資料；volume 維持 shares。

建議：
- 如果你沒有有效 FinMind token，.env 的 FINMIND_TOKEN 留空即可。
- 如果你有新 token，只填 token 本體，不要填 Bearer。
- 修改 .env 後要關掉伺服器視窗，再重新執行 RUN_DASHBOARD.cmd。
