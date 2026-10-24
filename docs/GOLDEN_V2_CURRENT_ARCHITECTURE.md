# Golden Transcript V2 — 現行架構與執行契約

> **現行 V2 架構的單一權威入口。** 觀測基準：2026-10-25 00:42 Asia/Taipei。結合正式映像、唯讀 Ledger 及容器觀測，明確區分「規格／實作／部署／實際驗收」。**本文件不代表 Golden QA PASS、雲端帳單或 Drive 已正式交付。**
>
> GitHub: Randytsay/course-transcript-mvp。正式已觀測 release SHA：17d33faf6239ca5a1fcdfe91f18cef202f271b0f（integration 分支已合併 PR #148）；VPS 正式路徑：/opt/course-transcript-v2-chat/releases/<exact-sha>。WebCodex project: agent:instance-20260503-1541-4d804cfebc0e4980:golden-v2-policy-dev-49620326。
>
> **閱讀順序：** 本文件 → [獨立 AI 全面審查交辦書](GOLDEN_V2_INDEPENDENT_AUDIT.md) → [G0–G8 驗收標準](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md) → [歷史 Gate 紀錄](GOLDEN_V2_GATE_STATUS.md) → exact-SHA 程式與原始 runtime/Drive 證據。GitHub 主分支、已部署 release 與帶未提交修改的開發樹不是同一版本。

## 1. 系統目標與不變原則

- 從 Google Drive 指定佛學長音檔，產生忠實繁體中文 SRT、校訂逐字稿 TXT、QA 證據、經驗證的原資料夾交付。
- full-audio Primary 使用 Gemini 3.8 Flash，Chirp 3 **獨立辨識原音**、提供時間權威；Gemini 3.8 文字校正走**不同的 text-only adapter**，不可把音檔送入校正 adapter；第三模型 Gemini 3.5 Transcribe 需另按模式/授權驗證。
- 音訊真實高於經文／參考逐字稿。LLM 不可加上沒有被說出的字，也不能推算或改寫 Chirp 的時間碼。
- 過去成功的付費 chunk/LRO 不可重投。原始 provider raw、operation_name、result SHA、source/clip identity、receipt、實際費用紀錄不可破壞。疑似未知付費結果凍結並對帳。
- Chirp operation DONE ≠ 聲學完整性 PASS ≠ Golden QA PASS ≠ 已完成 Google Drive 正式交付。

## 2. 由正式程式重建的資料流

~~~text
ChatGPT / Owner → V2 API（Drive URL 預覽、quote、job status、grant）
  app/golden_v2_chat_api.py / app/golden_v2_routes.py / golden_v2_review_routes.py
  ↓
Drive 精確來源與修訂 preflight → 標準化 FLAC、duration/hash、估價
  app/pipeline/golden_v2_drive_intake.py / golden_v2_quote_intake.py
  app/pipeline/golden_v2_source_preflight.py / providers/golden_v2_audio_staging.py
  ↓
隔離 V2 Ledger（source SHA、revision CAS、stage claim、額度、raw receipts）
  app/jobs/golden_v2_ledger.py
  ↓
Paid executor（已核准且證據相符才 dispatch；遇未知 outcome fail closed）
  app/pipeline/golden_v2_paid_executor.py / golden_v2_trusted_dispatch.py
  ├─ Gemini 3.8 Flash full-audio 主稿與 completeness
  │   app/pipeline/golden_v2_primary_stage.py
  └─ Chirp 3 原音獨立辨識與 timestamp、LRO 恢復、word merge
      app/pipeline/golden_v2_chirp_stage.py / golden_v2_chirp_recovery_worker.py
      └─ acoustic completeness → 有界 Standard 局部修復
         providers/golden_v2_acoustic_qa.py / pipeline/golden_v2_chirp_repair.py
         REVIEW_REQUIRED 時保持阻擋，不得假定為 COMPLETE
  ↓（provider 與聲學證據安全完成，才能繼續）
compare → [third_optional] → adjudication → golden_qa
  app/pipeline/golden_v2_coordinator.py / golden_v2_stage_runner.py
  providers/golden_v2_compare.py / golden_v2_final_gate.py
  ↓（QA PASS + SHA-bound manifest + exact owner publication grant）
SRT / TXT render → Drive pending/verify → 核准舊版備份 → promote/readback
  app/subtitles/golden_v2_render.py / app/pipeline/golden_v2_delivery.py
~~~

六個正式 Ledger Stage 的**實際固定順序**定義在 app/jobs/golden_v2_ledger.py 的 STAGES：gemini_primary → chirp_independent → compare → third_optional → adjudication → golden_qa。來源核准、acoustic QA 與 Drive publication 是額外控制閘，不要誤稱成同表獨立 Stage。FLASH_CHIRP 可合法 SKIP 第三模型。

**決策／程式的差異**：2026-10-14 的 owner 驗收決策允許 Gemini 與 Chirp 在共同 preflight 後獨立並行啟動；而 17d33faf 版目前的 Ledger claim_next / paid executor 依固定 Stage 順序派發。這是需要獨立 Agent 審核的已知規格—實作落差，不得在文件中假稱已實現並行。

## 3. 檔案權責表

| 層級 | 目前實作 | 要審查的契約 |
|---|---|---|
| API / owner | app/golden_v2_chat_api.py、app/golden_v2_routes.py、app/golden_v2_review_routes.py | route flag、actor、signing grant |
| Drive/intake | pipeline/golden_v2_link_discovery.py、golden_v2_quote_intake.py、golden_v2_drive_intake.py、golden_v2_source_preflight.py | 來源版本與 content identity |
| Ledger | app/jobs/golden_v2_ledger.py、golden_v2_read.py | stage/CAS、費用、重入、證據不變性 |
| Paid runtime | scripts/golden_v2_paid_executor_daemon.py、pipeline/golden_v2_paid_executor.py、golden_v2_runtime_services.py | 全域鎖、授權、付款防重複 |
| Gemini | pipeline/golden_v2_primary_stage.py、providers/gemini_primary_audio.py | 分段、輸出截斷、完整性、真實 model ID |
| Chirp | pipeline/golden_v2_chirp_stage.py、golden_v2_chirp_recovery_worker.py、golden_v2_chirp_window_repair.py | LRO 回執、word end、合併文字不遺失 |
| 補辨識/聲學 | pipeline/golden_v2_chirp_repair.py、providers/golden_v2_acoustic_qa.py | 真正有聲缺口與低成本修復 |
| 比對、裁決、Golden QA | pipeline/golden_v2_coordinator.py、golden_v2_stage_runner.py、providers/golden_v2_final_gate.py | 聲學 PASS、人工聽審證據、字幕全文一致性 |
| 發布 | app/subtitles/golden_v2_render.py、pipeline/golden_v2_delivery.py、scripts/golden_v2_delivery_daemon.py | 產物 checksum、owner grant、舊檔備份、Drive SHA readback |
| Deployment | deploy/golden-v2-chat-compose.yml、deploy/golden-v2-paid-compose.yml | API/Intake/Paid/Delivery 實際版本及憑證掛載 |

**任何檔案存在不等於該功能已在此任務上完成。** 審查必須以 Ledger revision、immutable artifact source SHA、provider receipt、runtime 紀錄和遠端 metadata 做交叉確認。

## 4. VPS 路徑與運行現況

- 與 V1 隔離的 Compose 專案：golden-v2-chat；API loopback 127.0.0.1:8816；獨立 release 在 /opt/course-transcript-v2-chat/releases/<sha>。
- 正式 SQLite 在容器：/app/data/golden-v2/ledger.sqlite；主機掛載：/opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite；原音與任務證據在 /app/data/golden-v2/jobs/<job-id>/。
- 原始 Chirp window evidence 通常在 jobs/<job-id>/golden-v2/chirp-evidence/<window-id>/；候選 acoustic QA、合併與最終 Golden QA 有不同檔案與 SHA，不得以舊 QA 判斷新候選。
- 正式及救援備份可能散在 /opt/course-transcript-v2-chat/backups 和 data/golden-v2/recovery-backups；只列檔不等於驗證可還原。
- **2026-10-25 00:42 唯讀 Docker 觀測**：API 映像 17d33faf、Healthy；Paid/Delivery 映像 17d33faf、Running；Intake 舊映像 8748404、Exited。獨立 Intake heartbeat 檔仍更新：必須對照 process PID、容器和來源，不得只看 heartbeat 宣稱 Intake 正常。
- 現行開發分支 codex/golden-v2-goal-completion（HEAD 9506e4f）有未提交 API/Intake/預算相關變更，**不是上述正式 release**；對其做任何操作應另建隔離工作區，不得 reset/stash 覆蓋。

## 5. 四筆任務現況快照（2026-10-25 00:42，非永久即時狀態）

| 音檔 | job_id | 狀態/修訂 | Gemini | Chirp | operations 與下游 |
|---|---|---|---|---|---|
| 251025 | v2p_c8f1e1cb3ecf53ccb1eae33b5ab221be | NEEDS_REVIEW rev15 | COMPLETE | REVIEW_REQUIRED | BASE 10 + REPAIR 2，12/12 DONE；compare/adjudication/Golden QA PENDING |
| 251018 | v2p_560465ab6db05e3da593837f4d76c630 | NEEDS_REVIEW rev21 | COMPLETE | REVIEW_REQUIRED | BASE 15 + REPAIR 3，18/18 DONE；下游 PENDING |
| 261017 | v2p_62a124bdd4595aca91c53085c3732ca7 | NEEDS_REVIEW rev12 | COMPLETE | REVIEW_REQUIRED | BASE 13 + REPAIR 1，14/14 DONE；下游 PENDING |
| 250503 | v2p_f8505d06c77f5789aa4bba9b461ee1f3 | PROCESSING rev5 | REVIEW_REQUIRED | RUNNING | BASE 13 DONE；REPAIR 11 PLANNED + 1 REVIEW_REQUIRED；下游 PENDING |

前三筆最近聲學候選合計 **49 項未解決**（12/25/12），其中 4 個區間確認偵測到語音。第四筆先前 acoustic 候選曾報 146 阻擋；**新一輪 repair 開始後必須重新核驗，不能套用舊值**。四筆都沒有觀察到 Golden QA PASS 的證據，不能宣稱已交付。Ledger actual_cents 不是供應商最終帳單。Google Drive 曾有人工／候選 SRT/TXT，最新狀態仍需 GET metadata / revision / readback 獨立驗證。

## 6. 長期無法結案的已驗證瓶頸與待證假設

**已驗證的流程阻擋：**
1. 前三筆 Chirp operations DONE，但 acoustic gate 仍 REVIEW_REQUIRED，控制器不推進 compare/adjudication/Golden QA。
2. 第四筆有 word end 等原始時間證據疑點；單筆 REPAIR REVIEW_REQUIRED，其餘 11 筆 PLANNED，不能重送已提交操作。
3. Intake 舊容器 Exited 而 heartbeat 檔更新，運行身分與訊號存在歧義。
4. 曾存在過長字幕與 audio/text 對齊問題；只看 SRT 結構合法不足以判斷真正品質。
5. CI/容器 healthy、候選 Drive 檔、provider DONE 均不等於出版合約完成。

**待審查的根因假設（不要預先當作已證實）：**聲學演算法將短停頓誤判、word timestamp 尾端解析或 overlap merge 丟字、候選與 QA SHA 錯配、缺乏真正音檔複核/可驗證聲學裁決、Stage/revision CAS 重入契約死結、owner grant/Drive source version 漂移、背景 worker 版本/heartbeat 假活、已完成修復無法滿足過嚴或錯誤的 QA Gate。

## 7. 結案定義與唯讀稽核範例

每筆獨立驗收：source SHA 綁定 raw → 完整覆蓋/word timing 與真實有聲證據 → 比對/裁決/Golden QA **對當前候選** PASS → SRT/TXT/manifest SHA 全同 → 合法 owner publish grant → 先備份已核准舊檔、Drive pending/readback、最終 SHA 與版本核對 → Ledger/交付 receipt 可驗。失敗時保留 REVIEW，不得修改 DB 造假 PASS。

**僅用嚴格唯讀方式稽核：**

~~~bash
docker exec -i golden-v2-chat-api-1 python - <<'PY'
import sqlite3
db = sqlite3.connect("file:/app/data/golden-v2/ledger.sqlite?mode=ro", uri=True)
db.execute("PRAGMA query_only=ON")
for row in db.execute("SELECT id,status,revision,approved_cents FROM v2_jobs"):
    print(row)
PY
docker inspect golden-v2-chat-api-1 golden-v2-chat-intake-1 \
  golden-v2-chat-paid-1 golden-v2-chat-delivery-1
~~~

嚴格的獨立 Agent 質詢範圍、必須交出的證據矩陣、任務完成格式：**[GOLDEN_V2_INDEPENDENT_AUDIT.md](GOLDEN_V2_INDEPENDENT_AUDIT.md)**。正式部署、付費呼叫、Drive 寫入都需要分開授權。
