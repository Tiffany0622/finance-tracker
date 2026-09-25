# 自動辨識與 Telegram 首批驗證

日期：2026-09-24（America/Los_Angeles）。使用者要求先完成程式、稍後決定辨識方式並建立 Bot；本次不使用真實金鑰、私人收據或正式交易作為 fixture。

## 已交付

- 「收據草稿」支援 JPEG / PNG / HEIC 上傳與文字輸入。先保存草稿、預覽、人工補日期／幣別／金額／帳戶／分類，支援多分類分攤、標籤及匯率；只有確認才呼叫既有 ledger service 正式入帳並連結收據。
- Ollama / OpenAI Responses provider 介面、嚴格結構化結果、Decimal 品項／小計／稅／小費／折扣驗算、缺值提醒、原始中英品項保存。模型不能選工具或直接寫帳；未辨識值是 null。
- 私聊白名單、限定 scope / 有效期限 / 可撤銷的 bridge token、持久 Telegram events / cursor、照片下載與長輪詢、確認卡片、帳戶／分類分頁、`/edit`、`/pending`、取消、`/today`／`/month`／`/networth`。預算明示尚未實作。
- 三類網路工作分別處理，租約心跳、逾時重試、429 retry_after、重啟恢復。舊 lease／舊 revision 不得覆蓋新草稿；Telegram 頂層交易與帳本不可變 guard 相容。長時間無更新會從安全 offset 重新輪詢，避免 Telegram 一週後隨機較小 update ID 被舊 offset 跳過。
- 圖片下載途中手動修改不丟掉照片，也不蓋掉人工欄位；未保存照片不能確認。取消草稿保留照片與歷程，不改財務餘額。
- Schema 0004_capture、OpenAPI 與前端型別同步。新表／未入帳圖片加入一致備份與隔離還原；保留 0003 備份驗證相容性。
- 選用 capture-bridge Compose profile 沒有 DB 密碼與檔案掛載；對外連線集中在該程序。啟用腳本於本機隱藏輸入金鑰，預設外部整合保持 disabled。

## 實際執行結果

| 驗證 | 結果 |
|---|---|
| Ruff / format / mypy | 通過；31 個 app 模組 |
| PostgreSQL 整合測試 | **61 項通過**，其中新增 21 項 capture 測試；全部使用 finance_test |
| 遷移 | 0003 → 0004 在測試庫通過；alembic check 無 schema 差異；不降版正式庫 |
| 前端 | TypeScript / production build、既有 2 項 Vitest 通過 |
| Playwright | **8 項通過**：桌面 1440×1050、手機尺寸 390×844 的登入、記帳、附件與草稿流程 |
| Docker | linux/arm64 映像建置、Compose config 通過；無網路／無 DB 機密的 bridge import 檢查通過 |
| 備份還原 | 真正 pg_dump → 新建 finance_restore_* 隔離庫 → pg_restore；未入帳原圖、草稿、parse attempt 與財務指紋一致 |
| 程序崩潰 | 子程序提交 Telegram inbox 後 os._exit(37)，重送 update 僅一份草稿／回覆工作，游標正確續收 |
| Adapter 串接 | 用合成 HTTP / 模型回應串接真實 API／DB，完成 Telegram 照片下載 → 本機原圖 → provider → 草稿 → 回覆；不宣稱曾連線真實 Telegram／模型 |

獨立案例涵蓋：照片 idempotency 衝突／偽圖片、關閉 AI 仍可手動入帳、人工分攤與幣別錯誤、兩個併發確認只有一筆財務效果、晚到辨識不蓋人工修改／取消、lease 過期與接手、重試耗盡及手動重試、維護鎖期間心跳、跨 owner／CSRF／scope／revocation 拒絕、群組／白名單外拒絕、重複 update、舊按鈕、報表指令、無 API key、無效 provider 回應、非法下載路徑與超額圖片、HTTP 限流與錯誤內容去識別化。

合成測試畫面已檢查：[桌面草稿](screenshots/capture-desktop.png)、[手機尺寸草稿](screenshots/capture-mobile.png)。修正初版對話框與表單選取後，8 項流程全數通過；補拍視窗範圍截圖時另重跑 2 項草稿測試通過，兩尺寸皆可捲動且無水平溢出。

## 尚待真實啟用驗收

- 尚未選定實際模型，未下載 Ollama 模型或呼叫付費 API；模型準確度、真實中英文收據品質及 p95 <15 秒保持未驗證。
- 使用者已建立 Bot；本機仍未成功設定，沒有以真實 Telegram 帳號做收送、手機拍照或新 Bot 睡眠喚醒驗收。
- 手機尺寸自動測試不代表真實 iPhone Safari 驗收。原始收據圖片 fixture 為明確標示 TEST DATA ONLY 的合成圖。
- 首批一張圖一份草稿，未實作相簿／多頁合併；Bot 內完整拆分類改在 Web 操作，商品正規化與品項逐欄人工修改尚待後續。
- 商家預設規則、每日摘要與主動排程通知未交付，Phase 2 未整體標 DONE。正式備份目的地仍沿用本機 data/backups，外接目的地待使用者指定。
- 既有 Starlette TestClient deprecation warning 與 Vite 主 bundle 大小警告仍保留；沒有放寬驗證條件。

正式部署已完成 0003 → 0004 遷移，升級前後各完成一份本機備份，財務指紋一致；API／Web／DB 健康，capture-bridge 依使用者選擇未啟用。

GitHub 首次檢查的後端與前端步驟通過；瀏覽器 fixture 因 CI 缺少 `PYTHONPATH` 失敗。已為該步驟明確指定 backend 匯入路徑並重新提交；最終 CI 結果以對應 commit run 為準。

## 啟用流程修正

使用者實際操作時混淆帳本帳號與 Bot 帳號，且原程式將 Telegram 所有失敗合併成同一訊息。現在從本機列出可用帳本、單帳號自動選取；隱藏欄位無法關閉回顯時停止；Telegram HTTP／DNS／憑證／逾時錯誤分開提示但不顯示 Token 或 URL。未知 User ID 可用 3 分鐘有效的隨機配對碼，僅接受相符私聊本人，輪詢不推進 offset、不改 allowed_updates，以保留待收訊息。

新增 13 項本機腳本測試（不連 DB／Telegram），在 Mac Python 3.9.6 與專案 Python 3.12 均通過；覆蓋帳號選擇、誤貼 Token、隱藏輸入失敗、HTTP／網路錯誤去識別化、無效／過大回應、群組／轉傳／舊配對碼拒絕、配對逾時與滿佇列保留、失敗不改設定、成功設定檔 0600 權限與保留既有項目；納入 GitHub CI。Ruff 通過，本機唯讀帳本自動選取通過。Mac 系統 Python 3.9.6 對 Telegram 公開 HTTPS 的無 Token 連線通過，這只證明當時 HTTPS 正常，不能判定先前隱藏輸入的 Token 為何遭拒。尚未使用新的真實 Token 做驗證，正式設定保持不變。
