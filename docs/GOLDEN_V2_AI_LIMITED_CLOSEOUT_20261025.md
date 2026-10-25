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
| 250503 | 3070 | 130 | `228ccfb7bfde6a17ea50848e6566b7cf9160fdb138ce6fdd9a6abe7c8d164d76` |
| 251025 | 2399 | 10 | `89a75f7507848c0fa7ce83a3566f333042f826f4854cc70e9f3f22906ac02fad` |
| 251018 | 3840 | 25 | `76c8f9ee3f0c581ddaf2f62a2828f298513a91e1935d179d81ff2b0a6f8343d1` |
| 261017 | 3035 | 9 | `3fe89da613ac3fc262697321bd3d92735f2067d54c2e86487a303ec144e0bfec` |

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
- **此 RC 只有核心證據/帳本 contract，尚未連接可信 Owner API endpoint、
  永續簽章產生器和 remote Drive gateway**；故不得宣稱「四筆 Production 已正式交付結案」。
  對外發布前必須另補接口測試、確認 production owner approval 與回復機制。
- 舊 `Golden PASS`、`confirm_listened` 與舊 `authorize-publication`
  路徑完全保持原樣。
- 不執行 Vertex/Gemini/Chirp/新版付費辨識，無意外新雲端費用。
- 失敗不得清空/回滾較新 paid provider receipt；SQL 正式備份只用於獨立評估和 disaster recovery。

## 進度/限制

新核心單元測試 5 PASS，包含重跑冪等、防音檔/QA 漂移、防未完成付費操作、
防偽造金章/人耳註記、無 grant/readback 不得 DELIVERED。
2026-10-25 四筆正式 shadow acceptance 4/4 PASS，production 正式 job/Golden stage 沒有變更。
這是**開發候選版**，不是對新 Commit 的 Production 上線授權。
