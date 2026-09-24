# Phase 0 驗證紀錄

日期：2026-09-23。首次 CI 驗證的程式提交為 `675a18be1f545f1b3042fe6e15c7ad3b9377395f`；同日使用者安裝 Docker 後的 [Mac 容器部署驗證](phase-0-mac.md) 已補齊 arm64 建置 / 啟動。本紀錄的測試資料與帳號皆為虛構；未匯入使用者財務資料、未建立正式登入帳號、未啟用外部整合。

## 結果

| 檢查 | 已執行結果 |
|---|---|
| 後端 Ruff / 格式 | 通過 |
| mypy | 13 個來源檔通過 |
| PostgreSQL 整合測試 | **22 passed**；含 Hypothesis 對不可信 CSRF 字串的案例 |
| Alembic / ORM 一致性 | 初始 migration 在空 DB 套用；`command.check` 沒有 schema drift |
| 前端 Vitest | **2 passed**：並行過期請求只輪替一次、斷線不回傳假成功 |
| TypeScript + Vite | production build 通過 |
| OpenAPI 型別 | 從後端產生 JSON 及 TypeScript schema，前端以生成型別建置通過 |
| 真實 Chrome 端到端 | **2 passed**：1440px 桌面及 390px 觸控尺寸；登入 → 儲存設定 → 背景工作 → 登出 → 再登入 → 斷線狀態 |
| 畫面檢查 | 已檢視桌面 / 手機截圖；沒有水平溢出，繁中、表單與導覽可見 |
| Compose | 官方 standalone Compose **v5.5.1** `config --quiet` 通過；工具的下載 SHA-256 已對官方 release digest 核對 |
| 容器平台中繼資料 | PostgreSQL、Node、Nginx 指定 digest 的映像清單含 linux/arm64；這不是實際容器啟動驗收 |
| 本機環境檔 | 隨機機密生成、權限 600、重跑拒絕覆寫通過；機密值未輸出至紀錄或 Git |
| 啟動腳本 / plist | shell 語法檢查通過；以 Docker 路徑替身生成 plist，`plutil -lint` 通過；未載入 launchd |
| GitHub Actions 乾淨 checkout | **通過**：Ubuntu 24.04 / x86_64 重現 22 項後端、2 項前端、2 項瀏覽器測試及生成契約無漂移 |
| Linux 容器實際啟動 | **通過**：CI 完成 Compose build、migration、API / Web HTTP health，主機 `127.0.0.1:8080/api/health/ready` 可連入；不等同 Mac arm64 容器驗收 |

後端仍有 1 個非阻斷的第三方警告：Starlette 提示未來 TestClient 將改用 httpx2。已鎖定的 httpx 0.28.1 在本次測試可用；升級時需連同 TestClient 相容性驗證，不以隱藏警告冒充已解決。

### 遠端 CI 證據

[GitHub Actions 成功執行 #35847379622](https://github.com/Tiffany0622/finance-tracker/actions/runs/35847379622)，對應程式提交 `675a18b`。後端 22 項測試 9.58 秒、瀏覽器 2 項測試 9.7 秒；容器建置、遷移與 Web 入口皆成功。

首次遠端驗證揭露 pytest 依賴本機 `PYTHONPATH`；已改由專案 pytest 設定提供來源路徑，並在取消本機環境變數後確認可載入全部測試。容器驗證另揭露 Web 只接 internal 網路時主機入口不可用；已讓 Nginx 同時接 edge bridge / private network，保留 loopback port 限制並加入真正 HTTP 健康檢查。修正後完整流程重跑通過。

CI 臨時金鑰每次隨機產生並遮罩，不使用本機 `.env` 或正式憑證。GitHub 另提示部分 action 宣告 Node 20，runner 已使用 Node 24 並通過；未來更新 action 版本時重新驗證。

## 已驗證的主要行為

- 無公開註冊、無預設正式帳密、CLI 初始化不覆寫已有使用者。
- Argon2id、HttpOnly / SameSite cookie、CSRF 與 Origin 拒絕、持久化登入限速、登出撤銷、refresh 重放撤銷整個 session family。
- TOTP secret 加密、同一步驗證碼不可重放、備援碼只能用一次、啟用與停用需目前密碼。
- 未設定幣別 / 時區保持 NULL；設定修訂衝突回 409；查詢別人的 job 回 404；audit 不可 UPDATE / DELETE。
- 同請求識別不重複排入工作；outbox 重派不重複；兩個 worker 只領取一筆工作；程序在寫入副作用後退出，重領後仍只有一次副作用。
- 舊 lease token 不能完成新 worker 的工作；備份維護鎖期間心跳仍可續約；失敗達 5 次後顯示 failed；漏過的備份排程不重複建立。
- 真正 PostgreSQL `pg_dump` → `pg_restore` 到另一個空資料庫 → schema / 核心筆數 / 附件 SHA-256 核對 → **在還原後的 DB 用 API 登入成功**。
- 拒絕向原 DB 或非空目標還原；checksum、不在 manifest 的檔案、非法路徑被拒絕；31 天前的成功備份可清理，最新完整備份保留；重試同一份備份不會把原資料時間改成現在。

## 測試環境

- macOS / Apple Silicon（arm64）。
- Python 3.12.14、uv 0.12.18。
- PostgreSQL 16.15，來自 [Postgres.app 官方版本](https://postgresapp.com/downloads.html)，只複製至不入 Git 的 `.tools/`；未安裝到 Applications。隔離測試 DB 使用本機 55432，沒有觸碰其他 DB。
- Node 24.19.0、pnpm 11.19.0、React 18.3.1、Vite 7.3.6、Playwright 1.63.0。
- 本機開發 API 8000、Vite 5173、worker 與測試 DB 用虛構帳本驗收；產品部署預設為 Compose Web 8080。
- 完整 Python / Node 依賴版本以 lockfile 為準。測試工具與資料下載均不入版控。

可重現命令及環境條件見 [README](../../README.md)。本機因系統 Python / Node 較舊，使用獨立的相容 runtime 與專案 `.venv`；未更動全機預設版本。

## 尚未完成的驗收

1. **LaunchAgent 實際載入、登入自動啟動、Docker 引擎延遲啟動、Mac 闔蓋 / 喚醒。** 沒有修改使用者的常駐或電源設定。
2. **實際另一顆磁碟 / NAS。** 目前只有隔離測試目錄的成功還原；正式 `BACKUP_HOST_DIR`、掛載狀態及權限仍需選定及驗證。
3. **iPhone Safari、私人遠端存取、Numbers。** 此階段只驗證 Chrome 觸控尺寸；真實 iPhone 的 RV-06 在 Phase 1、Numbers 匯出在 Phase 1 / 1.5。
4. **Phase 1 財務表與報表。** 目前無正式交易 / 分錄 / 報表；D1-08 必須把帳務不變量與代表性報表加入備份還原演練。

目前結論為「Phase 0 程式、原生環境主要流程、遠端 Linux CI 及 Mac arm64 容器部署已驗證，主機常駐及睡眠驗收待完成」，不是整個 Phase 0 已完全 DONE。

## 畫面證據

只含合成帳號與測試運作狀態，不含真實交易。

- [桌面總覽](screenshots/phase-0-desktop.png)
- [手機尺寸總覽](screenshots/phase-0-mobile.png)

## 實作時核對的官方來源

- [FastAPI 認證範例](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)、[Docker Compose 啟動依賴](https://docs.docker.com/compose/how-tos/startup-order/)。
- [uv Docker 整合](https://docs.astral.sh/uv/guides/integration/docker/)、[Vite 執行環境](https://vite.dev/guide/)、[pnpm 設定](https://pnpm.io/settings)。
- [Compose one-off command 與環境檔](https://docs.docker.com/reference/cli/docker/compose/run/)。
- [Docker 多網路連接](https://docs.docker.com/engine/network/)、[loopback 連接埠發佈](https://docs.docker.com/engine/network/port-publishing/)。
