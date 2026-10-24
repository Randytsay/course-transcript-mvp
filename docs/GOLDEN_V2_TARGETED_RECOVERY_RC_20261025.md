# Golden V2 — 最小安全續跑候選版（尚未 Production 發布）

**Release base:** 正式運行 Git commit `17d33faf6239ca5a1fcdfe91f18cef202f271b0f`。
**Scope:** 僅同步 Chirp lossless/review、受控 Standard repair plan 子批次，
四筆 owner SHA-bound + US$1 增量預算 reentry/recovery。
**Explicitly excluded:** PR #149 的 AI draft/delivery 變更、Production 自動部署、無證據 acoustic PASS、真人覆聽冒認。

## 已驗證且已執行的正式資料修復（不是 release deploy）

- `250503` 原始 13/13 Chirp BASE windows 已在正式 SQLite 無損恢復為 DONE；
  4 quarantine windows 各 0 字元遺失。
- 先前依 Owner 四筆合計 $1 grant 保留之 250503 單筆增量預算 **21 cents**：
  其中一個 `repair-r01-00` Standard operation 已送出並記錄 **1 cent**
  （不等於 provider 最終帳單）；同一 saved raw 後續發現「缺 word end」。
- 使用修正後的 `reconcile_reviewed_chirp_window` 在 shadow SQLite 試跑：
  21 word entries 保留、0 字遺失、original operation name/1-cent unchanged、
  `acoustic_timing_verified=false`；完成正式資料庫備份
  `ledger-pre-r01-window-timing-20261024T165700Z.sqlite` (SHA
  `39d2069368c419164fea8d1475f984c1b9032f450660e9da6f9f93ee65a26cb5`)
  後，**將該筆正式 operation `REVIEW_REQUIRED → DONE`**。
- 此時 `250503` 的其他 11 個 repair window 仍為 `PLANNED`，**沒有提交**；
  不能以 21-cent reservation 當成 11 筆已完成收據；更不能重新送已經 DONE 的 operation。

## Owner grant 檢驗（已在正式控制器驗證簽章；不輸出密鑰）

- `owner-grants/targeted-repair-four-sources-*.json` 存在、HMAC 驗證有效；
  exact four source SHA 與 ledger match，`authorized_cents=100`，
  `model=chirp_3_standard_targeted_repair`，未過期；
  `allow_production_deploy=false`，**不構成 Production 部署核准**。
- 帳本目前 250503 21 cents 事前保留，本次無新 cloud POST，實際雲端帳單
  未作獨立供應商核對。
- 剩餘另外三筆包含歷史已完成的 `REPAIR` operation；本版 reentry
  需核對其 immutable raw/operation/result/actual cents，不可以重送歷史修復。

## Release Candidate 部署前／恢復契約

1. 在 Owner 對 **此特定 Git SHA** 明確同意正式部署之前：
   **不得更新正式 worker/image、不得執行新 paid provider POST**。
2. 切換前重新查 `service-status`、Docker image SHA、4 job revisions、
   provider operation 狀態、持續費用／保留額度、source SHA、正式 SQLite
   `PRAGMA integrity_check`；做最新 SQLite 備份與可還原性測試。
3. 只在沒有 `SUBMITTING`、`SUBMITTED`、`OUTCOME_UNKNOWN` 或新 activity、
   saved operation 已全部對帳，才可考慮 CAS 重入。切勿從舊 claim hash
   還原 token，且 `PLANNED` 只是本地已準備，非已送出。
4. 先以隔離容器 `--network none` 套用本 RC 測試。
   RC 首先支援原始 13/13 DONE、修復後的 `repair-r01-00 DONE`；
   下一步依原有正式簽章 grant 及同一批次已保留預算
   `recover_interrupted_targeted_claim` 受控新 token，再以原
   `submit_approved_standard_repairs` 在 signed/ledger/claim 完整前提下只處理
   **尚未提交的 PLANNED**。任何 provider outcome 不明須立即 fail closed。
5. 另外三筆需分別完成 exact source + quote + batch-budget CAS，不得在
   250503 未對帳完成前強行並行付費，亦不可重做整檔。
6. 原音空窗聲學 `REVIEW`、語意 QA、AI_REVIEWED/Golden 各自獨立，
   完成實際文字補辨識才可發布。Drive 新 SRT/TXT 舊檔保護，
   readback SHA verified，絕不宣稱「所有四筆已結案」。
7. 若切換後異常：先停止新付費操作並對帳，再把 image 回切經驗證舊 SHA；
   **絕不可直接以部署前 SQLite 備份覆蓋可能已有更晚 provider receipts 的 live ledger**。

## 非網路測試

在原正式 runtime image、`--network none`、唯讀 mounted 程式碼，
本最小 RC 的 targeted reentry + missing-word-end + chirp recovery/stage/
repair driver **43 項 unittest PASS**。
完整功能尚不包含自動整批語意 QA 與 Drive 發布，不能以單一 RC
部署視為四筆錄音結案。

**Owner 待核准：正式 Production 切換到本最小 RC 的 exact commit SHA。**

## CI 安全閘補修（同一候選版的限定兩檔依賴升級）

PR 首次 CI 的「Next.js tests and production build」唯一失敗原因是
`npm audit --omit=dev --audit-level=high`：原 `next@16.3.8` 有
Critical security advisory（含 Image Optimization / Server Actions）。
既有 64 項 Node 測試、TypeScript 與 build 實際成功，並非 Golden
字幕 API/Worker regression。**未關閉 npm audit。**

只將 `frontend/package.json` / `frontend/package-lock.json`
的 Next 及相應 @next/env / SWC 鎖定依賴升級為 `16.4.3`。
保留原 lockfile 的 glibc/musl optional platform 限制，避免
Alpine 產生的鎖檔刪掉 Linux 平台依賴辨識。
獨立 Node 22 容器驗證：
- `npm ci`：完成
- `npm test`：64 / 64 PASS（repo root mounted）
- `npm run build`：正式 Next.js 16.4.3 建置成功
- `npm audit --omit=dev --audit-level=high`：**found 0 vulnerabilities**

這是為了維持原有 CI security gate 而做的最小前端依賴補修，
不是擴大 Golden 字幕業務範圍。RC 目前相對正式基底變動 **8 檔**；
Production 仍等待 exact SHA owner 授權。
