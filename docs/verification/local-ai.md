# 本機 AI 安裝與驗證

日期：2026-09-24（America/Los_Angeles）。使用者選擇本機方案；正式收據只在本機辨識，未使用雲端 API。私人圖片、辨識全文、金鑰及帳本識別碼不存 Git。

## 執行環境

- MacBook Pro Apple M4 Pro、24 GB 統一記憶體、macOS 15.7.8。
- 官方 Ollama 0.34.4 DMG；macOS `codesign --verify --deep --strict` 通過，`spctl` 判定 Notarized Developer ID。原生 CLI 位於 `/Applications/Ollama.app/Contents/Resources/ollama`。
- 最終選型為 `qwen3-vl:8b-instruct`，Q4_K_M，6,140,415,975 bytes。digest：`0533d74300e4f9bc367d675d4e64ffd073d50ff16a2b4096cc2e8a1cf8c96319`。本次下載但不採用的 thinking 模型已清除。
- LaunchAgent `com.finance-tracker.ollama` 已安裝並啟動：RunAtLoad / KeepAlive、127.0.0.1:11434、OLLAMA_NO_CLOUD=1、context 8192、單模型／單請求、keep-alive 2m。登入設定已檢查；實際重新登入及睡眠喚醒尚未驗證。
- Docker Desktop capture-bridge 可連到 `host.docker.internal:11434`；沒有改成 0.0.0.0 或新增公開連接埠。正式資料 schema 仍為 0004_capture。

## 啟用流程檢查

- 新增 `ollama-launchd.py` 與 `enable-local-ai.py`。啟用前檢查模型為本機 vision 模型、Docker 能連線；保留 Telegram Token／配對及其他設定，原子寫入 0600 `.env`。
- 本機腳本 19 項測試（既有 13 + 新增 6）於 Python 3.9.6／3.12 通過；涵蓋 probe 不改設定、成功保留機密、錯誤／逾時不寫設定、不回顯診斷、雲端／非 vision 拒絕、原生登入服務。
- 隔離 finance_test PostgreSQL 的 2 項 provider／transport 測試通過；更新的 Ollama 請求設定 `think=false`。Ruff / format 通過。測試資料庫已停止。
- API 重建後曾遇到 Nginx 保留舊 API 位址導致 502；重啟 Web 後 ready endpoint 回復。啟用腳本現在於 API 更新後明確重建 Web（`--no-deps`），避免此問題重現。

## 模型實測與限制

最初下載的 `qwen3-vl:8b`（digest `901cae732162…`）對應 thinking 模型。首次 GPU discovery 逾時，後續載入仍確認 M4 Pro Metal、37/37 層在 GPU；首次請求超過 120 秒。即使 `think=false`，限制 1000 token 的直接診斷仍帶 thinking 欄位並被截斷，因此明確改選 Instruct 標籤。這些失敗未產生正式交易。

Instruct 的首次直接測試耗時 **23.7 秒**（其中模型載入 9.9 秒，生成 198 token、無 thinking 內容），通過 ParsedReceipt 驗證；這次為剛下載模型的單次測試，不是 p95。

正式流程再由 Web 對使用者先前傳到 Telegram 的照片按「重新辨識」：API → capture-bridge → Mac Ollama → 原有草稿 → Bot 回覆通過。本次 Ollama 請求耗時 **22.7 秒**；工作首次 attempt succeeded，草稿 needs_review，保留原始照片與 1 個品項；所有 Telegram 傳送工作為 succeeded。正式交易筆數與辨識前相同，草稿沒有 confirmed_transaction_id。UI 自動從處理中更新為待確認。

逐欄核對原圖：商家、日期、實付總額、稅與小費符合；**小計誤讀為含稅總額、幣別為 null**，系統顯示品項合計不符及幣別待補等 4 個提醒。未自行更改辨識歷程、選帳戶／分類或確認入帳。這證明可用但仍需人工核對，不能宣稱這張照片每個欄位都正確。

`ollama ps` 確認最終模型 **100% GPU**、約 6.6 GB、context 8192；閒置後再次查驗，模型清單為空，已自動卸載。`.env` 權限 0600、雲端關閉、Ollama 登入服務及 capture 自動恢復標記均核對存在。正式 Web ready endpoint 通過，未改資料 schema。

單張測試不代表多種中英文收據、長收據、遮擋及不同光線全面通過；p95 <15 秒、冷暖多次量測與新整合睡眠喚醒保持待驗。AI 只能更新草稿，仍需人工選帳戶／分類、核對並確認。

參考：[Ollama 模型標籤](https://ollama.com/library/qwen3-vl/tags)、[Mac 安裝](https://docs.ollama.com/macos)、[Thinking 控制](https://docs.ollama.com/capabilities/thinking)。
