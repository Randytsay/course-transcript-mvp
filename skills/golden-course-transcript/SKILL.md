# Course Transcript 黃金字幕 Skill v1.0

## Purpose

把 Google Drive 中的課程音訊，以既有 Course Transcript production pipeline
完成 Chirp 3 + ChatGPT handoff 校訂，並將可追溯的 SRT、正式逐字稿與 QA
結果安全回寫來源資料夾。此 Skill 優先重用已完成且可信的 provider evidence，
不得因為進入新對話而重跑已成功的 Chirp 3。

## Invocation

使用者可用下列任一方式觸發：

- `黃金字幕：<Google Drive file/folder URL>`
- `用黃金流程處理這個 Google Drive 資料夾`
- 指定既有 Course Transcript job id 後要求接手 ChatGPT handoff

若來源與同資料夾 reference 已唯一且工作狀態清楚，採 zero-question 執行。
只有來源歧義、reference 歧義、Completeness Gate BLOCKED、需新增付費 provider
call、或輸出目的地不明時才停下確認。

## Source discovery

1. 解析 Google Drive file/folder URL。
2. 列出來源資料夾，辨識：
   - audio/video source
   - sibling `*逐字稿.txt`
   - existing SRT/TXT/manifest
3. 用 Drive file id、source_path、source checksum 或 job ledger 對應既有 job。
4. 先做 reconciliation：
   - job status / active_stage / revision
   - Chirp complete evidence
   - Completeness Gate
   - existing handoff bundle
   - existing Drive outputs
5. 不重跑已完成且可信的 Chirp / targeted repair。


## Automatic topic classification and profile routing

Folder intake uses deterministic evidence before any specialised correction profile is selected. Evidence may include folder/file names, same-folder reference transcript/material hints, explicit operator context and a bounded Chirp transcript sample.

Supported profiles:

- dacheng_buddhist — 佛教／《佛說彌勒大成佛經》；可使用 canonical scripture、mantra 與既有 Golden Corpus。
- market_america_training — 美安／Market America 商品與事業訓練；優先保留 Market America、SHOP.COM、Isotonix、OPC-3、NAD+、IBV、BV、UFO 等品牌／制度拼字，但不得自行加強健康療效、收入或事業成效宣稱。
- hvac_energy — HVAC／節能／BESS／EMS 技術課程；優先辨識 HVAC、BESS、EMS、IPMVP、ASHRAE、ISO 50001/50006、M&V、Modbus 等工程術語與單位。
- generic — 未達專用主題信心門檻的安全預設，不套任何專用詞庫。

Routing must fail safe:

1. 專用 profile 必須達最低分數與領先 margin；否則使用 generic。
2. 佛經 canonical / Golden Corpus 僅能在 dacheng_buddhist 啟用。
3. 美安詞庫不得進入 HVAC 或佛教 profile；HVAC 詞庫亦同。
4. 新 Drive folder 尚無 job 時，可先以檔名／資料夾／reference 做初判，再於 Chirp 取得文字後用相同 classifier 做確認；不得因此繞過既有 preflight、費用核准或 provider safety gate。
5. 若初判與 Chirp 後判斷衝突且會影響專用詞庫，應降為 generic 或人工確認，不可靜默切換到另一專用 profile。

### Market America product knowledge cross-check

當 profile 為 `market_america_training` 時，Chirp 固定字幕段建立完成後，額外建立一次性的
`market-america-terminology.json`。它透過 ShopClaw 的 read-only S2S terminology API
查詢目前課程文字中可能出現的美安產品，並只保留目前 Market America 商品主檔身份、
名稱／別名與 serving approved facts 中的術語證據。

- 只用於產品名、別名、成分／材料名稱等「拼字與辨識」輔助。
- 不讀取 ShopClaw 一般商品 raw `product_copy`、價格、owner cost 或 generic catalog。
- 不得把資料庫中的功效、健康、劑量、收入或行銷敘述加入原音訊沒有說出的字幕。
- 每個 job 只建立一次快照，後續 Gemini / ChatGPT handoff 都讀同一份 evidence，確保可重現。
- ShopClaw API 未設定、逾時、401、回傳格式錯誤時 fail closed：留下 unavailable snapshot，
  但不阻斷字幕；模型不得因此自行猜產品或成分。
- runtime 以 `SHOPCLAW_MA_TERMINOLOGY_URL` 與 `SHOPCLAW_MA_KNOWLEDGE_TOKEN`
  提供 S2S 連線；secret 不寫入 Git、manifest 或 handoff bundle。

## Evidence priority

1. Audio reality.
2. Chirp 3 provider word evidence.
3. Current canonical scripture/mantra context when content mode explicitly enables it.
4. Same-lesson human transcript as high-value spelling/context reference.
5. Golden Corpus / Error Memory / Golden Rules.
6. General terminology and ordinary Chinese.

Human transcript is never timing truth and never authorizes adding speech that the
audio/ASR completeness evidence has not established.

## Golden rules

1. **不漏內容**：Chirp 已確認的語音不得因 reference 缺字而消失。
2. **不憑空補內容**：reference 有、但音訊/ASR evidence 無法支持的文字不得硬補。
3. **專有名詞正確**：reference、canonical context 與 Golden Rules 用於拼字、
   經名、人名、佛教術語與固定咒語的保守校正。

## Timing contract

- Chirp 3 word-level timestamps 是唯一 timing truth。
- ChatGPT 只處理文字與語意，不提供或修改 provider timing evidence。
- Preferred handoff import 僅回傳完整 `segment_id + corrected_text`。
- 最終閱讀 cue 的合併/拆分由 deterministic renderer 依 Chirp word boundaries
  完成，不得依字數比例推算時間。
- Raw Chirp、merged words、provider responses 與 source segment timing 永久保留。

## Completeness gate

ChatGPT handoff 之前必須 PASS。

至少檢查：

- timeline structure / monotonic timestamps
- chunk/course-relative density
- audible mid-file subtitle gaps
- tail coverage
- targeted-repair residuals
- provider evidence integrity

若 FAIL：

1. 先依 production policy 執行 bounded targeted Chirp repair。
2. repair 後重新建字幕並 recheck。
3. 超過安全上限、budget gate、ambiguity 或 residual 仍存在時設
   `NEEDS_REVIEW / awaiting_review`。
4. 禁止用 reference 或 ChatGPT 補寫 Gate 尚未確認的漏辨識。

## ChatGPT handoff

PASS 後使用 handoff bundle：

- `chirp-raw.srt`
- `segments.json`
- `merged-words.json`
- `chirp-completeness.json`
- `raw-transcript.txt`
- `golden-rules.json`
- `canonical-context.json`
- `handoff-manifest.json`
- `INSTRUCTIONS.txt`

校訂時：

- 保留完整 segment coverage 與順序。
- 不改 raw evidence。
- 人工逐字稿只在 ordered alignment / acoustic evidence 支持範圍內使用。
- 佛經課程可套 canonical scripture/mantra，但講師白話、譬喻與改述不可強制
  換成經文原句。
- 嚴重 deletion/addition/rewrite 必須 fail closed 或回 raw Chirp text。

## Re-entry and deterministic finish

以 owner-only handoff import endpoint 回灌完整 `segment_edits`；legacy SRT import
僅在 cue 數與每個 timestamp 完全一致時可用。

Import 成功後只續跑：

ChatGPT text layer
→ deterministic cleanup
→ Golden Rules audit
→ semantic rendering
→ export
→ QA / validation
→ safe Drive publication

不得因此重跑 Chirp 或 server-side Gemini。

## QA acceptance

發布前至少要求：

- Completeness Gate PASS / handoff_allowed
- coverage residual = 0
- segment import coverage = 100%
- timestamps monotonic
- overlap count = 0
- empty cue count = 0
- invalid duration count = 0
- pathological short/long cue 有清單且符合 policy
- first/tail coverage 合理
- content drift / addition / deletion gate 通過
- final SRT/TXT 可重新解析
- manifest / checksums / audit evidence 完整

任何 QA fail 都不得自動發布。

## Drive delivery

來源 media 永不改名、覆蓋或刪除。

最終至少發布：

- `<source basename>.srt`
- `<source basename>_逐字稿.txt`
- `<source basename>_transcript_report.json`（或 production manifest 對應命名）

若 final sidecar 已存在，使用 production resumable safe-publish：
pending upload → verify → timestamped backup → promote → final verify。
Drive delivery failure 只重試既有 local artifacts，不得重跑 Chirp / ChatGPT。

## Audit manifest

至少保留：

- source id/path/checksum/duration
- job id / revision / workflow mode / content mode
- Chirp model/strategy/chunks/repair rounds
- completeness status
- reference file ids/names/digests
- ChatGPT handoff/import revision
- canonical / Golden Rules versions
- cue / word / text statistics
- QA verdict
- output SHA-256
- Drive publication state
- timestamps / created_at

不得保存或輸出 service-account JSON、OAuth token、rclone config、Cloudflare token
或其他 secrets。

## Handoff summary

每次完成時回報：

1. Source Drive folder/file.
2. Job id and whether Chirp was reused.
3. Completeness/repair result.
4. Reference files actually used.
5. ChatGPT correction/import result.
6. QA summary.
7. Final Drive filenames and verification.
8. Any residual review items.

