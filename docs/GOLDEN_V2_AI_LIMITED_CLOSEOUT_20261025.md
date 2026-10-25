# Golden V2 — AI 校對交付獨立結案候選版

## 目標與品質責任

**以已存在的 SRT/TXT 作可稽核、清楚揭露不確定區間的工作成果交付；不可變造 Golden PASS。**

此版本保留每筆 V2 job 原來的 `NEEDS_REVIEW`、`chirp_independent=REVIEW_REQUIRED`、`golden_qa=PENDING`。
獨立交付等級明確為 `AI_REVIEWED_WITH_OPEN_ITEMS_NOT_GOLDEN`，不宣稱真人覆聽、逐字原音核對全面通過或獲得完整 Golden 認證。
早先的 `AI_REVIEWED_NOT_GOLDEN` 嚴格機器檢查合約仍然有效（要求全部疑點為零）；本版**不修改或重用**該稱號。

## 接手實況（2026-10-25）

本地唯讀正式資產 + SQLite shadow copy 實測四筆全部可以產生 `PREPARED`，沒有修改 Production Ledger：

| source | SRT cues | remaining open acoustic items | proposal SHA |
|---|---:|---:|---|
| 250503 | 3070 | 130 | `ab16af4a1d7a231cc612ac2fe2a2bfeabf29c89df6b8e734c3b549a2f467774a` |
| 251025 | 2399 | 10 | `b5b8e1c4357d949365064dc48b3a6b9c38c86d41186da2ca56f97b3d5f76340b` |
| 251018 | 3840 | 25 | `fbe9b1de227467330823311ab088bd1611554c077bbc76107683fd8e75f05a49` |
| 261017 | 3035 | 9 | `ec9fcad47aab8e8785b0b220be4b6d1fea5554e2d6b448546c037c6efd8d9c30` |

「open acoustic items」來源為完整聲學檢查的 `REVIEW` 與未覆蓋的 `SPEECH_CONFIRMED`，
不是確定漏字件數。四筆現有 Golden workflow 狀態均為 `NEEDS_REVIEW`。
`261017` 特別使用 Drive 原有 3035 cues 的較完整 SRT，不採用 VPS 3183 cues 的另一候選稿；有來源 SHA 和 TXT 對位驗證。

## 工程契約

- `prepare_limited_manifest`：檢查正式 ledger 同一來源 SHA、來源 Preflight、
  所有 Chirp operations 的已結算 `DONE` 收據、SRT/TXT 逐條一致，
  acoustic triage exact SHA、時間碼解析/不重疊。所有未裁決聲學項目必須誠實保留。
- `register_proposal`：CAS 狀態和 manifest SHA，在**獨立** SQLite
  `v2_ai_limited_deliveries` 存 `PREPARED`，不修改原 stage/job，具有冪等防重。
- `finalize_readback`：只允許受信 Owner 層**驗證獨立簽章 grant**、
  且已經使用同一全域 Drive 鎖、安全檢查來源 version/MD5 與新檔名、完成雙檔
  實際上傳及 readback SHA 驗證後呼叫；其獨立回執為 `DELIVERED`，
  並再次驗證 Golden job CAS 沒有變動。
- Owner API 現已接入 `/api/v1/golden-v2/jobs/{id}/ai-limited/`：
  `status`（僅查詢）、`prepare`（完整 source QA 預檢與獨立帳本登記）、
  `authorize-publication`（登入 Owner 對精確 manifest、來源與輸出檔名簽 HMAC）、
  `publish`（獨立 source MD5/version 校驗、全域 Drive 鎖、不可覆寫同名檔、
  persisted pending ID、安全雙檔上傳、實際 readback SHA、獨立 DELIVERED receipt）。
- **預設關閉**新 API；必須由 Production owner 在部署核准後明確啟用
  `COURSE_TRANSCRIPT_V2_AI_LIMITED_API_ENABLED=true`，
  沒有密鑰/授權或目標內容衝突時均 fail closed。
- 此 PR 僅為可驗證的 **待部署候選版**；截至今日未修改 Production 新版本或
  正式 SQLite `v2_ai_limited_deliveries`。四筆已在 shadow ledger 做 PREPARED，但
  **還沒有正式 DELIVERED receipt**；不能宣稱四筆 production 已正式結案。
- 舊 `Golden PASS`、`confirm_listened` 與舊 `authorize-publication`
  路徑完全保持原樣。
- 不執行 Vertex/Gemini/Chirp/新版付費辨識，無意外新雲端費用。
- 失敗不得清空/回滾較新 paid provider receipt；SQL 正式備份只用於獨立評估和 disaster recovery。

## 進度/限制

核心帳本與上傳路徑離線回歸 21 PASS，包含重跑冪等、防音檔/QA 漂移、
防未完成付費操作、防假簽章、同名檔衝突、暫時網路中斷後沿用持久 Drive ID、
雙檔 readback 完成前不得 DELIVERED。
2026-10-25 最新四筆 source-bound shadow acceptance 4/4 PASS，正式 Golden stages 完全不變。
這是**開發候選版**，不是對新 Commit 的 Production 上線授權。
