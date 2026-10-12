# Golden Transcript V2 — 驗收 Gate 狀態追蹤

建立日期：2026-10-11　｜　規格版本：`V2.0-design-1`  
執行進度：**2026-10-12 重新盤點：G0–G7 八項 PARTIAL、G8 NOT_RUN，0/9 項通過。已驗證非付費 CI 不等於端到端或 production 驗收；main/production 未切換。**

### 2026-10-12 Goal G3 恢復工作者續報（已完成 source SHA CI）

- 新增 `app/pipeline/golden_v2_chirp_recovery_worker.py`：依 SQLite 已存的 `SUBMITTED` Speech operation ID 有界回收，提供正式 `us-speech.googleapis.com` Chirp 3／GCS client factory（未呼叫雲端）。`SUBMITTING` 結果未知不重送；超額成本暫停後仍允許對已提交操作回收已付費證據；`PLANNED` 不會被恢復程序自行提交。
- 新增 `tests/test_golden_v2_chirp_recovery_worker.py`：使用短音檔、真 SQLite 與 fake Speech/GCS 測試 crash/restart、未知結果、超額回收、證據竄改及輪詢上限。已在來源程式提交 `082a367ee9edb2f412a491307817a99f38151d3e` 驗證：Python **813 tests PASS**、Next.js 行為測試／build PASS、Docker image build/Compose PASS；[CI #38187565339](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38187565339)、[Release Compose #38187565337](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38187565337) 均 PASS。此筆為實測綠燈的**來源 SHA**，文件更新所產生的新提交仍應另查 CI。
- **G3 仍 PARTIAL**：本次只完成持久 operation 恢復整合；尚欠正式已核准付費提交的常駐調度、經可稽核帳單驗證的成本來源、Standard Batch bounded repair、真實 Speech API/UAT 與真人聲學 QA。**未發生任何 Chirp/Vertex 付費呼叫、GCS/Drive 寫入、production 變更**。

### 2026-10-12 本地 Goal 執行續報（待本分支 exact-SHA CI）

- 隔離工作樹 `/private/tmp/course-transcript-golden-v2-goal`；新交付分支 `codex/golden-v2-goal-completion`，與既有 Draft PR #143 的 `feat/golden-v2-gemini38-primary` 分開審查。正式部署版本仍未確認。
- 新增 provider-free V2 coordinator、獨立 Chirp 提交／回收 stage、可選 Gemini 3.5 third stage、聲學 QA、Chirp timestamp-only SRT/TXT render、來源 lineage 與 immutable evidence 寫入。SQLite 加上安全回收本地 claim 的版本檢查；可能已付費的 provider claim 不會被自動重送。
- 2026-10-12 本輪離線驗證（提交前工作樹）：完整 unittest **807 PASS**、另行執行三份重點 unittest **27 PASS**、`tests/test_chatgpt_handoff.py` pytest **27 PASS**、前端測試 **54 PASS**、compileall PASS、Next.js build PASS（19/19 頁）、Compose config PASS、`git diff --check` PASS。上述結果需以**本次提交 SHA 重新核對 CI**；全屬 mock／provider-free 測試。
- 已驗證 `SMART_2PLUS` 無爭議可省略第三模型；有爭議會停在 provider 或真人聲學審查；中斷後本地工作可恢復。未實際呼叫 Vertex／Chirp、未寫入 Drive、未部署，實際付費 **US$0**。
- **Gate 結論維持 G0–G7 PARTIAL、G8 NOT_RUN，0/9 PASS**。仍缺 Vertex endpoint/IAM/價格真實驗證、真實長音訊完整聲學驗收、獨立可信 reviewer 身分與真人聽音、人工 Gold 比對、owner 寫入 API/UI、Drive safe-publish 整合與 staging/production 驗收。現有重疊文字裁切要求逐個 seam 人工確認，沒有宣稱自動去重或整堂完成。

規格：[架構](GOLDEN_V2_ARCHITECTURE_PLAN.md)｜[開發順序與驗收矩陣](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md)｜[2026-10-12 實況盤點／六階段完工計畫](GOLDEN_V2_PROGRESS_AUDIT_2026-10-12.md)

> 此文件是會隨 PR/驗收更新的狀態登記簿。**只有提交機器／人工可核對的 evidence link、commit、測試報告與操作者資訊後，才能把某一 Gate 設為 PASS。** 「文件已提交」不代表其他工程 Gate 已 PASS。禁止憑對話主觀印象標已完成。

| 階段 | Gate | 狀態 | 目前可核對成果／阻礙 |
|---|---|---|---|
| P0 基線／隔離 | G0 | PARTIAL | 2026-10-11 已確認 GitHub main/feature branch、另外隔離 V2 分支；WebCodex 的舊 source Project `.git/worktrees` 無效，正式 runtime SHA／active transcription jobs／備份尚未驗證。未 reset/clean/stash。 |
| P1 3.7→3.8 | G1 | PARTIAL | V2 開發分支的 `correct_text.py`、`correct_text_cascade.py`、Worker/API/Proxy/UI 目標已改 3.8，模型政策與 SDK 配置非付費回歸 included 在提交 `73f1a826` 的 **743 項 Python CI PASS**；仍缺實際 Vertex smoke、相容權限／配額、正式部署與 rollback。 |
| P2 Gemini 3.8 音訊主稿 | G2 | PARTIAL | 新增 `golden_v2_audio_staging.py` 的本機 FFmpeg source-SHA FLAC 分段、`golden_v2_gcs_staging.py` 的經核准 GCS 不覆寫上傳，以及 `golden_v2_primary_stage.py` 串接 Gemini 3.8→SQLite，含超額預估阻擋、結果不明停機及 mock 整合測試；**已加入來源→normalized→窗段 SHA lineage 的本機證據、逐窗 raw SHA/身份驗證及人工 overlap seam 裁切後原始證據 CAS 綁定**；仍未驗證真實 Drive 來源 SHA、正式 Worker/Vertex、長音訊整堂聲學 coverage。 |
| P3 Chirp 獨立聲學比對 | G3 | PARTIAL | `golden_v2_compare.py`/`golden_v2_flow.py` 具備獨立差異比對與先 Gemini 後 Chirp 的順序；新增 `golden_v2_chirp_reuse.py` 可核對相同 normalized FLAC SHA、既有 Chirp completeness PASS 與提交證據後直接使用已付費成果（本次新 ASR 成本=0）；**另已具備獨立 Chirp 非同步提交／回收的 fake-provider stage 與本地聲學 QA**；尚未接正式 Speech-to-Text、bounded Standard repair Worker 與真人完整聲學 QA。 |
| P4 Gemini 3.5 Transcribe | G4 | PARTIAL | `gemini_third_transcribe.py` 實作 Vertex generateContent/VERBATIM、≤15m hard cap、選用/禁用與 mock tests；**尚未實際 Vertex smoke/worker 用量帳本**。 |
| P5 WebCodex 最終裁決與 Golden QA | G5 | PARTIAL | `golden_v2_final_gate.py` + `golden_v2_stage_runner.py` 已可寫入離線比對/QA 報告並持久化 `NEEDS_REVIEW`；**已以 mock 連接 Chirp 字詞時碼 SRT/TXT render 和 QA 報告**，仍未串接正式 handoff/真人整堂聲學驗收。 |
| P6 實測評分與最佳化 | G6 | PARTIAL | `app/evaluation/golden_v2_benchmark.py` 提供人工 Gold 的 CER 計算與測試；**無真實人工金標／三引擎樣本／價格比較**。 |
| P7 ChatGPT Skill / 持久任務 | G7 | PARTIAL | 新增 `app/jobs/golden_v2_ledger.py` SQLite task/claim/費用/來源視窗授權；`scripts/golden_v2_status.py` 唯讀查詢、`app/golden_v2_routes.py` 預設關閉且獨立 Bearer 驗證的唯讀 owner status API、`scripts/golden_v2_review_overlaps.py` 本機人工 seam 檢視／套用（操作員自述，無獨立聽音認證）；`golden_v2_primary_stage.py` 將核准 claim+FFmpeg+mock GCS/Vertex 整合、`golden_v2_stage_runner.py` 離線 QA 橋接；`skills/golden-course-transcript-v2/SKILL.md` 仍為草案。**未安裝 Skill、未接正式常駐 Worker／可寫入 owner API／付費 Vertex／Drive；唯讀 API 仍未部署**。 |
| P8 UI / 部署 / Drive 驗收 | G8 | NOT_RUN | 尚未 staging/canary/production／Drive 實測 |

## V2 文件提交紀錄

- `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md`：V2 分工、Gemini 3.8 遷移、完整流程、長檔/帳務/權限/安全。
- `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`：P0–P8 施工序、G0–G8 逐項核准與資料證據。
- `AGENTS.md`、`README.md`、`docs/KNOWLEDGE_INDEX.md`、`CURRENT_STATE.md`、`DECISIONS.md`：已更新 V2 索引/決策／舊系統現況界線。
- `docs/GOLDEN_V2_INTEGRATION_HANDOFF.md`：現有 V2 SQLite/bridge 的實作交接與尚未整合項目。
- `docs/GOLDEN_V2_PROGRESS_AUDIT_2026-10-12.md`：原始九項目標、當前各 Gate 真實證據、剩餘工作六個波次與權限/費用邊界。
- 非付費 CI：提交 `73f1a8265bd4458699cd66da1c9d6f88cf0779f1`，run [38157612105](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38157612105) 743 Python tests、Next.js、Docker build/Compose 通過；Release Compose [38157612126](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38157612126) 通過。**不代表 G0–G8 PASS 或 Google Vertex/Drive 實測通過。**
- 原本 V1 程式、任務、模型服務、Google Drive 檔案、正式部署：**未因本文件工作而變更**。

## 每次 Gate 更新欄位

```yaml
gate: G0
status: NOT_RUN  # NOT_RUN | BLOCKED | PARTIAL | PASS | FAIL
evidence_commit: null
target_branch: null
production_runtime_sha: UNVERIFIED
tests:
  passed: []
  failed: []
  not_run: []
provider_calls_started: false
provider_actual_cost_usd: 0
drive_mutation_started: false
production_mutated: false
approval_reference: null
blockers: []
next_action: "Start P0 read-only repo/runtime inspection and isolated worktree design"
updated_at: "2026-10-11"
```

注意：上方 YAML 僅是結構示例，未來每一 Gate 應有自己的最新紀錄、連結及是否真的發生 API 使用的證據。即使原本 V1 有相關功能，未經 V2 回歸測試也不能將 V2 Gate 標 PASS。
