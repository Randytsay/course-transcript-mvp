# Golden Transcript V2 — 三引擎、Gemini 3.8 音訊與 3.7 文字校正架構

> 文件狀態：**已核定的 V2 目標設計／尚非已部署事實**  
> 決策日期：2026-10-11；規格版號：V2.0-design-1  
> 專案：`Randytsay/course-transcript-mvp`  
> **任何 Agent 開工前先讀** `AGENTS.md`、`CURRENT_STATE.md`、`DECISIONS.md`、`KNOWN_ISSUES.md`、`GLOBAL_GUARDRAILS.md`、`AI_AGENT_HANDOFF.md`、`docs/KNOWLEDGE_INDEX.md`。  
> 本文件定義下一版「應該如何實作」，不得據此宣稱已合併、已上線、已完成成本／聲學實測。

## 1. 產品目標／非目標

目標：使用者只需以 ChatGPT 對話或既有管理介面，指定 Google Drive 檔案／資料夾，即可選擇 Gemini 3.8 Flash、Chirp 3、Gemini 3.5 Transcribe 組合，自動產生、比對並由 ChatGPT/WebCodex 校對 Golden 等級的繁體中文 `.srt` 與 `.txt`，生成可審核證據，安全回寫原資料夾。佛經、國台語混用、專有名詞與講述者原意是主要品質情境。

非目標：
- 不把 Gemini 的流暢文字當成音訊真實，也不以經文原文替代未朗讀內容。
- 不用 LLM 推算、平均分配或捏造字幕時間碼。
- 不因導入 V2 重做已完成且證據可信的 Chirp chunk，不破壞 V1 歷史紀錄。
- 不新增公開、未授權的媒體 API、第三方共享連結，或將憑證寫入 Git／日誌。
- 不把 `completed` 或 Drive 已發布等同於 `GOLDEN_PASS`。

## 2. 模型定版決策（明確取代舊 3.7）

| 引擎 | 模型 ID／服務 | V2 用途 | 主要證據 |
|---|---|---|---|
| 主稿 | `gemini-3.8-flash`，Vertex AI | 首先聆聽原音檔，形成**完整逐字稿主稿**；國台語、經文上下文辨識 | 原始回應、分段主稿、coverage、prompt/model/usage |
| 聲學 | Speech-to-Text V2 `chirp_3` | 在 Gemini 主稿完成**之後**，獨立辨識同一原音檔；字詞時間邊界及語音 coverage | GCS raw JSON、word timestamps、操作／chunk manifest |
| 可選第三引擎 | `gemini-3.5-transcribe`，Vertex AI | 獨立轉錄；全文品質基準或差異片段複核；時間碼可作參考不可推翻 Chirp 權威 | 原始轉錄、參數、分段與 usage |
| 文字校正 | `gemini-3.7-flash`，Vertex AI | 只校正 ASR 已產生的繁中字幕文字；不得接收音訊或改動 Chirp 字詞時碼 | 原始回應、文字修訂、prompt/model/usage |
| 裁決 | ChatGPT + WebCodex | 差異比對、音檔回查／局部重聽、語意與標點修訂、SRT/TXT 一致性及 Golden QA | decision log、標準化文字修訂、驗收報告 |

**模型角色政策：`gemini-3.8-flash` 僅用於 V2 full-audio primary recognition；Vertex AI `gemini-3.7-flash` 僅用於 text-only correction。** 兩者使用不同 adapter、prompt、schema 和內容型態。既有 raw evidence、manifest、統計及 production 說明必須保留實際 model ID，不能批次改寫。完成測試不代表正式 runtime 已切換。

**模型生命週期：** Google 官方模型頁目前標示 `gemini-3.7-flash` 退役日為 **2027-01-28**，替代型號為 `gemini-3.8-flash`。因此 3.7 是依本專案指定的暫行文字校正型號；正式上線前須完成替代型號相容性／品質測試及明確切換，不能讓它越過退役日仍成為依賴。參考：[Gemini 3.7 Flash 模型頁](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-7-flash)（查核 2026-10-12）。

Google 官方 2026-10-08 Gemini API release notes 說明 3.7 → 3.8 的已棄用／自動轉送；Google Cloud Vertex 模型生命週期另列 3.7 預計 **2027-01-28** 退役。兩個產品端點的轉送／退役行為應分別驗證，不應假設 Vertex 也必然自動路由。
- https://ai.google.dev/gemini-api/docs/changelog
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-7-flash
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-flash
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-transcribe

3.8 SDK 相容性驗證只適用於 full-audio adapter；文字校正固定使用 Vertex `gemini-3.7-flash` 的 text-only adapter/config。兩條路徑使用**不同 prompt、schema 與 provider adapter**，不得只替換模型字串。

注意 API 產品差異：Google AI Developer API 文檔列一般 1 小時、啟用字詞時間碼／diarization 為 30 分鐘；但本專案使用 **Vertex AI / Gemini Enterprise Agent Platform**，其目前官方模型頁面列單次最多 15 分鐘。因此 V2 的 Vertex adapter 一律以 **每次 ≤15 分鐘** 作為保守硬限制，並於 capability probe 核對實際端點／SDK 後才可變更。自訂詞彙與時間碼組合需依實際端點限制分離設定；標註／Smart 模式不應默默刪除口語而成為唯一逐字稿。將限制做成 capability probe，不把文件數值寫死在永遠不變的邏輯裡。

## 3. 核心資料流（順序為使用者正式需求）

```text
ChatGPT Skill 或現有網站：Drive URL + 選定辨識模式
    ↓
唯讀 Drive inventory → dedup/lineage → job/evidence reconciliation
    ↓
preflight：來源 checksum、duration、格式、成本上限、既有證據
    ↓
normalize + VAD 音訊區段；使用不可變 source SHA
    ↓
[1] Gemini 3.8 Flash（主稿先行）
    ├─ 優先用整段音訊上下文辨識完整逐字稿（單檔 API 支援範圍內）
    ├─ 過長／輸出截斷則自動分段＋上下文重疊＋去重合併
    └─ coverage / 完整性未達標不得標成 FULL_TRANSCRIPT_READY
    ↓
[2] Chirp 3 獨立聲學辨識同一原音檔
    ├─ 首輪 canonical bulk = Dynamic Batch；按已驗證計畫分 chunk
    ├─ 以 Chirp provider word boundary 建立時間軸與 SRT 骨架
    └─ 本機 Coverage/VAD → 僅對已證實聲音缺口做 Standard 局部 repair
    ↓
[3] 可選 Gemini 3.5 Transcribe
    ├─ 品質優先：獨立全音檔/分段轉錄
    └─ 智慧最佳化：只補高風險差異片段（若預先模式允許）
    ↓
paragraph/phrase/word 對齊（Gemini 主稿 ↔ Chirp timeline ↔ Transcribe）
    ↓
差異分類：漏句／增句／經文異文／台語／人名術語／時碼／無聲處虛構
    ↓
ChatGPT/WebCodex evidence-grounded 裁決
    ├─ 音訊優先，reference/經文/簡報僅輔助字詞拼寫
    ├─ 無聲學支持的 Gemini 補文不能寫入 final
    ├─ 不能確定 → REVIEW + 可播放音訊區間，不猜測
    └─ 僅更正文字，不覆寫 provider timestamp
    ↓
以 Chirp 字詞時間邊界決定 cue；重建標點、跨 cue 斷句
    ↓
Golden QA（結構＋聲學＋語意＋TXT/SRT 等價＋lineage＋成本）
    ↓
本地 `.srt` / `校正版逐字稿.txt` / QA / usage / manifest
    ↓
已授權 safe-publish → Drive pending/verify/backup/promote/final-verify
    ↓
比對 SHA-256、記錄發佈版本；canonical Golden 才允許進知識庫
```

**注意：「Gemini 完整主稿優先」不是「可以不驗證就寫稿」**。先通過 Gemini 自身的分段連續性／截斷偵測；Chirp 要獨立聽音，不能先餵 Gemini 正文引導其初次答案。可把經文及同堂材料於後段用作 spelling/context evidence，但不能自動添加音訊不存在的句子。

## 4. 長音檔／對齊／保真

1. Gemini 音訊每個 API 請求可接受單檔並非保證一次**輸出**無截斷：建立段落／字數密度、時間覆蓋與 token 結束原因檢查。分段採 overlap、hash 去重、段序證據及全文 reassembly 校驗；無法確認的區段停在 REVIEW。
2. Chirp 保留既有 15 分鐘級分 chunk（實際以 planner 上限與 provider 能力為準）、10 秒級 overlap、midpoint ownership 與成功 chunk 重用；不得使用文字相似度直接丟棄聲音。
3. 先以文本語義錨點建立「主稿段落 ↔ Chirp 起訖時間」關係，再根據 Chirp 原始 word span 決定 cue boundary；不存在可靠對應時輸出 alignment conflict 而非插值時間。
4. 時間戳、原始模型回應、經文引用片段、校訂前後 diff 分層保存。輸出的閱讀版 TXT 與 SRT 的**文字層**必須在約定的正規化（移除 cue/time/index、合法換行空白）後一致；經文、台語、數字、標點規範保留語義。
5. 模型輸出的 word timestamp 只可用作輔助交叉檢查；字幕時間權威仍是 Chirp，對無 Chirp 對齊證據的段落不能憑 Transcribe/Gemini 直接填補時碼。

## 5. 模式與自動選擇

| 模式 | 預設引擎 | 用途 | 限制 |
|---|---|---|---|
| `QUALITY_3WAY` 品質優先 | 3.8 先→Chirp→3.5 全文 | 初期比較與重要法會 | 必須經 preflight 成本核准 |
| `SMART_2PLUS` 智慧最佳化 | 3.8 先→Chirp；3.5 只針對高風險 | 有 Golden 基準集後的日常首選候選 | 沒驗證前不得自動宣稱優於三引擎 |
| `COST_CONTROLLED` 成本優先 | 可由使用者指定引擎子集，校對/完整性 Gate 不得省略 | 降低費用 | 若缺 Chirp 時間碼必須沿用既有可信 evidence，否則不得直接發布新的 Golden SRT |

可在 Skill 自然語言交辦或 UI 切換；每次任務凍結 `mode`、`engine_ids`、`model_versions`、`prompts`、`approval` 與 source digest，讓重試具可重現性。不允許已提交的 job 因預設模式改變而偷偷重跑。

## 6. 端點、身分、成本與授權

- 優先沿用同一 Google Cloud Project、Cloud Billing account 與既有授權良好的服務帳號；Gemini 走 Vertex AI / GenerateContent，Chirp 走 Speech-to-Text V2。**共用帳單 ≠ 共用單價／免費額度**。
- 先做不付費的 IAM/API/region/model availability probe；實際可用需以 API probe 為證，不以 Console 看到模型即宣稱成功。
- 三引擎分開核算 `audio_duration`、token input/output、單價快照、可能重試、cost estimate/reserve/actual，usage dedupe key = `job_id + engine + source_sha + window + attempt_id + provider_operation_id`。
- V2 首輪三引擎測試總**預算上限 US$10（待逐音檔與實際計費核准）**，為獨立測試預算，**不得誤用**既有 `COURSE_TRANSCRIPT_AUTO_AUTHORIZE_MAX_USD=10` 自動授權或 `COURSE_TRANSCRIPT_COST_LIMIT_USD=200` 作為本次測試許可。超預算、費率無法確定、缺權限須 fail closed。
- 無使用者就「特定來源／預估費用」明確同意前，不執行真實付費 provider 呼叫；本次寫文件不構成 API 花費授權。
- 付款／權限變更由帳戶擁有人授權；禁止 agent 新增 IAM、公開儲存桶、公開 Tunnel、寫出金鑰內容。

## 7. 狀態／資料契約（新增欄位，不破壞舊 job）

建議新 V2 stages（實作時映射／遷移既有 state machine，而不是直接覆寫舊 `jobs`）：

`inventory` → `preflight` → `gemini_primary` → `primary_completeness` → `chirp_independent` → `acoustic_gate` → `transcribe_optional` → `cross_compare` → `semantic_adjudication` → `timing_alignment` → `golden_qa` → `export` → `publish_pending` → `published_verified`

例外：`awaiting_confirmation`、`needs_review`、`deferred_budget`、`failed_retryable`、`failed_terminal`。同一 job stage 進度與 evidence 持久化至 SQLite WAL + immutable artifacts；worker restart 不得重送已成功 provider 呼叫。

每份 job 固定產出／保留：
- `source-manifest.json`（Drive id、來源路徑、SHA256、duration、lineage/canonical）
- `gemini-primary-raw.json`、`gemini-primary.txt`、`gemini-primary-coverage.json`
- `chirp-raw*.json`、`merged-words.json`、`chirp-completeness.json`、必要時 patch evidence
- `transcribe-raw.json` 或 `transcribe-skipped.json`
- `engine-comparison.json`（三套互相差異、每個疑點對應音檔窗與裁決）
- `adjudication.json`（候選文字、理由、引用證據類別／音檔區間、review 狀態）
- `final.srt`、`校正版逐字稿.txt`、`golden-qa.json`
- `usage-ledger.json`、`processing_manifest.json`、hash/發佈/版本及回滾資料

以上是 V2 建議 artifact schema（名稱可做相容映射），不表示當前系統已產出。

## 8. ChatGPT Skill / WebCodex 與介面分工

**Skill = 操作／編排入口，不是靠單段 prompt 運轉的 ASR 引擎。** 使用者可說「黃金字幕：<Drive URL>；品質優先，全部完成後回寫」，Skill 解析來源／模式，交由可授權的 WebCodex/VPS task API 執行。長時間 provider 工作由 VPS 持久化 worker/Job 負責，ChatGPT 關閉也不影響已提交任務；對話查進度需要重新讀取 ledger，不可假裝仍持續工作。

前端只加既有管理站最小必要功能：job 列表、模式／引擎、逐檔成本與進度、三稿差異並排、疑點音訊播放、人工裁決與待覆核、SRT/TXT 預覽、QA Gate、發佈版本。UI 和 Skill **共用同一後端 API 和 ledger**；不可各做一套業務邏輯。

回寫 Google Drive 保留 `drive_lock`、revision lock、pending→verify→backup→promote→final verify；保護原始媒體、人工參考逐字稿和已核定較新 SRT 的編輯權。Drive 失敗只重試 publication，不重新呼叫模型。佛經上下文 profile、Market America 術語、HVAC 術語都保留課程隔離。

## 9. 實作邊界與既有模組對接

先實際檢查 target commit，再以最小侵入整合；候選位置：
- `app/providers/`：新增 Gemini audio primary 與 3.5 Transcribe adapters，沿用 `correct_text_hardened.py` 的 audit/usage 概念，但音訊 API 不使用 text-only schema。
- `app/jobs/`：保存工作階段／provider operations／成本 ledger、超額停機、safe Drive delivery。
- `app/pipeline/dynamic_worker_production.py`：加 V2 mode dispatch，保留 V1 舊 job 恢復，不直接改 V1 immutable state。
- `app/subtitles/` + `app/providers/build_srt.py`：差異裁決、可靠對齊、SRT/TXT deterministic 渲染。
- `app/providers/chirp_completeness_gate.py`、`validate_outputs_hardened.py`：加入雙／三引擎 evidence gate，不用 LLM 降低聲學標準。
- `skills/golden-course-transcript/SKILL.md`：V2 operator contract，先維持 V1 Skill 可讀可用，待整合 gate 通過再切換預設。
- `frontend/`：沿用管理頁面和 Access 保護。
- `tests/`：mock/fake-provider、安全門檻、schema、長檔／重啟／release 回歸測試。
- `docs/STATE_MACHINE.md`、`docs/DATABASE_SCHEMA.md`、`RUNBOOK.md`、`CURRENT_STATE.md`：**等程式真正完成才更新為已實作狀態**。

## 10. 不可破壞的既有政策

沿用 `DECISIONS.md`、`GLOBAL_GUARDRAILS.md`、目前已合併與另分支尚未合併的 Golden 規範：
- 先 Inventory/Lineage，唯一 canonical，重用可信 evidence。
- Chirp Dynamic 只用於 canonical 大量首輪；任何 retry/repair 使用 Standard 和 bounded windows；已終止 operation 不可無限 polling。
- 本機 Coverage/VAD/QA 為 default，真正聲音缺漏才允許再次付費。
- V1 Golden v1.6/v1.7 與 `feat/media-inventory-dedup` 分支狀態必須先釐清；禁止覆蓋 dirty runtime worktree。
- 原本人工參考 `*逐字稿.txt` 必須保留；新校正版應用獨立檔名並安全版本化。
- 不得在新流程起步就重跑歷史九堂音檔；先建立黃金基準與非付費 regression。
- 嚴格遵守 immutable release SHA、VPS ARM64 build、container revision guard，保留 roll back。

## 11. 與驗收矩陣的關聯

每個施工階段都須以 [Golden V2 驗收與開發順序](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md) 的 Gate ID 提交證據：commit SHA、測試／QA 報告路徑、模型與成本、變更檔案、是否實際 provider 呼叫、是否改 Drive、是否更動 production、未完成項目。**沒有可核對證據就不能標示 PASS。**
