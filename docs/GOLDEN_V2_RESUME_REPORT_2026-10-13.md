# Golden V2 接手與驗證 — 2026-10-13

本輪從遠端 `83c8b6b4fb981a55d4f3c0ad222ebffb7f8d923b` 接續 Draft PR #144。
使用 `/private/tmp/course-transcript-golden-v2-goal`，原 Mac dirty worktree 未改動。
正式 G0–G8 仍 **0/9 PASS**；工程測試與真正音檔 Golden 驗收分別記錄。

## 已交付的工程

- 依最新使用者指示，所有新 Gemini 設定使用 **3.8**。補齊 Drive intake/
  provider 管理頁、狀態 API、OpenRouter、fake provider 的殘留 3.7 預設；
  直接新付費提交舊 Gemini 型號被阻擋。歷史 raw、operation、費用與模型 ID
  保留；QA/路由報告讀真實模型證據，缺失時為 unknown，不能從預設推論歷史。
- 一般 V2 單階段受控派發：aggregate preview、独立可信 owner/provider/pricing
  驗證、即時 Drive metadata、精確 revision/source/stage/aggregate cap 的
  SQLite CAS，最後串到既有 primary/Chirp/third adapters。沒有自動核准、
  credential loader、付費 HTTP endpoint 或排程；不拿四分鐘授權啟動整堂來源。
- 四分鐘 smoke 的 raw 先於校驗/核銷保存，Chirp operation ID 先於等待保存。
  Gemini SDK 禁止隱藏自動重試，單次 timeout 180 秒。前一階段 receipt
  每次續送前讀回 SHA/identity；費用不明、受損證據、timeout 均阻擋重送。
- 三份完成 receipts 可輸出本機覆聽包：FLAC、Gemini 兩稿 TXT、同源 Chirp
  candidate SRT/TXT、差異 JSON、真人覆聽模板、HTML 播放器與 SHA manifest。
  片段時間只引用 Chirp，母檔加 600 秒；無錨點保留 null。包一律 REVIEW，
  不能發布或自動標為 Golden，也不把模型相似度稱為準確率。

## 目前驗證

- 本機 Python 3.12 全回歸 **986 unittest PASS**；handoff **27 pytest PASS**。
- 前端 **62 tests PASS**、Next.js production build PASS、production dependency
  audit **0 vulnerabilities**；Python compileall、CLI help、Compose web/offline
  worker config 與 diff check PASS。
- VPS 現有獨立 GenAI 2.29.0 容器只在 `/tmp` 測試 source copy，四分鐘 adapter/
  ledger/GCS/bundle **38 tests PASS**；全部 fake clients，沒有真 GCS/模型呼叫。
  沒有用新程式覆蓋 runtime 或改 V1 dependencies。
- 本提交 exact-SHA GitHub CI 要在 push 後核對；PR 的最新檢查是遠端證據。

## 已核對的 VPS 與 SQLite 還原

- V1 API/frontend/pipeline-worker release label：
  `adafd952b9b89db111730ab28e8e8baff9551021`；API/frontend healthy。
- 已有隔離 smoke 容器 label：`83c8b6b4fb981a55d4f3c0ad222ebffb7f8d923b`。
  三個階段全部 PENDING；provider_calls_started=false。
- 128 jobs：73 completed、46 awaiting_review、8 cancelled、1 failed。
  未到期 source lease **0**；未到期 review lease **0**。
- 52 筆 running stage 均隸屬上述已結束/待審 job（36 awaiting_review、15
  completed、1 cancelled）。保留原紀錄，未清除；尚未透過 provider live
  operation reconciliation 證明它們全數無未完成外部請求。
- SQLite 透過 read-only source connection 的 backup API 建立快照；跨 SSH
  取得同一 SHA 後在 Mac **獨立還原**，integrity_check=ok，所有資料表逐列相等。
  快照 25,694,208 bytes，SHA
  `ec0789b7c177014a8d7105437a20f5626a4813e8f8a0cbdc88bc2e49da322815`。
  [無秘密的還原證據](evidence/golden-v2-db-restore-20261013.json)。原始 DB/
  還原 DB 只保留私有暫存區，未入 Git。V1 資料庫與服務未切換。
- data/jobs 約 12 GiB；VPS 可用約 24 GiB。本輪未做完整 jobs artifact 備份與
  回復，因此 G0 不得 PASS。不要把 DB 還原當作 media/raw/volume 備份完成。

## 尚未完成、不得推測通過

1. 可信且獲授權的 V2 cloud identity/private bucket、live source/region/model/
   pricing entitlement；模型 metadata GET/離線 SDK 成功不是 paid inference 證明。
2. 真音檔 Gemini/Chirp/optional third 使用、實際帳單與人類 Gold；四分鐘實際
   receipts 尚不存在，不能產出真實三稿或正式 Golden 字幕。
3. 整堂 paid worker 的 runtime/scheduler/recovery 接線、完整覆聽、獨立 reviewer
   身分與 ChatGPT/Skill handoff；新增受控函式不代表持續運行的 worker 已部署。
4. 差異/裁決 UI 接真帳本的 browser UAT、V2 安全 publisher 整合、完整 jobs
   備份還原、staging/canary/rollback 與 production cutover 驗收。

本輪模型/GCS/Drive mutation **0**、新模型支出 **US$0**、production cutover
**NONE**；無 IAM/firewall 更動。付費、Drive、Production 的原授權邊界仍生效。
