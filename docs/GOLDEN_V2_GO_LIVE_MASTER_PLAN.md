# Golden Transcript V2｜ChatGPT 主責全程上線計畫（2026-11-07）

> **狀態：PRODUCTION NO-GO。** 此文件是執行順序與驗收基準，不是 Owner 對任何正式異動、付費模型、Drive 寫入或部署的授權。
> 最新事實以 [Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173) 回報／REVIEW、[Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160) G1–G10 驗收表及**當下** Production 證據為準。本文件中的時間與 SHA 是帶日期的快照。

## 1. 交付目標與責任

最終目標不是 CI 綠燈，而是：對使用者明確提交的單一 Google Drive 音檔資料夾，可靠完成來源識別、可信估價、付費工作防重送、音訊與文字 QA、具品質狀態的 SRT/TXT 及必要 QA／人工覆聽說明，經授權寫回**原來源資料夾**，以 Drive 新檔 ID、讀回 SHA、Ledger 交付收據驗證。每次提交的**單一資料夾所有新增 AI 模型費用與預留總計上限 US$6.00（600 美分）**；不是每個檔案 US$6。

- **ChatGPT（technical lead / release manager）**：維護本計畫、拆解 G2–G10、檢查 PR ancestry 和依賴、開發／審查／測試／CI、反覆驗收 AGY 證據、準備回滾與部署方案、提交精確 Owner 核准請求及最終 GO/NO-GO。能自己透過安全工具完成的 GitHub/測試／唯讀檢查不轉交 AGY。
- **AGY（VPS-local operator）**：僅依 #173 的 `TASK AGY-LOCAL-NNN` 執行**既有權限內**不可由 ChatGPT 完成的本機查核；用 `RESULT AGY-LOCAL-NNN` 在同一 Issue 回報，等待 `REVIEW`。不自行擴權、不自動產生新任務、不認定 CI 等於正式成果。
- **Owner**：只需在風險動作前給**個別、具確切範圍**的核准，包括 Production 變更／merge 或 deploy gate、Ledger reconciliation 寫入、付費 Worker／模型請求、Drive 寫入、敏感權限及回滾。概括「請接手到上線」**不是**所有高風險動作的一次性授權。

## 2. 目前基線（2026-11-07；勿以此代替即時檢查）

- [PR #174](https://github.com/Randytsay/course-transcript-mvp/pull/174) **已合併 main**，merge SHA `7da1a06197b113789f38291128053b5e8efeaf12`；原 exact-head CI `42265204561`、Release Compose `42265204568` 均 SUCCESS。它只提供**離線、唯讀**的現有 SQLite 比對工具及合成回歸，**並非**雲端計費驗證或正式 Ledger 對帳完成。
- AGY-LOCAL-001 `ACCEPTED WITH VERIFIED ERRATA`：API image/health `82e0cd45...`（`127.0.0.1:8816`），Intake/Worker/Delivery 映像 `a18fd131...`，`/app` 唯讀綁定 `cdf9e73...`；歷史部署腳本沒有重建 API，且 symlink 切換非原子。實際三個背景容器 Compose 是 `cdf9e73.../docker-compose.yml`。
- AGY-LOCAL-002 初稿 `CHANGES_REQUESTED`：20221217 目前 DB 曾回報 16 local DONE/78¢（rev5），而 #160 歷史回報為 34 external SUCCEEDED/136¢（rev4）；**未證明相同 Operation 與原音來源**，未知外部帳單，不得重送／清零／猜測 18 筆遺失。**仍待 `RESULT AGY-LOCAL-002 v2`。**
- 最近受控 `service-status`：paid_execution=false、drive_publication=false、Paid Worker `STALE_OR_STOPPED`；Intake 和 Delivery running。根磁碟曾量測約 **19.90 GiB 可用／86.19% 使用率**，不可据此推斷既有 12.18 GB 壓縮備份能在同磁碟安全解壓或冷還原。
- #160 G1 代表初步程式修復門檻；**G2–G10 未正式關閉**。過往 Draft PR 有些已關閉未合併，有些堆疊在未合併分支；需查 commit graph，不可把 PR closed 當成 merged，也不可從 CI 成功推斷程式已部署。

## 3. 總流程與可驗證的完成條件

### P0｜證據與風險鎖定（#160 G4 前置）
**ChatGPT**：用 main 上 `scripts/golden_v2_compare_chirp_ledger_snapshots.py` 的輸出規格制定對帳矩陣；查現有 source/result SHA、互斥寫入與費用預留流程；保留所有 UNKNOWN，維持 NO-GO。
**AGY-LOCAL-002 v2（既有派工，不重複開新 TASK）**：僅用已授權唯讀權限，比對 Production 和 PR159／PR158 既存 `ledger.sqlite`，回報 20221217 操作集合交集／差集、來源是否可比、費用與 stage 之差、原始查詢有無單一明確 `BEGIN`，備份 SHA 的實際驗證範圍。不得輸出原始 Operation ID、來源機密、token、音檔；不得修改 DB／複製備份／改權限。
**出口**：本機操作／成本差異清楚；若 34 筆外部 LRO 仍缺本機不可變證據，標記 **BLOCKED-EXTERNAL-EVIDENCE** 並準備*另外*的 Owner 核准之唯讀 GCP LRO／Billing 查詢；未核對前絕不重新 POST。

### P1｜程式整合、來源綁定、修復護欄（G2、G3、G4）
**ChatGPT**：審核 #159–#172 分支關係、#171/#172 容納程度、所有 closed/unmerged PR；在隔離 DEV branch 實作早期 Chirp REVIEW_REQUIRED 重入、Owner Paid Grant revision/HMAC/CAS 與簽章來源一致性、provider idempotency、已付款 Operaton recovery、音訊 gap triage 與標記；補 mock fault/race/rollback/unit/Compose tests。不可將 AI-limited DELIVERED 假裝 Golden PASS。
**出口**：每項關卡有 exact SHA、獨立 Code Review、CI green、全套負測試與 source/cost/QA/Drive gate；不依靠操作者推測。

### P2｜真正可回滾的部署準備（G5）
**ChatGPT**：做 exact-source → immutable image → Compose manifests → ports/mounts/secrets references 的一致性矩陣；修正 API 與 worker 統一 release 的可審查部署／回滾設計，消除非原子 symlink 切換及不同 Compose invocations 造成的 split-brain；制定分離 API/intake/worker/delivery 的低風險切換順序和失敗即停條件。
**AGY（新 TASK 才能開始）**：盤點現有 protected backups 的校驗證據、冷還原目的地容量／隔離資源、既有授權下可做的唯讀可行性驗證。真正的端到端**隔離**冷還原演練需事先核准目標、資源、臨時資料保護與是否容許寫入；**不在正式 root 磁碟直接展開 12GB 備份**。
**出口**：已證明備份可用、rollback 真正演練、單一 exact SHA source/image 配對及 Live traffic/worker 安全路由；仍不等於批准 Production deploy。

### P3｜Owner Gate A：正式版本部署
提交含 *exact git SHA / image digest / Compose diff / source-DB identity / backup restore evidence / health checks / 預計影響 / 停機與回滾條件* 的一頁核准摘要。
**必須 Owner 明確批准該 SHA 與範圍** 才可由有權執行者部署（AGY 僅處理必要的 VPS 特權操作）；驗證 runtime `/health`、`runtime.json`、四個容器 OCI/mount、private port、資料庫 checksum/版本、API read-only states 全部一致；失敗即停並依授權回滾。

### P4｜歷史 Job 解鎖與精確結算（G6）
**Owner Gate B（Ledger 寫入獨立核准）**：先確認所有外部／本地 Operation receipts 已對帳、費用與來源 SHA 關聯可證。只允許精確 20221217 local reconciliation；比較前後快照與 audit receipts。**不**將 REVIEW_REQUIRED 改 PASS、不做雲端新推論、不發送 Drive。未知雲端結果則維持 BLOCKED。

### P5｜付費續跑與品質（G7–G8）
**Owner Gate C（付費動作獨立核准）**：在真實有效且伺服器已持久保存的範圍限定 signed grant/每資料夾 600¢ 費用上限內，根據 source_sha／Operation receipts 對 20221224、20221231 及已授權新工作執行一次性排程；保留重試 idempotency 與 GCP actual/estimated/reserved 差異；未知 Operation 不重投。
音訊缺字對齊：已有聲學證據、人耳覆聽紀錄、參考 SRT／原音校驗，不得將模型判斷當真人已聽；20221217 的 9 段 acoustic gaps 及其他 REVIEW 必須顯示可核對時間範圍與決策，品質不足就維持 REVIEW_REQUIRED。

### P6｜正式 Drive 回寫與交付（G9）
**Owner Gate D（Drive 寫入獨立核准）**：只允許指定來源資料夾、精確新檔 manifest、srt/txt（必要 QA/review 檔依版本規格）、不得覆寫既有檔，具必要 source SHA + approved grant。發布後讀回 Drive file ID/MD5 or SHA/revision，取得 Ledger 獨立 delivery receipt；human REVIEW 輸出不得混同 Golden PASS。

### P7｜端到端新資料夾 Canary 與收斂（G10）
由使用者提交一個明確新資料夾，逐關驗證來源去重、可信估價、已持久政策/授權、600¢ 交易/保留總額、執行、品質、交付、讀回、失敗再跑防重送與 Observability；提供正常/部分失敗/人工覆聽/回滾操作說明。**需所有 G1–G10 有真實證據才可最終 GO**。不以 UI 顯示、成功 CI 或單一 SRT 當全流程交付證據。

## 4. 跨工具交接與自動巡檢

- 唯一 AGY 任務/結果/Review 控制台：[#173](https://github.com/Randytsay/course-transcript-mvp/issues/173)。長期協議：[協作文件](GOLDEN_V2_AGENT_COORDINATION.md)。
- 唯一 End-to-End 驗收：[#160](https://github.com/Randytsay/course-transcript-mvp/issues/160)。只在真實 Gate 進展時更新，不要每次巡檢重複留言。
- 既有排程 **Golden V2 協作驗收**（已確認啟用、每 4 小時巡檢）檢視新 RESULT／PR/CI；**排程不是 AGY 的啟動器**。沒有新證據就不要發布「已完成」或重複通知。
- **ChatGPT 優先直接完成可安全做的工作**；AGY 僅執行已派 VPS-local work order。Owner 原則上只在 Gate A/B/C/D、來源或無法由工具取得的明確人耳裁決時介入。
- 每次產出記錄：UTC timestamp、任務 ID、source snapshot、commit SHA、CI URL/id、changed files、PASS/FAIL/UNKNOWN、所影響 Gate、無副作用與安全界線、下一責任人。公開 repo 不記錄憑證、完整音檔、原始 cloud Operation ID／簽章或私有 Ledger 內容。

## 5. 當前唯一待決瓶頸與下一動作

**優先 #1：等候同一張 AGY-LOCAL-002 v2 的正式回報。** 可使用合併 main 的 #174 comparator；不得自行宣告 34 vs 16 的差異已解。ChatGPT 同步推進 DEV 的 G2/G3/G5 安全設計，避免空等，但任何 Production gate 都不能因此提前通過。

**預設執行規則：** 能繼續的 GitHub 開發、CI、唯讀稽核與文件工作直接做；遇到需要新 AGY 權限、實際付費或正式寫入時，先產出精確操作、風險及回滾／費用上限，向 Owner 請求**單一關卡授權**，不可因「全程接手」模糊授權範圍。
