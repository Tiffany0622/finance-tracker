# Finance Tracker

保存在自己電腦上的個人財務空間。主要介面為繁體中文 Web，支援銀行／現金／信用卡帳戶、收支、轉帳、退款、圖表及 CSV 快照。

目前進入 **Phase 1 首批手動記帳功能**。登入、TOTP、背景工作與備份沿用 Phase 0；本版範圍及尚未交付項目見 [Phase 1 驗證紀錄](docs/verification/phase-1.md)。尚不代表需求文件中的全部 Phase 1 或 P0 完成。

## 開始記帳

1. 登入並完成偏好設定後，到「帳戶」新增銀行、現金或信用卡帳戶。期初餘額不算收入；信用卡欠款填正數、溢繳填負數。
2. 到「記帳」新增分類，再記收入或支出。分類支援兩層，也可在一筆交易內分攤；商家、備註與逗號分隔標籤皆可搜尋。
3. 帳戶間移動款項及繳信用卡費使用「轉帳」。本金不計支出；手續費另選支出分類。跨 USD / TWD 必填實際入帳金額及外幣到帳本幣別的匯率。
4. 用交易列的「退款」關聯原支出；同幣部分退款採實際退款日期扣減原分類，超額退款會拒絕。「更正／作廢」保留原分錄並沖回，需填原因。
5. 「總覽」依期間、帳戶、分類或文字篩選產生固定報表。分類點擊可篩選；展開明細核對數字，再「匯出 CSV（ZIP）」下載明細、分類、月度、帳戶、總結與 metadata。文字公式會加安全前綴，數字保持可分析格式；下載檔案不會回寫帳本。

外幣收支使用入帳時填寫的匯率。淨資產另採「帳戶 → 設定外幣估值匯率」保存的估值日期／來源，缺率顯示不完整，不以 1 補值。歷史淨資產曲線目前從有效分錄與已保存匯率**重建**，不是已完成每日封存；分類／文字篩選不適用淨資產。自動匯率、週期記帳、對帳、貸款與完整資料匯出仍列於後續任務。

## 收據附件（D1-04）

在「記帳」完成一筆交易後，按交易列的「收據」→ 選擇圖片 →「上傳／重試待處理圖片」。支援一次選多張、放大預覽、下載原圖及移除。原始圖片逐位元保留；HEIC 會另產生 JPEG 預覽，不需要瀏覽器原生支援 HEIC。

- 支援 JPEG / PNG / HEIC，每張最多 20 MiB、5,000 萬像素，每筆交易最多 20 張，按上傳順序排列。多影格／動態圖片及 PDF 明確拒絕。
- 上傳只增加附件，不辨識或更動金額／分類；自動辨識改從下方「收據草稿」開始。批次中成功的圖片會保留，只重試未完成項目。
- 更正／作廢交易不刪除原圖；目前交易列表只列有效交易，作廢的附件仍留在資料與備份中。移除附件需確認，交易金額不變。
- 已移除檔案與崩潰遺留檔，在至少 24 小時後由 Worker 清理；既有 30 天備份可能仍含舊照片。新備份只包含仍可用的正式附件，不包含 staging／孤兒／已移除圖片。
- 如遇遺失或雜湊不符，下載顯示錯誤，備份也會失敗，不把殘缺副本當成成功。請從完整備份還原，或移除後重新上傳。
- 桌面與手機尺寸自動測試、Mac / Linux arm64 HEIC 轉檔證據見 [D1-04 驗證紀錄](docs/verification/d1-04-receipts.md)。實際 iPhone Safari 選圖／拍攝仍待實機驗收。

## 自動辨識與 Telegram（首批）

新增「收據草稿」：照片或文字 → 可切換的 Ollama / OpenAI 辨識 → 人工核對、分類分攤 → 確認入帳。Telegram 支援私聊白名單、照片／文字草稿、帳戶／分類選擇、修正、確認、取消、`/pending` 及收支／淨資產查詢。

**預設尚未啟用外部整合**，不會在未選定 provider 或填入 Bot Token 時傳送照片。先完成程式，之後依 [啟用步驟](docs/capture-setup.md) 在本機設定；不要把金鑰貼到聊天。實作範圍與驗證邊界見 [辨識與 Telegram 驗證](docs/verification/capture-telegram.md)。

## 品項核對與商品紀錄

「收據草稿」先儲存帳務欄位，再於「品項核對／修正」增刪或修正品名、數量、單價、列金額與規格；未知留白。儲存已核對品項並確認入帳後，就能在「商品紀錄」搜尋全部日期與商店的購買紀錄，或傳 Telegram `/lookup 商品關鍵字` 查最近 5 項。

舊收據可勾「包含已入帳與已取消」後開啟補核對。原始辨識與歷次修正保留，商品明細不增加第二筆支出；帳務修訂後須重新核對。搜尋目前比對修正品名與原文，尚無多語同義詞、照片搜尋或規格換算。操作與限制見 [啟用／使用步驟](docs/capture-setup.md)，驗證見 [品項功能紀錄](docs/verification/receipt-items.md)。

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

修改 `.env` 後以 `sh scripts/start.sh` 重新套用。已安裝自動啟動時，先依下方說明進入維護模式，再使用 `docker compose stop`；不要使用 `down -v`，它會刪除資料庫 volume。

剛安裝 Docker Desktop 時，已開啟的終端機可能尚未更新 PATH。`start.sh` 與 plist 產生器會自動尋找 `~/.docker/bin/docker` 及 Docker Desktop 內附指令，不需重新開機。其他 Docker 管理命令可先使用 `~/.docker/bin/docker` 取代 `docker`，或重新開啟終端機並確認 `docker info` 可用。

### 本機資料與外部連線

- PostgreSQL：Compose 的 `db_data` volume；應用程式 DB 角色 `finance` 沒有 superuser / 建角色 / 建資料庫權限。管理員密碼只提供給 DB 容器。
- 附件：`data/app/attachments/`。D1-04 已提供原圖及安全預覽，使用隨機識別碼保存，需登入才能讀取。
- 備份：`.env` 的 `BACKUP_HOST_DIR`。預設 `./data/backups` 僅供起步 / 演練，**不能抵抗同一磁碟損毀**。正式使用前改成另一顆磁碟或已掛載 NAS 的專用目錄，並實際做還原演練；需讓本機使用者可寫入。
- `.env` 包含恢復 TOTP 與 session 所需的機密，請另存於加密的安全位置。一般 CSV / JSON 匯出不包含它；備份也不自動複製明文 `.env`。
- DB、API、worker 只連接 internal 私有網路；Nginx 額外連接 edge bridge，將 Web 發佈至主機的 `127.0.0.1:8080`。目前沒有外部整合；未來啟用 Bot、匯率或 AI 時，需依架構增加受控的對外連線設定，不能只填金鑰就當成已啟用。
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

若安裝的 Compose 不支援 `--env-from-file`，先在本機 shell 安全載入環境檔，再改用 `docker compose run --rm -e RESTORE_DATABASE_URL worker ...`。還原命令不啟動 Bot 或 worker 排程，會核對核心與帳本表筆數、schema、附件雜湊、分錄／分攤平衡及完整財務資料指紋。接著在獨立程序上用目標 DB、原 TOTP / JWT 機密驗證登入，通過後再規劃正式切換；不要直接把正在寫入的正式服務指向演練庫。

Phase 1 備份已涵蓋新增帳本表、分錄／分攤平衡、完整資料指紋及固定報表。舊 Phase 0 備份仍可校驗並隔離還原；還原後須遷移至當前 schema 才能啟動新版服務。若磁碟已滿、檔案不足或 manifest 不符，不會把半成品標為成功。

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

先完成一次手動 Compose 部署，再安裝使用者登入啟動服務：

```sh
python3 scripts/launchd.py
plutil -lint data/com.finance-tracker.start.plist
python3 scripts/launchd.py --install
```

LaunchAgent 名稱為 `com.finance-tracker.start`，位於 `~/Library/LaunchAgents/`。登入時及每 60 秒檢查一次；先啟動 Docker Desktop 並等待引擎，接著恢復本專案既有容器。它不重建映像或遷移資料，初次部署及升級仍使用 `start.sh`。獨立啟動程式和日誌位於 `~/Library/Application Support/FinanceTracker/`，避開 macOS 背景程序讀取 Documents 的權限限制；啟動設定不含密碼。

升級或暫停前，先進入維護模式：

```sh
touch "$HOME/Library/Application Support/FinanceTracker/maintenance"
docker compose stop
# 完成升級並確認健康後，解除維護模式：
rm "$HOME/Library/Application Support/FinanceTracker/maintenance"
```

要永久停用，使用 `launchctl bootout gui/使用者UID ~/Library/LaunchAgents/com.finance-tracker.start.plist` 並移走該 plist。睡眠期間服務暫停，喚醒後恢復並補跑到期工作；實測證據見 [Mac 驗收](docs/verification/phase-0-mac.md)。沒有更改主機的睡眠偏好；登入啟動設定已載入，但沒有替使用者登出或重開機。

備份目前保留在專案的 `data/backups/`；外接磁碟／NAS 目的地仍待使用者指定。改用外部路徑時必須先掛載並建立專用目錄，Compose 不會自動建立不存在的備份來源目錄；本機同磁碟備份不能抵抗整顆磁碟損毀。

## 文件與開發順序

- [需求](01-requirements.md)
- [系統架構](02-architecture.md)
- [資料模型](03-data-model.md)
- [開發計畫與狀態](04-dev-plan.md)
- [Agent 工作規則](AGENTS.md)

產品不依賴 Codex、開發用 skill 或 Notion 插件常駐。Git 只保存程式、文件、migration、lockfile 與虛構測試資料；帳本、收據、金鑰、備份、日誌及模型檔均排除。
