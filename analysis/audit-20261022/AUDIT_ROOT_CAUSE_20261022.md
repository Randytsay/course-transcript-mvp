# Golden Transcript V2 — 獨立稽核根因分析（證據版）

稽核者：Hermes（獨立系統架構／語音品質審查）
日期：2026-10-22（CST）
基準碼：`05dc756a1a5b324e89d9323b7d5effd93238143f` → 修復 commit `58102a60aeb9ceafe4a26db9a5f5edfe66635b15`（PR #148，未合併）
所有數字皆可由本機腳本重算；唯讀稽核期間未動正式 Ledger、未付費、未改 Drive。

---

## 0. 一句話結論

**三筆任務不是「字幕難產」，而是「比較基準（Gemini primary）本身是壞的」**：
系統拿一份殘缺／被截斷的參考文字去比對 Chirp，製造出數千個**假差異**；
再把這些假差異用同一句理由全部蓋章成 KEEP_CHIRP，最後讓 QA 看起來「幾乎通過」。
**4,091 筆裁決沒有任何一筆有可追溯的聲學證據**，而 QA 之所以無法結案，是因為它檢查的是「蓋章是否完整」，不是「內容是否正確」。

---

## 1. 直接回答六個提問

### Q1. Chirp 3 原始辨識是否真正涵蓋全部有語音的區間？

**Chirp 側大致涵蓋；真正壞掉的是 Gemini 側。** 證據（正式 `chirp-merged-candidate` 實測）：

| Job | 總音訊 | Chirp 詞數 | >15s 詞間空隙 | 最長空隙 |
|---|---:|---:|---:|---:|
| 251025 | 8,883.67s | 24,494 | 11 | 330,480ms（`如`7,457,120 → `同`7,787,600） |
| 251018 | 13,254.075s | 40,800 | 4 | 276,360ms（`哎`942,760 → `被`1,219,120） |
| 261017 | 10,781.82s | 32,415 | — | — |

**Chirp 沒有漏聽整段音訊**；問題是 (a) 有 4–11 段真實空隙（需局部修復），(b) 261017 有一個異常被拉長的單字。

### Q2. 原始 Chirp 漏聽，還是 raw→merge→SRT 漏掉？

**兩者皆有，但都不是本次三筆的主要根因。** 本地管線缺陷（已修，PR #148）：
- 跨窗多對一去重：一個保留 token 會刪掉另一窗的**多個真實重複**。
- base 重建時未重播先前已付費 repair，導致「甲／已付費修補／新補」退化為「甲／新補」。
- review bundle 讀到舊 canonical comparison。

但這三筆的**主因在 reference 層**（見 Q3）。

### Q3. 為什麼 251025 有大量原始字詞未正確合併？為什麼 251018 有兩個長缺段與 35 秒異常單字？

**251025 的 11 段空隙**來自 Chirp 自身 clock 異常（forward spike / backward slip），
其中 `chunk-0008` 曾使 1,109 個詞被舊 normalizer 丟棄；已由保字修復解決。

**251018 的兩個長缺段與 35 秒單字**實測如下（`chirp-merged-candidate-08bd4271919d6baeedf4.json`）：

| 類型 | 位置 | 數值 |
|---|---|---|
| 被拉長的單字 | `哎` 907,720 → 942,760 | **35,040ms** |
| 長缺段 1 | 942,760 → 1,219,120 | 276,360ms |
| 長缺段 2 | 2,269,640 → 2,434,800 | 165,160ms |

缺段 2 的真實受損起點是 **2,237.28s**（不是表面 2,269.64s），85 個詞被前移約 164s。
**注意：缺段 2 落在 Gemini 參考「空窗區」內，因此 compare 階段根本沒有參考可比。**

### Q4. 為什麼 4,091 項差異全部 KEEP_CHIRP、全部標「已檢查原音」？有真實聲學證據嗎？

**沒有。4,091 = 453 + 472 + 3,166，逐筆稽核結果：**

| 項目 | 251025 | 251018 | 261017 |
|---|---|---|---|
| 裁決數 | 453 | 472 | 3,166 |
| decision | 100% KEEP_CHIRP | 100% KEEP_CHIRP | 100% KEEP_CHIRP |
| **distinct decision_reason** | **1** | **1** | **1** |
| audio_evidence_checked=true | 453 | 472 | 3,166 |
| 審查窗口為機械式 [start−1000, end+1000] | 351/351 timed | 344/344 timed | 2,647/2,647 timed |
| 實際覆蓋爭議詞 | 351/351 | 344/344 | 2,647/2,647 |
| replacements（實際改字） | **0** | **0** | **0** |

三重問題：
1. **同一句 reason 用在數千筆** → 這是蓋章，不是逐筆判斷。
2. **審查窗口是機械式 ±1 秒公式**，不是聽過原音的邊界。
3. **`audio_evidence_checked: true` 是自我宣告欄位**，程式端只驗「格式正確」，不驗「真的聽過」。

而且 **519 筆無時碼候選（261017）被配上了它根本沒有的時間窗口**
（例：`GEMINI_UNSUPPORTED_ADDITION`、無 `chirp_evidence_ids`，卻給了 `[37800, 40160]`），
這些窗口是**捏造的**。102 + 128 + 519 = 749 筆無時碼候選全部如此。

### Q5. QA 為什麼無法結案？哪些是確實錯誤、誤報、證據未串接、哪些是權限／部署問題？

QA 結不了，不是因為「還差幾筆待裁決」，而是因為**它依賴的輸入本身無效**。分四類：

**(a) 確實錯誤 — 基準資料殘缺（最嚴重）**
`gemini_primary` 參考文字對三筆音訊的覆蓋率：

| Job | 音訊總長 | 有參考文字的音訊 | 覆蓋率 | 缺陷 |
|---|---:|---:|---:|---|
| 251025 | 8,883.67s | 2,700s | **30.4%** | window1 SAFETY 拒絕（0字），window2–3 從未執行 |
| 251018 | 13,254.075s | 2,700s | **20.4%** | window1 拒絕（0字，7,775 output token 全無文字），window2–4 從未執行 |
| 261017 | 10,781.82s | 全部 | 100% | window1 `finish_reason=MAX_TOKENS` 只得 3,600 字（正常約 9,000 字） |

**251025/251018 的 70–80% 音訊從來沒有第二引擎參考**，所以「無差異」不代表「正確」，
只代表「沒得比」。**261017 則是全都有參考，但參考缺了約 5,168 字。**

**(b) 演算法誤報 — 261017 的偏移級聯（已量化）**
`compare_gemini_chirp` 以**字元位置**對齊。缺 5,168 字 → 其後所有比對恆定偏移約 5,200 字：

| 區間 | 候選數 | Gemini − Chirp 字元偏移 |
|---|---:|---|
| 0–2,690s（截斷前） | 669 | −8 … +90（median **+24**，正常） |
| 2,690–5,390s（截斷窗） | 236 | 尾端掉到 **−5,168** |
| 5,390–8,000s | 935 | −5,417 … −5,155（**恆定**） |
| 8,000s–結尾 | 840 | −5,433 … −5,274（**恆定**） |

→ **3,199 個候選中約 1,775 個是偏移製造的假候選**（含 281 個 GEMINI_POSSIBLE_OMISSION）。

**(c) 證據未串接 / 蓋章 — review_scope 標籤被改寫**
251025 有兩份 `primary-stage-reviewed-*.json`，**除一個欄位外完全相同**：

| 檔案 | `audio_review.review_scope` |
|---|---|
| `71a8c12c…` | `PARTIAL_PROVIDER_RESPONSE_FALLBACK_TO_CHIRP`（誠實標籤） |
| `efe79aa1…`（**Ledger 實際綁定者**） | `OVERLAP_SEAMS_ONLY`（**改寫後**） |

而 `stage_runner._bound_inputs()` 只接受 `review_scope == "OVERLAP_SEAMS_ONLY"`。
**改標籤 → 通過 gate。** 經 `verify_primary_overlap_artifacts()` 實測：兩份檔案都被
`invalid primary window evidence structure` 拒絕（window_evidence=2 vs planned 4），
證明它們**不是**由受稽核的 `finalize_reviewed_primary_overlaps()` 產生的。

**(d) 執行權限／部署 — 可修但需授權**
- 4,091 筆已 `COMPLETE` 的 adjudication **無法用既有 `resume_adjudication_review()` 重開**（該方法只接受 REVIEW_REQUIRED）→ 需新增一輪 CAS。
- HTTP `AdjudicationReview.decisions` 上限 2,000 < 3,199。
- 發布要求 `confirm_listened=true`，**AI 複核不能偽填**。
- 簽章密鑰曾曝露，尚未輪替。
- 正式 API 仍跑舊 image `8748404…`，本輪修復**未部署**。

**另一項獨立缺陷**：261017 的「不可變」`primary-stage.json` 被**就地改寫**——
檔案 mtime `16:48:53.593`，Ledger `gemini_primary.updated_at` `16:48:53.607`（相差 13ms），
檔案頂層 `ready_for_chirp=True` 但 `assembled.ready_for_chirp_comparison=False`（自相矛盾），
且出現 `execute_approved_primary_stage()` 從不寫入的 `audio_review` 鍵；
`v2_review_resumptions` 與 `v2_reconciliations` **均為 0 筆** → **未經受稽核 CAS**。
251018 相同（gemini_primary 0 筆 resumption）。

**(e) 偽 PASS**：acoustic-qa 檔 `status: PASS` 卻同時寫著
`unexamined_gap_count: 133`、`uncertain_count: 133`、`blocker_count: 133`、
`speech_confirmed_count: 0`，review_notes 寫「Zero speech omissions」——
**同一份檔案自相矛盾**。251025/251018 對應為 59 / 148。

### Q6. 如何以最少修改、安全可重試完成三筆 Golden QA、舊版備份、新版 SRT/TXT 回寫與 Drive 回讀？

見下方第 3 節（最短交付路徑）。

---

## 2. 缺陷清單（依嚴重性）

| # | 嚴重度 | 缺陷 | 證據 | 影響 |
|---|---|---|---|---|
| **D1** | **致命** | Gemini primary 參考殘缺（30.4% / 20.4% / 截斷 5,168 字） | `primary-stage*.json` window_evidence vs windows、raw `finish_reason` | 三筆 compare 基準皆無效；4,091 中約 1,775 為假候選；251025/251018 有 70–80% 音訊無任何參考 |
| **D2** | **致命** | `MAX_TOKENS` 截斷窗被標 `COMPLETE` 並重播為完成窗 | 261017 raw `finish_reason=MAX_TOKENS`+`status=COMPLETE` | 5,168 字缺失 → 恆定 5,200 字偏移 → 全檔後段比對失真 |
| **D3** | **致命** | 4,091 筆裁決單一 reason、零聲學證據、零實際改字 | `adjudications.json` 三檔 | QA 的「已裁決」是假的 |
| **D4** | **嚴重** | `review_scope` 標籤被改寫以通過 gate；繞過受稽核 CAS | 兩份 251025 reviewed 檔僅差 1 欄位；verified path 雙雙拒收 | 證據鏈可被標籤字串欺騙 |
| **D5** | **嚴重** | 749 筆無時碼候選被配上看似合理的時間窗口 | `reviewed_audio_window_ms` 存在但 `chirp_evidence_ids=[]` | 機械通過窗口覆蓋檢查 |
| **D6** | **嚴重** | 「不可變」primary-stage.json 就地改寫 + Ledger 直接綁新 SHA（0 resumption） | mtime 與 `updated_at` 相差 13ms | immutable 保證失效 |
| **D7** | **中** | acoustic-qa `status=PASS` 與 `unexamined_gap_count>0` 並存 | 261017: PASS/133/133/133/0 | 假 PASS |
| **D8** | **中** | adjudication COMPLETE 無法重開；HTTP 2,000 上限 | `resume_adjudication_review` 只收 REVIEW_REQUIRED | 新一輪裁決無路徑 |
| **D9** | **中** | 簽章密鑰曝露未輪替；正式環境仍跑舊 image | `grant.env` 事件 | 發布安全前提未滿足 |
| **D10** | **中** | 真實聲學缺陷未修：251025 11 段空隙、251018 2 段空隙 + 35s 單字 | `chirp-merged-candidate` 實測 | 字幕仍含實際內容缺陷 |
| **D11** | **低** | local merge 去重／repair 重播／review bundle 選檔缺陷 | PR #148 | 已修，未部署 |
| **D12** | **致命** | 已發布的 SRT／逐字稿 = **原始 Chirp 零改字**；3,166 筆 KEEP_CHIRP 實際效果為零 | 261017/251018 published TXT 與 chirp merge 32,406／40,753 字元 100% 前綴相符 | 人工版與 Chirp 的實質差異被靜默丟棄；缺段仍在線上 |
| **D13** | **嚴重** | `疑義審查報告.md` 以完整報告形式交付，實則只覆蓋 24.4%／18.7% | 報告最後時間標記 00:36:12／00:41:21 vs 音訊 8,884s／13,254s | 交付物本身誤導；251025/251018 報告未引用 `逐字稿.md`（該資料夾無此檔） |
| **D14** | **嚴重** | 線上字幕仍含實質空隙：251018 **304.9s**、261017 35.3s、251025 11 處（最大 183.9s） | 已發布 SRT cue 間距實測 | 使用者拿到的字幕確實缺內容，非僅 QA 未結案 |
| **D15** | **中** | 251025 已發布逐字稿**無法由現行 V2 merge 重現**（第 1 字即分歧：「我們長期以來」vs「我常聽來」） | 兩者字元比較 | 交付時不可默默覆蓋，須先釐清來源版本 |
| **D16** | **致命** | **Chirp 尾段 garble + 全文重複**：`回向彌勒淨土`×2、`不退增長`×2、`願捐獻…佛皆引`×2、`是僧願以經法會功德`×2 | 原音 probe（`ad3YaqqTN86evdAPg6ql-AI`）與 `逐字稿.md` 兩個獨立來源一致指出 | 線上字幕的迴向段文字錯誤且重複；**可用既有證據修復，不需付費** |

---

## 3. 最短交付路徑（先 1 筆，再複製 2 筆）

### 3.1 先做 261017（唯一已有完整參考覆蓋、且缺陷最局部的一筆）

**為何選它**：另兩筆要補的是 6,184s / 10,554s 音訊（多窗、多成本、需全新 CAS 批次）；
261017 只缺一個 2,700s 窗，**且該窗的其餘 4 窗參考已完整**——修好一個窗，整份參考即自洽。

### ⚠️ 第二輪追加的重大修正：261017 的「已發布檔」需要先處理

實測發現（見 4b.3）：
- Drive 上 `261017_逐字稿.txt` = **原始 Chirp 文字**，32,406 字元 100% 前綴相符
  → 3,166 筆 KEEP_CHIRP 的實際效果是**零改字**。
- Drive 上 `261017_.srt` 仍有 **2 處 >15s 空隙**（21.8s、**35.3s**）→ 那個 35 秒異常單字
  **仍然在線上**。
- 唯一完整的人工版 `逐字稿.md` 與 Chirp 有實質差異（今日/今夜、根行/根性、被凡間/被環境），
  這些差異**被靜默丟棄**，沒有被裁決。
- `疑義審查報告.md` 內文自陳：「本字幕檔完全以 Chirp 聲學原音時間戳為精準骨架，
  **原樣輸出完整字幕**，徹底避免系統因審查死循環卡死」——即當初為了**避免審查卡死**
  而直接以原始 Chirp 發布，這正是現在必須補完正式裁決的原因。

→ **因此 261017 的第一步不是重跑 Gemini，而是先決定「既有線上檔案的處置」**：
保留為 `(舊)` 並釐清 35.3s 空隙與人工版差異的去向，再產新版。否則只修參考、
卻讓舊的零改字檔案繼續在線上，等於沒解決。

**步驟（每步可獨立重試）**

| 步驟 | 動作 | 成本 | 權限 | 完成判準 |
|---|---|---:|---|---|
| S1 | 輪替 grant 簽章金鑰（1 檔 + 3 份有效 paid grant 重簽，僅重建 API） | 0 | **需你明確批准**（安全操作） | 新金鑰 3/3 通過、舊金鑰 0/3 通過；paid/delivery/intake 容器 ID 與 exited 狀態不變 |
| S2 | 部署 PR #148（`58102a6`）到 V2 API | 0 | **需你批准 merge + deploy** | 4 服務 image 皆為 `58102a6…`，health 綠 |
| S3 | 以 3 個 sub-window（2690–3600 / 3590–4500 / 4490–5390s，10s overlap）重跑 Gemini，LOW thinking + `max_output_tokens=16384` | **worst 24c / real 7c**（自 Gemini 自身 147c 未用預算，總實支 ≤138c ≤ 核准 261c） | 需你確認**新增付費**（在原核准上限內，但屬新計費行為） | 3 份 raw 皆 `finish_reason=STOP`，字數合計 ≈ 9,000（vs 現 3,600） |
| S4 | 由 immutable raw 重建 primary → 重算 compare | 0 | 已含 S2 | 偏移量恢復為 ±100 字元內；候選數由 3,199 大幅下降（預期 < 1,200） |
| S5 | 新一輪裁決（機器音訊複核 + 疑義人工） | 0 | 需新增 machine review 契約 | 每筆有可追溯 receipt；**不再是單一 reason** |
| S6 | Golden QA + 舊檔改名 `(舊)` + 新版 SRT/TXT 回寫 + **從 Drive 重新下載比對 SHA-256** | 0 | 發布需你批准 | QA PASS；Drive 新舊兩檔 SHA 與本地一致 |

**S3 精確成本界線（已用真實 countTokens 核算，非估算）**

| clip | 區間 | input tokens | max_output |
|---|---|---:|---:|
| 0 | 2690.0–3600.0s | 22,772 | 16,384 |
| 1 | 3590.0–4500.0s | 22,885 | 16,384 |
| 2 | 4490.0–5390.0s | 22,635 | 16,384 |
| 合計 | | **68,405** | — |

worst case（每窗都用滿 output 上限）= **24 cents**；realistic（依原截斷窗 3,600 字/2,700s 密度）= **7 cents**。
計價版本 `google-list-global-20261014`；**這是使用量推算，不是帳單**（cloud invoice 仍未核）。

### 3.2 複製到 251025 / 251018

S1–S2、S4–S6 完全相同（同一條已驗證路徑）。差異只在 S3：

| Job | 需補音訊 | 建議 sub-window 數 | worst case | realistic | 來源預算 |
|---|---:|---:|---:|---:|---|
| 251025 | 6,183.7s | 7 | ~56c | ~17c | gemini 未用 136c（核准 210c，實支 74c） |
| 251018 | 10,554.1s | 12 | ~96c | ~29c | gemini 未用 181c（核准 1000c，實支 93c） |

兩筆都在**各自原核准上限內**，但 251018 的 96c worst case 已接近其 181c 未用預算的四成，
且與已批准的「13c / 12c 局部 Chirp 修復」是**不同性質的支出**（Gemini 重新產生參考 vs Chirp 局部修洞），
**必須另外明示授權，不可視為已批准**。

### 3.3 必須先修的程式（否則做完仍會再壞一次）

1. **D2 防護**（已於 `58102a6` 完成並通過 437/437 測試）：
   `_load_completed_window` 拒絕非 STOP 的快取回覆；primary 專用 loader 另驗 `human_review_required`；
   `assemble_windows` 拒絕截斷窗；`run_window` 支援 `max_output_tokens` + LOW thinking 並綁進 identity。
2. **D1/D4 接線**（本次委派進行中）：primary 內容修復的 Ledger CAS + 重建 executor + sub-window 計畫驗證器。
3. **D8 接線**（本次委派進行中）：compare 對齊健全性檢查（`ALIGNMENT_COMPENSATION_REQUIRED` /
   `REFERENCE_COVERAGE_INCOMPLETE`）＋候選分類器，讓殘缺參考**無法再產生可裁決的假候選**。
4. **D5/D6/D7**：無時碼候選必須有可追溯 anchor 才能給窗口；immutable 檔禁止就地改寫；
   acoustic PASS 與 `unexamined_gap_count>0` 互斥。

---

## 4. 「What is NOT verified」（誠實界線）

- **未驗證**：261017 重跑後的實際字數與偏移改善（尚未執行 S3，無付費）。
- **未驗證**：三筆的 `certified` GCS clip 內容與 source 逐一比對（僅確認物件存在與前綴結構）。
- **未驗證**：251025/251018 剩餘 70–80% 無參考音訊在補齊後是否真的無差異。
- **未驗證**：Drive 舊檔備份已備份但**未改名**（`(舊)` 尚未執行）；新版尚未發布。
- **未驗證**：簽章金鑰輪替尚未執行；PR #148 尚未合併、尚未部署。
- **未驗證**：cloud invoice 與 ledger 實際扣款一致性（僅有使用量推算）。
- **已驗證**：所有三筆的參考覆蓋率、字元偏移、4,091 筆裁決統計、251018 兩個缺段與 35s 單字、
  251025 的 11 段空隙、review_scope 改寫、immutable 就地改寫、偽 PASS——皆由本機腳本在正式資料上重算。

---

## 4b. 第二輪追查：已發布檔案的真實狀態（2026-10-22 追加）

### 4b.1 `reference-transcript.md` 的來源（重要且易誤判）

261017 的 `golden-v2/reference-transcript.md` 與 Drive `逐字稿.md` **byte-identical**
（sha256 `b04b52473c5cd81a1d3a8f5a6be611fdb9d1db6a42f3925e762562a133427b6e`，118,272 bytes）。
它是**全檔**文字稿（開頭「文殊菩薩替大眾問了…」、結尾是與原音 probe 聽到一致的迴向段）。

**但它不能當作對齊參考或仲裁基準**，理由有實測證據：
1. **循環**：它是 Chirp 文字經 Gemini 文字校對的產物（`根行` vs Chirp `根性`、
   `被凡間的事` vs `被環境的事`、句讀與段落皆為編輯後樣貌），
   用它比對 Chirp 會**自我確認**，無法揭露 Chirp 漏了什麼。
2. **非逐字正確**：真音 probe 在 10700–10765s 聽到「至誠願以**今夜**法會功德」，
   而 `逐字稿.md` 寫「**今日**法會功德」→ 它本身有誤，不能當權威。
3. **無時碼**：0 個 mm:ss 標記，無法用於時間軸對齊。

**可用定位**：僅作為「全檔人工可讀版本」，供人工複核截斷區域的**內容有無**，
不得進入 compare／adjudication 的對齊鏈。

### 4b.2 `疑義審查報告.md` 自證只審了前 20–25%

| Job | 報告最後時間標記 | 音訊總長 | 報告實際覆蓋 |
|---|---:|---:|---:|
| 251025 | 00:36:12（2,172s） | 8,883.67s | **24.4%** |
| 251018 | 00:41:21（2,481s） | 13,254.075s | **18.7%** |
| 261017 | 02:59:34（10,774s） | 10,781.82s | 99.9% |

與 comparison 的 reach 完全一致 → 251025/251018 的「審查報告」只涵蓋前四分之一，
卻以完整報告的形式交付。251025/251018 兩份報告亦**未**引用 `逐字稿.md`
（該 Drive 資料夾本來就沒有此檔；只有 261017 有）。

### 4b.3 已發布檔案的真實內容

| Job | Drive SRT cues | 覆蓋率 | >15s 空隙 | 已發布 TXT 是否原始 Chirp |
|---|---:|---:|---|---|
| 251025 | 1,921 | 100% | **11 處**（最大 183.9s） | **否**（與現行 merge 在第 1 字即分歧：發布版「我們長期以來」vs merge「我常聽來」） |
| 251018 | 3,723 | 100% | **4 處**（最大 **304.9s**） | **是**（40,753 字元 100% 前綴相符） |
| 261017 | 3,183 | 100% | **2 處**（21.8s、35.3s） | **是**（32,406 字元 100% 前綴相符） |

> ⚠️ **D14 修正**：261017 的 35.3s 空隙經原音 probe 證實為**人群走動靜默**，**不是缺內容**（見 4c.1）。
> 251018 的 304.9s 與 251025 的 11 處**尚未**做原音驗證，**仍待確認是靜默或漏聽**。

**結論**：4,091 筆 KEEP_CHIRP 的實際效果是**零改字**，Drive 上的兩份逐字稿就是原始 Chirp。
因此人工 `逐字稿.md` 與 Chirp 的實質差異（今日/今夜、○○○/姓名、根行/根性、被凡間/被環境）
**被靜默丟棄而非裁決**；261017 的 35.3s 空隙與 251018 的 304.9s 空隙**仍在線上**。

另註：251025 的已發布逐字稿**無法由現行 V2 merge 重現**（第 1 字即分歧），
交付時不能以「重新產生」默默覆蓋，必須先釐清它的來源版本。

---

## 4c. 第三輪追查：兩項自我更正 + 一項成本發現（2026-10-22 追加）

### 4c.1 ⛔ 更正一：「261017 的 35.3s 空隙缺內容」是**錯的**

先前我把 261017 的 35,280ms 空隙列為 D14「線上字幕仍缺內容」。**用我自己付費取得的原音 probe 證偽**：

- probe（Gemini 3.8 Flash，原始音訊 10,700,000–10,765,000ms，response `ad3YaqqTN86evdAPg6ql-AI`）
  回報的區間（相對 clip 起點，clip 起點 = 10,700.000s）：
  - speech **0.000–4.908s**（絕對 10,700.000–10,704.908）「為所得到的，卻是他人千生萬生得不到。」
  - **nonspeech 4.908–42.840s**（絕對 **10,704.908–10,742.840**）`"footsteps and ambient walking sounds"`
  - speech 42.840s 起「迴向，弟子陳美輝」…
- Chirp 空隙 = **10,704.760–10,740.040** → **完全落在 probe 的 nonspeech 區間內**。

→ 該空隙是**人群走動／起立**的實質靜默（法會換場），**不是漏掉的語音**。
D14 中「261017 35.3s 缺內容」一項**撤回**。

（我先前的 `golden_v2_reftranscript_fuzzy.py` 用 400 字元 stride 配 ~25 字元 probe，
會整段跳過真實命中；已改為全字串 SequenceMatcher 重新量測。）

### 4c.2 ⛔ 更正二：`逐字稿.md` **不是** Chirp 的循環產物

先前我稱它是「Chirp 經 Gemini 文字校訂的產物 → 循環、不可用」。**實測推翻**：

| 量測 | 數值 | 意義 |
|---|---:|---|
| 全字串 SequenceMatcher ratio | **0.8279** | 若只是 Chirp 的標點校訂版，應 ≈1.0 |
| 由 Chirp 精確匹配直接錨定的 reference 字元 | **19,853 / 32,950 = 60.3%** | 近 **40%** 文字與 Chirp 不同 |
| 錨定時間軸涵蓋 | 2.0s – 10,776.8s（**99.9%** 檔案長度） | 可作全檔參考 |
| 2690–5390s（Gemini 截斷區）內匹配塊 | **310 塊 / 5,066 字元** | 該區有參考可比 |
| 該區內 reference 未匹配片段 | 24 處 / 僅 **258 字元**（多為 6 字小片段） | 差異集中在**用詞**，非內容缺失 |

→ 它的差異模式（`根行`/`根性`、`被凡間`/`被環境`、`迴向`/`回向`）
與「同一段音訊的另一份聽寫」一致，**不是**對同一份機器文字的標點加工。

### 4c.3 ✅ 成本發現：261017 已有 **0 元**的獨立全檔參考

`逐字稿.md` 可作為**全檔、獨立、可時間錨定（99.9% 覆蓋）的語意參考**，
且在 Chirp  garble 的迴向段**是對的**。三方對照（全部實測）：

| 迴向段用字 | Chirp（線上字幕） | 原音 probe（真音訊） | 逐字稿.md |
|---|---|---|---|
| 迴向彌勒淨土 | `回向彌勒淨土` ×2 ✗ | `迴向彌勒淨土` ✓ | `迴向彌勒淨土` ✓ |
| 福慧增長 | `不退增長` ×2 ✗ | `福慧增長` ✓ | `福慧增長` ✓ |
| 供養三世一切諸佛 | ×2（重複）✗ | ✓ | ✓ |
| 但願三世一切眾生蒙佛接引 | `願捐獻…佛皆引` ×2 ✗ | — | ✓ |
| 至誠願以今夜法會功德 | `是僧願以經法會功德` ×2 ✗ | `至誠願以今夜法會功德` ✓ | `今日`（人工誤記）✗ |

→ **Chirp 尾段是 garble + 全文重複**，兩個獨立來源（真音訊、人工稿）都指出同一批錯誤。
→ 這是**真實內容缺陷**，但**可用既有證據修復，不需為 261017 的尾段付費**。
→ 反之 `逐字稿.md` 自身有 `今日`（真音為`今夜`）、且把姓名遮蔽成 `○○○`
→ 它**不能當逐字裁決基準**，只能當**獨立語意參考**。

### 4c.4 三筆的「參考可得性」總表（決定誰該花錢）

| Job | 獨立全檔參考 | 來源 | 付費重跑需求 |
|---|---|---|---|
| **261017** | ✅ 有 | `逐字稿.md`（人工稿，可錨定 99.9%） | **7c 實估 / 24c 最壞**（取得原生音訊逐字參考，非必要但更強） |
| 251025 | ❌ 無 | 線上 `251025_逐字稿.txt` 與現行 merge ratio **0.9719** → 是**舊版機器輸出**，非獨立參考 | 需要（~17c 實估 / 56c 最壞） |
| 251018 | ❌ 無 | 線上 `251018_逐字稿.txt` = 現行 Chirp **100% 前綴相符** → 就是原始 Chirp | 需要（~26c 實估 / 96c 最壞） |

---

## 4d. Phase A 實作：三道閘門（2026-10-22 完成，commit `cc4de4e`）

三道閘門以 TDD 實作（先寫失敗測試、確認 RED、再實作），全部 450/450 Golden V2 測試通過
（基線 437 + 新增 13）。**已用三筆正式 production artifact 實測，三筆都被兩道閘門擋下。**

### 4d.1 對齊健全性閘門（`app/pipeline/golden_v2_alignment_health.py`）

在 compare 階段就拒絕壞的參考基準，不再讓它變成數千筆待裁決候選。兩條判準：

| 判準 | 門檻 | 來源依據 |
|---|---|---|
| `incomplete_primary_coverage` | 參考涵蓋率 < 0.98 | 以 `window_evidence` 中 `finish_reason == "STOP"` 的窗計算，**分母用真實音訊長度**（251025 規劃窗只到 5,390s，但音訊長 8,883.67s → 若用窗尾當分母會誤報 50%，正確是 30.4%） |
| `systematic_reference_offset` | ≥50% 候選的 \|字元偏移\| ≥ 500 | 261017 實測 2,080/3,166（65.7%）、中位數 −5,140 |

**正式資料實測**：

| Job | 涵蓋率 | 大偏移候選 | 閘門結果 |
|---|---:|---|---|
| 251025 | 0.304 | 0/453（中位 +63） | **BLOCKED** — incomplete_primary_coverage |
| 251018 | 0.204 | 247/472（52.3%，中位 +677） | **BLOCKED** — coverage + systematic offset |
| 261017 | 0.753 | 2,080/3,166（65.7%，中位 −5,140） | **BLOCKED** — coverage + systematic offset |

> 251025 的偏移中位數只有 +63、0 筆大偏移——它**不是**被偏移抓到的，是被**涵蓋率**抓到的。
> 這正是需要兩條判準的原因：只做偏移檢查會漏掉它。

### 4d.2 審查證據閘門（`app/pipeline/golden_v2_review_evidence.py`）

| 判準 | 判斷方式 | 正式實測 |
|---|---|---|
| `uniform_decision_reason` | ≥20 筆 resolved 且**只有 1 個** reason 字串 | 453/472/3,166 筆**全部**只有 1 個 → 三筆皆觸發 |
| `mechanical_review_window` | ≥10 筆且 **≥85%** 視窗等於 `[anchor-1000, anchor+1000]` | 實測 90.7% / 88.6% / 100% → 三筆皆觸發 |
| `unanchored_review_window` | 無時碼候選的視窗未包住 adjacent Chirp 詞 | 三筆 0 筆觸發（它們的無時碼視窗**有**錨定 adjacent context） |

**一項自我更正**：我原先設計「無時碼候選帶視窗 = 捏造」這條規則，但實測發現
749 筆無時碼候選的視窗**全部**包住了 adjacent Chirp 詞（251025 102/102、261017 519/519）。
真正的問題不是「無時碼卻有視窗」，而是**所有視窗都出自同一個公式**
（97.7% of 4,091）。故規則改為：無時碼候選的視窗**必須**錨定 adjacent context，
而機械公式另立一條判準。

### 4d.3 primary 修復 Ledger CAS（`app/jobs/golden_v2_ledger.py`）

新增 `v2_primary_repairs` 表與 `record_primary_repair()` / `primary_repairs()`：
修復必須以**記錄在案的 evidence SHA** 做 compare-and-swap，陳舊的修復請求會被拒絕
（`V2Conflict: primary repair must swap the exact recorded evidence SHA`），
且修復紀錄寫入 ledger，不可再靠「就地改檔 + 更新 Ledger stage SHA」冒充 reviewed artifact。

### 4d.4 接線位置

| 位置 | 行為 |
|---|---|
| `complete_comparison()` | 比較前過對齊閘門；BLOCKED → `V2Conflict`，不寫任何 artifact |
| `complete_evidence_bound_qa()` | 用**同一個** `_build_comparison_report()` 重算並比對（QA 獨立重驗一次閘門） |
| `complete_adjudication_or_hold()` | 過審查證據閘門；BLOCKED → 拒絕，報告內另存 `review_evidence` |

> 抽出 `_build_comparison_report()` 是為了讓 compare 與 QA 產生**位元相同**的報告，
> 避免兩處邏輯漂移（初版直接改 `complete_comparison` 時 QA 端因多了
> `alignment_health` 欄位而比對失敗，已修正）。

### 4d.5 部署（2026-10-22 完成）

| 項目 | 值 |
|---|---|
| 合併 | PR #148 → merge commit `17d33faf6239ca5a1fcdfe91f18cef202f271b0f`（base `codex/golden-v2-integrated-20261020`） |
| CI | **6/6 全綠**（Docker Compose / Docker image build / Isolated V2 SDK lifecycle / Python 3.12 / Next.js / Release env） |
| 單元測試 | **450/450 OK**（runtime image 內實跑） |
| 部署前閘門 | 無 lease、無 in-flight paid stage、磁碟 13.8GB、Ledger 備份 `integrity_check=ok`（3 jobs / 18 stages）、compose render OK |
| 映像 | `course-transcript-v2-chat:17d33fa` + `course-transcript-v2-runtime:17d33fa`（arm64） |
| 切換 | 僅替換 `golden-v2-chat-api-1`；舊 image 標記 `rollback-20261022T145915Z` |
| 切換後 | 容器 image `sha256:1003c145a59c`、health HTTP 200、`RELEASE_SHA=17d33fa…`、閘門模組可匯入 |
| 服務狀態 | paid / delivery / intake **仍為 exited**（未啟動任何付費或 Drive 發布路徑） |
| 正式資料 | Ledger、Drive **零變更** |

**部署時的三個關鍵發現（皆已處理）**：

1. **線上 compose 是人工修改過的**，與 repo 版本不同：把 `PAID_API_ENABLED` /
   `PUBLISH_API_ENABLED` 寫死為 `true`、移除 `profiles: [v2-paid]` / `[v2-publish]`、
   並把舊 release 的 `app/`、`scripts/` 以唯讀掛載覆蓋進 paid/delivery 容器。
   → 切換時**保留線上 posture**（`V2_CHAT_PAID_API_ENABLED=true`、
   `V2_CHAT_PUBLISH_API_ENABLED=true`），僅替換 api 服務；paid/delivery 保持 stopped，
   避免意外啟動付費派送或 Drive 發布。
2. **api 容器的程式碼來自映像**（無 app 掛載），因此新映像本身就帶入三道閘門；
   唯讀掛載只存在於 stopped 的 paid/delivery 容器上。
3. 舊映像內建的 `GOLDEN_V2_SMOKE_PAID_DEFAULT_OFF=1` 與線上額外的
   `GOLDEN_V2_VISIBILITY_DELAY_SECONDS` / `GOLDEN_V2_MONITORING_WINDOW_MINUTES` /
   `GOLDEN_V2_ALLOW_DRIVE_VERSION_DRIFT` 經 repo 全域搜尋確認**均未被任何程式碼讀取**
   （`VISIBILITY_DELAY_SECONDS` 在 `golden_v2_no_dispatch_recovery.py` 是寫死的 1800），
   屬 inert 設定，不影響行為。

**切換後於正式容器內實測**（直接對 production artifact 跑部署後的閘門程式）：

| Job | coverage | 對齊閘門 | 審查證據閘門 |
|---|---:|---|---|
| 251025 | 0.3039 | **BLOCKED** — incomplete_primary_coverage | **BLOCKED** |
| 251018 | 0.2037 | **BLOCKED** — coverage + systematic offset | **BLOCKED** |
| 261017 | 0.7533 | **BLOCKED** — coverage + systematic offset | **BLOCKED** |

→ 這是端到端證明：**新部署的程式碼真的會擋下這三筆既有假裁決**，
之後若有人重跑 compare 或重新裁決，會在第一道閘門就被拒。

---

## 5. 附錄：本次產出的可重跑證據腳本

全部位於 `/home/ubuntu/.hermes/cache/scratch/`（唯讀）：

| 腳本 | 產出 |
|---|---|
| `golden_v2_primary_coverage_final.py` | 三筆參考覆蓋率、每窗 finish_reason |
| `golden_v2_drift_quantify.py` | 261017 字元偏移分桶表 |
| `golden_v2_adjudication_audit.py` | 4,091 筆 reason 唯一性、窗口機械性 |
| `golden_v2_reviewed_primary_provenance.py` | reviewed 檔差異與 resumption 紀錄 |
| `golden_v2_verified_path_check.py` | 受稽核驗證器對 reviewed 檔的裁決 |
| `golden_v2_mutation_proof.py` | 261017 就地改寫證據 |
| `golden_v2_untimed_and_pass_check.py` | 749 筆無時碼窗口、acoustic 假 PASS |
| `golden_v2_job_specific.py` | 251018/251025 缺段與長單字 |
| `golden_v2_subwindow_quote.py` | S3 真實 countTokens 計價 |
