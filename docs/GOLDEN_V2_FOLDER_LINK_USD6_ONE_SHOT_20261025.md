# Golden V2｜只貼 Google Drive 資料夾連結的日常工作 SOP（自動額度 US$6）

> 目標：新資料夾先盤點去重 → 可信報價 → **合計 US$6 以內自動核准** → 主稿 Gemini 3.8 Flash + Chirp 3 → 音字核對 QA → AI 校對 SRT/TXT 原資料夾交付；最終需具實際 Drive readback SHA 與獨立交付帳本 DELIVERED。
>
> **限制**：這是一份未發布到 Production 的候選版功能；不能因文件更新就宣稱新來源付費與新來源的全自動 AI-delivery 已完成端到端驗收。舊四筆已交付，原 Golden PASS 嚴格門檻不變。

## 永續的 US$6 規則

1. 使用者本次明確表示：**只要 US$6 以下就自動通過，不用再確認**。保守解釋是「每次使用者**主動貼出的一個資料夾**，全部音檔的可信估價／付費預留總和 <= **600 美分**」。不是「每支錄音 US$6」，更不准背景掃描其他資料夾後自動付費。
2. 預設不遞迴子資料夾；只有使用者明確要求才讀子資料夾，且仍算入同個資料夾的 US$6 預算。跨資料夾不得合併來源，絕對不可挪用先前補辨識四筆的 US$1 授權。
3. 透過原受保護 Owner CLI 的 `auto-budget-configure` 一次性在 V2 SQLite 登錄經 HMAC 簽章的政策。policy ID 固定為 `explicit-folder-600c-20261025`，模式只允許 `FLASH_CHIRP`（Gemini 3.8 Flash + Chirp 3），要求明確到期日、owner actor。可暫停（`enabled=false`），不從聊天記憶、環境變數或硬編碼將 US$6 當作真正付款授權。
4. 每筆新 job 使用 `auto-budget-authorize`（由已認證的工具代理執行，**不要求使用者第二次點同意**）前，必須已完成 source-preflight 與 `trusted-quote.json`，比對 source SHA / price_version / 有效期限 / Drive 來源路徑 / job revision / AWAITING_APPROVAL / 已經付款的其他同 SHA 工作。
5. 使用 SQLite BEGIN IMMEDIATE **原子檢查及記錄單資料夾 600 美分預留額度**，所有同資料夾的檔案共享額度，重試不能增加預留；每筆簽署獨立 `owner-paid-grant.json`，通過既有 paid worker 的簽章與 quota 查核；操作結果不明時不可重送。
6. **US$6 是模型費用的保守估計／已批准 provider reservation 的合計上限，不等於 GCP 最終發票上限**。目前 trusted quote 不含儲存、網路與附加雲端費用，超出可預估成本或 unknown quote 一律停下，不填 0。另受平台全域 exposure ceiling 制約，不能因政策自動批准而跳過全域限制。
7. 若 estimate 超過 US$6、source/quote 改變、政策過期/撤銷、全域費用安全閘超限、第三模型需要額外付款、帳本存在疑似重送或 provider outcome 不明，停止**付費**並一次性報告狀況。已完成的非付費檢查保留，不反覆要求貼連結。

## 下次收到 Google Drive 資料夾網址後實際操作

- **(A) 服務狀態**：`service-status`、`drive-health`；確認 Production SHA、intake 及 paid/delivery heartbeat 和 capabilities；必須在 `paid_execution=true` 才可出 paid authorize（此功能與 Worker running 不同）。
- **(B) 預覽去重**：`preview <URL>`，記錄 Google Drive ID、version、MD5、size，跳過既有 job、已發布 SRT/TXT；同名不同來源不要混。
- **(C) 可信成本**：對每個新音檔 `quote` → `quote-status` 到 COMPLETE；核對 `trusted-quote.json` 的來源 SHA、來源所在完整資料夾路徑、價格有效期限及合計金額。
- **(D) 一次性累計授權**：從 `auto-budget-status` 驗證可用的簽署政策；在所有引擎與定價證據符合時用 `auto-budget-authorize JOB --request-stdin` 傳送 `{"submitted_folder_path":"gdrive:<實際資料夾路徑>","confirm":true}`。所有音檔會共享同資料夾的 US$6 預算。
- **(E) 非重複辨識**：追蹤每支 job 的 paid readiness、Gemini 主稿、Chirp 3 時碼收據，未知 operation result 先對帳，不能直接重新提交。
- **(F) QA**：先完成所有可自動檢查的術語、語意、時間碼、漏段與 SRT/TXT 一致性。VAD 空窗不等於有漏字；Whisper 幻覺不能當原音。如果存在無法可信判定的音訊，保留帶時間碼的 REVIEW，不可虛構真人聽過。
- **(G) 交付**：只有在確實有對來源綁定的最佳 SRT/TXT、acoustic triage、完整歷史 provider DONE 與合法可交付內容時，才可由 Owner 安全入口建立 `ai-limited-prepare`、`ai-limited-authorize-publication`、`ai-limited-publish`（已公開授權的意圖可重用，但**每個發布 manifest 必須帶有效簽章**）。不可以呼叫腳本直接竄改 v2_jobs 或 Golden QA。
- **(H) 驗證**：每份新 SRT/TXT 在原始音檔同一 Drive 資料夾，新檔名含 `_AI校對_含待複核`，不可覆寫舊檔；保存 file ID、真正讀回 SHA、正式 SQLite `DELIVERED` 紀錄，保持原 Golden stage。需達嚴格 Golden PASS 者另走真實人工覆聽流程。

## 生產環境修復及測試狀態

2026-10-25：原停止的 intake Worker 已透過原 Compose 安全恢復，`intake_worker.state=running`。
目前 paid API / 標準 Golden publish API 仍停用；**不要**把 delivery worker 已運行等同於 paid API 已可接受新任務。AI-limited delivery API 已上線並完成舊四筆的 8/8 Drive SHA 驗證。
本 PR 實作可持久保存 HMAC 的 US$6 每資料夾預算政策、Owner policy/authorize 端點與 CLI、安全原子配額。並不包含新來源的通用 SRT 文字合併/自動 sound QA/全自動發布完整編排器；**不得宣稱「只貼網址」新案 end-to-end 已驗收**。在更新 Production 前必須依原流程完成 CI、備份/還原、部署核准、用一支新的低成本音檔做完整 smoke；付款授權只能在上述安全檢查通過後執行。
