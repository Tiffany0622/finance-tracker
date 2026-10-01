# D1-05 週期交易核心驗證

日期：2026-10-01。起點：main `0b5ccbeb6c9fc1d9c37d4d817fc1d15f4f5ba1c4`。

## 範圍與狀態

D1-05 = VERIFY。此交付為 backend 週期交易核心；Web 對帳差額／附理由確認調整、週期管理與補跑進度介面未交付。不得將 D1-05 或 Phase 1 標 DONE。

## 設計驗證

- 真實 PostgreSQL 16，虛構帳本與 disposable `finance_test*` DB；隔離還原至 `finance_restore_*`。
- 固定 UTC cutoff + 可控制 service clock；不依賴等待實際曆日或 DST。
- 28/29/30/31 日錨點、閏年／平年短月與下月恢復原日數。
- Los Angeles / New York 春季 gap 與秋季 fold 精確 instant；Taipei / Honolulu 同日跨時區；連續每日跨 DST 不變成固定 UTC 24 小時。
- daily / weekly / monthly interval；inclusive end_on；disabled 與停用 owner。
- concurrent / duplicate scan、批次接續、connection restart、cursor rewind 的唯一鍵。
- 真實子程序在 ledger commit 後、job ACK 前 `os._exit(37)`；lease 回收後保持同一 transaction_id。
- create_transaction 寫入後故障：整筆 financial effects 回滾，job retry 僅成功入帳一次；錯誤／過期 lease 不產生交易。
- optimistic revision conflict、catchup_pending、舊 occurrence 凍結金額與 revision；停用、重新啟用與縮短 end_on 取消待執行 job 效果。
- expect_only 無 transaction / job；auto_post actual_verified_at 保持 NULL。
- Owner scope、CSRF、輸入時區／日期／間隔／local_time、帳戶與分攤驗證。
- 完整備份的 recurring counts 與 fingerprint；還原後保留已 posted 交易並完成 pending job，重掃無重複。

## 執行結果

- `uv sync --project backend --frozen`：通過；未修改 lockfile。
- `sh scripts/check-backend.sh`：通過。Ruff lint／format、mypy（41 個 source files）、**198 個 PostgreSQL 測試**（其中 **36 個週期核心案例**）、`alembic check` 無新增 upgrade operations，並生成 OpenAPI。
- Migration 由既有 0006_products 升至 0007_recurring；舊 0005_items 備份 fixture 的 downgrade／upgrade 與隔離還原回歸通過，亦維持 0006_products 備份可讀性。
- `pnpm --dir frontend api:generate`：通過；backend/openapi.json 與 frontend/src/api/schema.d.ts 同批提交。
- `pnpm --dir frontend test`：9 個案例通過。
- `pnpm --dir frontend build`：通過。現有 Vite bundle 超過 500 kB 提示仍存在；本交付沒有變更 UI bundle 行為。
- PostgreSQL **16.15**、Python **3.12.14**；測試用 Linux 暫存 cluster，無生產資料。套件依既有 frozen lockfiles 安裝；runner 的 pnpm 為 11.25.0（repo baseline 11.19.0），未變更依賴。
- 既有 FastAPI/Starlette TestClient 產生 httpx deprecation warning；測試均通過，未因此升級 dependency。

## 尚未驗證／交付

- 真實 Mac 睡眠／喚醒與 launchd 重啟；目前以持久化 cutoff 補跑與程序 crash 測試驗證相同核心路徑。
- iPhone Safari、對帳確認調整 UI、週期規則管理 UI 與補跑進度 UI。
- 年度排程尚未支援；API 對 yearly 明確回 422。
- 自動入帳不是實際扣款證據；未實作銀行匹配、matched／actual_verified_at 人工核實流程。
