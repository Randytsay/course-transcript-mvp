# Golden V2｜ChatGPT 主責 × AGY VPS 執行協作規範

**共同工作台（唯一動態派工與回報入口）：[GitHub Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173)**  
**最終端到端驗收標準：[Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160)**  
**重要：本倉庫為 public。禁止張貼憑證、環境變數、完整 Docker inspect、非公開音檔或逐字稿內容。**

本文件為長期協作規則；#173 的最新 TASK / RESULT / REVIEW 評論是當次執行工單，#160 是正式驗收。PR 與 CI 是程式證據，但不等於正式服務已部署、Golden PASS 或 Drive 完成交付。

## 1. 角色分配

| 任務 | ChatGPT（總負責） | AGY（VPS 本機執行） | Owner（使用者） |
|---|---|---|---|
| 專案規劃、需求拆分、工作優先級、驗收 | 主責 | 提供本機事實 | 決定目標與授權 |
| GitHub 程式審查、開發、PR、回歸測試 | **預設由 ChatGPT 執行** | 只有需 VPS 本機資源而且 #173 明確派工時執行 | 正式合併權限 |
| 正式環境檔案／容器／SQLite **唯讀稽核** | 指定範圍、交叉驗證 | **執行需 VPS 權限的部分** | 提供存取授權 |
| 本機音檔、既有 provider sidecar、離線音訊修復實驗 | 設計、評估、驗收 | 僅針對指定來源、隔離目錄操作 | 核准新付費支出 |
| 真正的冷啟動備份還原演練 | 制訂驗收 | 在明確允許的隔離環境執行 | 涉及正式備份/服務時核准 |
| 付費模型、正式發布、資料庫修改、部署／回滾 | 審查 GO/NO-GO | 只有明確單次核准才操作 | **必須逐項明確授權** |

AGY 不是預設主責。Hermes 的舊 #170 報告是證據來源，不是需要再接收任務的 Agent。

## 2. 常態工作流程（避免複製貼上）

1. ChatGPT 直接完成所有可透過 GitHub connector、開發環境、文件和 CI 完成的工作。
2. 遇到非 VPS 不可的工作，由 ChatGPT 在 #173 張貼 `TASK AGY-LOCAL-NNN`，寫清楚 exact scope、可做／不可做、證據輸出格式、停止條件與參考 SHA。
3. AGY 每次在 VPS 工作**前先讀 #173 最新 TASK 與本規範**；確認 GitHub 認證能讀 Issue 並留言。只執行仍有效、未標註已完成的本機 TASK；沒有 TASK 就不自行擴大工作。
4. AGY 在同一 Issue 留言 `RESULT AGY-LOCAL-NNN`：`DONE | BLOCKED | NEEDS_APPROVAL`、UTC、hostname/environment（非敏感）、實際證據、SHA、殘留風險、影響與零副作用聲明。
5. ChatGPT 根據 Issue 留言與可獨立取得的程式／CI／唯讀資料進行驗收，回 `REVIEW AGY-LOCAL-NNN: ACCEPTED | CHANGES_REQUESTED | BLOCKED`，然後自行安排下一步；沒有 ChatGPT 的驗收，AGY 不可把 Task 標為正式完成。
6. 發生阻塞直接在該 Task 底下說明，無須長文複製回 ChatGPT。使用者在 ChatGPT 說「繼續 Golden V2」時，我會先讀 #173 再執行。

**技術限制：Issue 留言不會自動啟動 AGY。** 請先在 AGY 設定長期工作規則，讓它每次開啟專案時主動同步 #173。若要自動輪詢或定時啟動，必須在 AGY 本身建立相應任務／排程並取得系統授權；本文件不宣稱已設定。

## 3. 關鍵安全護欄

- 正式 `/opt/course-transcript-v2-chat`、`current` symlink、Docker/Compose、Worker、Ledger、Claims、Grants、GCP、Drive、secrets/IAM 一律**不准修改**，除非本次使用者另有明確、對象特定的授權。
- 禁止重送或重建不明結果的付費 Operation；Drive 404/409 不代表可重試或重新產生 ID。
- 來源音訊 SHA、SRT/TXT SHA、Owner Grant、API 收費與實際雲端帳單分別查核；未知維持 UNKNOWN，不補猜。
- US$6 是**每個提交的資料夾**之新模型成本上限，不是每個 Job 的自由額度。
- Golden QA `PASS`、`AUTO_ACCEPTED_WITH_REVIEW`、`AUTO_DELIVERED`、AI-limited `DELIVERED` 是不同狀態，不能互相替換。
- AGY 不得因為其自身有 sudo、git push 或權杖權限，就推定有正式操作授權。
- 正式來源目錄與受控入口版本不一致時先 NO-GO，禁止盲目回退到舊版或熱修改 live Compose。
- 任何授權要附上受影響路徑、Job ID、來源 SHA、指令、預估費用、備份/回滾與可驗證停止條件。

## 4. 資訊與工單的固定存放位置

| 類型 | 唯一位置 | 使用方式 |
|---|---|---|
| ChatGPT ↔ AGY 當前派工、回報、審查 | **GitHub Issue #173** | 最新 TASK、RESULT、REVIEW 留言 |
| 最終 G1–G10 驗收 | [Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160) | 只有核驗證據才能標 PASS |
| 程式修正與 Review | GitHub PR + Actions | 保留 exact HEAD SHA、Run ID；Draft 不代表合併 |
| 版本漂移查核工具 | [PR #172](https://github.com/Randytsay/course-transcript-mvp/pull/172) | `scripts/golden_v2_release_drift_audit.py`，read-only |
| 歷史 Hermes 稽核 | [Issue #170](https://github.com/Randytsay/course-transcript-mvp/issues/170) | 背景參考，不自動延續派工 |
| 私人憑證、真實音檔、敏感日誌 | 只在合規且已授權的私人環境 | 不貼進 public Issue/PR |

舊的 Issue #169 / #170 僅當作歷史來源。不要把它們當新的、與 #173 同等優先度的指令隊列。

## 5. 最簡短的初次啟動指令（僅需一次貼給 AGY）

> 你是 Golden V2 的 VPS 本機執行 Agent；ChatGPT 是專案總負責。請讀 `docs/GOLDEN_V2_AGENT_COORDINATION.md` 與 https://github.com/Randytsay/course-transcript-mvp/issues/173 ，確認你有 GitHub Issue 讀寫能力。往後每次工作先同步該 Issue 的最新 TASK，只做 ChatGPT 明確派給 AGY-LOCAL 的 VPS 工作，直接在該 Issue 留 RESULT，不再要求使用者轉貼報告。沒有新 TASK 就停止，不得變更 Production、使用付費 API 或寫入 Drive。

## 6. 初始執行基線（2026-11-06；非永久真相）

- #171 `66b73bbdb0ea6cd43a9c60d5d359dc248d57c72c`：限定範圍修正 CI 通過，Draft；實際音訊缺字尚未修復。
- #172 `bc4fa5ade4a65235d5c84d105717608a7537bb72`：離線版本漂移稽核工具和回歸測試通過 CI，Draft；不是可部署 release。
- ChatGPT 唯讀核對 `current` 指向 `cdf9e73dccd3a2b2c28d43c158a04b6f1d814f76`；受控入口回報 `82e0cd45aa7ad80d776e2dcd0b0cfe5383eeede0`；對 PR #166 manifest 的 **17 個檔案** SHA256 全數吻合。*沒有*驗證整個映像與原始 Git 提交 ancestry。
- 正式部署仍 **NO-GO**。#160 G2–G10 未經正式驗收。

更新狀態時請在 #173 留有時間戳及證據的留言，不要反覆重寫這段歷史快照。

## 7. 已驗收的本機事實補充（2026-11-06 UTC；動態派工以 #173 為準）

- `AGY-LOCAL-001` 已由 ChatGPT 以 **獨立**唯讀 Docker inspect、正式部署腳本及本機 HTTP GET 驗收，結果為 `ACCEPTED WITH VERIFIED ERRATA`。詳細證據及差異修正：[Issue #173 REVIEW](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6680686017)。
- API 容器 `golden-v2-chat-api-1` 使用舊 `82e0cd45...` 映像，**不掛載 /app 原始碼**；本機端口 `127.0.0.1:8816` 的 `/health` 回報同一版本，`18802` 目前不可用。
- Intake、Worker、Delivery 三個容器使用 OCI 標籤 `a18fd131...` 的映像，實際 `/app` **唯讀（RO）掛載** `cdf9e73...` 正式目錄。早期「/app RW」報告已被 Docker 實際證據推翻，不得繼續沿用。
- 三個背景服務的真實 Compose config label 是 **`cdf9e73.../docker-compose.yml`**，不是 `cdf9e73.../deploy/golden-v2-chat-compose.yml`；API 的 Compose 則位於舊版 `82e0cd45.../deploy` 且包含數個歷史 overlays。**同一 Compose project 由不同配置建立的容器混合運行**。
- 正式部署腳本 `/usr/local/sbin/golden-v2-chat-deploy` 已唯讀確認含 `rm -f current; ln -s ...` 的非原子 symlink 切換，且 `docker compose up -d ...` 只列三個背景服務、未列 API。這不能當成修改 Production 的授權。
- `AGY-LOCAL-002` 已派工在 [Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6680703079)：需本機權限的正式 Ledger／備份 **唯讀** 對帳。ChatGPT 受控帳號直接讀取 `ledger.sqlite` 遇 `PermissionError`，不能自行提權。完成前 #160 G2–G10 仍 OPEN；正式部署 **NO-GO**。

## 8. AGY-LOCAL-002（2026-11-06）唯讀 Ledger 與還原空間驗收

- ChatGPT 已於 [Issue #173 留下 CHANGES_REQUESTED](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6681245715)，**不是 COMPLETE**。AGY 的唯讀盤點與先前 Ledger 快照有重大差異，需先補證，不得直接恢復付費 Worker、重送 Operation 或修改 Ledger。
- 現有正式 Ledger 僅由 AGY 使用本機既有權限進行查詢；其報告：`journal_mode=DELETE`、`integrity_check=ok`、外鍵違規 0；20221217 rev 5，**16 local Chirp DONE / 78¢**；但 #160 舊基線為 rev 4，**34 externally reported SUCCEEDED / 136¢**。兩份並非已完成對帳，雲端實際帳單仍 UNKNOWN。查明時需分清 Operations／Windows／來源 SHA／不同時點資料庫，避免將結果臆測成 18 筆遺失。
- AGY 報告自動資料夾政策仍 `enabled=0`、`v2_auto_acceptance_deliveries` 1 筆 AUTO_DELIVERED、`v2_ai_limited_deliveries` 5 筆 DELIVERED 及 1 筆 PREPARED。上述資料來自其特權讀取，ChatGPT 未能用現有權限獨立重查；AI-limited DELIVERED 不等於正式 Golden PASS。
- ChatGPT 透過獨立、唯讀 `statvfs` 實測 `/` 的使用者可用空間 **12,047,904,768 bytes（約 11.22 GiB）**，使用率 **92.21%**；AGY 回報既存備份 `jobs.tar.gz` 大小 **12,184,091,570 bytes（約 11.35 GiB）**。現有磁碟不可在原地做完整解壓冷還原；未驗證解壓後實際容量需求。若要進一步演練，必須先規劃獨立有足夠容量與隔離權限的目標，並取得明確授權。
- **備份校驗範圍：**AGY 已回報既存 `ledger.sqlite` SHA 與 manifest 相符；`jobs.tar.gz` 的 SHA 僅從 manifest 讀取，該次 **未重新計算或驗證**。此前腳本/DB smoke check 並非容器端到端冷開機演練。
- **多步查詢的一致性：**即使 SQLite 使用 DELETE journal 與 `mode=ro/query_only`，如未以單一顯式讀取 transaction 包住數次 SELECT，就不能保證跨查詢皆為同一個邏輯快照。請 AGY 在修訂報告中指明，而非僅根據沒有 WAL/SHM 檔宣稱完整快照。
- 下一步為 **同一工單 `AGY-LOCAL-002 v2`** 補證，僅允許已核准的唯讀 VPS 查詢；詳見 #173，**未額外授權新的 VPS 變更或付費請求**。
