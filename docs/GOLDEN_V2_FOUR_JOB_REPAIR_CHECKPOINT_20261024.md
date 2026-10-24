# 2026-10-24｜Golden V2 四筆錄音正式修復 Checkpoint

**結論：四筆字幕尚未 Golden / AI_REVIEWED 驗收完成。**
本次已把 250503 的 4 個正式 Chirp quarantine window 修復成 DONE，
並將四筆全長錄音的候選長空窗做 provider-free 聲學檢查及局部 FLAC 切分；
這是實際完成的修復工作，不是只讀盤點，也不是最終語音補字。

## 修復前置與原始證據

- 正式執行版本仍是 `17d33faf6239ca5a1fcdfe91f18cef202f271b0f`；
  PR #149 為 Draft，尚未合併／正式部署。
- 精確來源：`250503` job
  `v2p_f8505d06c77f5789aa4bba9b461ee1f3`，
  source SHA `806c867e5e1cab13228bb8a77f7fee2ef072b3290b4d3fa53c9ae09bb1ebeb69`。
- 正式 SQLite 先做一致性備份：
  `/app/data/golden-v2/backups/ledger-pre-250503-lossless-reconcile-20261024T135848Z.sqlite`，
  SHA-256 `3171d2638de1dbd5defcc60774eeaf9d31987a43058eff64a93c5f2715fb3612`，
  備份與事後正式 DB 都通過 SQLite integrity/quick check。
- 使用當前 PR #149 經 157 項測試的 lossless window repair source，
  在相同版本 runtime image、`--network none`、只讀掛載程式與
  掛載已驗證帳本/證據目錄執行，未更版正式 Paid Worker。
- 原始 `chunk-0001/0003/0004/0005` 保存的 provider-raw、
  result SHA、operation、實際記帳成本均經 CAS 核對；
  已於正式 ledger `REVIEW_REQUIRED → DONE`，全部 **0 字元遺失**，
  並明確保留 `acoustic_timing_verified=false`。
  **250503 13/13 Chirp windows = DONE**，但 stage `RUNNING`，
  Gemini `REVIEW_REQUIRED`，後續 QA/發布仍未 PASS。

## 全四筆實際 FLAC 聲學 QA（所有候選長空窗已掃描）

| job | 長空窗 | SPEECH_CONFIRMED | NONSPEECH_CONFIRMED | REVIEW | 未掃描 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 250503 | 146 | 17 | 0 | 129 | 0 |
| 251025 | 75 | 3 | 62 | 10 | 0 |
| 251018 | 164 | 2 | 137 | 25 | 0 |
| 261017 | 149 | 3 | 137 | 9 | 0 |
| **合計** | **534** | **25** | **336** | **173** | **0** |

`SPEECH_CONFIRMED` 是局部原音 RMS/VAD 與語音相容的聲學證據，
**不是 lexical evidence，不足以直接填字或宣稱整段完整漏講**；
`NONSPEECH_CONFIRMED` 是現行本地聲學閘輸出，不等於人工確認靜音。
任何 `REVIEW` 應維持待處理，不以 heuristic 直接通過 Golden。

本地 QA SHA-256：
- 250503 `2248fbec086d53a56a4ed9b21aa45ad7451eccc656cc3d3bdc4be3c833e289d5`
- 251025 `cac76cdc565696dbad1b778c3028b8b8149467dfe6c6a3b35062bc93aa85c099`
- 251018 `438d5a9fb5da0569ec664f8a74f6a9e942a4cfa5fa9ee189bb4d193a28e85af3`
- 261017 `f67f6cf7cf3aad6724c6bbbf61dea04029d99d5cf7c10f0bde9147f56ac8f91f`

## 有正面語音訊號的局部候選音訊已實際切檔

利用 `prepare_local_repair_clips` 及已存在來源 FLAC，逐個
驗證剪輯長度和 SHA-256，保存於每筆
`golden-v2/review-only-clips-20261024/`；
不可當成已審核的修復方案，尚未上傳 GCS。

| job | 切片數 | 總局部音訊 | 保守 Standard Batch 估價 |
| --- | ---: | ---: | ---: |
| 250503 | 17 | 424.32 秒 | US$0.21 |
| 251025 | 4（3 個長空窗，1 個拆兩片） | 785.76 秒 | US$0.23 |
| 251018 | 2 | 451.52 秒 | US$0.13 |
| 261017 | 3 | 56.64 秒 | US$0.04 |
| **合計** | **26** | **1,718.24 秒** | **US$0.61** |

每片具 `clip_sha256`、`source_sha256`、精確 start/end，
已在正式來源目錄重新讀回校驗。報價使用
`standard_chirp_cents` 2026-10-14 保守 list pricing；
**只是修復作業的試算，不是帳單，不含 GCS 等額外費用**。
每筆都設 `submit_authorized=false`、`gcs_uploaded=false`、
`provider_calls_started=false`。此前已存在的 Chirp LRO 沒有重送。
本輪新增計費：**US$0**。

## 正式交付狀態與必要解鎖

- `250503` 已保存 `AI_DRAFT_REVIEW_REQUIRED` SRT/TXT 2860 cues，
  32212 Chirp 字詞單位，draft manifest SHA
  `b6059b69dfff3e8c21d1eacea399c93c991152df1f7cad7fdfb5bd1722100ba5`；
  Drive 原有術語修正版不覆寫。
- 251025、251018、261017 仍 `NEEDS_REVIEW`；Drive 已有
  SRT 修復校字候選檔，但不是正式 Golden QA PASS。
- 局部補辨識下一步需要對每個 candidate gap 完成原音
  聲學＋既有 Gemini/Chirp/同目錄逐字稿的精確 lexical target，
  並經該**來源及 Standard repair** 的有效 owner grant、quote、
  stage claim、總額上限和 GCS/provider receipt 對帳；
  不得把原工作 global cap 或 VAD 結果冒充付費授權。
- 原有付費 stage `claim` 不可由 hash 反推；`claim_next`
  明確不允許重複 claim 正在 RUNNING 的 paid stage，
  `reclaim_stale_local` 亦明確排除付費 stage。
  不得透過 SQL 偽造 claim 以送出修復；需安全的受控重入契約，
  這是 PR #149 尚需補足的 paid-resume 聯動工作。
- 待音訊漏段補齊及 machine semantic/acoustic QA 正式驗收後，
  才進行獨立 AI_REVIEWED 或 Golden 交付的 signed grant、
  Google Drive 新版 SRT/TXT 回寫及 readback SHA。
