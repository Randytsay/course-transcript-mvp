# Golden Transcript V2｜2026-10-12 實況盤點與六階段完工計畫

> **性質：當日唯讀核驗 + 下一階段工程規劃。** 不代表已部署、執行付費 API 或修改 Google Drive。  
> 最初盤點對應 [PR #143](https://github.com/Randytsay/course-transcript-mvp/pull/143) 舊版提交 `73f1a8265bd4458699cd66da1c9d6f88cf0779f1`；2026-10-12 隔離續作進入 [Draft PR #144](https://github.com/Randytsay/course-transcript-mvp/pull/144)，新 SHA/CI 另以 Gate 狀態簿為準。
> 權威規格：[架構](GOLDEN_V2_ARCHITECTURE_PLAN.md)｜[G0–G8 完整驗收矩陣](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md)｜[最新狀態](GOLDEN_V2_GATE_STATUS.md)｜[整合交接](GOLDEN_V2_INTEGRATION_HANDOFF.md)。

> **更新（2026-10-12）：**使用者已明確決定把 ASR 後的純文字校正由 3.7 改為 3.8。以下先前提到的 3.7 續報屬歷史記錄，不再代表預定正式模型。對應目前 Draft PR #144；實際 Vertex 3.8 校正仍待小額 smoke 與正式驗收。

## 1. 使用者原訂完工定義

交付「從自然語言交辦 Google Drive 資料夾／音檔、到可安全發布的正式 Golden SRT 與 TXT」的持久系統：

1. 使用 **Gemini 3.8 Flash** 首先完整獨立聽音形成繁體中文主稿；另外改用 **Vertex Gemini 3.8 Flash** 做 ASR 後的純文字校正，兩個 adapter 不互相代替；長音檔截斷／分段、overlap 對齊與 coverage 不得漏話、重複或虛構經文。
2. 使用同一原始音檔由 **Chirp 3** 獨立辨識與提供真實字詞時間戳；沿用 Dynamic Batch 正常首輪、Standard bounded repair、VAD／聲學完整性 Gate、已有可信 chunk 只重用不重送。
3. 可選 **Gemini 3.5 Transcribe**：品質優先跑全文，智慧最佳化只補高風險區間；Vertex API 端點能力與用量必須實測確認。
4. **ChatGPT/WebCodex** 針對模型差異與可聽原音、同堂經文／資料做文字裁決；經文只用於查詞，不得補出未講內容，台語不確定區間保留 REVIEW。
5. 以 Chirp 原始字詞時碼做穩定 SRT cue；與正式逐字稿 TXT 文字相等，Golden QA 沒有聲學／語意／內容完整性 blocker 才算 GOLDEN_PASS。
6. **SQLite/VPS 持久化 Worker**：來源 digest、費用核准、進度、斷線接續、避免重複付費；可供多 Agent 接手。
7. **ChatGPT Skill 和管理介面共用 API/狀態**：對話交辦、進度查詢、模式選擇、模型差異、音檔重聽、人工裁決、審核發布。桌機、平板、手機可操作。
8. 在核准前提下安全回寫**原 Google Drive 資料夾** `.srt`、`校正版逐字稿.txt`、QA/版本證據，保護原始音檔、既有人工稿與新版，並能檢驗 hash、避免衝突、回滾。
9. 三引擎品質、成本、效能以人工金標（CER/WER）、漏句、時碼、佛經、國台語、費用和延遲比較；不把模型共識誤稱準確率。

## 2. 查核到的客觀事實

### 2026-10-12 Goal continuation — authenticated Drive source preflight

- `/golden-v2` now accepts an explicitly confirmed Drive path, mode and six-stage estimate, then calls a default-off same-origin owner proxy and separately write-token-gated API route. The backend freshly resolves the exact path and rejects ambiguous duplicate names.
- It streams at most 2 GiB by default into a private artifact-root temp directory; Drive file ID, version, size, MD5, name, MIME and parent must remain identical before/after. Downloaded size/MD5 are verified, local SHA-256 binds the snapshot, then existing FFmpeg lineage/preflight stores the original and normalized FLAC and registers the deterministic job. Only after successful verification does the ledger receive `AWAITING_APPROVAL`; replay returns the current ledger state.
- Source intake performs Drive GET and local writes only. No Vertex/Chirp call, Drive mutation, IAM/firewall change, production configuration or deployment occurred. End-to-end Drive credentials, Cloudflare timeout and real-file behavior remain unverified; G7/G8 remain partial.
- Local evidence: mock Drive intake **9 tests**, existing source preflight **10 tests**, owner API **12 tests**, full unittest **871 PASS**, handoff/output-safety pytest **30 PASS**, frontend **62 tests**, Next.js production build, Python compileall and diff checks pass. Exact-head [GitHub CI #38234152582](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38234152582) and [Release Compose #38234152515](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38234152515) passed on source SHA `189e10017f6396a8321035d5c6e8d89f93af136c`, including Python 3.12 tests. Additional local unittest/pytest used a temporary Python 3.13 environment.

### 2026-10-12 Goal continuation — local source intake and persistent worker

- 在隔離分支 `codex/golden-v2-goal-completion` 提交 `ab0431956052652d52bb93040f06407ccb7bb3b0`：加入 snapshot-first 本機 source preflight、不可重複的未核准 V2 registration、artifact-root/symlink 防逃逸，以及無網路、無雲端憑證、opt-in 的持續本機階段 worker。後續 owner API 也改為相同來源／模式／估價 request 冪等，避免 HTTP 重試建立重複待核准 job。
- Python 3.12 離線測試 **858 unittest／27 handoff pytest** 通過；compileall、production API import、兩個 CLI、local/release Compose 與 diff check 通過。exact-SHA [CI #38210501522](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38210501522) 和 [Release Compose #38210501398](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38210501398) 均 PASS。
- source observation 僅與呼叫端提供資料及本機 bytes 比對，未由 Google Drive 認證；worker 未在 VPS 啟動。沒有付費模型呼叫、Drive/GCS 寫入、IAM/firewall 變更或 production 部署；G0–G7 仍 PARTIAL、G8 NOT_RUN、0/9 PASS。
- 後續 owner API registration 依 source SHA、mode 與完整 stage estimates 冪等；相同重送回傳最新 status/revision，不會建立重複待核准工作。回歸測試涵蓋核准後重送及變更估價的新 identity；最新本機完整 unittest **859 PASS**、handoff pytest **27 PASS**。後續 exact source `c262a77c7bd955c0ac744cd2efd373c5f1407be2` 的 [CI #38231087163](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087163) 與 [Release Compose #38231087126](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087126) 均 PASS，涵蓋 Python 3.12、前端測試/build、Docker images 及 Compose。

### 2026-10-12 Goal continuation — responsive owner status console

- 隔離分支新增 default-off `/golden-v2`：可查看既有 job 的來源 SHA、模式、ledger 狀態、六階段狀態及費用/證據；只對既有 `AWAITING_APPROVAL` job 提交 revision-bound 預算核准。它不建立 job、不讀取 Drive、不呼叫付費 provider、不發布結果。
- Next.js same-origin server routes 將 status/write bearer token 留在伺服器，production 只允許設定的 transcript admin host 並要求 Cloudflare Access identity headers；核准 POST 要求 HTTPS same-origin。Compose optional env file 預設 `/dev/null`，功能未配置、未部署，Access edge 仍須實際阻擋未登入流量。
- 前端 **60 tests**、production build、本機 web/offline worker Compose config 通過；release Compose config 用明確 SHA 與臨時掛載路徑完成 config-only 檢查，沒有 build image。瀏覽器檢視只有 responsive layout 和 unavailable-ledger state，沒有接真帳本或執行核准。exact source [CI #38231087163](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087163) 及 [Release Compose #38231087126](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38231087126) 均 PASS；沒有付費呼叫、Drive/GCS 寫入、IAM/firewall 變更或部署，G0–G7 PARTIAL、G8 NOT_RUN、0/9 PASS。

- PR #143 **open、draft，47 files changed**，開發分支 `feat/golden-v2-gemini38-primary`，查核 HEAD `73f1a826…`。
- GitHub Actions **CI run #38157612105**：Python **743 tests PASS**、Next.js build PASS、Docker image build PASS、Compose PASS。
- GitHub Actions **Release Compose run #38157612126**：環境/來源驗證 PASS。**這些是離線 CI，不是 Vertex／Chirp 真實音檔或 VPS 部署驗收。**
- 實際程式具 Gemini 音訊 adapter、FFmpeg 分段、模擬 GCS 上傳／費用核准、SQLite ledger、review-only Chirp text diff、可信既有 Chirp PASS evidence reuse、第三模型 adapter、SRT/TXT QA gate、CER 計算、狀態唯讀 CLI、V2 Skill 草稿。
- WebCodex 對黃金字幕的 5 個已登錄 Project 顯示 `git_available=false` 且 `active_jobs=0`，**無法據此確認正式主機的 Git release SHA、已進行中的 ASR、實體 DB 備份、運行中 V2 Worker 或真實 Cloud API 權限**。
- 本輪另外完成經 SHA/模型身分/來源 lineage 驗證的 Gemini 重疊 seam 原稿審查、人工裁切後 immutable artifact + ledger revision CAS、唯讀 owner status API（預設關閉，未部署）；離線驗證含 **807 unittest／27 重點 unittest／27 handoff pytest／54 frontend tests**。人工審查目前僅本機操作員自行宣告，未驗證實際聽音。
- **G0–G8 目前 0/9 過關；G0–G7 均 PARTIAL，G8 NOT_RUN。** 尚未取得任何正式 V2 三引擎真實音檔 Golden QA 通過證據。
- 沒有已驗證的真實付費模型呼叫、正式 Drive V2 寫回或 production cutover 證據；截至此盤點不宣稱曾發生這些操作。

### 2026-10-12 隔離續作：模型政策與 owner 核准入口

- 在分支 `codex/golden-v2-goal-completion` 提交 `2c3243ad976b3f1c45ca16a0c4cedc5392e50436`：V2 ASR 後純文字校正使用 Vertex Gemini 3.7；通用 Vertex 文字生成保留獨立的 `VERTEX_MODEL` 設定，沒有 Gemini 2.5 呼叫路徑。Google 官方標示 Gemini 3.7 Flash 將於 2027-01-28 退役，正式上線計畫需預先驗證替代型號。
- 新增獨立 write token/feature flag 的 job 與 Chirp repair 核准 API：需核准精確 revision、費用、reference 和明確確認；每 job 預設 US$10 ceiling；只寫既有 SQLite ledger，不會啟動 provider。API 預設關閉、未部署，沒有 browser token 或 worker integration。
- 本地 Python 3.12 驗證 **831 unittest／27 handoff pytest／54 frontend tests**、Next.js production build、compileall 及 `git diff --check` 全數通過。精確來源 SHA `2c3243ad976b3f1c45ca16a0c4cedc5392e50436` 的 [GitHub CI #38206197000](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38206197000) 與 [Release Compose #38206197011](https://github.com/Randytsay/course-transcript-mvp/actions/runs/38206197011) 均 PASS；以上皆非付費模型或 production UAT。
- Gate 維持 **G0–G7 PARTIAL、G8 NOT_RUN、0/9 PASS**；沒有 live provider 呼叫、IAM/網路變更、Drive/GCS 寫入或部署。仍缺真實模型/帳單驗證、持久 Worker、聽音及人工 Gold、review UI/Skill、Drive 和 staging 驗收。

## 3. P0–P8 真實進度和缺口

| 原階段 | 已實作／可證 | 缺口／不能宣告完成 |
|---|---|---|
| P0 基線及隔離 | GitHub 隔離分支／Draft PR；未破壞舊工作樹 | VPS production exact SHA、active jobs/leases、真正 DB 備份還原、media-inventory 分支整合 |
| P1 模型角色分流 | Vertex Gemini 3.8 text correction 與 Gemini 3.8 audio primary 分離 Adapter/Schema；模型政策單測／整體 CI | 兩種真實 Vertex 呼叫與相容性、IAM/region、功能旗標及正式切換 |
| P2 Gemini 全音訊主稿 | audio adapter、FFmpeg FLAC clipping、原始→normalized→clip SHA lineage、GCS opt-in、mock primary、重疊 seam 人工覆核及 SHA 綁定 | 真實 Drive 來源 attest、真人重疊聽音、長音檔完整 acoustic/VAD coverage、真 Vertex、VPS Worker |
| P3 Chirp 比對 | Gemini-first mock orchestration、文字差異、Chirp word-time、可信舊證據重用、新 Chirp fake-provider async submit/recovery；新增保守 VAD 正語音證據、費用/範圍精確核准、≤5 分鐘 Standard 修復 clip、GCS 不覆寫與重啟補傳、target-only merge，QA REVIEW 阻擋 compare | 真 Speech-to-Text async、可稽核帳單、常駐 Worker、長音訊完整聲學 coverage、真人聽音與雙引擎 UAT |
| P4 3.5 Transcribe | Vertex adapter、VERBATIM、≤15m 策略、選用模式/mock | 正式 SDK/模型可用性、真實 provider response 及用量、第三稿比對及 mode 路由持久整合 |
| P5 校對/Golden QA | 比對 REVIEW、Chirp 字詞時間 SRT/TXT mock 出稿、QA 記入 SQLite、原始 SHA 受保護的 seam review CLI；新增 source/revision-bound comparison review package/import | ChatGPT/WebCodex 對話傳輸、獨立 reviewer 身分與真人整堂聲學驗收、Drive 出稿全流程 |
| P6 實測評測 | 人工 gold CER 計算及 `scripts/golden_v2_benchmark.py` 配對引擎評測：同來源 SHA/window ID、reviewer reference、raw-evidence SHA、按類別 CER；未知成本/耗時不補零 | 真實金標樣本、三引擎相同音檔、國台語/經文/雜音、時碼/價格/耗時實測；helper 不認證 gold/reviewer/evidence 真實性 |
| P7 Skill/任務入口 | SQLite stages、重試/未知結果限制、唯讀 CLI、default-off status/write owner API、snapshot-first CLI、network-disabled local worker、`/golden-v2` 狀態/核准頁及精確 Drive 路徑唯讀 intake（Drive metadata 前後比對後才建立未核准任務）；Skill 草案 | 自然語言 URL parsing、paid executor integration、VPS continuity、ChatGPT/Skill handoff、真實 Drive／Cloudflare 驗證、staging identity |
| P8 UI/發布 | 本機 responsive `/golden-v2` 六階段狀態/預算核准與 source intake 表單 | 真帳本 browser UAT、Cloudflare 長請求/Drive 實測、音訊差異播放/人工裁決、staging/canary、V2 Drive 回寫驗證、正式上線 |

## 4. 剩餘工作重組成六個**完工波次**（現有 P0–P8 / G0–G8 不廢除）

### W1 基線與安全接軌（P0/P1）
- 確認實際 VPS 運行 SHA、活躍 job / lease、dirty worktree、V1/V2 分支衝突、復原方式；做完整 DB/data 備份及還原驗證。
- 補原始 Drive SHA → normalized audio SHA → clip SHA 的 source lineage；驗證使用既有私有 GCS 儲存桶及端點 IAM 所需能力。
- 完成 3.8 SDK/設定/狀態、相容性和可回退 feature flag 的非付費測試；實際付費 smoke 留到 W4 具來源預估的核准後。
- **交付／驗收：**可重現 isolation、備份還原證據、分支 reconciliation、無 V1 任務中斷；G0 確認 PASS，G1 非付費項完成。

### W2 真正可續跑的三引擎管線（P2/P3/P4/P7）
- 將 V2 `v2_*` ledger / primary adapter 接到真正私有 VPS Worker；在 provider 呼叫前嚴格核准 source+window+model+費用。
- 將既有 hardened Chirp Dynamic submit/async recovery/Standard repair 透過**V2 階段契約**接入，成功 evidence 重用；新增 offline Standard repair plan、精確範圍/費用核准及 crash-safe clip upload，但仍需接正式 Worker、invoice-bound usage reconciliation 及 live UAT。
- 第三引擎 QUALITY_3WAY 全文／SMART_2PLUS 差異片段選擇、費用限額和 disabled=zero-cost。
- **交付／驗收：**以 fake provider 在真 Worker 演練 source→Gemini→Chirp→optional third，kill/restart 無重送、無超額、無時間軸造假；G2–G4 工程整合部分完成。

### W3 長音檔、最終校稿與 Golden 出稿（P2/P3/P5）
- 音訊 overlap 合併需聲學錨點；字幕詞語時間對齊、漏段／經文／台語差異來源提示。
- 接回 ChatGPT/WebCodex 與已存在的 handoff，支援高可信自動裁決及無法辨識的短窗人工 review。
- SRT/TXT **同一份 finalized token/cue 決策出稿**、結構/時碼/Golden QA 驗收；嚴格封鎖所有 BLOCKED。
- **交付／驗收：**以含國台語與經文的離線已確認材料實測，長音檔、重啟、缺段、截斷、衝突均 fail-closed；G5 測試證據。

### W4 小額真實模型實測與客觀最佳化（P1/P2/P3/P4/P6）
- 使用者核准**具體音檔和預估費用**後，在原先建議的 V2 新增 US$10 累計上限內執行 3.8、Chirp 3、3.5 的短音檔 smoke，再逐步測多樣錄音。
- 建立人工核對 gold：正常國語、國台語混用、佛經密集、背景噪音／停頓、長音檔；統計 CER/WER、缺漏、字詞時碼偏差、費用、處理耗時、重新辨識率。
- 只有真實可用 API 和 Golden QA 通過後才把 G1–G6 缺失逐項收斂為 PASS；依實測決定預設 QUALITY_3WAY / SMART_2PLUS。
- **交付／驗收：**可比對 benchmark、真 provider raw/usage、金標修訂及費用上限證據；尚未核准就保留 NOT_RUN。

### W5 ChatGPT Skill／後台管理的正式操作入口（P7/P8）
- owner-only API：預檢→估費→核准→提交→查進度→疑點音訊 review→再驗收→安全發布；Skill 與 UI 共用同一個 durable job/ledger。
- 管理頁實作任務模式與費用、進度、三稿並排、音檔定位播放、逐段裁決、Golden QA、版本；電腦／平板／手機操作驗收。
- **交付／驗收：**ChatGPT 關閉、VPS worker 仍可獨立續跑；重新對話可讀取真實 job，不會虛構進度；G7 PASS，G8 UI 部分完成。

### W6 發布與正式切換（P8）
- 先 staging/canary 的 exact SHA、ARM64 Docker、健康檢查、舊 V1 job 兼容和回滾演練。
- 依核准範圍執行 Drive pending→verify→backup→promote→readback SHA；原始音檔、人工稿、已核定修訂不可靜默覆蓋。
- 使用者核准 production cutover 與 Drive mutation，逐堂小批量驗證，更新 RUNBOOK/STATE_MACHINE/DB schema，交付可維護日常 SOP。
- **交付／驗收：**真實來源檔→兩份最終字幕→Drive SHA 證據→Golden QA/知識庫一致，完整回滾可演練；G8 PASS。

## 5. 關鍵路徑及資源安排

**優先順序：W1 → W2 → W3 → W4 → W5 → W6。** 其中 W3 可與 W2 的 fake-model 開發並行；W5 的純前端草稿可先做，但不能跳過後端驗收直接正式上線。

真正阻擋完工的關鍵路徑：
1. **可信 VPS source/runtime/backup**：無法確認 production SHA，不能安全部署。
2. **V2 持久化 Worker + 真實 Chirp 非同步狀態**：目前已有本機一次性 Worker 模擬及 SQLite/adapter，尚未部署真正可連續運轉的 Worker；不能靠 chat session 當長工 runner。
3. **原音聲學完整性 + Gemini 長音檔 overlap**：重疊片段裁切已能在本機按原音區間逐段人工覆核並保留 SHA；但未驗證真人實聽或整堂 coverage，不能宣告已能完整處理長法會。
4. **ChatGPT/WebCodex 最終裁決與 publisher**：目前的 QA gate 有規則，但沒有真正整堂正式出稿/Drive 寫回路徑。
5. **經批准的真模型 smoke + 人工金標**：只靠 743 項 fake/offline 測試不能驗證台語/經文實際辨識品質。

## 6. 必須由使用者介入

- 當 Google Cloud Vertex/Speech API/GCS 的 IAM、Billing 或模型端點需要擁有人操作時，才請使用者核准（**不要求貼金鑰**）。
- 首次真實付費 smoke/benchmark，提供具體來源、單次預估、US$10 剩餘預算，由使用者授權；之前同意的是規劃，**不是未限制逐批扣款授權**。
- 只對模型無法確定的佛經／台語疑義呈現短音訊做人工裁決。
- 由使用者確認正式 V2 cutover 和 Drive sidecar 發布範圍。

其餘隔離分支開發、非付費測試、資料契約、QA、文件、PR/CI、修正可由 Agent 直接完成。**不能承諾 ChatGPT 對話結束後 Agent 自動持續開發，除非實際在 VPS 建立並證實持久化 Runner/排程。**

## 7. 下次開始的最小安全開工指令

1. 只讀檢查實際 VPS: current release SHA、active ASR jobs+leases、五個 git unavailable WebCodex projects 的來源與可恢復 Git checkout、DB/Drive version lock。
2. 於新的乾淨管理 worktree 接續 PR #143；不得 reset/clean/stash dirty source。
3. 已有本機來源 lineage 與 fake-provider Chirp async 狀態契約；接著補真實 Drive 來源 SHA attest、Speech-to-Text 非同步整合、完整覆聽與安全回復測試。
4. 每次更新本文件與 `GOLDEN_V2_GATE_STATUS.md`，寫 commit、CI、spent USD、Drive/prod mutated 與未解 blocker；保持 PR draft 到 Gate 真正收斂。

