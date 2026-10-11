# Golden Transcript V2 — 開發序、驗收矩陣及交付簽核

> 2026-10-11 決策基準｜`V2.0-design-1`｜狀態：**規格已建檔，尚未表示任何程式完成或上線**  
> 上位技術規格：[GOLDEN_V2_ARCHITECTURE_PLAN.md](GOLDEN_V2_ARCHITECTURE_PLAN.md)  
> 現況先讀：[CURRENT_STATE.md](../CURRENT_STATE.md)；Agent 使用：[AGENTS.md](../AGENTS.md)、[AI_AGENT_HANDOFF.md](../AI_AGENT_HANDOFF.md)。  
> 此表是**驗收合約**。任何後續 Agent 均不得把「PR 已建立／測試程式存在／pipeline completed」誤報為正式 `GOLDEN_PASS`。

## A. 已核定的產品決策

1. V2 目標改為 `gemini-3.8-flash`，不要再新增 `gemini-3.7-flash` 依賴；僅在程式與 deployment 經正式驗證後宣告切換，歷史回應繼續記錄真實模型版本。
2. 順序：**Gemini 3.8 Flash 完整逐字稿先行 → Chirp 3 獨立辨識時間軸 → 可選 Gemini 3.5 Transcribe → ChatGPT/WebCodex 交叉裁決 → Golden SRT/TXT/QA → 安全 Drive 發布**。
3. 第一輪驗證採 **`QUALITY_3WAY`**（三引擎完整比較），不以「多數決」代替真實錄音；有聲學爭議須音檔重聽或 REVIEW。
4. Skill + VPS 任務引擎為主要操作流程；現有網站補輕量管理與差異審核，不另建一套邏輯。
5. 首輪真實測試建議（且上限）US$10，**這是成本框架不是立即執行扣款授權**。逐批必須確認具體來源、預估費用與剩餘核准額度。
6. **正式切換、寫回 Drive 與需要帳戶擁有人操作的 IAM/計費變更，必須經使用者核准；** 本文件及非付費開發不構成此核准。
7. 佛經參考文字／既有人工逐字稿用於拼寫與上下文，不能把未朗讀內容補入；Chirp 是 SRT 的原始時間碼權威。
8. 不變更正在執行的 V1 jobs、不更動既有已成功 provider evidence，不覆蓋其他 WebCodex dirty worktree。

## B. 建議開發順序：P0 → P8

| 階段 | 具體內容 | 核心交付 | Gate／可否由 Agent 自行完成 |
|---|---|---|---|
| **P0 安全盤點與隔離** | 找出 main、`feat/media-inventory-dedup`、dirty runtime worktree、實際生產 SHA、active jobs、V1 Skill 與 DB migration 狀態；備份計畫，獨立 V2 branch/worktree | inventory、branch divergence/dirty report、風險與 rollback 清單 | **G0**；唯讀探測與文件可自動做，備份要驗證 |
| **P1 3.7→3.8 相容遷移** | 將 V2 使用的 text-only adapter/default 換 3.8、處理 `thinking_level` 與不支援參數；不改 V1 歷史 evidence，feature flag 保持舊路徑可回退 | 3.8 provider adapter、mock tests、model/version usage 記錄 | **G1**；非付費自動，真 API smoke 需同意 |
| **P2 Gemini 3.8 音訊主稿** | 新增 audio-primary adapter、整檔上下文、必要時分段重疊與全文重組、截斷與完整性 Gate、繁中／台語／經文 prompt、不可變原稿 | `gemini-primary.txt`、raw、coverage manifest | **G2**；mock 自動，真音檔需核准 |
| **P3 Chirp 獨立比對** | 在 P2 主稿驗證後才啟動 Chirp，同音檔獨立 ASR，沿用 Dynamic first pass / Standard repair；文本-聲學對齊、差異標記 | Chirp raw/timing、coverage、`engine-comparison.json`（雙引擎） | **G3**；mock 自動，已授權的真實聲學測試才付費 |
| **P4 第三引擎** | 加 `gemini-3.5-transcribe` Vertex adapter，分段能力與 words/diarization/custom vocabulary 限制探測、可跳過與模型組合切換 | 第三稿與 usage、模式路由、三引擎比較 | **G4**；mock 自動，實際模型可用性需 API 核實 |
| **P5 WebCodex 校對與 Golden QA** | 片段比對、經文與台語裁決、爭議區間重聽、word boundary 重建 SRT、TXT 同源渲染；衝突轉 review，不憑空補內容 | `adjudication.json`、`.srt`、校正版 `.txt`、Golden QA | **G5**；程式／規則可自動，模糊語音需人工裁決 |
| **P6 比較與最佳化** | 至少用國語、國台語混用、密集經文、錄音品質較差各一組（可同一檔兼多特徵）；建立人工確認 Gold reference；評估 CER/WER、漏句、經文、時間碼、成本、速度 | 基準集、模型 benchmark、推薦模式與告警閾值 | **G6**；Agent 統計，自然語言判錯 Gold 需適量人工抽驗 |
| **P7 對話 Skill + 任務 API** | 升級 Skill、VPS Durable Worker、恢復/重試/進度/成本/審核指令；與 UI 共用 API，避免 ChatGPT 對話中斷中止 jobs | V2 Skill、task endpoint、handoff contract、重啟測試 | **G7**；非付費自動 |
| **P8 介面、試營運、正式切換** | 既有 UI 模式切換、並排逐字稿、疑點播放、進度、QA、費用、發布，CI/security/ARM64/exact-SHA staging→canary→rollback 演練 | 使用說明、deploy/rollback 證據、Drive sha 驗證 | **G8**；正式切換/Drive 突變需擁有人同意 |

可平行但不能偷跑：P1 的 API mock 與 P0 非破壞盤點可平行；P3 的 mock 開發可先做，但 paid Chirp **不得在 P2 主稿通過前依 V2 流程觸發**；P4 SDK probe 與 P2/P3 mock 可平行；P7 UI 設計可先構思但不能假造後端已可用。

## C. 核心驗收 Gate（證據必須寫出 PASS / FAIL / NOT_RUN）

### G0 — Baseline / 安全
- [ ] G0-01 main HEAD SHA 與 current branch、未提交修改、未合併分支差異已記錄，**沒有對 dirty runtime worktree 做 reset/clean/stash**。
- [ ] G0-02 正式環境 release label／revision guard 校驗；若無權限，明確寫 `UNVERIFIED`。
- [ ] G0-03 已確認進行中的 jobs、提交中的 provider operations 和 lease；**沒有造成任務重送**。
- [ ] G0-04 SQLite DB + data/jobs snapshot 保存與 SHA／基本還原可用性記錄；備份前不得動 production。
- [ ] G0-05 現行 Golden v1.6/v1.7 分支與 main 合併方案已整理，未把 branch-only 規格稱為 production。

### G1 — Flash 3.8
- [ ] G1-01 V2 呼叫路徑模型字串全部為 `gemini-3.8-flash`；歷史 3.7 evidence 保留。
- [ ] G1-02 `thinking_budget` / 不相容 generate config 已依官方規範更新，錯參數會被測試攔下。
- [ ] G1-03 可在未設定真實金鑰時用 fake provider 測試（不暗中使用實際 API）。
- [ ] G1-04 使用 Vertex 同專案的權限/region/model probe 結果被記錄；真實 smoke 若未核准則 `NOT_RUN`。
- [ ] G1-05 有 feature flag / rollback，且既有 V1 job resume 不受破壞。

### G2 — 主稿完整性
- [ ] G2-01 Gemini 3.8 **先於 Chirp**執行，新 job stage 排序與 durable event log 可證。
- [ ] G2-02 短音檔可完整產生繁中主稿，保留原回應、token 使用量與停止原因。
- [ ] G2-03 長音檔超輸出／截斷可分段、重疊、去重並恢復重組；**未覆蓋的語音不會宣告 completed**。
- [ ] G2-04 網路錯誤重啟後已完成 segment 不重送；保留每段 attempt、source SHA、音檔 offset。
- [ ] G2-05 國台語混用與佛經測試中，不把 prompt/reference 沒有聲學支持的文句硬塞進 final。

### G3 — Chirp 和雙引擎比較
- [ ] G3-01 初次 Chirp 是獨立辨識，沒有提前使用 Gemini 主稿直接作其答案。
- [ ] G3-02 Chirp word timestamps monotonic、midpoint ownership、overlap merge 正確；舊成功 chunk 不重送。
- [ ] G3-03 Coverage/VAD 對真空白區與 audible gap 可區分；bounded Standard repair 而非 full rerun。
- [ ] G3-04 差異報告至少有 `source_time_start/end`、Gemini/Chirp 候選、差異類別、風險、引用的 evidence IDs。
- [ ] G3-05 無法對齊的 Gemini 段落會被列 review，不產生模型虛構時間碼。

### G4 — Gemini 3.5 Transcribe
- [ ] G4-01 能獨立選擇開關；`QUALITY_3WAY` 全文、`SMART_2PLUS` 按需觸發；OFF 時沒有 API 花費。
- [ ] G4-02 Vertex/Agent Platform 預錄轉錄採保守 ≤15m chunk（依端點當時模型 capability 再驗）；自訂詞彙與時間碼／說話者分類需依 Vertex capability 驗證，不自行臆測不相容；並自動分段。
- [ ] G4-03 使用同一 Google Cloud Billing/Project 的可用性、usage/費用證據有紀錄；未核實不宣稱成功。
- [ ] G4-04 三稿可比對，但「2 比 1 多數決」不得直接覆寫音訊或 Golden 字幕。

### G5 — 裁決 / Final SRT & TXT
- [ ] G5-01 原音檔第一優先；Gemini、Chirp、Transcribe、經文／簡報的 provenance 有分類。
- [ ] G5-02 朗讀原經文與老師白話改述可區分；經文字面變更有依據與差異紀錄。
- [ ] G5-03 ChatGPT/WebCodex 只回傳穩定的文字修正／裁決及來源 span；時間碼由 deterministic Chirp alignment 出。
- [ ] G5-04 所有已確認有聲音的片段都有文字或明確 review blocker；無聲處不得補字。
- [ ] G5-05 SRT 序號連續、cue 有效、時間單調、`overlap=0`、`empty_cue=0`、`invalid_duration=0`。
- [ ] G5-06 `SRT_text_normalized == TXT_text_normalized`（同源文字，保留合法標點、經文，不准只用文字長度判斷）。
- [ ] G5-07 團隊 Golden QA 所有 blocker = 0 才標 `GOLDEN_PASS`；不能用完成率掩蓋未解 blocker。

### G6 — 模型評測
- [ ] G6-01 代表性金標音檔需經人工確認，不以任意模型輸出當 ground truth。
- [ ] G6-02 逐模型 CER/WER（按語言適當正規化）、經文錯字、漏段、時間偏差、成本、延遲均有紀錄與樣本數。
- [ ] G6-03 模型比較保留原始差異統計與配對樣本，不只顯示單個綜合分數。
- [ ] G6-04 跟 V1 baseline 對相同音檔、相同 QA 規則做 regression；未達到明確指標就不切換日常預設。
- [ ] G6-05 `SMART_2PLUS` 的模型加呼叫比例與節費效果先由真實數據證實，才宣稱最佳化。

### G7 — Skill / 長工流程
- [ ] G7-01 自然語言 Drive URL + 模式 → durable job_id；能查最新進度、失敗原因、review 區間。
- [ ] G7-02 客戶端／ChatGPT 對話斷線後，VPS worker 可續跑；重連能以 job_id 查狀態。
- [ ] G7-03 重啟、短暫 provider timeout、Drive 暫時中斷均不重送完成的付費步驟。
- [ ] G7-04 Budget、Auth、Drive safe-publish 的審核與非授權拒絕都有自動測試。

### G8 — UI / 部署 / 發布
- [ ] G8-01 桌機／平板／手機都能查看模型選擇、進度、三稿差異、疑點音檔、QA/費用。
- [ ] G8-02 所有 `pytest/unittest`、前後端 build、型別檢查、dependency audit、Compose 驗證和 smoke 全通過（若例外須逐項記錄）。
- [ ] G8-03 staging/canary 確認舊 V1 job resume、資料庫遷移與 rollback；使用 exact commit SHA 而非 dirty runtime。
- [ ] G8-04 真實付費測試有特定來源、單次預算與累計 US$10 核准，不因自動 cost threshold 而略過。
- [ ] G8-05 Drive 正式 sidecars 安全交易（pending/verify/backup/promote/final verify），舊版本留存，原始音檔/原人工逐字稿不被覆蓋。
- [ ] G8-06 遠端 Drive final SRT/TXT 重新讀回、SHA-256 核對、publication freshness 與 Golden marker 正確。
- [ ] G8-07 使用者確認 production cutover，完整 release label/health/job/QA/費用/Drive 驗收證據保存。

## D. 代表性測試集與量化評估

規劃 test cohort（均先確認授權再付費）：
- T01 國語講解 + 正常語速；T02 國台語交替；T03 密集佛經、經名、咒語；T04 背景音／停頓／收尾；T05 一份長於單次輸出能力的長音檔；T06 一份已驗收 V1 舊作為 regression。
- 結構硬門檻：`audio coverage` 不得有未審核的可聽見語音缺口；SRT `invalid=0`、`overlap=0`、`empty=0`；SRT/TXT 正規化文字必須一致；所有 blocking review = 0 才正式 Golden。
- 文字指標：依人工 gold 衡量 CER/WER、未朗讀增字、經文逐字差、台語關鍵詞、整句漏段。**在尚未建 Gold reference 前，不可稱三引擎「準確率 99%」。**
- 效能：原音檔小時數、端到端 wall-clock、每引擎 paid seconds/tokens、cost USD、retries/reuse ratio、人工介入分鐘；跟 V1 相同音檔配對。
- 先保證不退步，再根據 baseline 設定合理的數字型改善 target；新門檻需實測後經使用者接受並回寫文件，不能由 Agent 自行宣稱達標。

## E. Agent 提交驗收紀錄的固定模板

每次 PR／進度回報應填：

```text
Milestone / Gate: P?/G?
Repo, branch, exact commit SHA:
Production runtime SHA (verified/unverified):
Changed files:
Tests & outcomes (pass/fail/not_run):
Audio/test case SHA / corpus id:
Providers actually called? NO/YES (which, how many, paid usage):
Estimated USD / Actual USD / Reserved USD / User approval reference:
Old provider evidence reused vs regenerated:
Coverage, VAD, Golden QA verdict:
SRT/TXT alignment / output digests:
Drive mutated? NO/YES, folder/source/sidecars/final readback sha:
Production mutated? NO/YES:
Unresolved blockers + next minimal action:
Rollback path:
```

維持 `GATES_STATUS.md` 或機器可讀 `reports/golden-v2-gates.json`，但只有具可稽核 evidence 的 Gate 才能從 `NOT_RUN` → `PASS`。更新證據不得覆寫歷史 provider raw files。

## F. 使用者真正需要介入的事情（保持最少）

**一般不需介入**：專案盤點、V2 開發隔離、寫程式、假 provider 測試、差異分析規則、CI、Skill/介面開發、staging 低風險驗證、報告整理。

**只在必要時介入**：
1. 若 Google Cloud 的 Vertex 模型或 Speech API 權限、帳務、配額不足，由擁有人操作 IAM/Billing 授權；Agent 不要求在聊天傳送金鑰。
2. 第一次真的要呼叫三個付費引擎時，由擁有人確認**具體來源音檔、預估金額、當前 US$10 測試預算餘額**。
3. 低可信台語／佛經語句需要人類裁決時，僅呈現特定音訊區段與三稿差異。
4. 最終 production 部署、替換正式 SRT/TXT／回寫 Drive 前，由擁有人確認變更、驗收與回滾方案。

**現階段執行範圍：**僅將規格與驗收寫入 GitHub；**沒有改 production、沒觸發付費模型、沒寫入 Drive**。下一個安全行動是 P0 唯讀盤點／開發分支隔離；不可把本設計文件當作 P1 以後 PASS 證據。
