# Golden V2｜Worker 自動救援與 AI 校對版契約（2026-10-24）

## Release scope / 安全邊界

**此分支尚未部署正式環境。** 所有測試必須在無 Google 模型 API、
無 Drive 寫入的環境完成；實際啟用需按 exact SHA 重建 paid worker。
不可以在未對帳目前運行中/未知結果的付費工作前重新啟動 paid worker。

### Known-refusal auto rescue

Paid worker 在 `--auto-rescue-known-refusal` 明示開啟時：
1. 僅挑選已獲簽署核准、Gemini `REVIEW_REQUIRED` 且 Chirp `PENDING`
   的 `NEEDS_REVIEW` 任務；必須能從不可變 Gemini 原始回應證明
   `SAFETY` 已知拒答，否則不得進入下一步。
2. 以原 job/source/revision、已核准預算、可信 quote、當前 Drive 檔案身分
   確認 `claim_independent_rescue`（SQLite CAS），只執行一次 Chirp
   規劃與提交。全域 aggregate cap 與單檔 owner grant **兩者都要符合**，
   不能把全域上限當成單檔使用者授權。
3. 對 Chirp `RUNNING` 且 Gemini 仍在 hold 的任務，只執行 GET-only
   已儲存 operation 的恢復；不觸發新的模型 POST。當 provider operation
   `SUBMITTING`/未知費用、聲學空窗、需人耳裁定時，原始 evidence
   繼續保存且 fail closed。
4. 此救援**不會**假定 Gemini 原聲覆蓋完整、不會自動把
   `gemini_primary` 變 `COMPLETE`、也不會以 Chirp 結果冒充 Golden PASS。
   真實的音訊/字幕完整性仍需要後續合格 QA。

### AI 校對品質分級 / 機器驗收

`AI_REVIEWED_NOT_GOLDEN` 是和 `Golden PASS` **完全獨立**的品質等級：
- AI 憑證須來源、Chirp 字詞時間碼、acoustic QA、雙檔 render manifest 的
  SHA-256 證據全部綁定；timeline、acoustic、semantic 與 SRT/TXT
  consistency 均為 PASS；`missing_windows`、
  `unverified_audio_intervals`、`unresolved_semantic_conflicts`、
  `unverified_srt_cues` 必須全部為 0。
- 任何不明的可聽音訊區間不得以「靜音」帶過。AI 証書明確記載
  `human_listening: NOT_CLAIMED`、`golden_acceptance: NOT_CLAIMED`。
- 未經最終經過 ledger/不可變檔驗證的 AI 凭證**不能當成授權**。
  校對成果不能直接呼叫 Golden 交付路徑；禁止傳
  `confirm_listened: true` 或覆寫既有 `.srt`/`.txt`。

### AI 校對版交付契約（準備供後續 API / Delivery Worker 接入）

必須由完全分離的 owner HMAC signed grant 指定 `quality_tier`、
`job_id`、`source_sha256`、`certificate_sha256`、
`render_sha256`、`revision`、`actor`、`owner_confirm` 和
**兩個具 `_AI校對` 後綴、原資料夾的新建檔名**。
新建目標數量查詢必須為 0；有同名衝突就停止，不能改名/覆蓋。

### 本 PR 的第二階段已接入（可測試，但正式預設關閉）

- `app/pipeline/golden_v2_ai_delivery.py`: ledger Chirp COMPLETE、
  原始 Chirp/acoustic SHA、機器 QA 報告、SRT/TXT render manifest
  互相校驗；AI 校對證書只能由真實檔案組裝；提供獨立
  `attest_ai_review`、`authorize_ai_publication` 與
  `publish_granted_ai_outputs`。
- 管理員 owner API `attest-ai-review` 及
  `authorize-ai-publication` 使用現有私有 token，不含
  `confirm_listened`，不得混用 Golden publish grant。
- Delivery Worker 支援原資料夾新建 `_AI校對.srt` 和
  `_AI校對.txt`，預先存下 ID、斷線後恢復同一 ID、
  SHA-256 回讀與來源版本再次檢查；遇到同名檔絕不覆蓋。
- API 預設 `V2_CHAT_AI_PUBLISH_API_ENABLED=false`，交付
  Worker 仍須 `V2_CHAT_AI_DELIVERY_ENABLED=true` 才啟用。
  此兩個開關彼此獨立，且任何交付仍須每任務額外的
  signed owner grant。
- 可用 `python -m unittest tests.test_golden_v2_ai_delivery
  tests.test_golden_v2_ai_review_contract` 進行離線測試。

### 可驗證的機器 QA 準備與對話指令

- 新增 `prepare_ai_quality_report`：從實際 ledger
  `golden_qa COMPLETE`、`safe_publish_allowed=TRUE`、
  Chirp/acoustic SHA、最後的 SRT/TXT content hash 及
  嚴格可解析的時間軸自動組成 `ai-review-report.json`，
  再生成不可冒稱 Golden 的 AI 證書；未完整通過 QA
  不會生成假 `PASS`。
- 新增 owner-only `prepare-ai-review`、`attest-ai-review`、
  `authorize-ai-publication` API，從 `scripts/golden_v2_chat_client.py`
  傳 JSON 可操作。交付仍需獨立 owner signed grant。
- Delivery worker 的 AI 發布採獨立環境旗標預設關閉。
  此版本可對通過 Golden **機器 QA**、但未宣稱人工覆聽的
  SRT/TXT 生成 AI 校對版；`Golden` 認證依舊另有
  嚴格人工流程。

### 上線前必須補齊的功能

**最重要：獨立於 Golden final QA 的 AI-only renderer/QA 還未完成。**
目前後端只會在 Golden QA 通過時保存正式 `final.srt` /
`final-corrected-transcript.txt` / `render-manifest.json`。
目前已新增以 Golden 機器 QA PASS 為可信來源的 AI 報告產生器，
但它還無法在 Golden `REVIEW_REQUIRED` 時，自行產生經聲學與語意驗證的獨立候選字幕。不能將
`ai-review-report.json` 的字串 `PASS` 當成實際原聲證據。
應另以既有 Chirp raw operations、聲學 PASS、Gemini 或另一合法
語意證據、差異裁決、可追溯修訂，無新付費前提下形成
完整候選檔；有真實缺漏時產生時間碼清單，不可自行宣稱
machine QA PASS。

因此本 PR 保持 Draft；通過單元測試不代表已完成
`250503` 字幕，也不能直接重啟正式 paid worker。
正式發布應先在無付費請求的 shadow smoke test 驗證來源、
未完成 LRO、費用上限、備份／還原、舊任務／new job 隔離，
經 Owner 明確核准後再以 exact SHA 佈署。


## 2026-10-24 新增：Chirp 原始時碼救援與 AI 草稿

- 舊 Worker `reconcile_reviewed_chirp_window` 會將 offset 異常直接保留
  REVIEW；`finish_approved_chirp` 的時碼錨點檢查亦會被
  缺失 offset 例外中斷。現採 **確定性的本地無損修復**：
  僅已存在不可變 raw、已知越界／重疊錯誤、有原始合法時間錨點、
  字元完全保留、時碼單調且在來源窗格（+250ms 容差）範圍內時
  允許將單窗轉為 DONE。保存每一修復 note、raw digest 和
  `acoustic_timing_verified=false`，不重送付費服務。
- 正式資料的**隔離帳本副本**測試：`250503` 的 4 個被隔離分段
  `chunk-0001/0003/0004/0005` 均能本地無損恢復；
  13/13 個分段有可驗證的來源字詞紀錄。模擬整段合併得
  32,212 Chirp 字詞單位，時間軸末端 10,800.160 秒，
  **聲學閘依然 REVIEW**，並沒有冒稱完全驗收。
- 新增 `AI_DRAFT_REVIEW_REQUIRED` 候選稿路徑：
  2,860 條 SRT、對應 TXT、SHA-256、immutable manifest 與
  原始 Speech 結果 ancestry；強制 `publication_allowed=false`，
  `semantic_gate=REVIEW`、`acoustic_gate=REVIEW`，
  不假冒 `AI_REVIEWED_NOT_GOLDEN` 或 `Golden PASS`。
- 本地救援 Worker 如果 Chirp 全部回來但真實 acoustic QA 阻擋，
  可自動整理上述候選稿，使後續校對不必重新 ASR。
  候選稿產生不會提交 provider 請求、修改原音或回寫 Drive。

**剩餘的聲學驗收與語意裁決不可由時間碼歸一化等同 PASS。**
候選稿可供校對，不可自動外發。Production Worker 尚未更版。

### 250503 真實聲學抽查（正式 Drive 與帳本未修改）

在同版 V2 runtime、無網路、唯讀掛載 source/ledger 的隔離容器中，
對全部 13 個已保存 Chirp 操作先安全還原（4 quarantine windows 無損），
再執行本地 `inspect_v2_acoustic` 的**前 12 處**長空窗抽查：

- 全段候選稿計 **146 處長空窗**（聲學 QA 的 gaps_examined 資料）。
- 其中 **2 處 SPEECH_CONFIRMED**：
  `239320..262240 ms`（03:59.320–04:22.240）、
  `745520..762920 ms`（12:25.520–12:42.920）。
- `REVIEW` 144 處，內含 **134 處因設定的初輪上限而尚未探測**；
  另外抽查 10 處聲學證據仍不足，不能當成靜音。
- 此測試沒有產生全部 146 處的最終聲學 verdict。
  這兩處明確有語音的空窗應交給有剩餘預算且正式授權的
  **定點語音補辨識**，不是重新 POST 全部 Chirp 13 個窗格。
- 不得以 `AI_DRAFT_REVIEW_REQUIRED` 冒稱 `AI_REVIEWED_NOT_GOLDEN`
  或本檔正式 SRT 已交付。
