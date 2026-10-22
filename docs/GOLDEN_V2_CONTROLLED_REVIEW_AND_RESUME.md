# Golden V2：離線修補與受控續跑邊界

狀態：**development only / NOT DEPLOYED / Golden 0/3**。
分支 `fix/v2-review-freshness-20261023`，基底為實際部署的
`17d33faf6239ca5a1fcdfe91f18cef202f271b0f`。本次沒有 merge、部署、啟動
Worker、付費生成、正式 Ledger 修改或 Drive 寫入。

## 已實作且實測的修改

1. `/jobs/{job_id}/review` 改用 `get_v2_status()` 的 SQLite `mode=ro`
   snapshot；GET 不再開啟具寫交易能力的 Ledger。
2. 新增 `golden_v2_review_snapshot.py`：對 REVIEW_REQUIRED 的 Chirp stage，
   依完整 Ledger SHA 選取 canonical/full-SHA/held-candidate 檔案，核對
   schema、job、source，再用 canonical JSON digest 核對 immutable acoustic
   report。短檔名不是證明；mtime 不是 selector。不同報告同時綁定同一候選
   時回報 AMBIGUOUS；來源、檔名 hash、內容衝突則拒絕。
3. 沒有 Golden-QA attestation 時，新綁定報告置於 `reports.acoustic-qa.json`；
   舊報告移至 `historical-acoustic-qa.json`，仍標記 legacy_unbound。已有
   QA-attested acoustic chain 保持原邏輯優先。新 snapshot 明示
   `CHIRP_TIMELINE_GAP_ASSESSMENT_ONLY`、`golden_pass=false`、
   `human_listening_attested=false`，不把 VAD 視為逐字聽審。
4. `BudgetedModels` 不再把 adapter 的 16,384／8,192 output cap 擅自改為
   65,536。保留更小上限及 LOW thinking 設定，拒絕無效／超額上限，不修改
   原 SDK config；未指定上限才沿用既有總 reservation cap。

這是兩個狀態／執行契約缺陷的修補，不是把字幕宣告為正確。

## 真實資料讀回與演練

證據根目錄（私有 VPS，不含憑證輸出）：
`/srv/ai-workspace/tmp/golden-v2-controlled-resume-20261023/`。

- `p1-proof/`：父代理重跑 historical/deployed 相同測試。舊版
  `8748404` exact repro 失敗；正式版 51/51、418/418 排序變體與既有 3 個
  merge tests 通過。四方 module SHA（git/release/live/import）一致。
  GitHub unresolved + outdated thread 不是目前 exact P1 仍存在的證明。
- `backup-restore/verification.json`：SQLite integrity/FK/table digests
  通過，249 份 evidence 實際還原，44 份 provider raw 可驗。
  **音檔只驗來源身份，沒有複製／還原；這不是完整音訊災難復原演練。**
- `provider-operations-readback.json`：44/44 saved operations 經真正 GET
  對帳為 DONE，raw/cost 綁定可驗；BatchRecognize/GenerateContent 為 0。
  DONE 證明 operation 結束，不證明字幕正確，也不是最終雲端發票。
- `patch-tests/restored-rehearsal/verification.json`：新版真正 HTTP route
  在備份 read-only mount 成功讀三筆，250 個輸入檔（含 Ledger）前後 SHA
  不變。再在無憑證、network-none、tmpfs DB 副本執行真正 executor、local
  coordinator、publication gate：零 provider factory 呼叫；三筆仍
  `NEEDS_REVIEW`，publication 皆被 Golden QA gate 拒絕；所有 table digests
  不變。**這是安全拒絕／不重送演練，不是成功續跑至交付。**
- `patch-tests/drive-readback/verification.json`：重新列三個真實來源資料夾，
  驗 source ID/MD5，下載 6 份待審副本及 6 份既有正式檔，ID/SHA 全相符。
  待審副本不是 Golden promotion；本輪 Drive 寫入 0。

目前候選的 gap assessment（blocker / uncertain / unexamined）：
- 251025：12 / 11 / 0；97 個 probe slices 有 VAD 資料。
- 251018：25 / 25 / 0；171 個 probe slices 有 VAD 資料。
- 261017：12 / 9 / 0；150 個 probe slices 有 VAD 資料。

這些是演算法待釐清區間，**不是已確認漏語數**。舊入口顯示的 64/149/133
及 59/148/133 unexamined 來自其他 merge 的 historical reports。

## 尚未消除的交付阻礙（依優先序）

### P0：比較基準不是全檔有效的獨立辨識

`patch-tests/restored-rehearsal/primary-baseline-verification.json` 是重新
讀取 Ledger-selected primary 與 native raw 的結果，不只沿用歷史報告：
三筆 raw SHA／identity 均匹配，但 clean-STOP windows 的既有 coverage 指標
分別為 0.30392844398767627、0.20371093418439234、0.7532884058535572。
三筆目前的 alignment gate 都 BLOCKED。

251025／251018 第二窗無可用文字且 requires review；261017 第二窗
2690–5390 秒為 MAX_TOKENS，只有 3,600 字，卻被歷史 raw 標為 COMPLETE、
human_review_required=false。這些 artifacts 必須保留，不得事後改字串變合格。
4,091 份歷史 KEEP_CHIRP 自述不能補上缺失的原音審查。

### P0：安全重入與合格證據必須一起完成，不能靠開 Worker

API 的 paid/publish config flags 實際為 true；service capabilities 為 false
是因 paid/delivery heartbeat 不可用。停止的三個 Worker 仍指向舊 `8748404`
images，不能直接 `docker start`。而真正 executor 只選 QUEUED/PROCESSING；
三筆目前 NEEDS_REVIEW，所以單純啟動也不會前進。

現有 `resume_chirp_review_for_repair()` 只接受原六-stage 狀態
COMPLETE/COMPLETE/COMPLETE/SKIPPED/COMPLETE/REVIEW_REQUIRED；不接受目前
Chirp REVIEW_REQUIRED、後續 PENDING 的形狀。現有 primary repair builder
與 `record_primary_repair()` 也不等於已有通到 compare 的完整 promotion
流程：builder 產生 content-repair schema，compare 仍要求原 primary-stage
及特定 reviewed-stage 契約。正式付費前，需先把此 handoff 在隔離副本接通
並驗證，不能只更新 SHA、改狀態或把 intent 當成已驗收。

重新啟用 paid/publication 前，還須核對既有 grant-key 事件的輪替收據；
目前不聲稱輪替已完成。輪替／重簽與服務 recreate 需另列精確授權。

### P1：本分支修補尚未 review/merge/deploy

正式入口仍是舊版讀法；以上新結果來自新碼加真實還原證據。不能宣稱線上
入口已修好。此處也沒有認證整個 merge 演算法對所有音訊都正確。

## 驗證結果與重跑

三個 RED 均實際出現：GET 呼叫 writable Ledger；舊 64 被誤當 current 12；
16,384 cap 被改成 65,536。之後 focused GREEN 與安全邊界驗證通過。

- Golden V2 suite：474/474。
- Gemini primary adapter suite：12/12（不包含在前一個 filename pattern）。
- 20 個 review tests 包含 byte/canonical SHA 差異、完整版本檔、歷史選版、
  tampering、source/job conflict、重複報告、歧義、symlink、size、auth、404
  及 QA chain 優先序；runtime services 共 7 個 tests，含真實 SDK config。
- 未跑全 repo／frontend suite、未建新 release image、未作線上重啟驗收。
  現有 runtime 的 Starlette/httpx deprecation warning 是接受的既有警告；
  host 不具 google-genai 不能取代容器實測。

```sh
# 在含既有 dependencies 的 runtime 裡，掛載本分支於 /app:ro，network none：
python -B -m unittest discover -s tests -p 'test_golden_v2*.py' -v
python -B -m unittest discover -s tests -p 'test_gemini_primary_audio.py' -v
```

## 最短交付路徑：261017 先完成，再複製

1. 先離線接通 reviewed-Chirp 與 repaired-primary 的受控 promotion，保持
   raw／舊 stage 快照、source、cost、revision CAS 及 downstream invalidation；
   要求真正 source-bound 原音裁決，不移除 QA 或偽造 listened。
2. 為 261017 唯一已證實截斷的 primary 窗 2690–5390 秒規劃較小子窗，
   LOW thinking + 實際保留的 bounded output；免費 count_tokens 重新報價後
   才申請 exact-plan paid grant。重用其他 clean raw，**不要重送 44 份 Chirp**。
   目前沒有新的精確價格或付費授權；舊預算餘額不自動等於此 repair 授權。
3. 對新基準重建 comparison；舊 4,091 adjudications 不重用。完成 gap／
   semantic／姓名及「今日／今夜」原音裁決，AI 只記為 AI review；模糊處
   集中交真正 reviewer。全檔 QA 不是抽一段或 VAD 全掃就算完成。
4. 新 SRT/TXT 同源同文、時碼可追溯、無未解 blockers，Golden QA 真正 PASS
   後取得 job/revision/source/render/QA 綁定的 publication grant。列出舊檔
   exact IDs/versions/SHA，備份並保留 `(舊)`；用已有 pending-ID transaction
   正式發布，下載 SRT/TXT 驗 SHA、receipt，再測 idempotent retry 不重送 ASR。
5. 261017 真正交付後，對 251025、251018 重複同一路徑；缺失 primary 範圍較大，
   各自報價，不能把第一筆成功視為其餘兩筆已通過。

本輪新付費為 0；正式 Ledger 仍為 87、108、116 cents。雲端最終發票未驗。
下一步所需授權應拆清：review/merge 的 exact commit、API-only 部署
（其他 Worker 維持 stopped、Ledger diff=0）、必要的 key 輪替／重簽，及
另外報價的單筆 bounded repair。**API-only 部署本身不會完成 Golden。**
