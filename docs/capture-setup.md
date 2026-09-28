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
- Bot 回覆草稿後，可按「選擇幣別」「選擇帳戶」「選擇分類」「確認入帳」「取消草稿」。選幣別只標示金額的幣別，不會自動換匯；仍須與記帳帳戶一致。卡片同時顯示原始辨識的小計、稅、小費及折扣，未知為「不明」，明確 0 才顯示 0。金額或日期等可用 `/edit 草稿編號 amount=10.50 date=2026-09-24 currency=USD merchant="商家名稱"` 修正；完整分攤、標籤及匯率在 Mac 網頁調整。
- **重新辨識**：待確認／失敗卡片按「重新辨識」→ 閱讀影響 →「確定重新辨識」。這可能取代已儲存的金額、日期、商家及幣別，保留原圖與辨識歷程；尚未保存照片時顯示「重試下載」。未啟用 AI 不會提供辨識按鈕，但仍可重試下載。處理中顯示「查看進度」「取消草稿」；不要反覆重送照片。若按鈕已過期，輸入 `/pending` 開啟最新草稿卡片。
- `/pending` 列最近 10 份未完成草稿，`/pending 2` 看下一頁；`/today`、`/month`、`/networth` 使用帳本時區與既有報表快照；`/budget` 明示尚未實作。
- 程式升級不會主動重發或改寫既有 Telegram 訊息；要看到新按鈕，輸入 `/pending` 後點選草稿，或傳送下一張收據。已入帳／取消草稿不會再次變成待辦。
- 每張圖片最多 20 MiB，支援 JPEG / PNG / HEIC。初版一張圖片一份草稿；相簿多圖會分成多份，**尚未自動合併多頁收據**。PDF、多影格圖與商品照片搜尋尚未開放。
- 取消草稿不影響帳戶餘額，照片與辨識歷程保留在本機及備份。已入帳後請從記帳頁更正或作廢，不能再次確認同一草稿。

## 收據幣別預填

新版辨識會參考以下線索，Web 與 Telegram 共用：

1. 收據或你提供的文字有 USD、US$、美元，或 TWD、NT$、新台幣等明確幣別，優先預填。
2. 沒有明確幣別但讀到足夠的美國／台灣商家地址時，預填 USD／TWD，卡片顯示「地區推測」及讀到的地址。例如 `Palo Alto, CA 94301` 可作美元線索。
3. 只有英文、中文或 `$`、地址不足、不同幣別同時出現，或非支援幣別時，留給你選擇。加拿大／澳洲的英文收據不會只因英文就選美元。

地址與照片上的文字仍由模型讀取，可能看錯；核對卡片會保留初次判斷理由。需要更正時，在 Bot 按「選擇幣別」，或在網頁改「收據幣別」。改選不會換算金額，也不會改原始辨識結果。選好符合幣別的帳戶、核對金額與提醒後，才按「確認入帳」。

新收到的收據會使用新版。既有未入帳草稿可按「重新辨識」後確認重試；重辨識可能取代草稿的金額／日期／商家等人工修正，請先確認需要再操作。已入帳交易不會自動變更。實測與限制見 [幣別辨識驗證](verification/currency-inference.md)。

## 草稿建議分類

開啟可編輯的收據草稿後，「分類」區會自動顯示最多三個候選及理由。先參考近期 500 筆同收支類型中、同商家已入帳的分類，再參考商家、備註及品項文字。例如 `Membership` / 會員費可提示「會員／訂閱」，牛奶可對應既有「食物」或「食材」。商家忽略全半形、大小寫和多餘空白，但不猜測商家別名。

- 按「使用此分類」才會選入表單；不會覆蓋你手動選好的分類。之後仍需「儲存草稿修改」與「確認入帳」。
- 顯示「尚未建立」時，按「建立並選用」可先修改名稱與上層分類，再儲存；取消會返回原草稿。
- 多分類分攤先選「建議套用至」的目標列，建議只更換那列分類，金額由你核對填寫。
- 修改商家／備註，或儲存已核對品項後會更新；也可按「更新建議」。只有常用或現有選項、缺乏對應證據時，會明示資訊不足。連線失敗仍可手動選分類。

本功能在 Mac 上讀取既有紀錄與有限中英關鍵字，不額外呼叫 AI 模型或上傳照片；不支援自由語意推理。分類的最終用途仍需你判斷。目前候選呈現在 Web 核對視窗，Telegram 仍使用既有分類選擇流程。

## 品項核對與歷史商品搜尋

收據的「新增分類」用來建立分類名稱，儲存後會自動選入原收據或指定分攤列；可指定上層分類，收入／支出類型沿用目前收據。取消或按 Esc 會回到收據並保留已填內容。分類建立後仍需「儲存草稿修改」才會保存收據的選擇。

「加入分類分攤」用於把整筆金額拆到多個分類，例如 60 元分成 40 元食材、20 元日用品；每列需選分類並填寫大於 0 的金額，合計須等於收據總額。新列留白待填，不再預填 0。若誤加分攤，可移除多餘列；只剩一列時會回到單一分類，沿用整筆收據金額。

1. 開啟「收據草稿」，先核對日期、幣別、總額與記帳帳戶，按「儲存草稿修改」。
2. 在「品項核對／修正」逐列修正品名、數量、單價、列金額、規格與備註，可增刪列。未知數字留白；單價不明時不要填整筆消費金額。
3. 勾選逐項核對確認，再按「儲存已核對品項」。最後照原流程「確認入帳」；整張收據只記一筆支出。
4. 到「商品紀錄」輸入品名或收據原文搜尋，或在 Telegram 送 `/lookup 蘋果`。
5. 舊的已入帳照片：在「收據草稿」勾「包含已入帳與已取消」，開啟後直接補核對品項。已入帳的品項也可從商品搜尋結果開回修正，帳務總額不會改動。

品項列金額依收據保存，稅與小費不另行攤入、退款不抵扣，空值不是零。重新辨識或更改帳務後，原人工品項保留但需重新核對，期間暫不列入搜尋。取消／作廢來源不顯示在有效歷史。Bot 查詢只回最近 5 項，完整清單與照片在 Web；本機網站未設定遠端連線前，不能假設手機外網能開啟 localhost。

核對中可按右上角 X、底部「完成／關閉」或 Esc 離開。有未儲存修改時，網頁會顯示「繼續編輯／捨棄修改並關閉」；不會直接刪掉原收據或已入帳資料。「儲存草稿修改」「儲存已核對品項」會保留視窗方便繼續核對；「確認入帳」「確定取消草稿」成功後會自動返回列表。連線逾時會保留輸入並恢復關閉按鈕；伺服器可能已完成操作，請先重新載入確認，再決定是否重試。

### 用中文與英文別名查同一商品

1. 在「商品紀錄」展開「管理商品名稱與別名」，建立商品，例如名稱「牛奶」，別名每行一個填「鮮乳」「MILK」。商品名稱及別名不可與同帳本其他商品重複；可從清單選既有商品修改。
2. 開啟收據「品項核對／修正」，在各列「對應商品」選擇牛奶，再勾確認並儲存。原本「ORGANIC WHOLE MILK」的收據文字、單價與包裝規格保留。未入帳者仍需照原流程確認入帳。
3. 在 Web 搜尋「牛奶」「鮮乳」或「milk」，或傳 Telegram `/lookup MILK`，可找到已連結的有效購買。別名比對完整名稱，忽略全半形、英文大小寫、多餘空白；品名與原文仍支援部分關鍵字。
4. 既有品項不會自動歸類。若連錯商品，開回品項改選或選「未連結」，重新核對儲存即可；舊修正版本保留、帳務不變。商品改名或移除別名後，查詢依最新設定生效。

目前商品名稱／備註不自動填入單位或價格。不同品牌、容量可分別建立商品，或只作分類搜尋；不能因同屬牛奶就直接比較每瓶價格。商品備註尚不參與搜尋，沒有自動同義詞／商品合併功能。

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
