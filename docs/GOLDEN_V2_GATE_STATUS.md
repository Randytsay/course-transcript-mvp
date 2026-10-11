# Golden Transcript V2 — 驗收 Gate 狀態追蹤

建立日期：2026-10-11　｜　規格版本：`V2.0-design-1`  
執行進度：**V2 Draft PR #142 已建立；P0–P4 正在開發，全部 Gate 均未取得正式 PASS**。

Draft PR：https://github.com/Randytsay/course-transcript-mvp/pull/142

規格：[架構](GOLDEN_V2_ARCHITECTURE_PLAN.md)｜[開發順序與驗收矩陣](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md)

> 此文件是會隨 PR/驗收更新的狀態登記簿。**只有提交機器／人工可核對的 evidence link、commit、測試報告與操作者資訊後，才能把某一 Gate 設為 PASS。** 「文件已提交」不代表其他工程 Gate 已 PASS。禁止憑對話主觀印象標已完成。

| 階段 | Gate | 狀態 | 目前可核對成果／阻礙 |
|---|---|---|---|
| P0 基線／隔離 | G0 | PARTIAL | 已查 VPS `/opt/course-transcript-source` 為 dirty main、timer active；已隔離 GitHub branch 與 `/tmp/golden-v2-20261011` 測試複本；正式 SHA、DB 備份／還原證據仍缺 |
| P1 3.7→3.8 | G1 | PARTIAL | 程式已改 3.8 及 thinking_level，新增模型-aware pricing／輸出驗證；mock 通過；仍有其他舊路徑 3.7 且真 Vertex smoke 未獲核准／執行 |
| P2 Gemini 3.8 音訊主稿 | G2 | PARTIAL | `gemini_audio_primary_v2.py` 實作單音檔呼叫原型、分段規劃／重疊合併／截斷檢測及付費守門；尚未接 durable worker 或實際錄音完整性 |
| P3 Chirp 獨立聲學比對 | G3 | PARTIAL | `engine_comparison_v2.py` 產出差異／Chirp 聲學時間窗；尚未 V2 Chirp job orchestration、bounded repair 實測 |
| P4 Gemini 3.5 Transcribe | G4 | PARTIAL | `gemini_transcribe_v2.py` VERBATIM 及 capability/approval 檢查；尚未接 UI/worker 或 Vertex 真實模型驗證 |
| P5 WebCodex 最終裁決與 Golden QA | G5 | NOT_RUN | 原有 CHATGPT_HANDOFF / Golden QA 可借鏡；新版未完成 |
| P6 實測評分與最佳化 | G6 | NOT_RUN | 尚未取得人工 Gold 基準與付費模型結果 |
| P7 ChatGPT Skill / 持久任務 | G7 | NOT_RUN | V1 Skill 已存在，V2 新契約未建／驗證 |
| P8 UI / 部署 / Drive 驗收 | G8 | NOT_RUN | 尚未 staging/canary/production／Drive 實測 |

## 2026-10-11 初次工程證據
- PR: [#142 V2 WIP](https://github.com/Randytsay/course-transcript-mvp/pull/142)（Draft；不允許合併／發佈）
- 隔離 Runner：`/tmp/golden-v2-20261011`（新複本；不同於 dirty production source）
- 非付費測試：13/13 V2 初期成功；24/24 新增 V2 測試成功；74/74 既有+新增聚焦回歸成功
- **全套測試**：已提交執行，結果待記錄；不得據聚焦測試宣告 G1–G4 PASS。
- Vertex/Speech 真 API：NOT_RUN；Drive 寫入：NO；Production 改動：NO；付費金額：US$0。
- 待完成：完整引擎序列、持久化與授權帳本、金標準音檔評測、Skill／UI、部署回滾及 Drive 遠端 SHA。
- 每次續作先查最新 branch HEAD/PR CI/status 和 `CURRENT_STATE.md`，不得以本紀錄替代 live code。

## V2 文件提交紀錄

- `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md`：V2 分工、Gemini 3.8 遷移、完整流程、長檔/帳務/權限/安全。
- `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`：P0–P8 施工序、G0–G8 逐項核准與資料證據。
- `AGENTS.md`、`README.md`、`docs/KNOWLEDGE_INDEX.md`、`CURRENT_STATE.md`、`DECISIONS.md`：已更新 V2 索引/決策／舊系統現況界線。
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
