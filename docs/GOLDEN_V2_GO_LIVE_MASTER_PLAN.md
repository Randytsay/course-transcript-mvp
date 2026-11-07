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

## 6. Owner 決策格式（2026-11-07 起的強制規範）

使用者要求**凡需確認一律提供選擇題、最佳建議及原因**，不可只問「是否同意」也不可丟一長串提示詞請使用者轉交 AGY。每次僅提出當前真正阻塞的**單一 Gate**，各選項都要明說立即授權範圍及風險；未選擇前保持現有安全狀態。建議用 `A / B / C`，並附：

- **決策主題**：對應 Gate A（正式部署）、B（Ledger 精確修正）、C（付費／重送防護）、D（Drive 發布）或特殊的正式備份隔離還原；列出版本 SHA、Job/source 範圍及前置證據。
- **A（推薦）**：以最窄權限、最小樣本、可驗證/可回滾方式逐段推進。逐項說明為何最安全，以及費用上限／授權有效期。
- **B（保守）**：暫停有副作用的階段，只跑離線、唯讀或人工覆聽，以更多時間換取較低風險。
- **C（若適用）**：另一個具體可行但較高風險/成本的替代路徑；若目前工具／權限根本不支持，不應當成能執行的選項。**永遠不提供繞過備份、跳過 Owner Grant、強制標 Golden PASS、重送未知 Operation、直接改帳本的捷徑。**
- **選擇後**：先核對實際 Owner 授權形態（有些操作需後端已簽署/持久化 grant，不以聊天室文字代替），再於 #173 留 `OWNER DECISION` 的**最少必要、非秘密**摘要、執行權限與下一個 AGY/ChatGPT TASK。沒有授權工具就明確說無法代為啟動/執行，絕不宣稱已完成。

### 目前推薦的關卡策略（不是已取得的核准）

| 關卡 | 推薦 A | 原因 | 保守 B |
|---|---|---|---|
| AGY-LOCAL-002 v2 唯讀證據 | 由有**既存** VPS 讀取權限的 AGY 核對正式/PR159/PR158 Ledger；只上傳彙總證據 | 目前受控 ChatGPT 入口讀取正式 SQLite 會 PermissionError，先核對 34/136 vs 16/78 才能防止重送與漏帳 | 暫不啟動 AGY，ChatGPT 繼續無權限的 DEV/CI；G4 持續 UNKNOWN |
| Gate A 部署 | source+image SHA 一致、獨立還原成功後，限定一版及健康檢查、明確回滾點的逐步更新 | API/Worker 歷史 split-brain，直接整套替換風險高 | 維持舊版但仍禁止新付費/發布 |
| Gate B Ledger | 僅對已驗證來源、Operations、費用完成的**一筆 Job**做最小受控寫入 | 避免誤刪 historical receipts／重算費用 | 繼續維持 REVIEW / PROCESSING 並補雲端查證 |
| Gate C Paid | 精確一個來源資料夾、後端有效簽署 policy、累計 600¢ 新模型費用／預留上限、未知 Operation 不重送 | 控制總費用與防重付 | 僅試算／乾跑，不啟動 Paid Worker |
| Gate D Drive | 指定原始資料夾、指定新檔 manifest、新建不覆寫、讀回驗 SHA，附正式 QA/人工覆聽標記 | 可稽核且能防止覆寫原檔/錯標 PASS | 先給本地候選輸出、暫不寫回 Drive |

**工作習慣**：ChatGPT 在等待 Owner 答案的同時仍可推進其他獨立 DEV/CI/唯讀項目；排程監控不得以 Owner 沒回覆為由關閉 G2–G10 或自動跳過授權。

## 7. AGY-LOCAL-002 v2 驗收結果與下一關（2026-11-07）

**正式驗收連結：**[REVIEW #173](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6703907296)、[G4 註記 #160](https://github.com/Randytsay/course-transcript-mvp/issues/160#issuecomment-6703910394)。此節取代第 5 節建立當時「仍等 AGY-LOCAL-002 v2」的暫時說明；第 5 節保留作稽核歷史。

- **AGY-LOCAL-002 v2：ACCEPTED（限本機唯讀調查完成）**。AGY 回報的 current、PR159、PR158 SQLite 具有相同來源識別；三份均有 16 筆 Chirp DONE／78¢，操作名稱完全交集、沒有差集、共享結果 SHA 和已知費用均無差異；不同的是 Job revision 4 → 5、Chirp stage RUNNING → REVIEW_REQUIRED。跨 DB 只是逐一穩定讀取，沒有雲端跨系統原子快照。
- 這些屬**AGY 既有 sudo 權限的本機報告**；ChatGPT 仍無法直接開啟受保護的 Production SQLite，已獨立查核比對器來源與交易／NO-GO 行為，但尚無雙方獨立 DB 重跑。歷史的 34 筆／136¢ 仍是**未證實的外部回報**，不能稱有 18 筆已確定遺失、也不能以 16／78 當整個 Google Cloud 費用帳單。
- **G4 尚未關閉**：需要 Owner 另行選擇是否授權 AGY 對 *同一來源音訊* 進行有既存 IAM 權限的**唯讀 GCP LRO status/list 與計費／匯出收據核對**。任何 Cloud Billing/BigQuery 查詢的潛在費用和讀取範圍應在執行前確認；若權限不足、缺少帳單明細或會觸發未知成本，應回報 BLOCKED，不能自行增權或呼叫付費模型。雲端 GET 不代表已被授權、也不代表必然零費用。
- 2026/10/29 已有一次**檔案及 SQLite 層級**備份解壓驗證 PASS（Issue #160），但完整 Docker/API/Worker 冷啟動還原仍 UNKNOWN。11/7 重新唯讀測得 root available 21,504,757,760 bytes（約 20.03 GiB、86.11% 使用），較 AGY 所引過時的 11.22 GiB 高，但單憑可用容量不足以保證隔離式完整冷還原可行。
- 正式服務仍舊版 API `82e0cd45...`、付費 Worker 停止／心跳過期、`paid_execution=false`、`drive_publication=false`。**G2–G10 OPEN，Golden V2 Production NO-GO**。不得因本機 16 筆 PASS 啟動 Chirp 重送、直接 settle／覆蓋 Ledger、繞過人體覆聽、將 AI-limited 當 Golden PASS 或 Drive 正式發布。

**下一個需要 Owner 的選擇題（推薦 A）：** A＝核准指定來源與時間範圍的 GCP Operation/Billing **唯讀核對**（既有權限，先確認潛在費用；禁止新推論／POST）; B＝繼續離線開發/人耳 QA，G4 維持 BLOCKED；C＝暫停此課程雲端核帳與付費處理。不同方案均**不**含部署、Ledger 寫入、付費 Worker、Drive 寫入等授權。

## 8. AGY-LOCAL-003：雲端作業部分驗收（2026-11-07）

- 正式 [AGY 回報](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6704072555) 與 [ChatGPT REVIEW](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6704234214)；主驗收 [#160 註記](https://github.com/Randytsay/course-transcript-mvp/issues/160#issuecomment-6704237738)。判定 **ACCEPTED_PARTIAL**，不是 G4 PASS。
- AGY 在既有 IAM 下回報以 Speech-to-Text V2 `operations.get` 核對 20221217 **16/16 已記錄 Operations：HTTP 200、done=true、無 error**；這只證明已知 16 筆雲端運算完成，未證明完整 Output/Receipt SHA、所有曾發送的 Operations 或實際每筆帳單金額。API 結果由 AGY 在受限 VPS 執行，ChatGPT 未重新實際呼叫 GCP，也未取得敏感原始 ID/Response。
- `operations.list` 受核准限額只能讀兩頁／共 40 筆，只有六筆與本 Job 已知 16 筆交集，回應仍有 `nextPageToken`。報告所稱「無其他來源綁定」僅限這 40 筆，且 31+3 筆其他 Job 分類的可重現綁定方法尚待 AGY 以既有本機紀錄補說明。歷史 **34 SUCCEEDED／136¢ 仍 UNRECONCILED**，不能解讀成查明 18 筆不存在。
- Cloud Billing 權限與費用未經證明，**BigQuery billing query 沒執行**。需分清 Cloud Billing Reports Console 的現有報表（可能提供日期、專案、服務/SKU 分組的彙總）與 BigQuery SQL（可能按查詢資料量付費）；前者即使能看，也通常不足以唯一映射每個 LRO。官方參考：[Billing Reports](https://docs.cloud.google.com/billing/docs/how-to/reports)、[Speech V2 List operations](https://docs.cloud.google.com/speech-to-text/docs/reference/rest/v2/projects.locations.operations/list)。
- AGY-LOCAL-003 正式完成其**受限的部分任務**；只需依既有本機紀錄釐清預檢權限測試請求是否包含在總數 18 內，以及 40 筆來源分類的證據，不再自動發新 GCP 呼叫。正式環境 Paid/Drive 仍關閉、G2–G10 OPEN／NO-GO。

**新的 Owner 選擇題（未得到任何新授權）**：
A（推薦）：**授權使用現有的 Cloud Billing Reports 畫面唯讀檢查** 2026-10-26 起相關日期／專案／Speech SKU 帳務，禁止啟動 BigQuery SQL、匯出設定或 IAM 調整；若無法以現有帳號存取則 BLOCKED。原因：可先無付費分析查詢風險地了解計費彙總與既有歷史聲明是否矛盾，但不是 LRO 級帳單證明。
B：**僅核准額外限量的 Speech V2 歷史 Operations.list 查閱**（範圍、最大頁數、費用在執行前再次書面確認），增加對外部是否另有作業的覆蓋率，但雲端費用帳單仍 UNKNOWN。
C：**不新增任何雲端讀取**，先開發 G2/G3、音訊真人覆聽資料與 G5 DEV 釋出護欄，G4 明確 BLOCKED。

無論採哪個選項，都**不授權**任何模型辨識請求、重送、Ledger 訂正、Worker 啟動、部署、Drive 寫入、IAM 擴權或付費 BigQuery SQL。
