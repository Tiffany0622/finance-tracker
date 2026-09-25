# 本機收據品質修正：提示版本 2

日期：2026-09-24–25（America/Los_Angeles）。模型與環境沿用 [local-ai.md](local-ai.md)：M4 Pro / 24 GB、Ollama 0.34.4、`qwen3-vl:8b-instruct` Q4_K_M。僅使用本機推論；私人圖片與完整辨識結果不進 Git。

## 修正及保護

- 分別讀取印出的 Subtotal、稅、小費、最終付款與找零，JSON schema 同時送入 Ollama 提示及 format。
- 新工作固定 prompt_version=2；舊工作缺少版本沿用 1，辨識 attempt 保留原始結果及版本。schema_version 仍為 1，DB 仍為 0004_capture，無 migration。
- 模型仍可能由裸 `$` 推測 USD。測試中要求模型提供幣別文字證據也出現捏造，該方案沒有採用。最終用使用者原始文字／圖說的明確幣別交叉核對；純圖片需要手動選擇，AI 建議只供參考。
- 租約、草稿 revision、人工修改與確認入帳保護維持；不重寫舊辨識歷程。

## 合成圖片實測

執行 `backend/.venv/bin/python scripts/evaluate-local-ai.py --output .tools/receipt-eval-v2-final.json`。測試圖片在記憶體中由合成 fixture 渲染，沒有使用真實商家、帳本、Telegram ID 或照片。

| 案例 | 單次辨識秒數 | 結果 |
|---|---:|---|
| 英文：Total 後另列小費及信用卡實付 | 26.24 | 全部預期欄位符合 |
| 繁體中文：TWD、收據折扣及稅 | 15.59 | 全部預期欄位符合 |
| 現金：付款／找零、只有裸 `$` | 12.17 | 金額等符合；幣別應為 null，模型猜 USD |

**原始模型案例通過 2 / 3，評估腳本以 exit 1 結束。** 三份的 amount、subtotal、tax、tip、discount 與日期符合預期；不把應用層的幣別防護計成 OCR 正確。機器可讀結果見 [receipt-eval-v2.json](receipt-eval-v2.json)。每份只跑一次，沒有宣稱 p95 <15 秒或長收據／不同光線全面達標。

## 真實原圖唯讀比對

使用先前已授權的本機收據預覽直接呼叫新版模型，耗時 24.3 秒。以原圖核對日期、實付、小計、稅及小費均符合；先前錯把含稅 Total 當小計的情形已改善。模型仍猜 USD，應用層在沒有明確文字圖說時會保留幣別 null。

本次檢查時使用者已將照片草稿確認入帳，另一份文字草稿已取消；因此只做讀取與記憶體中比較，沒有重新辨識或覆蓋正式草稿，沒有確認任何交易。

## 程式驗證

- 真實隔離 PostgreSQL finance_test：69 項測試通過；含版本 1 工作相容、重試版本與歷史保存、7 組幣別來源／衝突案例、延遲回覆不覆蓋人工修改、確認冪等及 bridge 流程。
- Ruff / format、mypy、Alembic check、OpenAPI 產生通過；API 契約未改。
- 前端 4 項 client 測試與 TypeScript / Vite build 通過；Docker API / Web build 通過。Vite 仍提示既有主 bundle 大小，非本次新阻擋。
- 本機評估腳本獨立執行，不在 CI 假裝呼叫真實模型；CI 使用合成 adapter / PostgreSQL 整合測試。

## 本機部署

部署時以維護標記暫停自動恢復，停止舊 capture-bridge 後依序更新 API / worker、bridge 與 Web；完成後移除標記。Web ready endpoint 回覆 ready，實際瀏覽器顯示本機辨識整合服務運作中、Telegram 已收到訊息；bridge heartbeat 在 60 秒內。

部署前後 transactions 與 capture_drafts 的整列指紋一致；辨識歷程筆數未增加，已入帳／取消狀態維持。這次沒有把測試資料寫入正式帳本。隔離測試 PostgreSQL 已停止；正式 Docker 資料庫及 Ollama 繼續運作。

## GitHub 瀏覽器檢查發現的登入競態

[首次 CI](https://github.com/Tiffany0622/finance-tracker/actions/runs/36106460598) 後端／還原演練、設定腳本、前端及契約檢查通過，瀏覽器 7 / 8 通過；最後一項在登入時失敗。合成 trace 顯示初始頁同時呼叫兩次 CSRF endpoint，接著 refresh 與 login 回應 403，畫面提示驗證失效。React StrictMode 會重複執行初始化 effect，原本兩份 token 請求可能讓 Cookie 與前端 token 不一致。

修正 client 的 `prepareCsrf()`，並行初始化及尚無 token 的 mutation 共用一份 pending promise；成功與失敗皆釋放 pending promise，失敗仍可重試。沒有關閉 CSRF、放寬 Origin 或提高登入限流。兩項新增單元測試確認只發出一次 token 請求、mutation 等待同一 token，以及失敗後可重新初始化；前端共 4 項通過。保留首次失敗證據，後續以新提交重跑完整 CI。

後續仍需更多中英文實拍樣本、冷暖多次延遲、長收據／模糊照片、iPhone 端到端與 Ollama 睡眠喚醒。D2-02 / D2-06 保持 DOING。
