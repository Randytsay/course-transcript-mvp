# Golden Transcript V2 — 驗收 Gate 狀態追蹤

建立日期：2026-10-11　｜　規格版本：`V2.0-design-1`  
執行進度：**2026-10-12 重新盤點：G0–G7 八項 PARTIAL、G8 NOT_RUN，0/9 項通過。已驗證非付費 CI 不等於端到端或 production 驗收；main/production 未切換。**

### 2026-10-12 Goal G3 恢復工作者續報（已完成 source SHA CI）

- 新增 `app/pipeline/golden_v2_chirp_recovery_worker.py`：依 SQLite 已存的 `SUBMITTED` Speech operation ID 有界回收，提供正式 `us-speech.googleapis.com` Chirp 3／GCS client factory（未呼叫雲端）。`SUBMITTING` 結果未知不重送；超額成本暫停後仍允許對已提交操作回收已付費證據；`PLANNED` 不會被恢復程序自行提交。
- 新增 `tests/test_golden_v2_chirp_recovery_worker.py`：使用短音檔、真 SQLite 與 fake Speech/GCS 測試 crash/restart、未知結果、超額回收、證據竄改及輪詢上限。已在來源程式提交 `082a367ee9edb2f412a491307817a99f38151d3e` 驗證：Python **813 tests PASS**、Next.js 行為測試／build PASS、Docker image build/Compose PASS；[CI #38187565339](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38187565339)、[Release Compose #38187565337](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38187565337) 均 PASS。此筆為實測綠燈的**來源 SHA**，文件更新所產生的新提交仍應另查 CI。
- **G3 仍 PARTIAL**：持久 operation 恢復已整合；尚欠正式已核准付費提交的常駐調度、經可稽核帳單驗證的成本來源、Standard Batch bounded repair、真實 Speech API/UAT 與真人聲學 QA。**未發生任何 Chirp/Vertex 付費呼叫、GCS/Drive 寫入、production 變更**。

### 2026-10-12 Goal continuation — Chirp partial-submit recovery

- `GoldenV2Ledger.resume_chirp_submissions()` 可在 worker 中斷於多窗 Chirp 提交之間時，依 SQLite job revision 與 stage heartbeat CAS 旋轉 claim，只接續仍為 `PLANNED` 的窗段；先前 `SUBMITTED`/`DONE` operation identity 必須完整保留。
- 任一窗段仍為 `SUBMITTING`（provider POST 可能已受理但 operation ID 未落盤）時，續跑會 fail closed；不會重送該窗，也不會核發新 claim。舊 claim 在續跑後失效，revision 不符或 recovery grace 未到也會拒絕。
- 新增 3 個 fake Speech/SQLite restart regression cases；Chirp stage suite **10 PASS**，只使用合成 FLAC、fake GCS/Speech，沒有雲端呼叫。
- 已將改動接到同分支有界恢復 worker 後，本機完整 Python **815 PASS**、V2 專項 **124 PASS**、handoff pytest **27 PASS**、compileall 與 `git diff --check` PASS。來源提交 `001ed9b9ecb1ef3d23df0b3e944bbc35cb7b065e` 的 [CI #38198541708](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38198541708) **PASS**（含 815 Python tests、Next.js tests/build、Docker image build、Compose）；[Release Compose #38198541691](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38198541691) **PASS**。
- Gate 維持 **G0–G7 PARTIAL、G8 NOT_RUN、0/9 PASS**。真實 Chirp worker/雲端 operation、Standard repair、長音訊聲學覆蓋、真人 Gold、Drive/UI/部署仍未驗收。

### 2026-10-12 本地 Goal 執行續報（exact-SHA CI PASS）

- 隔離工作樹 `/private/tmp/course-transcript-golden-v2-goal`；新交付分支 `codex/golden-v2-goal-completion`，與既有 Draft PR #143 的 `feat/golden-v2-gemini38-primary` 分開審查。正式部署版本仍未確認。
- 新增 provider-free V2 coordinator、獨立 Chirp 提交／回收 stage、可選 Gemini 3.5 third stage、聲學 QA、Chirp timestamp-only SRT/TXT render、來源 lineage 與 immutable evidence 寫入。SQLite 加上安全回收本地 claim 的版本檢查；可能已付費的 provider claim 不會被自動重送。
- 2026-10-12 本輪離線驗證：完整 Python unittest **824 PASS**、修復/QA/worker 專項 **33 PASS**、`tests/test_chatgpt_handoff.py` pytest **27 PASS**、compileall 與 `git diff --check` PASS。初次 CI 揭露聲學測試夾具含第二個邊界 gap；已限定測試門檻隔離尾段案例，Python 3.12 重跑全數通過。
- 已驗證 `SMART_2PLUS` 無爭議可省略第三模型；有爭議會停在 provider 或真人聲學審查；中斷後本地工作可恢復。未實際呼叫 Vertex／Chirp、未寫入 Drive、未部署，實際付費 **US$0**。
- **Gate 結論維持 G0–G7 PARTIAL、G8 NOT_RUN，0/9 PASS**。仍缺 Vertex endpoint/IAM/價格真實驗證、真實長音訊完整聲學驗收、獨立可信 reviewer 身分與真人聽音、人工 Gold 比對、owner 寫入 API/UI、Drive safe-publish 整合與 staging/production 驗收。現有重疊文字裁切要求逐個 seam 人工確認，沒有宣稱自動去重或整堂完成。

### 2026-10-12 Standard bounded repair implementation

- `chirp_completeness_gate._vad_assessment()` now records conservative positive-speech evidence separately from robust non-speech. Acoustic QA sends only `SPEECH_CONFIRMED` gaps to a paid-repair proposal; confirmed silence passes and audible-but-uncertain gaps remain manual review.
- Acoustic probing is bounded to 16 gaps and 30 seconds per gap. Any longer or unexamined gap remains REVIEW and cannot become a paid repair proposal.
- Added source-SHA-bound, local-only repair clip extraction (≤5 minutes per clip, ≤12 clips / ≤10 minutes per proposal), a durable SQLite repair approval bound to the exact acoustic-report SHA, target ranges, clip/input/output identities and cent ceiling, plus immutable generation-guarded GCS staging. Resuming after a crash rotates the Chirp claim and recreates only missing objects from exact local clip identities.
- Standard submissions require a gateway with Dynamic Batching disabled. Repair words replace base words only when their midpoint falls inside the approved target ranges; all raw base and repair result JSON stays immutable. `chirp_independent` remains RUNNING through acoustic REVIEW, so comparison cannot start until acoustic QA passes.
- Fake Speech/GCS integration covers base recognition → positive-speech proposal → exact cost approval → Standard submission → restart-safe clip upload → target-only merge → acoustic PASS → compare unlock. In exact HEAD `de52cc9af27ac13d91210f0c65f4d5cde178a00b`, GitHub [CI #38204588170](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38204588170) and [Release Compose #38204588132](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38204588132) both PASS, including all 824 Python tests, frontend tests/build, Docker image build and Compose validation. All cloud-shaped calls were fake; no paid API call, real GCS/Drive write or production change occurred.
- **G3 remains PARTIAL**: no live Speech request or real long-course/VAD UAT; no independent invoice reconciliation or deployed submission/recovery scheduler; the separately gated SQLite-only owner approval API is implemented but disabled and undeployed; review UI/API, 真人聽音及完整雙引擎驗收仍未完成. G0–G7 remain PARTIAL, G8 NOT_RUN, 0/9 PASS.

### 2026-10-12 模型政策與 owner 核准入口（exact-SHA CI PASS）

- 精確來源 SHA `2c3243ad976b3f1c45ca16a0c4cedc5392e50436` 已推到隔離分支 `codex/golden-v2-goal-completion`，PR #144 維持 Draft。V2 ASR 後純文字校正使用 Vertex Gemini 3.7；通用 Vertex 文字生成保留獨立設定；測試確認不配置 Gemini 2.5。
- `app/golden_v2_routes.py` 新增分開 token/flag 的 job 與 repair approval；強制 revision、明確確認及每 job 預設 US$10 上限，僅更新既有 SQLite ledger，無 provider 呼叫。API 預設關閉且未部署。
- 本機 Python 3.12：**831 unittest、27 handoff pytest**；前端 **54 tests**、Next production build、compileall、`git diff --check` PASS。精確來源 [GitHub CI #38206197000](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38206197000) 與 [Release Compose #38206197011](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38206197011) 均 PASS，涵蓋 Python、前端、Docker image、Compose 與 release provenance。
- Google 官方頁標示 Gemini 3.7 Flash 將於 **2027-01-28** 退役，正式上線前須驗證替代型號及切換；沒有付費模型呼叫、GCS/Drive 寫入、IAM/firewall 變更或 production 部署。G0–G7 仍 PARTIAL、G8 NOT_RUN、0/9 PASS。

### 2026-10-12 Goal continuation — source-bound audio review bundle (exact-SHA CI PASS)

- Source commit `2d74acbbb89bfdc7f43cc8abffa4b7ad93d9f017` adds an offline candidate review bundle bound to normalized-audio SHA, comparison evidence, candidate snapshots, and ledger revision. Applying a review can claim only the zero-cost `adjudication` stage; correction patches must match exact Chirp word evidence. Listening/reviewer identity remain explicit operator self-attestation, not independently authenticated evidence.
- Python 3.12 local regression: **835 unittest**, **27 handoff pytest**, compileall, CLI help and `git diff --check` PASS. Exact-SHA [GitHub CI #38207135117](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38207135117) and [Release Compose #38207135128](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38207135128) both PASS, including frontend tests/build, Docker images, Compose and release provenance.
- `skills/golden-course-transcript-v2/SKILL.md` now distinguishes implemented-but-undeployed status/job-registration/approval APIs and local review commands from missing Drive source preflight, persistent VPS worker, and conversation handoff. It remains a draft and is not installed or connected.
- No provider call, Drive/GCS mutation, IAM/firewall change or production deployment occurred. P5/P7 remain PARTIAL; G0–G7 remain PARTIAL, G8 NOT_RUN, 0/9 PASS.

### 2026-10-12 Goal continuation — approval-gated job registration (exact-SHA CI PASS)

- Source SHA `131eb73cbc980459b11edba0808a620b508ac258` adds `POST /api/v1/golden-v2/jobs` behind the owner write token and disabled-by-default feature flag. It validates a normalized-source SHA shape, mode, complete nonnegative per-stage cent estimates and the configured job ceiling, then creates only an `AWAITING_APPROVAL` SQLite row. A trusted caller must establish that the digest matches intended audio; this endpoint does not fetch or verify media bytes. A separate revision-bound approval is still required before any worker/provider stage.
- Local Python 3.12: **836 unittest**, **27 handoff pytest**, compileall, production API import, review CLI help and `git diff --check` PASS. Exact-SHA [GitHub CI #38207679768](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38207679768) and [Release Compose #38207679804](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38207679804) both PASS, including frontend tests/build, Docker images, Compose and release provenance.
- Negative tests cover disabled route, wrong token, unconfirmed/malformed input, incomplete or over-ceiling estimate, missing schema without migration, and persisted unapproved status. No provider call, Drive/GCS write, IAM/firewall change or production deployment occurred. G7 remains PARTIAL; G0–G7 remain PARTIAL, G8 NOT_RUN, 0/9 PASS.

規格：[架構](GOLDEN_V2_ARCHITECTURE_PLAN.md)｜[開發順序與驗收矩陣](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md)｜[2026-10-12 實況盤點／六階段完工計畫](GOLDEN_V2_PROGRESS_AUDIT_2026-10-12.md)

> 此文件是會隨 PR/驗收更新的狀態登記簿。**只有提交機器／人工可核對的 evidence link、commit、測試報告與操作者資訊後，才能把某一 Gate 設為 PASS。** 「文件已提交」不代表其他工程 Gate 已 PASS。禁止憑對話主觀印象標已完成。

| 階段 | Gate | 狀態 | 目前可核對成果／阻礙 |
|---|---|---|---|
| P0 基線／隔離 | G0 | PARTIAL | 2026-10-11 已確認 GitHub main/feature branch、另外隔離 V2 分支；WebCodex 的舊 source Project `.git/worktrees` 無效，正式 runtime SHA／active transcription jobs／備份尚未驗證。未 reset/clean/stash。 |
| P1 模型角色分流 | G1 | PARTIAL | V2 full-audio primary 使用 3.8；Chirp 後純文字校正固定 Vertex Gemini 3.7。adapter/worker/API/proxy/UI defaults 與模型政策測試已分流；仍缺兩種模型的真實 Vertex smoke、權限／配額、正式部署與 rollback。Google 官方頁標示 Gemini 3.7 Flash 將於 2027-01-28 退役，上線前需核定替代型號與切換時程。 |
| P2 Gemini 3.8 音訊主稿 | G2 | PARTIAL | 新增 `golden_v2_audio_staging.py` 的本機 FFmpeg source-SHA FLAC 分段、`golden_v2_gcs_staging.py` 的經核准 GCS 不覆寫上傳，以及 `golden_v2_primary_stage.py` 串接 Gemini 3.8→SQLite，含超額預估阻擋、結果不明停機及 mock 整合測試；**已加入來源→normalized→窗段 SHA lineage 的本機證據、逐窗 raw SHA/身份驗證及人工 overlap seam 裁切後原始證據 CAS 綁定**；仍未驗證真實 Drive 來源 SHA、正式 Worker/Vertex、長音訊整堂聲學 coverage。 |
| P3 Chirp 獨立聲學比對 | G3 | PARTIAL | `golden_v2_compare.py`/`golden_v2_flow.py` 具備獨立差異比對與先 Gemini 後 Chirp 的順序；既有 Chirp PASS 可經 SHA/來源驗證重用；Speech V2 adapter、async fake-provider submit/recovery、已知 operation ID 的 revision-CAS 部分續送及未知 SUBMITTING 凍結均已離線驗證。**另新增 VAD positive-speech gate、exact-plan/cost-approved Standard bounded repair、不可變 clip/GCS identities、restart-safe re-upload、target-only merge；acoustic REVIEW 會阻擋 compare，只有 PASS 才完成 Chirp stage。**仍欠常駐付費 Worker、可稽核帳單、真實 Speech API/長音訊聲學 UAT 與真人聽音。 |
| P4 Gemini 3.5 Transcribe | G4 | PARTIAL | `gemini_third_transcribe.py` 實作 Vertex generateContent/VERBATIM、≤15m hard cap、選用/禁用與 mock tests；**尚未實際 Vertex smoke/worker 用量帳本**。 |
| P5 WebCodex 最終裁決與 Golden QA | G5 | PARTIAL | `golden_v2_final_gate.py` + `golden_v2_stage_runner.py` 可持久化離線比對/QA；`golden_v2_review_candidates.py` 新增來源 SHA／revision 綁定的本機人工候選 review/import，可選 KEEP_CHIRP 或以 Chirp word evidence 校正；**仍未串接 ChatGPT/WebCodex 對話傳輸、獨立 reviewer 身分或真人整堂聲學驗收**，無爭議也不等於正確。 |
| P6 實測評分與最佳化 | G6 | PARTIAL | `app/evaluation/golden_v2_benchmark.py` + `scripts/golden_v2_benchmark.py` 新增離線配對引擎 CER 評測：強制同一來源 SHA/window IDs、人工 Gold provenance 與各引擎 raw-evidence SHA，按類別統計；成本/耗時缺值保留 unknown。**無真實人工金標／三引擎樣本／帳單比價，欄位 SHA 尚未獨立讀回驗證**。 |
| P7 ChatGPT Skill / 持久任務 | G7 | PARTIAL | 新增 `app/jobs/golden_v2_ledger.py` SQLite task/claim/費用/來源視窗授權；`scripts/golden_v2_status.py` 唯讀查詢、`app/golden_v2_routes.py` 分離 read/write token 的 owner API（新增 source-SHA job register、job/repair approval，write 預設關閉、每 job ceiling 預設 US$10、只寫 SQLite、絕不提交 provider；呼叫端負責來源 SHA preflight）、`scripts/golden_v2_review_overlaps.py` overlap seam review 與 `scripts/golden_v2_review_candidates.py` 比對候選 review/import（操作者自述，無獨立聽音認證）；`golden_v2_primary_stage.py` 將核准 claim+FFmpeg+mock GCS/Vertex 整合、`golden_v2_stage_runner.py` 離線 QA 橋接；`skills/golden-course-transcript-v2/SKILL.md` 仍為草案。**未安裝 Skill、未接正式常駐 Worker／V2 管理 UI／付費 Vertex／Drive；owner API 未部署**。 |
| P8 UI / 部署 / Drive 驗收 | G8 | NOT_RUN | 尚未 staging/canary/production／Drive 實測 |

## V2 文件提交紀錄

- `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md`：V2 模型角色分流（3.8 音訊、3.7 純文字校正）、完整流程、長檔/帳務/權限/安全。
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
next_action: "Continue provider-free integration and exact-SHA verification; live gates remain unrun"
updated_at: "2026-10-12"
```

注意：上方 YAML 僅是結構示例，未來每一 Gate 應有自己的最新紀錄、連結及是否真的發生 API 使用的證據。即使原本 V1 有相關功能，未經 V2 回歸測試也不能將 V2 Gate 標 PASS。
