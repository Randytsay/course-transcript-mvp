# Golden Transcript V2 — 獨立 AI Agent 全面根因審查交辦書

> **可直接交給第二個 AI Agent 的工作指令。** 目標不是重寫現況報告，而是釐清「為什麼四筆音檔辨識曾成功、局部補辨識也做了，仍然無法完成 Golden QA 與 Drive 正式交付」，提出可驗證、最小、可恢復、符合成本與證據安全的修復方案。**第一階段限定嚴格唯讀、無付費、無發布；經 Owner 另行授權才可修改正式環境。**

## 0. 執行前提（優先順序）

- 唯一現行架構：[GOLDEN_V2_CURRENT_ARCHITECTURE.md](GOLDEN_V2_CURRENT_ARCHITECTURE.md)。先閱讀 AGENTS.md、CURRENT_STATE.md、DECISIONS.md、KNOWN_ISSUES.md、GLOBAL_GUARDRAILS.md、AI_AGENT_HANDOFF.md、docs/KNOWLEDGE_INDEX.md；再讀本文件與 Golden V2 QA/投產合約。
- GitHub Repo: https://github.com/Randytsay/course-transcript-mvp ；VPS 專案：agent:instance-20260503-1541-4d804cfebc0e4980:golden-v2-policy-dev-49620326。
- **正式已觀測 SHA**：17d33faf6239ca5a1fcdfe91f18cef202f271b0f；正式路徑：/opt/course-transcript-v2-chat/releases/<sha>；Live Compose 專案 golden-v2-chat。開發樹 codex/golden-v2-goal-completion 在 2026-10-25 觀測到未提交程式，不能當 runtime 或被 reset。
- PR #145 Open/Draft；PR #147 仍需精確核對 CI（曾有 Python 測試 CANCELLED）；PR #148 合併至 integration branch，與 17d33faf release 對應。**確認 base/head/merge 和精確 checks**，不能把早期快照當最新。
- 任何結論以來源 SHA、時間、證據文件和重現步驟為前提；若無權存取要標「未驗證」，不可猜測。

## 1. 規定的唯讀工具及硬性禁止

- 使用 WebCodex VPS 上 run_shell（僅讀取）。可用 Docker inspect/ps/logs、read-only SQLite、現有 artifacts/備份清單及 checksum、heartbeat/PID，GitHub connector 的 PR/CI 讀取，Drive connector 的 list/metadata/revision/下載回讀 **唯讀**。
- SQLite 僅以以下 URI 開啟，而且立即執行 PRAGMA query_only=ON：

  file:/app/data/golden-v2/ledger.sqlite?mode=ro

- 不得寫 Ledger、DB、Drive、GCS；不得啟動付費 Gemini/Chirp/重送已完成 LRO；不得 deploy/merge Draft PR；不得覆蓋/清除備份、刪除 provider raw、以猜測批准費用、進行 blanket docker prune；不得將任何秘密、token、service-account 或含憑證 remote URL 印在報告。
- 驗證真實費用必須 provider 計費/usage 憑據；SQLite actual_cents 是內部 ledger，不可當最終 billing export。
- 不要求主人重新核准 251018 已核准 US$10，也不要要求重新聽審先前已交辦的四段；缺獨立音證時直接指出需何種證據及可用替代路徑。
- 假如要驗證本地純計算，僅允許離線／斷網／唯讀檔案副本或測試資料；修復任務必須另列提案與授權邊界。

## 2. 四筆必查任務與最後一筆官方快照

| 音檔 | job_id | 截至 2026-10-25 00:42 Ledger | 最需要回答 |
|---|---|---|---|
| 251025_.MP3 | v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be | NEEDS_REVIEW rev15、12 Chirp DONE | 為何補辨識後 12 acoustic blockers 仍不 PASS？ |
| 251018_.MP3 | v2p_560465ab6db05e3da593837f4d76c630 | NEEDS_REVIEW rev21、18 Chirp DONE | 25 acoustic blockers；有無 time/text mismatch，例如 00:38:20 的長 cue？ |
| 261017_.MP3 | v2p_62a124bdd4595aca91c53085c3732ca7 | NEEDS_REVIEW rev12、14 Chirp DONE | 12 acoustic blockers；Drive 較新逐字稿是否與本次音訊/字幕一致？ |
| 250503_.MP3 | v2p_f8505d06c77f5789aa4bba9b461ee1f3 | PROCESSING rev5；13 BASE DONE、1 REPAIR REVIEW_REQUIRED、11 REPAIR PLANNED | 為何一筆 repair raw 缺 word end 且無同時後繼？剩餘 11 筆能否被有界安全恢復？ |

前三筆最近聲學候選為 49 個阻擋（其中 4 個確認偵測到語音）；第四筆更早的 acoustic 候選曾為 146 blockers（17 confirmed speech），**不能和最新修復後結果混為一談**。

## 3. 請逐層回答的根因問題

### A. Program control flow / state machine

1. 讀取 app/jobs/golden_v2_ledger.py:STAGES 與所有轉移方法，重建每個 job 的實際 Stage 狀態機；指出何處把 acoustic REVIEW_REQUIRED 綁定到 chirp_independent REVIEW_REQUIRED，使 compare/adjudication/QA 永久不能 forward。
2. 若既有證據被新 merge/repair 替換，程式如何將 SHA-bound evidence 對接新的 gate？是否缺少 review resume / CAS / claim re-entry？提供 **函式／行號／輸入契約／失敗條件**，不只「建議重啟 worker」。
3. Paid executor 碰到 REVIEW_REQUIRED 時做甚麼？嘗試再跑會不會啟動新付費 request？是故意 fail closed 還是實作死結？提出合法 recovery transition，禁止直接更改 stage state 造假 PASS。
4. 檢查 owner publish grant 是否只能在 Golden QA 完成後使用，Drive publish 是否可無重辨識 idempotent retry。

### B. Recognition / audio completeness / timing

1. 對四筆的 source SHA、normalized duration、Gemini raw 每段 completeness、Chirp BASE/REPAIR operation 原始 result SHA/operation name、word start/end、合併 candidate SHA 建立 provenance 表。
2. 按證據重跑**離線** Chirp merge / acoustic scan 的純函式，核對原 raw recognized text 是否被丟失、重疊窗去重是否誤殺語音、時間段尾端是否有 interpolation/失落 word。
3. 針對 acoustic gaps 區分真正可聽語音、確認無語音、判讀不確定、尚未驗證；若僅靠簡單 VAD / density 分數，不能宣稱已確認有聲或已確認無聲。
4. 分析 missing word end has no same-time successor 的原始 raw 結構，設計**不可創造虛構 timestamp**的本地原始證據修復或明確人工覆核流程；已成功 receipt 不准再提交。
5. 驗證過長或過短 cue、字數與顯示速度、時碼相交、長檔全尾端覆蓋；不能以 formatting PASS 取代 audio-text PASS。

### C. Runtime / deployment / locking / scheduling

1. 唯讀比對四個 Docker image digest、release SHA、啟動時間、restart count、real PID、掛載、heartbeat 來源、Compose 的完整性。
2. 特別查 **Intake 舊映像 8748404 Exited，但獨立 intake heartbeat 文件仍更新**：是否有其他 host daemon，heartbeat 作假活，還是路徑共用？
3. 檢查 Paid Worker 如何根據 stage 取 job、全域鎖、callers、失敗的 result、是否會 busy loop，是否有 N 次 retry 放大帳務風險。
4. 分離 code CI vs live production；不得以 unit tests 通過證明 service/recovery/Drive 能正常運作。

### D. Budget, immutable receipts, backup & safe publication

1. 比對 base/repair operations、stage actual_cents 和 approved_cents，列出每筆未使用額度；無 provider billing export 時明示費用**未完成第三方對帳**。
2. 建立備份 inventory（路徑、SHA、時間、SQLite integrity/restore 可否驗），**絕不能用舊備份覆蓋新版正式 Ledger**。
3. Drive 每筆原資料夾唯讀列出實際 ID/name/size/version/modified/parents；區分「現存」與「本次 Golden 正式交付」。需要時對較新逐字稿的修訂做 SHA/revision 檢查，避免舊程式覆蓋。
4. 對 app/pipeline/golden_v2_delivery.py 驗證 pending → readback → (舊) 備份 → promote → final checksum 是否具備 idempotence 和 editor-safe revision gate。

## 4. 可安全用來取得第一批證據的命令

~~~bash
# 請保留確切時間戳及容器 release，無需看憑證值
date -Is
docker ps -a --filter name=golden-v2-chat \
  --format '{{.Names}} {{.Status}} {{.Image}}'
docker inspect golden-v2-chat-api-1 golden-v2-chat-intake-1 \
  golden-v2-chat-paid-1 golden-v2-chat-delivery-1

# 正式 Ledger 唯讀；請勿省略 query_only
docker exec -i golden-v2-chat-api-1 python - <<'PY'
import sqlite3
c=sqlite3.connect("file:/app/data/golden-v2/ledger.sqlite?mode=ro",uri=True)
c.execute("PRAGMA query_only=ON")
print(c.execute("PRAGMA quick_check").fetchone())
for job_id,status,revision in c.execute("SELECT id,status,revision FROM v2_jobs"):
    print("JOB",job_id,status,revision)
    for row in c.execute("SELECT name,state,actual_cents,evidence_sha FROM v2_stages WHERE job_id=? ORDER BY ord",(job_id,)):
        print("STAGE",row)
    for row in c.execute("SELECT purpose,status,count(*),sum(actual_cents) FROM v2_chirp_operations WHERE job_id=? GROUP BY purpose,status",(job_id,)):
        print("OPS",row)
PY
~~~

其他命令只要涉及生成檔案、SQLite 新建 journal/WAL、provider SDK 初始化或 paid requests，第一階段都不使用。測試建議使用與 release 同 SHA 的 network=none / read-only container 並避免掛載正式 Ledger 為可寫。

## 5. 交付內容（不能只回覆「待人工確認」）

請將所有分析寫成五份審查成果（可以一份報告分章）：

1. **可信流程圖／狀態轉移圖**：實際入口、各 Stage/動作、錯誤分支、責任程式檔與行號。標示原規格與 live behavior 差異。
2. **四筆 Evidence Matrix**：source SHA / Ledger revision / operation receipts / acoustic candidate SHA / blocker 分類 / upstream vs downstream / Golden QA / Drive final delivery，逐項用 verified/unverified/blocked 標明。
3. **依嚴重度排列的真實根因**：P0/P1/P2；每項必須有可檢查的 source evidence、具體故障機制、重現／反證方法、最小修改點、是否需要 Owner、是否需付費。
4. **修復執行清單**：先 provider-free correction→純本地音證裁決→必要時有界新辨識；列出可離線驗證的 fail-closed regression tests、可回退方式、四筆各自完成與 Drive readback Gate，避免無限補辨識。
5. **阻擋矩陣與最小人工動作**：哪些 Agent 已有證據可自動結案，哪些真的需要原音或 owner 另授權；禁止重問已提供資訊。

報告中明確分成「已驗證」「高度可能、待實驗」「無法取得證據」，**不許把 49/146 當即時或自動等同於漏字**。如果有測試，只報真實執行數與結果。所有發現要標明對應 exact SHA。

## 6. 完成標準與階段界線

**獨立審查的完成標準**：另一位工程師不需讀幾百段聊天也能依文件安全重現、知道確切阻擋、決定最小修復/測試、如何合法讓四筆分別通過 Golden QA 與 Drive readback。僅審查完成並不代表字幕正式交付。

**第二階段修復（另有權限時）**：先製作 isolated repair PR + CI/本地測試；正式 Production 合併部署須 Owner 明確核准。新增付費辨識只能在既有核准來源和 budget 範圍內、經過防重送驗證後按確認缺漏區間執行；沒有真實 QA PASS，不發正式檔。任何一次回報必須逐 job 呈現未解決項，而不是提供沒有證據的「已修復」。

## 7. 給 Agent 的一句交辦要求

**請不要繼續提出「重啟 worker」或「再跑 Chirp」等沒有原始 evidence 的泛用建議。直接從 live release 的 Ledger/CAS、raw word window 與 acoustic QA 契約出發，找出目前確切哪一個判斷阻止下一個 Stage，並提供最小可驗證修復方法。**
