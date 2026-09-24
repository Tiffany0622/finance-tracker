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
