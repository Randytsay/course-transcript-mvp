# Golden Transcript V2 — 驗收 Gate 狀態追蹤

### 2026-10-13 — P5 latest four-minute real-evidence QA rehearsal (does NOT advance G5 to PASS)

- New owner policy: 3.8 main semantic transcript; Chirp 3 acoustic and original word timestamp authority; 3.5 Preview optional targeted review, not mandatory full third model. ChatGPT supervises semantic arbitration, WebCodex bounded QA execution, VPS Durable Worker intended for queued continuation, authenticated owner final authorization.
- Real source `17db78ae…` (600–840 seconds, 240s), **all three genuine model receipts already completed** and reused without inference. Their estimated application cost totals US$0.10 for successes; old GA 3.5 failed request billing still unknown.
- `app/pipeline/golden_v2_p5_evidence_trial.py` + ten offline regression tests: 3 receipts verified by source SHA; optional Preview's prior receipt hashes validated; 12/12 human choices and user's explicit 「所以比如有長行」 only. Pure comparison + immutable-cue candidate 60 SRT segments and deterministic TXT; 710 Chirp timing words; 12 >1.5s gaps flagged as unknown audio status.
- Golden QA state **REVIEW_REQUIRED; GOLDEN_PASS=false; safe_publish_allowed=false** (5 blockers: no independently certified entire-audio listen, no VAD/Chirp coverage, no 3.8→Chirp word alignment, unresolved text differences, no final natural subtitle segmentation/approval).
- No paid calls, no Drive write, no V1 change, no production cutover. G5 remains **PARTIAL**; G0–G8 have not all PASSED. Detailed decision/command/QA evidence: `docs/GOLDEN_V2_P5_CHATGPT_WEBCODEX_QA_2026-10-13.md`.


建立日期：2026-10-11　｜　規格版本：`V2.0-design-1`  
執行進度：**2026-10-12 重新盤點：G0–G7 八項 PARTIAL、G8 NOT_RUN，0/9 項通過。已驗證非付費 CI 不等於端到端或 production 驗收；main/production 未切換。**

### 2026-10-13 — 接手：3.8-only defaults、可信單階段派發、audit-first smoke 與 DB 還原

- 最新 owner 指示「拿掉所有 3.7 的設定，只用 3.8」：已補齊 owner intake/provider UI、狀態 API、OpenRouter 和 fake-provider 預設；direct legacy Gemini 提交被阻擋，歷史模型/用量/raw 維持真實。詳見 [本輪完整盤點](GOLDEN_V2_RESUME_REPORT_2026-10-13.md)。
- 一般 V2 `execute_trusted_paid_stage` 串接獨立核准/capability/pricing callback、live Drive check、source/stage/revision/aggregate-cap CAS 與現有 adapters；只有注入可信 clients 時可用，未配置 paid daemon/HTTP/CLI，**不是 runtime 部署或整堂付費授權**。
- 四分鐘 smoke raw 在校验/核銷前落盤，Chirp operation 在等待前落盤，SDK paid retries 明確停用；下一階段核對 receipt SHA。新增只讀本機覆聽輸出、Chirp 同源候選 SRT/TXT/HTML/manifest，**真 receipts 尚未產生**。
- Python 3.12 **986 unittest**、handoff **27 pytest**、前端 **62 tests/build**、dependency audit **0 vulnerabilities**、compileall/Compose/diff PASS；VPS 獨立 GenAI 2.29 image 的 `/tmp` source-copy 四分鐘套件 **38 fake-client tests PASS**。Exact-SHA GitHub CI 在 push 後另核對。
- V1 runtime label `adafd952...`，0 unexpired source/review leases；52 running stage 均屬 completed/cancelled/awaiting_review jobs，尚未 live operation reconciliation。SQLite 25.7 MB 快照 SHA 校驗、跨主機獨立還原、integrity check 與全部表逐列比對 PASS；**12 GiB jobs artifacts 的備份/還原仍未完成**，詳見 [無秘密 JSON 證據](evidence/golden-v2-db-restore-20261013.json)。
- 實際 smoke 三階段仍 PENDING；新模型支出 $0、無 Drive/GCS/IAM/firewall/production cutover。G0–G7 PARTIAL、G8 NOT_RUN、正式 **0/9 PASS**。

### 2026-10-13 — Provider API 與 GCS 接線、獨立 2.29 SDK 相容性驗證（尚無費用）

- 新 `golden_v2_smoke_providers.py`：Gemini 3.8/3.5、Chirp 3 實際 SDK 方法和嚴格回應／用量證據處理；標準刊價與 token/音訊時間換算至美元分、向上進位。無用量／模型無輸出／Chirp 時碼錯誤皆不可推進。
- 新 `golden_v2_smoke_gcs.py`：短片段 SHA 固定的私有 GCS 專屬路徑；上傳前建不可覆寫的 GCS 意圖紀錄、僅允許 generation=0 建立而非覆寫，timeout 不重送；消費前重新下載綁定 generation 的真實 bytes 驗雜湊；需注入受信任授權。
- 新 `golden_v2_smoke_dispatch.py`：將 GCS 驗證、三模型 Adapter、費用核算與 SQLite `REQUEST_STARTED` 串接成一次性階段，預設不自動呼叫，沒有附帶可跳過 trusted owner/capability verifier 的付費 CLI。
- 查出目前 image `google-genai 1.75.0` **不支援** Gemini 3.5 `AudioTranscriptionConfig.mode/word_timestamp` 與 `GenerateContentConfig.audio_transcription_config`；獨立 `Dockerfile.golden-v2-smoke` 將 V2 專用 SDK 固定為 `google-genai 2.29.0`，已在無憑證 Docker 內實測新 SDK 欄位與兩代 fail-closed 行為；**不修改生產 V1 的 dependencies**。
- 付費模型實測 **NOT_RUN**、GCS 寫入 **NOT_RUN**、Drive 更動 **NONE**。剩餘阻擋為受信任的 V2 identity/私有 bucket／region+pricing entitlement 和實際收費結果驗證。此開發成果不是 Gate PASS。

### 2026-10-13 — Isolated one-clip paid dispatch ledger (fake-provider acceptance)

- 實際 4 分鐘來源已在 VPS `golden-v2-real-audio-test-ad01f98/data/smoke/clip-trial.sqlite` 建立 create-once 測試帳本，三個模型階段均 `PENDING`、總上限 1000¢、`provider_calls_started=false`；沒有提交付費請求。
- 在既有受信任 V1 Worker 使用 Google IAM `testPermissions` 唯讀檢查現有 GCS bucket，`storage.objects.create/get/list/delete` 全為 True，`storage.buckets.get` False。後者的 HTTP 403 不等於不能上傳物件。唯讀 Cloud Probe 已修正為 Speech regional `us-speech.googleapis.com`、GCS 物件清單與精確 IAM 查詢；Gemini metadata GET 404 仍不足以判定推論不支援。未更動任何 V1 工作檔案。

- 新增 `app/pipeline/golden_v2_smoke_ledger.py` 與 `scripts/golden_v2_smoke_trial.py`：將前一輪的來源綁定 4 分鐘方案寫入**獨立 SQLite 一次性帳本**；初始化和狀態查詢不可付費，模型順序為 Gemini 3.8 → Chirp 3 → Gemini 3.5；各階段標準預留 200¢，累計不得超過 user-approved 1000¢。每階段 `BEGIN IMMEDIATE` 與 `REQUEST_STARTED` 先於 provider call 持久化，結果和實際費用儲存 immutable receipt；逾時／中斷／費用不明一律 `OUTCOME_UNKNOWN` 防重送；雙 worker 競爭僅一人可派發。
- 僅在**雙重受信任 callback**（owner authorization、provider+pricing）均通過後，developer adapter 才能 dispatch。當前**沒有真實 provider adapter／GCS 權限或 SDK 付費路徑接線**，測試全部注入 fake provider。這是開發進展，**非真實三模型／正式 G0–G8 Gate 通過**。不可用 Google Drive 母檔 ID 冒充 4 分鐘衍生音訊。

### 2026-10-13 — Owner approved USD10 short real-audio paid-smoke scope (execution blocked)

- 使用者已同意為現成的 **4 分鐘衍生真音檔**付費測試，總上限 USD 10；不擴大到 141 分鐘原始音檔。完整音檔既有 `v2p_b075478d5d505ee49c330016c6d12023` **仍 AWAITING_APPROVAL、付費估價全 0**，不得把它當成 4 分鐘測試的已核准付費來源。精確 source/clip 雜湊與測試路徑見 [真正音檔付費測試交接](GOLDEN_V2_REAL_AUDIO_PAID_SMOKE_RUNBOOK.md)。
- 新增隔離的、預設離線 `scripts/golden_v2_cloud_probe.py`（受信任 operator 設置 `--check-live` 時才做 GET-only Vertex/Speech/GCS metadata probe），不印憑證或帳戶 ID；不做 paid inference / GCS writes / Drive writes。新 `scripts/golden_v2_live_smoke_plan.py` 驗證原始 Drive preflight 與完整 MP3、600–840 秒衍生片段的 SHA、版本、片段長度，並以不可覆寫的 JSON 記錄 USD 10 提議預算與模型順序。**該文件不是帳單核准簽章或可執行 Ledger claim，`paid_execution_enabled=false`**。
- 對現有 V1 服務帳戶憑證的跨容器掛載探測遭安全檢查阻擋，因此未繼續嘗試或繞過。後續必須在獲核准的受信任執行環境完成單獨 V2 identity/私有 bucket 和真實 endpoint/cost 驗證，再開發可審計的一次性付費 stage runner。本輪禁止宣稱有真 Gemini/Chirp 模型使用，G0–G7 PARTIAL、G8 NOT_RUN、正式 0/9 PASS。

### 2026-10-12 — Read-only live Drive source revision check

- Authenticated source intake now retains the exact Drive source path and downloaded MD5 in its immutable preflight record. A separate `verify_live_drive_revision` check re-resolves that path through Drive GET metadata and compares current file ID, version, size and MD5 with the saved original bytes. Changed or unavailable metadata blocks a future paid claim; older preflight records without these fields also fail closed. This check does not claim a stage, call a paid model, or grant permission to bill. A future trusted executor must recheck immediately before its revision-bound claim and handle any source change.
- Offline fake-Drive tests cover unchanged source, changed identity/version/size/MD5, read failure and local tampering. Local Python 3.12 full unittest **926 PASS**, focused source/readiness/revision **28 PASS**, compileall and diff check PASS. Exact-SHA CI remains to be checked after push. No live Drive read or write, GCS write, provider call, or production change occurred; formal G0–G8 remain 0/9 PASS.

### 2026-10-12 — Atomic paid dispatch CAS and aggregate trial preview (not live executor)

- 新增唯讀 `app/pipeline/golden_v2_paid_dispatch.py`、`scripts/golden_v2_paid_dispatch.py` 與 owner-token-only `GET /api/v1/golden-v2/jobs/{job_id}/paid-preview`（沿用 default-off V2 status API）。預覽輸出任務 revision、來源 SHA、下一付費模型、逐階段估價、整批已核准任務的保守總曝險與版本指紋；不外洩 approval reference 原文、claim token 或憑證。
- `GoldenV2Ledger.claim_next` 新增可選的 `expected_revision`、`expected_source_sha`、`aggregate_cap_cents`，在同一個 `BEGIN IMMEDIATE` SQLite 原子交易比對。使用整個 V2 Ledger 內**已核准所有任務**的實際已結算金額＋未結算階段保守預算；超過共用試驗上限、有其他 RUNNING／OUTCOME_UNKNOWN／OVER_BUDGET 付費階段均禁止透過此受控入口 claim。維持既有呼叫者的相容性，因此**未呼叫新增參數的舊入口並不能宣稱已套用全域上限**。
- 預覽指紋只是版本識別，**不是簽章、授權或金額保證**。成功預覽仍固定 `provider_calls_allowed=false`，實際付費前須由獨立可信實作確認即時 Drive 版本、模型可用性、價格與擁有者的逐來源核准。沒有接上可連網的付費 V2 Runner，也不代表 Provider 費用已由 Google 帳單驗證。
- 隔離 VPS 無網路 Docker tests：Ledger/Preview/Owner API、預算越界、雙 Worker revision-CAS、來源證據與未結清付費請求的 **50 項相關測試通過**；完整 exact-SHA GitHub CI 待本輪提交後核對。G0–G7 PARTIAL、G8 NOT_RUN、正式 Gate 0/9 PASS。V1 production 與 Golden V2 隔離 canary 不在本次部署範圍。

### 2026-10-12 — Persistent worker exposes paid-stage blockers

- Provider-free `golden_v2_worker` now includes the existing read-only paid-stage readiness assessment when a job stops at a provider stage. Operators can distinguish missing verified Drive intake, stale source evidence, insufficient approval, and prior ambiguous paid receipts from a generic provider wait. The worker still does not claim a paid stage or call Vertex, Speech, GCS, or Drive.
- Reject symlinked V2 ledger files and artifact roots before opening the mutable SQLite ledger. Local Python 3.12 validation: full unittest **910 PASS**, focused worker/readiness **22 PASS**, compileall and diff check PASS. Exact-SHA GitHub CI remains to be checked after push. No provider call, Drive/GCS mutation, or production deployment occurred; G0–G7 remain PARTIAL, G8 NOT_RUN, 0/9 PASS.

### 2026-10-12 — Paid-stage readiness preflight (development, no paid executor)

- 新增 `app/pipeline/golden_v2_paid_readiness.py` 及直接可執行的唯讀 CLI `scripts/golden_v2_paid_readiness.py --database ... --artifact-root ... --job-id v2p_...`。只接受已經過 Drive source-preflight 登錄並有有效預算核准的 V2 job；驗證穩定 job registration SHA、source original/FLAC SHA、lineage manifest、Drive observation、本機費用上限與階段順序。
- 任務未核准、SHA／Drive 版本證據與註冊紀錄不一致、source-preflight 缺失、目錄 symlink、已有 Gemini 付費 receipt／異常 claim 都回傳明確 blocker。使用資料庫 `mode=ro`，**不 claim stage、不啟動模型、不上傳 GCS／Drive、不重新送出已付費視窗**；通過本地檢查依然輸出 `provider_calls_allowed=false`，必須另外完成真實 provider capability、計價與 Drive revision 驗證。
- 已在無網路容器執行 fake-provider／來源寫入器整合與反向案例；**GitHub exact-SHA CI、真實 Vertex/Chirp/GCS/Drive、付費 Worker executor 尚待驗證，G0–G7 PARTIAL、G8 NOT_RUN，正式 Gate 0/9 PASS**。不可從此 CLI 推論真實付費就緒。

### 2026-10-12 — Follow-up safety hardening (verification pending)
- Golden 3.8 primary / 3.5 third audio paid-call reservations now fsync parent directories after `O_EXCL` creation and after atomic audit-record replacement. A directory sync failure prevents calling a paid provider; barriers are never automatically discarded.
- Owner approvals now guard against *cross-job* reuse of an already approved normalized audio SHA. A second job for the same source requires separate explicit `repeat_source_confirmed=true` approval, with an owner UI warning and fresh checkbox confirmation. This is not a cross-job distributed lock: reprocessing remains possible after explicit approval.
- Added provider-free regression tests for directory fsync and repeated source approval. CI/VPS evidence for this follow-up is pending; G0–G8 remain unchanged, 0/9 formal gates passed. Draft PR remains unmerged; no production/Drive/provider mutation.

### 2026-10-12 — Historical window receipt compatibility and latest CI

- 增加唯讀 `scripts/golden_v2_audit_slots.py --job-dir <V2-job-dir>`：掃描既有 3.8 主稿與 3.5 Transcribe 的付費紀錄，區分 canonical COMPLETE、legacy COMPLETE 需重新索引、重複 slot、未知狀態、損壞檔；**不刪檔、不搬移、不呼叫模型**。
- 強制執行前相容性防護：即使舊版稽核檔使用 prompt/config 雜湊檔名，只要同 source SHA／model／GCS 視窗已經有舊紀錄，**禁止再次付費 POST**，並轉由人工核對／安全遷移；缺損或無法識別的歷史紀錄也先 fail-closed。
- 程式 SHA `d40e81f330c77c7fa90d29022ea2ac0716b8cf8b` 的 [GitHub CI #38239849570](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38239849570) **Python 890/890 PASS、Handoff pytest 27 PASS、Next.js、Docker image、Compose PASS**；[Release Compose #38239849451](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38239849451) PASS。均為離線測試，尚未改正式 VPS、跑真模型或 Drive 回寫。**G0–G8 仍 0/9 PASS**。

### 2026-10-12 — Provider paid-window idempotency and budget checkpoints

- 隔離分支 `codex/golden-v2-goal-completion`，程式驗證 SHA `ef489f0911d0a844e22b50d87ad7646e6d36d36d`。Gemini 3.8 音訊主稿及 Gemini 3.5 第三辨識引擎改為 POSIX `O_EXCL` 排他建立 `REQUEST_STARTED` 原始稽核檔；**同一模型／source SHA／GCS 視窗** 只能保有一筆請求，改提示詞／改第三引擎參數不得依原 claim 重複計費；崩潰殘留、空檔或讀取損壞均需 reconciliation，絕不盲目重送。
- `golden_v2_primary_stage.py` 和 `golden_v2_third_stage.py` 新增 `golden_v2_window_budget.py` **逐窗累計費用 checkpoint**；前一窗已超額或未能核銷費用時，保存已取得的 provider receipts，記 `OUTCOME_UNKNOWN`／`NEEDS_RECONCILIATION` 並停止下個 POST。
- 非付費測試覆蓋併發競態、改提示詞／設定繞過、受損 request barrier、來源授權失敗、第一窗超額及未知費用；exact-SHA [CI #38238895306](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38238895306) **Python unittest 885 PASS、handoff pytest 27 PASS、Next.js、Docker image、Compose 全部 PASS**，且 [Release Compose #38238895433](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38238895433) PASS。
- **遷移缺口**：舊版 V2 可能有依完整 prompt/config 命名的 window raw 檔；部署前必須盤點／核對歷史 receipts 並決定安全索引遷移，不得僅因新 slot 檔名不存在就重新付費。尚未驗證 POSIX 持久 volume 在正式 VPS 的 fsync/多 Worker 行為、真 Vertex 配額／計費或實際帳單；P2/P4/G2/G4 仍 **PARTIAL**，G0–G8 **0/9 PASS**，不代表已部署。

### 2026-10-12 — Gemini 3.8 post-ASR text-correction migration

- 依使用者最新指示，**新 ASR 後文字校正**從 Vertex `gemini-3.7-flash` 改用 `gemini-3.8-flash`。完整原音主稿仍使用 3.8，兩套 Adapter/Schema/Prompt **保持分離**，Chirp 3 仍提供時碼。
- 已更新 correction 主程式／cascade／Vertex Adapter／provider route／MiniMax fallback／Worker／Proxy／前端與環境範例；移除 3.8 不建議的 `temperature`，刷新 prompt/cache 版本；3.8 文字費率依 [官方 Google Vertex 定價](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing) 單獨入表，保留歷史 3.7 計價資料。
- 變更仍在 Draft PR #144；**非正式環境切換，未呼叫真實付費 API，不代表 3.8 文字校正已完成真實驗收**。下列舊 3.7 續報屬當時的不可變歷史，不是最新模型選擇。
- 已新增 **歷史舊任務阻擋**：若持久化任務仍帶 `gemini-3.7-flash`（含 OpenRouter 別名），新的校正提交會 fail-closed，不能靜默重寫核准模型或誤向 3.7 扣費；既已提交的 3.7 Vertex Batch 可繼續唯讀查詢及核對，不清除歷史 raw、operation 或用量。新增假 Provider 單測，正式模型切換仍需逐任務明確審核。

### 2026-10-12 Goal continuation — authenticated Drive source preflight

- 新增 default-off `POST /api/v1/golden-v2/source-preflight` 與 `/golden-v2` 表單：只接受目前允許的 Drive 精確路徑、模式、六階段整數估價與明確確認；維持 admin-host／Cloudflare Access、same-origin HTTPS、獨立 write token、feature flag 與 job ceiling。
- Backend 重新解析精確路徑並拒絕重名，下載前後讀取 Google Drive metadata，串流下載（預設單檔上限 2 GiB），核對 file ID、version、size、MD5、名稱、MIME、parent 與本機 bytes。並行匯入會 fail-fast 回 409，避免同時佔用多份大音檔暫存空間。接著做本機 FFmpeg 正規化，保存 original、FLAC、lineage 與 manifest；只有全部成功才登錄 `AWAITING_APPROVAL`。下載／metadata 不一致會丟棄暫存檔且不建立任務。
- 入口只執行 Drive GET 與本機隔離空間寫入，不改 Drive、不呼叫 Vertex／Chirp、不排隊辨識；估價不構成核准。來源 SHA 後續任務仍須獨立 revision-bound 預算核准。設定預設關閉，沒有真實 Drive 憑證呼叫或 production 啟用。
- Local mock 驗證包含串流雜湊、重複路徑、超量、下載版本變動／錯誤內容清理、並行匯入 fail-fast 及孤立暫存；Drive intake suite **9 tests**、既有 preflight **10 tests**、owner API suite **12 tests**、完整 unittest **871 PASS**、pytest handoff/output-safety **30 PASS**、frontend **62 tests**、Next.js production build、compileall 與 `git diff --check` PASS。提交 `189e10017f6396a8321035d5c6e8d89f93af136c` 的 exact-head [GitHub CI #38234152582](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38234152582) 及 [Release Compose #38234152515](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38234152515) 均 PASS，CI 包含 Python 3.12、前端 tests/build、Docker images 與 Compose。額外本機 unittest/pytest 使用暫存 Python 3.13 環境。
- 此功能仍屬 source-level code。未連真 Drive、未辨識任何檔案、未驗證 Cloudflare／production request timeout、未部署；G7 PARTIAL、G8 NOT_RUN、G0–G7 PARTIAL、0/9 PASS。

### 2026-10-12 Goal continuation — local source intake and persistent worker code

- 新增 `scripts/golden_v2_source_preflight.py`：只接收本機已下載原始音訊與呼叫端提供的唯讀 Drive observation；先複製 immutable snapshot 並比對 bytes/SHA，再以 FFmpeg 建立 16 kHz mono FLAC，保留原件、正規化音訊、Drive observation lineage 及 source-preflight manifest。此處驗證的是欄位與本機 bytes 一致，未連接或認證 Google Drive。
- Preflight 以完整 manifest SHA 派生穩定 job ID；同一來源/版本/模式/估價重跑不重複建立 SQLite job，Drive 版本或設定變更則產生新 identity。Job 僅停留在 `AWAITING_APPROVAL`；估價不是報價或核准。
- Owner API 的相同來源 SHA／模式／逐階段估價重送也會回傳同一 job ID，避免客戶端逾時重試生成重複待核准項目；仍受獨立 write token、feature flag、確認欄位與費用上限保護。
- 新增 `scripts/golden_v2_worker_daemon.py` 與隔離 Compose `golden-v2` profile。常駐程序只掃描已核准 active jobs，處理本機 compare/adjudication/QA，缺本機 artifacts、付費 stage 或真人聽音需求都回報 blocker。Compose 容器 `network_mode: none`，不掛載雲端憑證，未加入預設 profile，未在 VPS 啟動。
- 離線驗證：完整 Python unittest **858 PASS**；`tests/test_chatgpt_handoff.py` pytest **27 PASS**；其中來源 preflight **10 PASS**、immutable evidence **6 PASS**、worker **7 PASS**、ledger **10 PASS**。Symlink regression 確認不可寫出 artifact root；兩個 CLI help、compileall、API import、local/release `golden-v2` Compose config 與 `git diff --check` PASS。使用 fake FFmpeg/ffprobe，沒有 Drive/GCS/Vertex/Chirp 呼叫或寫入。
- exact source SHA `ab0431956052652d52bb93040f06407ccb7bb3b0` 的 [CI #38210501522](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38210501522) 與 [Release Compose #38210501398](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38210501398) 均 PASS，含 Python 3.12 tests、frontend tests/build、Docker images、Compose 與 release provenance。此為非付費 CI，不是 live provider、VPS 或 production 驗收。
- **G7 仍 PARTIAL、G0–G7 仍 PARTIAL、G8 NOT_RUN、0/9 PASS**：Drive URL 實際讀取、VPS 部署/持續性實測、付費 provider runner、Skill/session transport、獨立真人 reviewer、Drive 發布與 production/staging 驗收仍未完成。隔離分支已有 default-off owner console source，尚未配置或部署。沒有改 IAM/firewall 或 production。

### 2026-10-12 Goal continuation — idempotent owner registration

- Owner job registration now derives a stable identity from source SHA, mode, and the complete per-stage estimate. An identical HTTP retry reuses the original job; it returns the current ledger status/revision after approval, while a changed request creates a distinct unapproved job.
- Regression coverage verifies replay, changed-estimate identity, current-state reporting after approval, and unchanged write-token/feature-flag/cost-ceiling boundaries. Local Python 3.12 validation: full unittest **859 PASS**, handoff pytest **27 PASS**, compileall, production API import, and `git diff --check` PASS. The combined exact-source validation on `c262a77c7bd955c0ac744cd2efd373c5f1407be2` passed [GitHub CI #38231087163](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087163) and [Release Compose #38231087126](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087126), including Python 3.12 tests, frontend tests/build, Docker images and Compose. No provider, Drive/GCS, IAM/firewall, or production operation occurred.

### 2026-10-12 Goal continuation — disabled owner console source implementation

- Added `/golden-v2`, an owner status console for the existing SQLite ledger: source SHA, mode, current state, per-stage cost/evidence, and a six-stage progress rail. It can submit a revision-bound budget approval only for an already-registered `AWAITING_APPROVAL` job; it does not create jobs, fetch Drive files, run paid providers, or publish results.
- Same-origin Next.js routes keep the distinct status/write bearer tokens on the server, validate the V2 job identity and exact approval payload, reject cross-origin mutations, and return `Cache-Control: no-store`. Production access is restricted to the configured transcript admin hostname and requires Cloudflare Access identity headers; approval POSTs also require same-origin HTTPS. The edge must continue enforcing Access on that hostname.
- UI is default-off (`COURSE_TRANSCRIPT_V2_ADMIN_UI_ENABLED`); the optional Compose environment file defaults to `/dev/null`, and no tokens or V2 API flags were configured. The console/API are not deployed. Its responsive browser check covered only the unavailable-ledger state; no live ledger or approval was exercised.
- Local verification: frontend **60 tests PASS**, Next.js production build PASS, local web/offline-worker Compose configuration PASS, and release Compose configuration PASS with an explicit SHA and temporary host paths (config parse only; no image build). Exact-head [GitHub CI #38231087163](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087163) and [Release Compose #38231087126](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087126) both PASS. No provider call, Drive/GCS mutation, IAM/firewall change, or production action occurred; G0–G7 remain PARTIAL, G8 NOT_RUN, 0/9 PASS.

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
| P0 基線／隔離 | G0 | PARTIAL | 2026-10-13 已核對 V1 runtime label、128 jobs 與 0 未到期 source/review lease，SQLite 快照跨主機還原及全部表逐列一致；未 reset/clean/stash。仍欠 12 GiB jobs artifacts 備份還原、52 歷史 running stage 的 live operation reconciliation 和分支整合。 |
| P1 模型角色分流 | G1 | PARTIAL | **最新 2026-10-12 使用者決策：Gemini 3.8 Flash 同時作為音訊主稿與 ASR 後純文字校正（各自獨立 Adapter）**。PR #144 分支已更新 correction/cascade/Vertex Router/Worker/Proxy/UI/價格與測試，保留原始 3.7 歷史 evidence；需以當前 HEAD CI 驗證，且**尚無真實 Vertex 3.8 純文字 API smoke、VPS 正式 SHA、配額／費用與 rollout 驗收，不得標為 PASS**。 |
| P2 Gemini 3.8 音訊主稿 | G2 | PARTIAL | 新增 `golden_v2_audio_staging.py` 的本機 FFmpeg source-SHA FLAC 分段、`golden_v2_gcs_staging.py` 的經核准 GCS 不覆寫上傳，以及 `golden_v2_primary_stage.py` 串接 Gemini 3.8→SQLite，含超額預估阻擋、結果不明停機及 mock 整合測試；**已加入來源→normalized→窗段 SHA lineage 的本機證據、逐窗 raw SHA/身份驗證及人工 overlap seam 裁切後原始證據 CAS 綁定**；仍未驗證真實 Drive 來源 SHA、正式 Worker/Vertex、長音訊整堂聲學 coverage。 |
| P3 Chirp 獨立聲學比對 | G3 | PARTIAL | `golden_v2_compare.py`/`golden_v2_flow.py` 具備獨立差異比對與先 Gemini 後 Chirp 的順序；既有 Chirp PASS 可經 SHA/來源驗證重用；Speech V2 adapter、async fake-provider submit/recovery、已知 operation ID 的 revision-CAS 部分續送及未知 SUBMITTING 凍結均已離線驗證。**另新增 VAD positive-speech gate、exact-plan/cost-approved Standard bounded repair、不可變 clip/GCS identities、restart-safe re-upload、target-only merge；acoustic REVIEW 會阻擋 compare，只有 PASS 才完成 Chirp stage。**仍欠常駐付費 Worker、可稽核帳單、真實 Speech API/長音訊聲學 UAT 與真人聽音。 |
| P4 Gemini 3.5 Transcribe | G4 | PARTIAL | `gemini_third_transcribe.py` 實作 Vertex generateContent/VERBATIM、≤15m hard cap、選用/禁用與 mock tests；**尚未實際 Vertex smoke/worker 用量帳本**。 |
| P5 WebCodex 最終裁決與 Golden QA | G5 | PARTIAL | `golden_v2_final_gate.py` + `golden_v2_stage_runner.py` 可持久化離線比對/QA；`golden_v2_review_candidates.py` 新增來源 SHA／revision 綁定的本機人工候選 review/import，可選 KEEP_CHIRP 或以 Chirp word evidence 校正；**仍未串接 ChatGPT/WebCodex 對話傳輸、獨立 reviewer 身分或真人整堂聲學驗收**，無爭議也不等於正確。 |
| P6 實測評分與最佳化 | G6 | PARTIAL | `app/evaluation/golden_v2_benchmark.py` + `scripts/golden_v2_benchmark.py` 新增離線配對引擎 CER 評測：強制同一來源 SHA/window IDs、人工 Gold provenance 與各引擎 raw-evidence SHA，按類別統計；成本/耗時缺值保留 unknown。**無真實人工金標／三引擎樣本／帳單比價，欄位 SHA 尚未獨立讀回驗證**。 |
| P7 ChatGPT Skill / 持久任務 | G7 | PARTIAL | 新增 `app/jobs/golden_v2_ledger.py` SQLite task/claim/費用/來源視窗授權；`scripts/golden_v2_status.py` 唯讀查詢、`app/golden_v2_routes.py` 分離 read/write token 的 owner API（source-SHA registration、job/repair approval 與精確 Drive 路徑 source-preflight；write 預設關閉、估價受 US$10 預設上限約束，Drive 僅 GET、建立未核准工作，絕不提交 provider），`scripts/golden_v2_review_overlaps.py` overlap seam review 與 `scripts/golden_v2_review_candidates.py` 比對候選 review/import（操作者自述，無獨立聽音認證）；snapshot-first 本機 preflight、無網路 worker profile、隔離分支 default-off `/golden-v2` owner status/intake/approval console。Skill 仍為草案。**未安裝 Skill、未部署 Worker/UI/API、未接付費 Vertex／對話 handoff；live Drive/Cloudflare 驗證未執行**。 |
| P8 UI / 部署 / Drive 驗收 | G8 | NOT_RUN | 隔離分支有本機 default-off 任務／六階段狀態、精確路徑唯讀來源預檢及單筆預算核准入口；未做真帳本 browser UAT、staging/canary/production 部署或 live Drive 實測，音訊差異播放及出稿/發布整合仍未完成 |

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