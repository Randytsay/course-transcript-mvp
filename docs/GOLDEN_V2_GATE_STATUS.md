# Golden Transcript V2 — 驗收 Gate 狀態追蹤

建立日期：2026-10-11　｜　規格版本：`V2.0-design-1`  
執行進度：**P0–P7 多項為獨立分支上的部分工程成果，不代表任何 Gate PASS；P8 尚未開始。main/production 未切換。**

規格：[架構](GOLDEN_V2_ARCHITECTURE_PLAN.md)｜[開發順序與驗收矩陣](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md)

> 此文件是會隨 PR/驗收更新的狀態登記簿。**只有提交機器／人工可核對的 evidence link、commit、測試報告與操作者資訊後，才能把某一 Gate 設為 PASS。** 「文件已提交」不代表其他工程 Gate 已 PASS。禁止憑對話主觀印象標已完成。

| 階段 | Gate | 狀態 | 目前可核對成果／阻礙 |
|---|---|---|---|
| P0 基線／隔離 | G0 | PARTIAL | 2026-10-11 已確認 GitHub main/feature branch、另外隔離 V2 分支；WebCodex 的舊 source Project `.git/worktrees` 無效，正式 runtime SHA／active transcription jobs／備份尚未驗證。未 reset/clean/stash。 |
| P1 3.7→3.8 | G1 | PARTIAL | 已於 V2 隔離分支修改 `correct_text.py`、`correct_text_cascade.py` 的 3.8 target 及 prompt version，新增 `test_golden_v2_model_policy.py`；尚未執行完整測試、SDK 真實 API smoke、正式部署；其他 3.7 路徑仍待盤點。 |
| P2 Gemini 3.8 音訊主稿 | G2 | PARTIAL | 新增 `golden_v2_audio_staging.py` 的本機 FFmpeg source-SHA FLAC 分段、`golden_v2_gcs_staging.py` 的經核准 GCS 不覆寫上傳，以及 `golden_v2_primary_stage.py` 串接 Gemini 3.8→SQLite，含超額預估阻擋、結果不明停機及 mock 整合測試；**尚未接正式 V1/Chirp Worker、Vertex 實測、原始 Drive 與 normalized SHA lineage、重疊去重／聲學 coverage**。 |
| P3 Chirp 獨立聲學比對 | G3 | PARTIAL | `golden_v2_compare.py`/`golden_v2_flow.py` 具備獨立差異比對與先 Gemini 後 Chirp 的順序；新增 `golden_v2_chirp_reuse.py` 可核對相同 normalized FLAC SHA、既有 Chirp completeness PASS 與提交證據後直接使用已付費成果（本次新 ASR 成本=0）；**尚未接新 Chirp 非同步送件／回收／bounded repair Worker 和 V2 完整聲學 QA**。 |
| P4 Gemini 3.5 Transcribe | G4 | PARTIAL | `gemini_third_transcribe.py` 實作 Vertex generateContent/VERBATIM、≤15m hard cap、選用/禁用與 mock tests；**尚未實際 Vertex smoke/worker 用量帳本**。 |
| P5 WebCodex 最終裁決與 Golden QA | G5 | PARTIAL | `golden_v2_final_gate.py` + `golden_v2_stage_runner.py` 已可寫入離線比對/QA 報告並持久化 `NEEDS_REVIEW`；**未串接現有 handoff/字幕渲染及真人聲學驗收**。 |
| P6 實測評分與最佳化 | G6 | PARTIAL | `app/evaluation/golden_v2_benchmark.py` 提供人工 Gold 的 CER 計算與測試；**無真實人工金標／三引擎樣本／價格比較**。 |
| P7 ChatGPT Skill / 持久任務 | G7 | PARTIAL | 新增 `app/jobs/golden_v2_ledger.py` SQLite task/claim/費用/來源視窗授權；`scripts/golden_v2_status.py` 唯讀查詢；`golden_v2_primary_stage.py` 將核准 claim+FFmpeg+mock GCS/Vertex 整合、`golden_v2_stage_runner.py` 離線 QA 橋接；`skills/golden-course-transcript-v2/SKILL.md` 仍為草案。**未安裝 Skill、未接實際 V2 worker/API/付費 Vertex/Drive**。 |
| P8 UI / 部署 / Drive 驗收 | G8 | NOT_RUN | 尚未 staging/canary/production／Drive 實測 |

## V2 文件提交紀錄

- `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md`：V2 分工、Gemini 3.8 遷移、完整流程、長檔/帳務/權限/安全。
- `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`：P0–P8 施工序、G0–G8 逐項核准與資料證據。
- `AGENTS.md`、`README.md`、`docs/KNOWLEDGE_INDEX.md`、`CURRENT_STATE.md`、`DECISIONS.md`：已更新 V2 索引/決策／舊系統現況界線。
- `docs/GOLDEN_V2_INTEGRATION_HANDOFF.md`：現有 V2 SQLite/bridge 的實作交接與尚未整合項目。
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
