# 三次重啟時段可行性：唯讀確認

檢查時間：2026-08-30 15:42:31，Asia/Taipei；15:49:58 記錄後續使用者確認。
沒有修改電源或 App 設定。

## 1. 排程未改期，也沒有重啟

| slot | 現有台北時間窗口 | 排定間隔 |
| --- | --- | --- |
| R1 | 2026-08-31 02:30–03:00 | 距原 M0 超過 12 小時 |
| R2 | 2026-08-31 06:30–07:00 | 距 R1 4 小時 |
| R3 | 2026-09-01 02:30–03:00 | 距 R2 20 小時；距 R1 **剛好 24 小時** |

`line` heartbeat 仍 ACTIVE，`line-phase-d` 仍 PAUSED；本輪没有呼叫 automation_update。
`logs/line_model_shadow/restart_variance_20260830/plan.json` 未更動，SHA-256：
`910fb2ae34d28d139d73ecfe86473401b3bfb7d0b24a69e8e9a7db6e0c392196`。
使用者已明確確認三個時段可保持主機不睡眠、App 開啟，原文：

> 我確認：8/31 02:30、8/31 06:30、9/1 02:30（台北時間）能讓電腦不睡眠、Codex App 保持開啟

確認紀錄：`logs/line_model_shadow/restart_variance_20260830/availability_confirmation.json`。
因此保持既有三時段，不另改期或重建排程；這仍不免除每次即時准入檢查。

若使用者回覆其他可用時段，才同步更新 plan.json、量測計畫與既有 heartbeat；
維持至少數小時間隔及每次300秒預算，不建立第二份重複排程。
若未改期，沿用既有准入／錯過不補跑規則，不將 skipped/missed 算成完成。

## 2. 目前能直接證明的事

完整命令與輸出：`logs/line_model_shadow/norton_support_20260830/power_readonly.json`。

```text
powercfg /getactivescheme
powercfg /query SCHEME_CURRENT SUB_SLEEP
powercfg /a
```

三個命令均 exit 0；原文關鍵片段：

```text
電源配置 GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (平衡)
GUID 別名: STANDBYIDLE
可能設定單位: 秒
目前的 AC 電源設定索引: 0x00000000
目前的 DC 電源設定索引: 0x00000258
```

```json
{"power_line_status":"Online","battery_charge_status":"NoSystemBattery","future_availability_confirmed":false}
```

這是15:42的原始採證值，當時尚未收到確認，故保留 false、不回寫歷史；
後續使用者確認另存上述 confirmation.json，兩者不是矛盾。

因此目前使用 AC，閒置睡眠 timeout 為0（不因這個 idle timeout 自動睡眠）；
DC 值為600秒，但目前查到沒有系統電池，不把 DC 值當作現行供電狀態。
0的含義依 [Microsoft STANDBYIDLE 官方規格](https://learn.microsoft.com/en-us/windows-hardware/customize/power-settings/sleep-settings-sleep-idle-timeout)。

同次查到 ChatGPT.exe 與 codex.exe 程序，部分程序 start time 可讀、部分為 null，
全部保留；沒有把 null 補成假時間，也不把程序存在當作 GUI 健康或未來整晚可執行證明。
本系統支援 S3、休眠、快速啟動；不支援 S0 低電源閒置。没有主動進入或禁止任何電源狀態。

## 3. 使用者已確認，仍保留執行限制

- AC idle=0 不阻止手動睡眠／關機、電源中斷、系統重啟、App 關閉或工具權限失敗。
- 允許喚醒計時器不等於 Codex 已建立 OS wake timer；本次沒有新增或驗證喚醒機制。
- 目前 App 程序存在，不保證 02:30／06:30 仍有可用 App／登入／本地檔案／執行權限。
- 使用者已確認原三時段，無須再追問；若後續主機狀態或授權改變，再依原停止／跳過規則處理。
  使用者承諾保持開啟不等於已測過未來執行結果，不將其當作新的重啟成功樣本。

[OpenAI 本地排程文件](https://learn.chatgpt.com/docs/automations?surface=app)要求本地工作時保持
主機開啟且 App 運行。官方文件複核日期：2026-08-30。

## 4. 階段界線

新增 R1/R2/R3 仍是0/3；不修改已簽核的量測方法。
量測結束或部分跳過後，照量測計畫第7節交付全樣本變異報告，再由使用者核定正式中斷預算。
在使用者看完報告並另行核准前，不部署任何 Phase 1 內容；不討論或實作 Phase 5。
Norton 支援稿是文件交付，不代表 TLS 問題已修復，也不授權修改網路或抗毒設定。
