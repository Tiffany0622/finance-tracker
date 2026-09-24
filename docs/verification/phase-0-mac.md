# Phase 0：Mac 容器部署驗證

日期：2026-09-23（America/Los_Angeles）。接續 [首次驗證紀錄](phase-0.md)，使用者已安裝並啟動 Docker Desktop。本次只啟動本專案，未匯入財務資料、建立正式登入帳號、安裝 LaunchAgent 或變更主機電源設定。

## 環境與結果

| 項目 | 實測結果 |
|---|---|
| Docker Desktop | 使用者畫面顯示 4.92.0 / Engine running |
| Engine / Compose | 29.8.0 / v5.5.1 |
| 容器架構 | 前端、後端、PostgreSQL 三份映像均為 linux/arm64；後端程序回報 aarch64 |
| 首次啟動 | `sh scripts/start.sh` 完成鎖定映像下載、前後端建置、資料庫初始化、migration 與健康檢查，exit 0 |
| 重複啟動 | `sh scripts/start.sh --no-build` 沿用既有容器、volume 及資料，migration 可重跑，exit 0 |
| Web / API / DB | `http://localhost:8080/api/health/ready` 回傳 `{"message":"ready"}`；Web、API、DB healthy，worker running |
| 私有入口 | 只有 Web 發佈 `127.0.0.1:8080`；API / DB / worker 沒有主機 port mapping。列表中的 `5432/tcp` 是映像宣告，並非對外發佈 |
| Schema / 帳號 | `0001_phase0`；正式 DB 使用者數為 0，等待使用者親自設定登入帳密 |
| DB 權限 | 應用程式角色的 superuser / createdb / createrole 全部為 false |
| 備份與掛載 | 在 worker 容器手動執行 `create_backup()` 後 `verify_backup()` 通過；空白系統包含 2 筆幣別，無帳號 / TOTP / 正式交易 |
| 瀏覽器 | 本機正式 Nginx 入口可顯示繁中登入頁及初始化提示；本次未執行正式帳號登入 |
| launchd 設定檔 | 以實際 Docker 路徑生成，`plutil -lint` 通過；尚未安裝 / 載入 |

本機 Docker 指令安裝在 `~/.docker/bin/docker`，執行中的終端機尚未更新 PATH。已修改啟動腳本及 plist 產生器，優先使用既有 PATH，找不到時再使用 Docker Desktop 的已知安裝位置。實際首次啟動及重複啟動均通過，無需重新開機或修改全機 shell 設定。

## 使用者下一步

在專案資料夾的終端機執行：

```sh
~/.docker/bin/docker compose exec api python -m app.cli init-user
```

依提示輸入自己的帳號及至少 12 字元密碼；密碼由使用者在本機輸入，不貼入對話。建立後在 `http://localhost:8080` 登入，再選擇帳務幣別與時區。初始化程式遇到已有帳號會拒絕覆寫。

排程器已運作；每日自動備份需先有啟用中的帳號。本次手動驗證的空白系統備份不能取代正式資料及外接磁碟的還原演練。

## 狀態與未完成項目

- D0-01 / D0-02 改為 **DONE**：Mac arm64 容器建置、啟動、migration、私有連接埠及健康檢查已有實測證據。
- D0-07 仍為 **VERIFY**：尚未測試 LaunchAgent 載入、Docker 延遲啟動、Mac 登入 / 闔蓋 / 喚醒及私人遠端存取。
- 實際外接備份磁碟 / NAS、正式帳本還原、iPhone Safari 與 Numbers 按原計畫另驗收。
- 原生與 CI 的登入 / TOTP / 工作恢復測試結果仍見首次驗證紀錄；本次部署沒有把測試帳號放入正式資料庫。

目前服務保留運行，無需保持 VS Code 終端機開啟。需要暫停時執行 `~/.docker/bin/docker compose stop`，不要刪除資料庫 volume。

## 登入啟動與真實睡眠收尾（2026-09-23 晚間）

本節是使用者自行建立正式帳號、完成登入後的續驗，更新前文首次部署時的狀態。

- 已安裝並載入 `~/Library/LaunchAgents/com.finance-tracker.start.plist`，RunAtLoad=true、StartInterval=60。獨立啟動程式位於 `~/Library/Application Support/FinanceTracker/autostart.py`，日誌亦在此目錄；不含憑證，避免 Documents 的 macOS 背景存取限制。
- `launchctl print gui/501/com.finance-tracker.start` 回報 last exit code=0。刻意停止本專案 web／worker 後，由下一次 LaunchAgent 掃描自動恢復，Web ready 通過。未替使用者登出或重開機；不把設定與載入測試冒稱實際重新登入驗收。
- `pmset -g log`：22:21:05 初次進入睡眠，22:21:36 系統短暫 DarkWake，22:22:20 再次睡眠，22:29:31 因使用者操作喚醒。使用者回覆「已喚醒」。沒有改變主機睡眠設定。
- 三筆 probe 原訂 22:20:33、22:22:03、22:24:03 執行，實際完成於 22:20:34、22:22:04、22:29:50；均 succeeded、attempts=1，各只有一筆 job_effect。第三筆證明睡眠期間到期工作於喚醒後補跑；診斷沒有寫入財務資料。
- 正式設定目前 `BACKUP_HOST_DIR=./data/backups`，解析為 `/Users/tiffany/Documents/ChatGPT/finance-tracker/data/backups`。已確認此位置可寫入完整備份。尚無使用者指定的外接磁碟／NAS；同磁碟備份不能取代外部容災，目的地仍待確認。
- 私人遠端／iPhone 入口保持未配置；仍只在本機 loopback 提供服務。Phase 1 程式工作依使用者本次指示同步開始，D0-07 不因上述部分驗證而整列宣稱 DONE。
