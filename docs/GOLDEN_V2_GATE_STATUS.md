# Golden Transcript V2 — 驗收 Gate 狀態追蹤

建立日期：2026-10-11　｜　規格版本：`V2.0-design-1`  
執行進度：**V2 Draft PR #142 開發中；P0–P5、P7 各有部分程式或規格成果，但 G0–G8 均未達正式 PASS**。

Draft PR：https://github.com/Randytsay/course-transcript-mvp/pull/142

規格：[架構](GOLDEN_V2_ARCHITECTURE_PLAN.md)｜[開發順序與驗收矩陣](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md)

> 此文件是會隨 PR/驗收更新的狀態登記簿。**只有提交機器／人工可核對的 evidence link、commit、測試報告與操作者資訊後，才能把某一 Gate 設為 PASS。** 「文件已提交」不代表其他工程 Gate 已 PASS。禁止憑對話主觀印象標已完成。

| 階段 | Gate | 狀態 | 目前可核對成果／阻礙 |
|---|---|---|---|
| P0 基線／隔離 | G0 | PARTIAL | VPS `/opt/course-transcript-source` 是 dirty main，timer active；讀取 DB 聚合狀態為 completed 73、awaiting_review 46、cancelled 8、failed 1，未碰任何 job。已隔離 branch 與 `/tmp/golden-v2-20261011`；正式 runtime SHA、完整備份/還原仍需驗證 |
| P1 3.7→3.8 | G1 | PARTIAL | 程式已改 3.8 及 thinking_level，新增模型-aware pricing／輸出驗證；mock 通過；仍有其他舊路徑 3.7 且真 Vertex smoke 未獲核准／執行 |
| P2 Gemini 3.8 音訊主稿 | G2 | PARTIAL | `gemini_audio_primary_v2.py` 實作單音檔呼叫原型、分段規劃／重疊合併／截斷檢測及付費守門；尚未接 durable worker 或實際錄音完整性 |
| P3 Chirp 獨立聲學比對 | G3 | PARTIAL | `engine_comparison_v2.py` 產出差異／Chirp 聲學時間窗；尚未 V2 Chirp job orchestration、bounded repair 實測 |
| P4 Gemini 3.5 Transcribe | G4 | PARTIAL | `gemini_transcribe_v2.py` VERBATIM 及 capability/approval 檢查；尚未接 UI/worker 或 Vertex 真實模型驗證 |
| P5 WebCodex 最終裁決與 Golden QA | G5 | PARTIAL | `engine_comparison_v2.py`、`golden_v2_finalizer.py` 和 `golden_v2_dry_run.py` 已能用先前已準備的三稿/Chirp 模擬證據產出 SRT/TXT 候選；6 項整合測試通過；沒有真的跑完整 WebCodex 裁決、VAD/Golden QA |
| P6 實測評分與最佳化 | G6 | NOT_RUN | 尚未取得人工 Gold 基準與付費模型結果 |
| P7 ChatGPT Skill / 持久任務 | G7 | PARTIAL | 未啟用 Skill 交辦規格已建立；`golden_v2_store.py` 新增獨立 SQLite WAL 階段證據存取，6 項重啟/immutable/不跳關測試通過；仍未接既有 Worker、API、費用/租約資料表 |
| P8 UI / 部署 / Drive 驗收 | G8 | NOT_RUN | 尚未 staging/canary/production／Drive 實測 |

## 2026-10-11 初次工程證據
- PR: [#142 V2 WIP](https://github.com/Randytsay/course-transcript-mvp/pull/142)（Draft；不允許合併／發佈）
- 隔離 Runner：`/tmp/golden-v2-20261011`（新複本；不同於 dirty production source）
- 非付費測試：13/13 V2 初期、24/24 引擎、74/74 聚焦、30/30 stage+引擎、10/10 stage+finalizer、6/6 三引擎模擬串接、18/18 SQLite+Transcribe+整合、19/19 3.8 routing 回歸均通過。最近一次已完成完整測試 **724/724 ALL PASS**（commit `22a958f`，Python unittest；後續僅文件更新）。
- **全套測試**：最初因 PyYAML/pytest 依賴缺失的載入錯誤已修復，依次測得 701/701、717/717、**724/724 PASS**（最後測試 commit `22a958f`，Runner 隔離路徑 `/tmp/golden-v2-20261011`）；仍未真實 Vertex/Chirp 音訊付費驗證，不代表 Golden 全部通過。
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
