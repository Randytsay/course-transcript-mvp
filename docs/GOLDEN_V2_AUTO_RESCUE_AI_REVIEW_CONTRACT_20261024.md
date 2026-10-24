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

正式可交付前的**剩餘工程**：將此 validator 與可信 ledger
SHA 檢查接到獨立 `authorize-ai-publication` API，完成 AI 專用
signed grant 驗證、Driver 源檔 freshness、new-file-only 上傳、
重入去重與 Drive hash 讀回，再做 mock 故障注入驗收。
這一步沒完成之前，不得在 UI 顯示「已回寫」。
