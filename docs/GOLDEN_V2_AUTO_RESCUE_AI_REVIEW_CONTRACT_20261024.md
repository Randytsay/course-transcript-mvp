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
