# Golden Transcript V2 — ChatGPT / WebCodex 操作 Skill（開發中）

> **尚未啟用／尚未部署，不能直接宣稱已具備自動執行能力。** 本文件為 PR #142 的 V2 Skill 對話與工具契約草案。V1 `skills/golden-course-transcript/SKILL.md` 仍為正式流程，直到 G7/G8 PASS 並獲正式切換核准。

## User intents

- 「黃金字幕：這個 Google Drive 資料夾，三引擎品質優先」
- 「這堂法會使用 Gemini 3.8 當完整主稿、Chirp 3 當時間碼，第三引擎有需要才跑」
- 「查看上一批 Golden QA 是否完成，哪些台語或經文音訊需要重聽」
- 「比較三個模型對同樣音檔的準確度與每小時成本」

預設 `QUALITY_3WAY`，使用者可以改成 `SMART_2PLUS` 或 `GEMINI_CHIRP`。使用者指定模式屬 job 設定，提交後不可偷偷改變或覆寫過去的原始辨識回應。

## Tool + backend contract

1. ChatGPT/WebCodex 只做意圖解析、呼叫已授權的 Project/Runner API、讀取 ledger、局部文字裁決和回報；**不依賴長時間聊天連線做持久化工作**。
2. 以 `Drive file/folder id` 解析來源，再執行 provider-free `inventory/lineage`。找到以前的 job 先比對 source sha、raw manifests 及已完成證據，不可直接重跑。
3. 依 `app/jobs/golden_v2_stage_contract.py` 驗證 stage 次序，使用 `gemini_primary` 先產生全文**候選**，本身需通過 primary completeness；Chirp 隨後獨立辨識音訊並提供唯一定時來源；可選 Transcribe 是獨立聲學文字佐證，不能當作 Chirp 時間碼替代。
4. `app/providers/engine_comparison_v2.py` 目前提供候選差異報告；全部 ADDITION/OMISSION/WORDING_CONFLICT 都要保留可重聽原始音檔區間。經文和原人工逐字稿只能作 spelling/context 佐證，**不得擅自補上沒有朗讀的內容**。
5. ChatGPT/WebCodex 的校訂回灌必須帶穩定 segment ID、source digest、修正前文字、修正後文字、裁決 evidence ID、審核狀態，不得帶模型自行創造的時間碼。
6. `app/subtitles/golden_v2_finalizer.py` 只可產出 STRUCTURAL_PASS_ONLY 的 SRT/TXT 候選。待既有 Golden Acoustic/VAD/QA/Lineage/Publish gates 全 PASS 才能升為 Golden。
7. 實際付費提交之前必須獲得**特定來源與預估金額**的事前核准，並使用獨立 US$10 測試總上限。不能將 V1 的 $10 自動通過閥值當成這次的付費授權；任何未知費率、超限、跨來源簽核都 fail closed。
8. Google Drive safe-publish 只沿用現有的 pending→verify→backup→promote→final-verify，可恢復且保留原媒體、原人工逐字稿、舊輸出版；網路重試不能重送已完成付費辨識。
9. 生產環境必須依據 `runtime_revision_guard.py` 驗證 immutable exact SHA 和先前備份，不能以 GitHub main、dirty worktree、Agent 口頭報告代替真實上線證據。

## Status reporting

對話中的「繼續」「進度如何」「接手完成」都先檢查現有 durable `job_id` 及最新 DB/events：
- 若仍在後端排程：回報當前 stage、stage attempt、provider operation、已有證據，**不可假裝 ChatGPT 自己正在背景執行**。
- 若 stage 已成功：只執行下個未完成 stage，不能重新啟動已完成 provider。
- 若失敗：辨識是 pending、terminal、輸出傳播延遲、local QA、API 配額、費用 gate、Drive 交付，採最小範圍修復與 bounded retry。
- 若費用或授權未核准：停於 awaiting_confirmation，列出真實待授權來源、預估費用、已花費及保留額度。
- 若只有非付費模擬測試：標記 `MOCK_VERIFIED`，不可寫 `GOLDEN_PASS`。
- 若要求上線：先提交 PR、完整測試/安全審核、Golden 基準音檔比較、rollback，最後由使用者批准正式 cutover。

## Model capability contracts

| Provider | Model | Required client | V2 use |
|---|---|---|---|
| Gemini primary | `gemini-3.8-flash` | Vertex AI GenAI client `vertexai=True` | Full-audio context → transcript candidate |
| Chirp timing | Speech-to-Text V2 `chirp_3` | existing Speech V2 service account | Word timestamps, acoustic completeness |
| Optional transcription | `gemini-3.5-transcribe` | Google Agent Platform GenAI client `enterprise=True` | VERBATIM comparative transcript; 15m conservative chunk |

同一 Google Cloud Project/Billing 並不意味著兩個 Gemini 模型可以一律沿用相同的 API 參數；應使用各自獨立 adapter、授權與費用紀錄。實際模型能力須以正式平台官方規格及該 Project 的 probe 結果確認。

## Gate for this Skill becoming active

**G7 PASS prerequisites:** 實際V2後端任務 API、user identity/Auth/expense gate、可持續 worker、對話重入、錯誤重試、完整 Golden QA 與發佈前端點、可驗證失敗注入測試全部完成。此草案存在 ≠ 已啟用 Skill。

參考：
- `docs/GOLDEN_V2_ARCHITECTURE_PLAN.md`
- `docs/GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md`
- `docs/GOLDEN_V2_GATE_STATUS.md`
- `CURRENT_STATE.md`, `DECISIONS.md`, `GLOBAL_GUARDRAILS.md`
