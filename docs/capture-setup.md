# 收據辨識與 Telegram 啟用

程式預設關閉外部整合。即使沒有 AI / Bot 金鑰，也能在「收據草稿」上傳照片、手動補齊日期、金額、幣別、帳戶及分類，儲存修改後確認入帳。已入帳交易仍使用「記帳 → 收據」管理附件。

## 建立自己的 Bot

1. 在 Telegram 找到官方 [@BotFather](https://t.me/BotFather)，送出 `/newbot`，依提示建立名稱及 username。
2. 保存 Bot Token，只在下面的本機隱藏輸入提示貼上，不要貼到聊天、GitHub 或截圖中。
3. 不需要先查數字 User ID。設定時可以用一次性配對碼辨認你的 Telegram 私聊；群組及其他帳號不會建立草稿。
4. 在專案目錄執行：

```sh
python3 scripts/configure-capture.py
```

腳本先讀取本機帳本：只有一個帳號時自動選取，多個時依編號選擇；不需輸入 Bot 帳號當作帳本帳號。接著詢問辨識方式、模型名稱及選用的 Bot Token（隱藏輸入，不會顯示字元）。會透過 `getMe` 核對 Bot。詢問數字 User ID 時直接按 Enter，就會顯示一次性的 `/start finance_…` 指令；請在 3 分鐘內把整行傳給自己的 Bot（不是 BotFather），終端機會辨認你的 User ID。不要分享配對碼。若已知道數字 ID，也可以手動填寫。

配對不會確認或刪掉 Telegram 待收訊息；大量積壓、現有輪詢程序或 Webhook 會停止並提示改用手動 ID，不會自動關閉既有整合。最後核對帳本／Bot／User ID，輸入 `yes` 才產生僅允許 capture bridge 的 API token、撤銷舊授權，以 0600 權限原子更新 `.env`，再套用 Compose。一次只綁定一個帳本使用者與 Telegram 私聊。整合授權有效 365 天；過期或換 Bot 時重新執行腳本。腳本不會替你建立 Bot 或選付費方案。

若 Token 曾貼在非隱藏欄位、聊天或截圖中，先到 BotFather 的 `/mybots` → 選 Bot → API Token → Revoke current token，依提示取得新的 Token；Bot 帳號不需重建。程式會分別提示 Token 無效（HTTP 401／404）、DNS／網路、HTTPS 憑證、逾時或限流，不會回顯 Token、完整 API URL 或遠端錯誤內容。TLS 錯誤必須修復憑證或代理設定，不能關閉驗證。

若尚未決定辨識模型，可選 `disabled` 並只填 Bot Token；照片仍會下載並保存為待人工確認的草稿。兩者都關閉時，腳本停用整合容器，核心記帳繼續運作。這個初版停用後保留未處理工作與資料，不會刪除照片。

## 選擇辨識方式

| 方式 | 必要設定 | 照片／文字流向 |
|---|---|---|
| disabled | 無 | 僅保存本機；Telegram 傳送的內容仍經過 Telegram |
| ollama | Mac 上可用的 Ollama 視覺模型，支援結構化 JSON | 去除 EXIF / GPS 的 JPEG 預覽送至 Mac Ollama；文字送同一模型 |
| openai | 可用的視覺模型完整 ID、OpenAI API Key | JPEG 預覽或文字送 OpenAI Responses API，`store=false`；另計 API 費用 |

此 Mac 已選 macOS 原生 Ollama + `qwen3-vl:8b-instruct`（M4 Pro / 24 GB）。首次安裝下載約 6.1 GB，後續本機推論不需 AI API Key 或每次 API 費用；Telegram 傳輸仍需網路。本機模型也會辨識錯字或金額，須核對原圖。不以合成 adapter 測試宣稱實際辨識品質；多份中英文收據與冷暖啟動延遲仍需量測。`store=false` 不代表雲端服務所有保留政策都被關閉。

### 本機模型安裝與啟用

1. 從 [Ollama 官方 Mac 下載頁](https://ollama.com/download/mac) 安裝到 `/Applications/Ollama.app`。本專案以 LaunchAgent 啟動內附 CLI，勿同時讓 Ollama GUI 與本服務各自啟動 server。
2. 在專案目錄執行以下命令（已有 Telegram 配對時不必重跑 Bot 設定）：

```sh
python3 scripts/ollama-launchd.py --install
/Applications/Ollama.app/Contents/Resources/ollama pull qwen3-vl:8b-instruct
python3 scripts/enable-local-ai.py
python3 scripts/enable-local-ai.py --apply
```

第一個腳本會生成 `data/com.finance-tracker.ollama.plist`，並安裝到 `~/Library/LaunchAgents`。它僅綁本機、設定 `OLLAMA_NO_CLOUD=1` 關閉雲端、context 8192、一次一份請求；閒置 2 分鐘卸載模型，下一份收據需重新載入。模型留在 `~/.ollama`，日誌在 `~/Library/Application Support/FinanceTracker/logs`。

請使用完整 `8b-instruct` 標籤。Ollama registry 的 `8b`／`latest` 在此次查驗對應 thinking 版本；即使 API 設定 `think=false`，本次實測仍產生長推理並逾時，不能當作 Instruct 的同義名稱。[官方模型標籤](https://ollama.com/library/qwen3-vl/tags)。首次 GPU 初始化可能較久，效能紀錄見 [本機驗證](verification/local-ai.md)。

不加 `--apply` 僅檢查本機 vision 模型與 Docker 連線；會用短暫容器探測，不啟動另一份 Telegram 輪詢，檢查失敗不改 `.env`。套用後保留 Bot Token／白名單／既有整合授權，並重建 Web 代理以重新解析 API 的內部位址。到網頁「收據草稿」開啟之前的照片，按「重新辨識」；新照片會自動建立辨識工作。仍須核對金額／日期／幣別、選帳戶及分類後再確認入帳。

要停止 AI 但保留 Telegram 收件，可在本機 `.env` 僅將 `CAPTURE_PROVIDER` 改成 `disabled`，然後執行 `docker compose up -d --no-build --wait`；不要刪 Bot 設定或 `capture-enabled` 標記。停止原生模型服務可執行 `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.finance-tracker.ollama.plist`；若要取消下次登入啟動，再移除該專案 plist。模型檔不會因此刪除。

Ollama 建議直接在 Mac 執行以使用 Apple 硬體；整合容器使用 `http://host.docker.internal:11434`。若容器不能連到僅綁 loopback 的 Ollama，需另外確認 Ollama 綁定／Mac 防火牆；本腳本不自動開放 LAN port。進階使用也可在 Mac 原生執行 `python -m app.capture.bridge`，使用 `CAPTURE_API_URL=http://localhost:8080/api/v1/capture-bridge` 與 `OLLAMA_URL=http://localhost:11434`，並在本機安全載入所需的整合機密；先停用 Compose 整合容器與 capture-enabled 自動恢復標記，勿同時執行兩份 Bot。不要將 Ollama 的無驗證介面直接暴露到網際網路。

## 使用方式

- **網頁**：收據草稿 → 選圖或輸入文字 → 等待辨識／手動修正 → 儲存草稿修改 → 核對提醒 → 確認入帳。支援 USD / TWD，帳戶幣別須與填寫幣別相同；跨基準幣記帳仍需入帳匯率。分類分攤在網頁編輯，合計須等於總額。
- **Telegram**：先對自己的 Bot 送 `/start`，接著傳單張照片、圖片文件或記帳文字。以文件傳送可避免 Telegram 的相片壓縮；本機保存的是 Telegram 實際提供的位元組。
- **幣別核對**：新版圖片草稿預設由你選幣別；AI 的猜測只顯示為參考。若文字或照片圖說明確寫 `USD`、`US$`、美元／美金或 `TWD`、`NT$`、新台幣／新臺幣，且只出現一種幣別並與辨識一致，才自動帶入。僅有 `$` 或地址不會自動推定。Bot 可用 `/edit 草稿編號 currency=USD` 補齊；所有資料仍須確認才入帳。
- Bot 回覆草稿後，可按「選擇帳戶」「選擇分類」「確認入帳」「取消草稿」。金額或日期等可用 `/edit 草稿編號 amount=10.50 date=2026-09-24 currency=USD merchant="商家名稱"` 修正；完整分攤、標籤及匯率在 Mac 網頁調整。
- `/pending` 列最近 10 份未完成草稿，`/pending 2` 看下一頁；`/today`、`/month`、`/networth` 使用帳本時區與既有報表快照；`/budget` 明示尚未實作。
- 每張圖片最多 20 MiB，支援 JPEG / PNG / HEIC。初版一張圖片一份草稿；相簿多圖會分成多份，**尚未自動合併多頁收據**。PDF、多影格圖與商品 `/lookup` 不在本次範圍。
- 取消草稿不影響帳戶餘額，照片與辨識歷程保留在本機及備份。已入帳後請從記帳頁更正或作廢，不能再次確認同一草稿。

## 恢復與診斷

整合容器使用長輪詢，不需要公開網址、Webhook 或路由器 port forwarding。API 與資料庫仍只走私有網路；`capture-bridge` 是選用 profile，沒有 DB 密碼或附件磁碟掛載。

收到事件先提交資料庫再推進 offset。照片下載、AI 及 Bot 傳送有各自租約與重試；一般失敗指數退避，尊重 Telegram `retry_after`，最多 5 次。失敗草稿可人工修改或按重新辨識；重新辨識會產生新的工作，雲端可能再次計費。延遲結果不能蓋掉較新的人工修改。傳送成功但確認回覆遺失時，Telegram 回覆可能重複；確認入帳本身有冪等保護。

Mac 睡眠期間不能處理，喚醒後接續已保存工作。Telegram 尚未被本機接收的 update 最多保留 24 小時，不能承諾關機超過一天仍不漏件。已啟用登入恢復的 Mac 升級時，重新執行 `python3 scripts/launchd.py --install`；啟用腳本的 `capture-enabled` 標記讓啟動器一併恢復整合容器，停用時移除此標記。

網頁顯示整合程序最近是否連線、Telegram 是否已收到過訊息，不把金鑰已設定當成模型已通過實測。可查看 `docker compose logs --tail=50 capture-bridge`；程式只輸出錯誤代碼，不記錄金鑰、遠端錯誤本文或收據內容。每日摘要與推播排程尚未實作，因此沒有主動排程通知。

參考：[Telegram Bot API](https://core.telegram.org/bots/api)、[Ollama vision](https://docs.ollama.com/capabilities/vision)、[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)、[OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision)。

## 開發者：重跑本機辨識評估

在已安裝後端鎖定依賴、Ollama 與模型的 Mac 執行：

```sh
backend/.venv/bin/python scripts/evaluate-local-ai.py --output .tools/receipt-eval.json
```

腳本將 `backend/tests/fixtures/receipt-eval.json` 的合成收據渲染成圖片，逐欄比較金額、日期及幣別；不讀真實收據、不寫帳本、不連 Telegram／雲端。任一欄位不符以非零狀態結束，完整保留錯誤。`--prompt-version 1` 可比對舊提示，`--repeat 3` 可重複量測；其他系統須用 `--font` 指定可顯示繁體中文的 TTF / TTC 字型。這是選擇性模型實測，不是 CI mock 測試，三份乾淨合成圖也不代表真實收據全面驗收。
