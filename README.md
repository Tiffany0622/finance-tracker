# Finance Tracker

保存在自己電腦上的個人財務空間。主要介面為繁體中文 Web；帳戶、記帳與圖表將從 Phase 1 逐步加入。

目前已實作 **Phase 0 基礎程式**：登入與可選 TOTP、首次設定、PostgreSQL migration、背景工作、備份 / 隔離還原、CI 與本機啟動腳本。已驗證範圍及尚未完成的部署驗收見 [Phase 0 驗證紀錄](docs/verification/phase-0.md)。此版本尚不能作為完整記帳系統。

## 啟動本機系統

需要已啟動的 Docker 引擎與 現行 Docker Compose（docker compose 指令）。Mac 可使用 Docker Desktop 或 OrbStack。首次建置需網路下載鎖定的映像與依賴；核心執行不需 AI、Telegram 或 Notion 金鑰。

```sh
python3 scripts/configure.py
sh scripts/start.sh

docker compose exec api python -m app.cli init-user
```

1. `configure.py` 建立權限為 `600` 的 `.env`，產生隨機 DB / JWT / TOTP 機密；不覆寫已有檔案。它會記錄本機 UID / GID，讓非 root 容器可寫入掛載目錄。
2. `start.sh` 等待容器引擎、建立資料目錄、建置服務、完成 migration，再等待健康檢查。DB 沒有對外發佈連接埠；只有 `127.0.0.1:8080` 提供 Web。
3. `init-user` 在終端機互動設定帳號及至少 12 字元的密碼。**沒有預設登入密碼、公開註冊或自動建立的正式使用者**。
4. 開啟 [本機財務空間](http://localhost:8080)，登入後選擇自己的基準幣別與時區。請使用這個完整網址；`APP_ORIGIN` 必須與實際瀏覽器來源一致。
5. 「帳號安全」可將金鑰加入驗證器 App，確認驗證碼後啟用 TOTP。當次顯示的備援碼請另外安全保存；每組只能用一次。

修改 `.env` 後以 `sh scripts/start.sh` 重新套用。要暫停服務使用 `docker compose stop`；不要使用 `down -v`，它會刪除資料庫 volume。

### 本機資料與外部連線

- PostgreSQL：Compose 的 `db_data` volume；應用程式 DB 角色 `finance` 沒有 superuser / 建角色 / 建資料庫權限。管理員密碼只提供給 DB 容器。
- 附件：`data/app/attachments/`。Phase 0 建立儲存與備份基礎，附件上傳在 Phase 1。
- 備份：`.env` 的 `BACKUP_HOST_DIR`。預設 `./data/backups` 僅供起步 / 演練，**不能抵抗同一磁碟損毀**。正式使用前改成另一顆磁碟或已掛載 NAS 的專用目錄，並實際做還原演練；需讓本機使用者可寫入。
- `.env` 包含恢復 TOTP 與 session 所需的機密，請另存於加密的安全位置。一般 CSV / JSON 匯出不包含它；備份也不自動複製明文 `.env`。
- Phase 0 Compose 網路為 internal，沒有外部整合。未來啟用 Bot、匯率或 AI 時，需依架構增加受控的對外連線設定，不能只填金鑰就當成已啟用。
- 遠端 / 手機外出存取尚未配置。選定私人連線及 HTTPS 後需同步設定 `APP_ORIGIN`、`COOKIE_SECURE=true`、代理與來源限制，再做實機驗收。

## 備份與隔離還原

Worker 啟動後每分鐘掃描上次成功備份時間；超過一天即安排備份。主機喚醒後會補最近一份，使用穩定日期識別避免重複。失敗工作會退避重試，最多 5 次，最後失敗及備份過期顯示於 Web；Telegram 通知要到 Phase 2。

```sh
# 手動立即備份；輸出容器內的完整備份路徑
docker compose exec worker python -m app.cli backup

# 使用上一步回傳的目錄；不要填 .incomplete 目錄
docker compose exec worker python -m app.cli verify-backup --source /backups/備份UUID
```

備份包含 PostgreSQL custom dump、附件及 SHA-256 manifest，完成後才以原子 rename 發布。受控業務寫入與附件變更在備份期間受維護鎖保護；job 心跳是唯一允許繼續的運作中繼資料寫入。每份備份記錄 app commit / schema / DB 版本，保留 30 天且不刪最新成功備份。

還原只允許**空白且名稱以 `finance_restore_` 開頭**的另一個資料庫，以及空目錄；不支援直接覆蓋正式庫。先由資料庫管理員建立隔離目標，例如：

```sh
docker compose exec db createdb -U postgres -O finance finance_restore_drill
```

在一份不入 Git、權限 `600` 的本機環境檔（例 `data/restore.env`）中設定 `RESTORE_DATABASE_URL`。使用 `.env` 中應用程式的 `POSTGRES_PASSWORD`，目標 host 為 `db`、database 為 `finance_restore_drill`；不要將密碼貼到命令列或對話。然後執行：

```sh
docker compose run --rm --env-from-file data/restore.env worker \
  python -m app.cli restore --source /backups/備份UUID \
  --target-data /data/restore-drill
```

若安裝的 Compose 不支援 `--env-from-file`，先在本機 shell 安全載入環境檔，再改用 `docker compose run --rm -e RESTORE_DATABASE_URL worker ...`。還原命令不啟動 Bot 或 worker 排程，會核對 Phase 0 核心筆數、schema 及附件雜湊。接著在獨立程序上用目標 DB、原 TOTP / JWT 機密驗證登入，通過後再規劃正式切換；不要直接把正在寫入的正式服務指向演練庫。

Phase 1 會把新帳本表、分錄平衡及代表性報表加入還原核對。若磁碟已滿、檔案不足或 manifest 不符，不會把半成品標為成功。

## 本機原生開發與測試

建置基線：Python 3.12、Node 24.19.0、uv 0.12.18、pnpm 11.19.0、PostgreSQL 16。套件解析結果鎖於 `backend/uv.lock` 和 `frontend/pnpm-lock.yaml`。本次 Apple Silicon 原生驗證採 Python 3.12.14 / PostgreSQL 16.15；詳細版本見驗證紀錄。

```sh
uv sync --project backend --frozen
pnpm --dir frontend install --frozen-lockfile
```

設定環境變數 `DATABASE_URL`（`postgresql+psycopg://...`）、`JWT_SECRET`、`TOTP_KEY`、`APP_ORIGIN=http://localhost:5173`、`DATA_DIR`、`BACKUP_DIR`。機密可沿用本機 `.env`，但**原生 DB URL 要改為本機可連接的位址**；Compose 專用的 `db` 主機名在 Mac 原生程序中無法使用。建立資料目錄，讓 `pg_dump` / `pg_restore` 的 PostgreSQL 16 版本位於 PATH。

```sh
# 以下後端命令在 backend/ 執行
cd backend
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli init-user
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
# 另一個終端機在 backend/ 執行
.venv/bin/python -m app.worker
# 另一個終端機在專案根目錄執行
pnpm --dir frontend dev
```

Vite 僅監聽 loopback，將 `/api` 代理至 `127.0.0.1:8000`。它是開發伺服器；正式本機介面使用 Compose 的 Nginx 靜態建置。

### 驗證指令

後端測試**需要真正 PostgreSQL**。建立只含虛構資料、名稱以 `finance_test` 開頭的可丟棄 DB，將它設為 `TEST_DATABASE_URL`；測試會清空該 DB 的應用表，並短暫建立 / 移除 `finance_restore_...` 還原庫，所以測試角色需有 CREATEDB。不要授予正式應用角色這個權限。

```sh
sh scripts/check-backend.sh
pnpm --dir frontend api:generate
pnpm --dir frontend test
pnpm --dir frontend build
```

`check-backend.sh` 跑 Ruff、mypy、pytest、migration drift 檢查並更新 OpenAPI。執行前也要讓 `DATABASE_URL` 指向同一測試庫，以便測試後的 Alembic 檢查；OpenAPI 產生本身不需 DB 連線。`backend/openapi.json` 與生成的 TypeScript schema 需一起提交，CI 會檢查漂移。

瀏覽器驗收另用虛構帳本：在空的 `finance_demo...` / `finance_test...` 資料庫完成 migration 後，可從根目錄執行 `PYTHONPATH=backend backend/.venv/bin/python scripts/seed-demo.py`。此命令拒絕正式庫及已有使用者；測試帳號為 `alice`，密碼是測試程式中的 `synthetic-test-passphrase`，**只供隔離驗收使用**。

啟動指向這個虛構庫的 API、worker 與 Vite 後：

```sh
pnpm --dir frontend test:e2e
```

預設使用已安裝 Chrome。CI 設 `E2E_CHANNEL=chromium` 並安裝 Playwright Chromium；測試包含桌面及 390px 觸控尺寸。這不代表真實 iPhone Safari 已通過。

## macOS 開機 / 登入啟動

先完成一次手動 Compose 啟動，並將容器引擎設定為登入時啟動，再生成可審查的 LaunchAgent：

```sh
python3 scripts/launchd.py
plutil -lint data/com.finance-tracker.start.plist
```

確認內容後，將該 plist 放入自己的 `~/Library/LaunchAgents/`，使用 `launchctl bootstrap gui/使用者UID 檔案路徑` 載入。它在登入及每 5 分鐘嘗試以 `--no-build` 啟動已有映像，等待引擎最多 60 秒；不會自行安裝或開啟 Docker GUI。日誌在 `data/logs/`。

本階段未自動安裝常駐服務，也未變更您的睡眠設定。要完全停止自動啟動，先用 `launchctl bootout gui/使用者UID ~/Library/LaunchAgents/com.finance-tracker.start.plist` 卸載，再停止 Compose。真正闔蓋 / 喚醒及外部備份磁碟測試仍需在您選定的部署環境驗收。

## 文件與開發順序

- [需求](01-requirements.md)
- [系統架構](02-architecture.md)
- [資料模型](03-data-model.md)
- [開發計畫與狀態](04-dev-plan.md)
- [Agent 工作規則](AGENTS.md)

產品不依賴 Codex、開發用 skill 或 Notion 插件常駐。Git 只保存程式、文件、migration、lockfile 與虛構測試資料；帳本、收據、金鑰、備份、日誌及模型檔均排除。
