# D1-04：收據附件驗證

日期：2026-09-24（America/Los_Angeles）。本次只交付 T-05 / D1-04，不代表 Phase 1 全部完成，也未啟用 OCR、AI 或 Telegram。

## 已交付

- 「記帳 → 交易列 → 收據」可一次選多張 JPEG / PNG / HEIC，分批保存、查看順序／尺寸／大小／時間、放大預覽、下載原圖與確認移除。
- 單張 20 MiB、5,000 萬像素，每筆 20 張。MIME 與實際解碼一致；拒絕偽圖片、損壞檔、PDF、多影格及超額檔。路徑只用隨機 UUID，原始檔名經正規化且不當作儲存路徑。
- 原圖不改動位元組；另產生最長邊 1600px JPEG 預覽，依 EXIF 旋轉、透明背景轉白、不帶 EXIF / GPS / XMP。所有讀取需登入與 owner 驗證；寫入沿用 CSRF / Origin。
- 上傳不更動交易或報表數字；更正及作廢保留附件。附件刪除與帳務作廢分開，既有備份保留政策不變。
- Schema `0003_receipts` 新增 attachments、receipts、transaction_attachments、receipt_attachments。收據尚未辨識的狀態是 not_requested；沒有捏造 OCR 金額或品項。
- 檔案先 fsync / rename，再提交 DB 關聯；同 key 重試及併發只建立一份 metadata／頁序。磁碟寫入失敗回傳可重試錯誤，程序退出遺留的無引用檔延遲清理。
- 每 5 分鐘清理至少 24 小時的 staging／孤兒／未引用 deleting 檔；維護鎖阻擋與備份衝突的寫入。正式備份只包含 active 圖片，核對引用、原圖／預覽雜湊及四張表的指紋。

## 已執行的檢查

| 項目 | 結果 |
|---|---|
| Ruff / format / mypy | 通過，22 個 application 模組 |
| PostgreSQL 整合測試 | **40 項通過**；使用隔離 finance_test，未使用正式帳本作為 fixture |
| 遷移 | 0002 → 0003 → 0002 → 0003 在測試庫通過；alembic check 無新增操作；正式庫不降版 |
| 前端 | TypeScript / production build 通過；既有 2 項 Vitest 通過 |
| Playwright | **6 項通過**，桌面 1440 × 1050、手機 390 × 844；調整選圖區後另重跑 2 項附件測試通過 |
| 視覺 | 已開啟並檢查桌面／手機截圖，修正初版選圖區過度擠壓；視窗內可捲動、無水平溢出 |
| HEIC | pillow-heif 1.8.0 + Pillow 12.3.0，Mac arm64 與 Linux arm64 容器成功將合成 HEIC 轉成 JPEG；原圖下載逐位元相同 |
| 一致備份／隔離還原 | 實際帳本結構 + 上傳的合成 HEIC + 預覽／關聯／頁序完成 pg_dump → 隔離還原；原圖、帳戶餘額、固定報表、登入均核對成功 |
| Docker | Mac 上的 linux/arm64 前後端映像建置通過；HEIC 在無網路容器中實際解碼 |

獨立案例包括：跨 owner 預覽／下載／新增／移除拒絕、未登入拒絕、CSRF 拒絕、同鍵異內容 409、兩個併發重試只有一份、20 張限制與移除後再上傳、偽 MIME / PNG 像素炸彈 / chunked 超額傳輸、EXIF 旋轉與預覽去 metadata、下載原圖比對、移除不改金額、作廢保留原圖、檔案缺失／符號連結／校驗失敗阻擋備份、受獨占備份鎖保護的寫入、原子 rename 後真正 process exit 37 的恢復、磁碟錯誤重試及孤兒不進備份。

測試截圖只含虛構資料：

- [桌面收據視窗](screenshots/d1-04-desktop.png)
- [手機尺寸收據視窗](screenshots/d1-04-mobile.png)

## 邊界與後續驗收

- HEIC 相容性證據使用自行生成的合成 fixture，不代表所有 iPhone 相機模式或真實 iPhone Safari 已驗收；實機選圖／拍攝與私人遠端連線仍屬 D1-08 未驗項目。多影格／動態 HEIC 明確拒絕，需拆成單張。
- 現在從已入帳交易附圖；沒有辨識商家、金額與品項。Telegram / 草稿確認仍依 Phase 2。
- 交易列表目前只顯示有效交易；作廢附件仍保存在 DB、檔案及備份中，尚無專用歷史附件瀏覽頁。
- 備份仍沿用既有設定，本機 data/backups 不是外部磁碟保護；正式目的地仍待使用者指定。
- 本機自動測試有既有 Starlette TestClient deprecation warning；Vite 主 bundle 約 763 kB / gzip 256 kB 警告仍保留，未提高門檻。
- GitHub CI 與正式 Mac 部署結果以本次提交／任務交付記錄為準；以上數字是實際本機驗證，沒有用編譯通過冒充實機測試。
