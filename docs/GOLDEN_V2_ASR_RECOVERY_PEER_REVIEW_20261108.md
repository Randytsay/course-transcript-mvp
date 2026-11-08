# Golden Transcript V2｜ASR 漏字自動修復：跨 AI 同儕審查與最小可行性驗證（2026-11-08）

> **文件用途**：讓不具備本次 ChatGPT 對話的第二位 AI／VPS Agent，依據可追溯的 GitHub 證據獨立審查問題根因、挑戰技術選型、提出更好的替代方案及最小可行性實驗（POC）。不是已批准實作、部署或 paid API 的任務命令。
>
> **當前結論**：Golden V2 仍為 **PRODUCTION NO-GO**。2026-10-31 課程已交付 AI-limited TXT/SRT 與 11/08 另外版本的 reviewable SRT，但 **Golden QA 未通過**。目前**不能**斷言「Chirp 3 本身不穩定」或「八段全是 Chirp 漏字」；首先須區分 provider 原始 transcript 是否有字、word offsets 是否完整、merged words 是否丟字、對齊與 SRT renderer 是否錯誤，以及缺口是否真的包含人聲。
>
> **資料敏感性**：本 repository 是 PUBLIC。本文件只記錄已公開的安全 metadata / hash / URL；**不要**提交私有語音、逐字稿全文、token、operation 全名、私有 DB、雲端儲存 URI、服務帳號資料或人耳聽寫內容。
>
> **證據截點**：2026-11-08 Asia/Taipei。閱讀時請先查最新 Issue #160、#173 和實際 release/PR SHA，不能把本文的時間點快照視為即時實況。
>
> **工作邊界**：本文是供同儕審查的「設計草案＋可驗證假設」。如需對 VPS 受保護資料做新動作，必須依 Issue #173 的 AGY-LOCAL TASK / RESULT / REVIEW 協作流程取得確切 scope；另需使用者獨立授權任何 Production mutation、owner grant／Ledger、模型安裝/下載、可能付費的 API、Billing 查詢、Drive 寫入、merge／deploy。禁止把讀本文件當成任何執行許可。

## 0. 給第二位 AI 的精簡委託

**你要做的不是立即寫一個全新 ASR 系統。** 請先回答：

1. 目前八段字幕空白屬於 **provider 原始 ASR 空白／transcript 有字但 words 缺時間／raw 有字而 merge 遺漏／對齊器錯誤／SRT 打包錯誤／實際非人聲** 的哪一類？每一類需哪些證據才能確認？不得用音量峰值當作語音存在證明。
2. 舊 V1 Completeness Gate／Targeted Standard Batch Repair 是否已可重用？V2 的實際卡點在 interface、early REVIEW_REQUIRED state、grant/cost、provider evidence、還是音訊辨識本身？
3. 是否有比 Silero VAD + sherpa-onnx（或 FunASR）+ source-bound alignment 更小、更好維護的方案？請比較 CPU、ARM64、中文／台語、模型 license、模型下載大小、時間戳證據、運行成本與風險。
4. 能否在**不碰 Production、不送雲端、不下載模型**的情況下，先完成 raw/merged/SRT 正規化比對與八段 gap 分類？要執行實際離線模型時需另列授權及資源估算。
5. 交付一份有 **PASS/FAIL/UNKNOWN** 的 POC 判定，展示 source SHA、八段 intervals、raw/merged word/text coverage、VAD output（如獲准）、模型輸出（如獲准）、resource usage、failure cases、下一個最小修復 PR 的範圍。**不可**把「有一份 draft SRT」當 Golden PASS。

**優先讀**：[總驗收 Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160)；[協作 Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173)；[協作規範](GOLDEN_V2_AGENT_COORDINATION.md)；[上線總計畫](GOLDEN_V2_GO_LIVE_MASTER_PLAN.md)；[AGENTS.md](../AGENTS.md)；[GLOBAL_GUARDRAILS.md](../GLOBAL_GUARDRAILS.md)。此任務屬於**外部同行評審**；除非 #173 已派發新的 AGY-LOCAL 任務，請只做你原本權限可做的 repo/公開資料審查。

## 1. 專案使命與原訂管線

Repository：https://github.com/Randytsay/course-transcript-mvp

使用者指定 Google Drive 課程資料夾後，最終希望在**原資料夾**可靠產生來源綁定、可稽核、具語音支撐的繁體中文 TXT/SRT，主要內容包含佛經／國台語、經名及術語。理想流程：

1. Drive 來源辨認與去重 → SHA/duration/lineage → 成本預估與核准；
2. 標準化為 16 kHz mono FLAC；保留原始檔與來源 SHA；
3. Gemini 音訊主稿（理想 V2 規格為 Vertex gemini-3.8-flash；需依實際執行證據確認）；
4. Google Speech-to-Text V2 Chirp 3 獨立分段辨識；保留 **provider raw transcript 與 word offsets**，所有 chunk 使用全音檔 absolute milliseconds；
5. VAD/acoustic coverage → 只在已確認的缺口做有上限的 Standard Batch／離線重辨識；不可重送已完成 Operation；
6. Gemini／第三 ASR／人工參考稿比對，音訊優先，不可憑經文上下文補出未講內容；
7. 文字裁決 → 可信聲學錨點對齊 → cue segmentation/renderer → Golden QA；
8. 授權後 versioned Drive SRT/TXT/QA 安全發布、讀回檔 ID/SHA，Ledger 完成交付。

**注意**：以上部分是目標設計而非目前已在 Production 全部落地。官方 Golden V2 #160 G2–G10 仍 OPEN；Gemini 主稿有沒有對某一特定課程完成，不能由理想規格推定。已完成一份 AI-limited 候選輸出不代表 compare/adjudication/golden_qa 完成。

重要約束：既有安全規範以 Chirp provider 字詞時間戳作為時間證據；**當 provider timestamp 不存在時不得讓 LLM 平均分配、想像或無聲學依據地回寫正式時間碼**。如獲准引入第二聲學對齊模型，應定義其獨立證據與審核門檻，修改「時間權威」政策前需單獨審查，不能暗中替換。

## 2. 實際範例：2026-10-31《金剛果論》31～32

- 來源原資料夾：https://drive.google.com/drive/folders/14YfBNRbSsXaUcdEhy5GaSmOJLDHDjL55
- Job ID：v2p_6c8bfcd842315946abbac8d257f0bb70
- 原始 MP3：261031_.MP3；duration **10517.2114 s**；SHA-256 **62911bbd08d35eff84c1f614d6c1000c3b8015f0b25d6df49d9b4acb99753f44**。
- Normalized FLAC：16kHz mono；duration **10517.1994 s**；SHA-256 **8afc970062db3b5eb186daf11f84ab43d84e513ca9442b09c7ea74f34b4fa247**。報告指出 Job/source/stage/operation lineage match。
- AGY-LOCAL-004 報告：**12 個** Chirp chunk（chunk-0000..0011），**29,769** 個合併字詞 offset；stage local actual **59¢**（不是獨立雲端發票證明）。Job rev4 **NEEDS_REVIEW**、chirp **REVIEW_REQUIRED**、compare/adjudication/golden_qa **PENDING**。
- 11/04 AI-limited SRT：**2,879 cues**，SRT 基礎語法 pass、可見文字與校對 TXT 正規化後一致；**217 cues <300ms**；cue #212 **656 字／3.60 秒**；**八段 >8s gap 總 840.14 s**，其中三大 gap **754.98 s**。這明顯是失敗的閱讀與時間軸品質，**卻不等於證明所有 gap 都有漏掉的語音文字**。
- 11/07 AGY-LOCAL-005 在隔離 scratch 製成 **8 個 16k mono PCM16 WAV**，總 padded **888.14s**，資料大小約 28.4 MB；回報每個 clip 的 SHA／樣本數驗證一致。**該成果是「準備可覆聽片段」，不是完成覆聽、VAD 驗證、ASR repair、forced alignment 或 Golden QA。**
- 11/08 另外保存一份 **2,814 cues** 的「供核對／時間部分推估」draft SRT，保留 11/04 可見校對文字，並提供八段疑慮回覆表；其中 **76 段採 provisional 時間**，不能稱為聲學對齊 PASS。該次特定 Drive draft 已讀回 SHA，屬一次性手動批准交付，**不代表 Standing auto-publish policy 已部署**。
- Production Paid Worker 與新的自動 Drive 發布仍按最近受控回報停用；PR #181 為 DEV Draft，含 timeline QA、review intervals、human review template 與 progressive candidate generator，**非已合併 Production 流程**。

### 八段精確 gap（由 AGY-LOCAL-005 來源報告；時間為原始音檔絕對座標）

| ID | 對應 cues | gap 起 | gap 迄 | gap 秒數 |
|---|---|---|---|---:|
| 01 | 212→213 | 00:13:29.000 | 00:20:19.200 | 410.20 |
| 02 | 213→214 | 00:20:20.200 | 00:20:40.720 | 20.52 |
| 03 | 420→421 | 00:29:49.920 | 00:32:53.440 | 183.52 |
| 04 | 421→422 | 00:32:54.020 | 00:35:35.280 | 161.26 |
| 05 | 511→512 | 00:36:43.800 | 00:37:00.520 | 16.72 |
| 06 | 1701→1702 | 01:45:10.200 | 01:45:27.200 | 17.00 |
| 07 | 2156→2157 | 02:10:51.760 | 02:11:00.720 | 8.96 |
| 08 | 2870→2871 | 02:54:07.080 | 02:54:29.040 | 21.96 |
| **合計** | | | | **840.14** |

每段 scratch WAV 含前後各 3s 的背景上下文（合計 **888.14s**）。請勿將 840.14s 的字幕 gap 與 888.14s 的有 padding 檢查窗混為一談，也不能把「無 provider word」翻譯成「無人聲」。

### 關鍵證據矛盾，必須原始位元組級重新對帳

- AGY-LOCAL-004 曾述在 provider-raw 的 **1942.840s** 有孤立字詞，並報告主要 gap 有無 word offsets。
- AGY-LOCAL-005 稱該 1942.840s 是 PR #181 **synthetic fixture**，查詢實際 **chirp-merged.json** 八段為 zero anchors。
- **兩份報告的證據層級不等**：provider-raw 與 merged 是不同 artifact。即使 merged 沒字，也無法單憑這點斷言原始 provider 回覆無字。
- 必須針對 SHA-matched chunk-0000/0001/0002，將 **raw alternatives[*].transcript、words[*].start/endOffset、window absolute start、merge ownership、最後 SRT cues** 逐層核對，輸出**彙總統計和每段分類**，不要在公開 GitHub 貼課程原文或 raw JSON。

來源：[AGY-LOCAL-004](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6706738290)、[AGY-LOCAL-005](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6709882350)、[正式 reviewer 提醒](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6716758028)、[11/08 draft 交付](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6717485807)。

### 已在原 Google Drive 資料夾中的使用者檔

- 261031_逐字稿.txt（人工／原參考，不能當聲學真值）
- 261031_AI校對_含待複核.txt（AI-limited 校對候選）
- 261031_AI校對_含待複核.srt（11/04，有問題的時間軸）
- 261031_字幕候選_供核對_時間部分推估_20261108.srt（11/08 provisional）
- 261031_8處疑慮時間_現有詞句_可直接回覆_20261108.txt
- 261031_.MP3

不要覆寫上述檔案，也不要將 provisional time 當 Golden。

## 3. Provider/中間產物實際在哪裡

依 AGY 既存私有來源唯讀回報，正式資料根目錄大致是：

~~~text
/opt/course-transcript-v2-chat/data/golden-v2/
  ledger.sqlite
  jobs/
    v2p_6c8bfcd842315946abbac8d257f0bb70/
      golden-v2/
        source-lineage.json
        source.original
        normalized.flac
        chirp-evidence/chunk-0000/provider-raw.json
        chirp-evidence/chunk-0001/provider-raw.json
        ...
        chirp-evidence/chunk-00xx/window.json
        chirp-merged.json
        chirp-acoustic-qa-*.json
        candidates-aligned/aligned.srt
        candidates-aligned/aligned.txt
~~~

上述路徑是報告中的合理定位，**仍須在允許的唯讀身分下重新 find/realpath 確認，不可假定檔名在所有版本完全一致**。八個 WAV 位於另行隔離的 private scratch（AGY 只回報 /home/ubuntu/ 下 mode 0700，未公開精確 path）。

原始 Chirp 通常是 Google Speech-to-Text V2 BatchRecognize 結果 JSON，包含多個 results / alternatives / transcript / words / startOffset / endOffset；V1 解析路徑在 [chirp_chunk.py](../app/providers/chirp_chunk.py) 中從 provider JSON 取 alternatives[0].words 生成 words.json。**這只是「可能因 text/word coverage 不一致而漏字」的待驗證假說，不能直接推論 V2 的實際路徑也有相同 bug。**

Golden V2 的 GCS 原始回應 bucket/object 不在公共 repo；只讓具有原既有權限的操作員私下核查，禁止將私有 bucket/operation ID 貼入 PUBLIC Issue。

## 4. 為什麼一直無法正式驗收：拆開「技術缺口」和「營運 Gate」

### A. 聲學／字幕技術缺口（20261031 G8）

1. **辨識作業 DONE 不等於 recognition completeness**；Chirp 3 以 word offsets 建立 timeline，但有長達 410s、184s、161s 的原 SRT no-cue gaps。
2. **根因尚未確定**：是模型未轉出文字、文字存在但無 word offsets、merge 遺漏、切片相對偏移錯誤，還是 alignment/cue renderer bug？不應提前歸咎於 Chirp 模型不穩定。
3. **RMS/audibility 不能證明人聲**；現有 WebRTC VAD 只能辨識部分 speech/non-speech，且長 gap、背景音樂、誦經或國台語仍可能誤判；要有可復算的 speech interval evidence。
4. **已有 repair-planner ≠ V2 repair 自動閉環**：V1 [chirp_completeness_gate.py](../app/providers/chirp_completeness_gate.py) 已有 _vad_assessment、build_auto_repair_patch_plan（預設 per-round 600,000ms、12 patches、single 300,000ms、context 5s）、後續 Standard Batch 方向；但 #160 G2 early-Chirp REVIEW_REQUIRED reentry、grant/revision/cost/idempotency 和 V2 report schema／merge／QA 尚未正式完成部署。
5. **字幕 renderer 本身也獨立失敗**：有 656 字／3.6s、217 short cues。只是修補 ASR 字詞，不會自動讓文字 segmentation、跨 gap 對齊、minimum cue duration、CPS、重疊／去重等全部合格。
6. **Gemini/ChatGPT 校稿不是已無問題**：11/04 SRT 可見文字等價 TXT，只證明文件一致，未證明實際聽寫精確。compare/adjudication/Golden QA 仍 PENDING；不得用語意合理性填補聲學空白。

### B. 其他正式環境／營運 Gate（不能跟 10/31 聲學修補混為一談）

- #160 G2–G10 OPEN、Production NO-GO；含 owner grant/HMAC/revision、已付費 Chirp LRO 對帳、不重送、release exact SHA、冷還原/rollback、Worker/Drive 授權、final QA、canary。
- 2022/12/17 另一筆 Job：v2p_cce7a9147a495027bd16f5619ad17b8d。舊外部聲稱 **34 operations/136¢** 未被證實；本機資料與 AGY-LOCAL-003 報告 **16 known operations/78¢**，其中 16/16 provider get done。尚未對帳全部雲端作業與 Cloud Billing，**不能**將 18 筆視為實際遺失，也不能重新 POST。
- 正式 API / Background Worker 過往有不同 image SHA / RO source bind / Compose split-brain，根據 #173 當下證據重新查；不可從 DEV PR CI GREEN 直接推斷安全上線。
- 每個提交的資料夾新增模型費用與預留總計受 **US$6.00 cap** 約束；不是每個檔 $6。何時能自動付費依持久 signed grant、目前 Stage/Owner 權限核准。
- 2026-10-31 的 11/08 preview 屬單次授權、版本化**草稿交付**；不得將 Golden QA 均為 PENDING 的結果偽稱 final delivered。

## 5. 值得借鑑的 GitHub 方案（截至 2026-11-08）

> 以下是設計候選，不是已安裝／已在本 VPS 實測的技術棧。**需要獨立核對 checkpoint license、模型版本、CPU benchmark、ARM64 wheel、模型大小和台語/佛經實際準確率。** 不推薦一次安裝六個大型模型。

| Repository | 主要用途 | 值得借鑑 | 限制／風險 | 初步優先 |
|---|---|---|---|---|
| [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | speech activity detection | 16kHz、ONNX CPU，可與 Chirp word interval 做 speech-uncovered comparison；MIT 庫 | 不生成文字；演講、音樂、誦經、安靜發音均需真實語料驗證；ONNX I/O 要自己接 | **P1** |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 本機 CPU ASR + VAD runtime | 支援 ARM64 Linux、Silero VAD、中文 Paraformer/Zipformer/SenseVoice 路線；避免整個 PyTorch 大環境 | runtime 支援 ARM64 ≠ 所有 checkpoint 有同等精確/可用 timestamps；模型 weights/license/大小必查 | **P2 首個離線 ASR 候選** |
| [modelscope/FunASR](https://github.com/modelscope/FunASR) | 中文 ASR、FSMN VAD、Paraformer、標點、句子時間戳 | 參考成熟的中文分段 pipeline、timestamp/schema；可能在佛經中文較適合 | 安裝/model path 可能偏重；FunASR toolkit 支援某功能 ≠ 每 checkpoint 都支援；部分模型方言需實測 | **P2 比較或 fallback** |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 獨立 Whisper ASR、Silero VAD | CPU int8、word-timestamp / VAD 可供交叉辨識 | 小 CPU VPS 耗時和記憶體可能不合；中文術語與台語要量測；Whisper timestamps 不能直接替代聲學驗收 | **備援** |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | 強制對齊／詞時間碼 | 若文本已人工或模型確認，可研究中文 alignment | 語言特定對齊模型，中文/繁簡、罕字、佛經、CPU/torch 依賴；forced alignment 不會替你證明文字真的被說出 | **P3 可選** |
| [jianfch/stable-ts](https://github.com/jianfch/stable-ts) | subtitle segmentation / timing heuristics | 借鏡 silence suppression 與 SRT cue 分組 | **官方 README 表示開發無限期暫停**；不建議作為新系統核心依賴 | **僅參考** |

外部官方證據：
- [Google Cloud Chirp 3 文件](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3)：啟用 word-level timestamps 可能造成辨識品質下降；BatchRecognize 啟用 word offsets 目前文件一般上限 20 分鐘。操作前重新驗證目標 region/version，不能把這個推測成所有雲端呼叫一定漏字。
- [Silero VAD README](https://github.com/snakers4/silero-vad)：16 kHz + ONNX，偵測 speech，不做 ASR。
- [sherpa-onnx README](https://github.com/k2-fsa/sherpa-onnx)：ARM64 Linux，離線 VAD/ASR，支援多模型。
- [FunASR README](https://github.com/modelscope/FunASR)：checkpoint/pipeline 不同，模型輸出、時間戳支援需 individually verify。
- [WhisperX README](https://github.com/m-bain/whisperX)：語言特定 forced alignment；需相應 alignment checkpoint。
- [stable-ts README](https://github.com/jianfch/stable-ts)：開發暫停。

### 初步架構提案（請審查者主動挑錯）

1. **P0 raw-first 原因分層**：只有確認 provider-raw 中**真缺 transcript／真的缺 time offsets**，才進入 speech recovery；如果 raw/merged/SRT 中間遺失，優先修 deterministic parser/merge，不花第二次模型費。
2. **P1 VAD Coverage Map**：對原 FLAC 或既有 WAV 取得 speech intervals，與 provider word intervals、SRT cue intervals 分別交集。產出每個 gap 的分類：
   - RAW_HAS_TEXT_AND_WORDS / MERGE_LOSS
   - RAW_HAS_TEXT_NO_WORD_TIMING / ALIGNMENT_REQUIRED
   - RAW_NO_TEXT_WITH_VAD_SPEECH / ASR_RECOVERY_CANDIDATE
   - VAD_NON_SPEECH_SUPPORTED / KEEP_GAP_WITH_EVIDENCE
   - UNCERTAIN_SPEECH_OR_PROVENANCE / REVIEW
   每類要保存來源 SHA、模型/閾值、cut boundaries、原始與絕對時間、證據 ID；不得將 VAD=non-speech 自動當作音訊最終無人聲結論。
3. **P2 bounded windows**：根據 VAD speech start/end（有必要時用 20–60s window 及 3–5s context，純屬建議初始值，依實測調整），拆分 410s 長 gap；如果 gap 被切在原 Chirp chunk 邊界，要把原 chunk ownership、overlap 和 absolute offset 重建正確。
4. **P2 ASR fallback**：優先比對已保存的原始 transcript 與人工原參考，再對確定缺失的窗口測試 sherpa-onnx；若中文效果差，測 FunASR；如果仍不可靠，之後才對特定 bounded window 提出已核准 Standard Batch 或獨立 ASR。**嚴禁在沒有 Owner Paid Grant 時自動呼叫任何雲端模型**。
5. **P3 alignment & merge**：先來源綁定 verified speech + candidate text；只接受有可追溯 source-window/offset/time 的詞或句。原 Chirp 可信 words 保留 immutable，新 patch 附 provider/model/version/source SHA/sha256；context 重疊用確定 ownership 去重，碰到衝突保留 REVIEW，不能憑字串相似就刪已朗讀內容。
6. **P4 final SRT render/QA**：合併已證實的文字，重新分句、控制過短 cue、過長文字、高 CPS、空段/時間超界、重疊與 TXT 一致；無可靠聲學錨點只能進入明確的 preview/provisional track，不能 Golden PASS。
7. **P5 re-entry**：在 DEV-only tests 通過後，對接 V2 早期 Chirp REVIEW_REQUIRED 的可信 reentry / signed grant / idempotency / retry budget / crash recovery。不讓使用者或 AI 只靠 state relabel 繞過 QA。

**請特別檢查反例**：
- Chirp raw transcript 有文字，但 words 是空的；最可能先做重新對齊，非重送辨識。
- VAD 在佛經誦念／背景音樂上有 false positive，不能觸發無限 paid repair。
- 同一說話者講台語、佛經專名；第二 ASR 可能 hallucinate 文字，不能因與參考稿接近就當真實逐字。
- 覆蓋率不能只用「word timestamps span/audio duration」來算，因為自然靜音和 VAD false negative 會扭曲意義。
- 片段末端 overlap / relative offset / ms-vs-seconds / inclusive/exclusive 邊界；不得導致重複字幕或跨窗口錯位。
- 10/31 的 656 字被丟進 cue #212，不能靠均勻分配 410 秒就宣稱修好。
- 當 repair window 超出 per-round 600s 時應分輪排程，不得失敗就全重送，也不得 bypass cap。
- 如果缺口仍不能由音訊支持，**仍要保留 owner-reviewable candidate**，而不是把課程整份卡死；Golden PASS 另走嚴格 gate。

## 6. 下一步「最快可行性」：先做沒有模型、沒有費用的 POC-0

**強烈建議先做 POC-0，再決定是否安裝 Silero/sherpa-onnx。** 目前八段 WAV 已被 AGY 回報產生，不應無故重做整堂課切片。

### POC-0｜raw→merged→SRT 因果審計（唯讀、零下載、零推論）

輸入：20261031 的 immutable source SHA、既有 chunk-0000/0001/0002（其他缺口對應 chunk 亦需納入）、各 window.json、provider-raw.json、chirp-merged.json、aligned.srt/校對 TXT、既有 gap manifest。任何敏感輸入皆保留本機私有目錄。

操作：

1. 用已具權限身份查證 **source FLAC SHA / manifest SHA**、檔案大小/可讀性；拒絕非同一 source、不同版本的 sidecar。
2. 從所有 provider-raw **同時計算**：
   - 每個 gap 中 raw transcript 可用字數或字詞量；
   - raw words 數、有效 offset 數、offset 是否落在窗口、是否為 relative 秒轉 absolute 毫秒；
   - 來源 chunk、overlap、ownership 與重複/被 drop 數量；
   - merged words 在 gap 內數量；
   - SRT cue 覆蓋與 TXT 可見文字保存；對異常超長 cue 計算匹配的原始位置／上下文。
3. 產生八列 **非敏感聚合 JSON/TSV**，每列至少包含 gap id/start/end、source SHA、raw transcript nonempty?、raw word count、raw invalid-offset count、merged word count、SRT gap class、分類/UNKNOWN 原因、下一個最小動作。
4. 排除任何自動重新辨識；輸出不包含講課原文、raw JSON 或秘密；保護既有 result SHA；可用 synthetic fixture 對四種反例測試解析器。
5. 若發現 raw 中有詞而 merged 沒詞，建立最小 DEV 修補 regression，先確認能零費用解掉幾段；反之若 raw 真的沒有字，才進下一 POC。

**POC-0 退出條件**：八段均有明確 evidence-backed classification（允許 UNKNOWN，但必須說缺的證據）；對 raw vs merged 的 **1942.840s 矛盾**有重新可復算的解釋；未做任何 provider/model/Drive/Production mutation。若原始 raw 不可讀，回報 BLOCKED，禁止猜測。

### POC-1｜Silero VAD 在隔離環境測試（須確認執行授權及 wheel/weight 來源）

先用已保存的 8 WAV 和少量已知有人聲/已知靜音的對照窗（同樣來源、私有）。只跑 CPU、16k mono。控制 thread、峰值 RSS、wall time、硬碟用量；至少試兩組 VAD threshold/merge/short speech/long silence 參數。不要把每一個 VAD spike 都直接轉為付費補辨識。

輸出：
- 八個 exact-gap 之 VAD speech intervals；原始 WAV absolute time mapping；
- 人工已標註對照樣本上的 precision/recall、誤判案例（若無人工 gold，必須寫 **NOT_EVALUATED**）；
- VAD 與 word/cue 時間線交集；分類 SPEECH_LIKELY / NON_SPEECH_LIKELY / UNKNOWN；
- CPU/RAM/檔案 hash/模型版號；不能宣稱 VAD 100% 準確。

**POC-1 退出條件**：模型能在現有 CPU/記憶體限制內跑完八段並產出 deterministic intervals；來源和 offset 檢查 PASS；至少能區分「需要 ASR 的候選」與「疑似無語音」，但聲學判決仍需抽驗。

### POC-2｜只取最小樣本驗證本機中文 ASR（須新授權下載模型／安裝依賴）

先挑 2–3 段 POC-1 顯示 SPEECH_LIKELY 且 raw 確認缺字的區間：至少一段短、從最長 410s gap 中截取一段、如有台語另取一段。比較兩個候選：
- 路線 A（CPU/ARM64 優先）：sherpa-onnx + 具體 pin 版的 Paraformer / SenseVoice / CTC checkpoint；
- 路線 B（中文模型／字詞時間戳）：FunASR 具體可在 CPU 運行的 Paraformer pipeline。
不可只因「repo 支援 ARM64」就宣稱單一 checkpoint 在本機可順利安裝或高精準。模型授權及來源、網路下載成本、磁碟 headroom 在行動前列明。

輸出：
- 原始音檔 slice SHA、模型來源/授權/版本、raw transcript **只留私有**、是否具 time offsets、耗時、峰值 RAM、磁碟、CPU；
- 與人耳標註的少量匿名統計之 CER/句子漏掉情況；無 gold 時報告 UNVERIFIED，不以模型之間一致視為真實準確；
- 對已見過的 10/31 gap，證明至少**一個已確認有人聲且 raw 沒文字的區間**可以生成可信轉錄候選；如模型只生成文字卻沒可靠時間戳，另標 alignment_required，不得作 SRT acoustic PASS；
- 確認效果差則 STOP，不升級到整堂重跑。

**POC-2 退出條件**：有可追溯 source-bound 的 transcript 候選與資源紀錄；對聲學有效性的判斷有人工／獨立證據；未知保留 UNKNOWN。只有在可重現改善時才選型。

## 7. 建議技術驗收矩陣（不是已通過）

| Gate | PASS 要件 | FAIL / HOLD |
|---|---|---|
| E0 Evidence integrity | 來源 FLAC/MP3、raw/merged/SRT 同 source，hash/readback 能核實；只以既有權限讀 | SHA 不符、無法存取、raw/merged 證據衝突未解 |
| E1 Gap attribution | 八段都給 raw/merged/renderer 可追溯分類和原始 absolute-ms | 只看字幕空段就宣稱 Chirp 漏字 |
| E2 VAD reliability | 對樣本 speech / non-speech 有覆聽標註之 precision/recall；對誦經/台語/音樂有失敗記錄 | 無人工 gold 卻宣稱準確率或 silence；誤判直接啟動 paid |
| E3 Local ASR feasibility | 在 resource budget 內可重現至少一個 speech-confirmed missing interval 的 transcription；指出相對/絕對 offset | 只有漂亮文字沒有音訊對照，或模型安裝 OOM |
| E4 Acoustic alignment | time offsets 依 source 證據、padding、chunk ownership 而來，合理且可核查 | 用文字長度等比分配長 gap 時間；經文作為虛構時間 |
| E5 Cue/renderer | 無 invalid/overlap/blank，TXT normalized equality；過短/過長/CPS/多分鐘 gap 報告可說明；無 656 字/3.6s | 短 cue、過長 cue 仍大量殘留且不標記 |
| E6 Repair idempotency | CAS/revision/owner grant、Operation identity、immutable patches、budget cap、resume/crash/duplicate tests | reentry 觸發重送已成功 Chirp 或超付費 cap |
| E7 End-to-end | 新課程 canary 可在正確授權下穩定完成、Drive readback fileID/SHA、Golden QA 獨立通過；draft 與正式標記分開 | PR/CI GREEN 或 AI-limited delivered 被當 Golden |
| E8 Safety | 無原檔覆寫、無公網洩漏、無未授權 paid/Drive/Production/IAM、可停止／回滾 | 任一未授權副作用即 FAIL |

**建議 benchmark 門檻（先草擬，待真實 gold baseline 後鎖定）**：
- 所有 **speech-confirmed** 真實缺口應有 transcript 或明確 REVIEW；絕不以 VAD=non-speech 直接刪掉原文。
- 每個 patch input SHA 與 output SHA 必須存在、全音檔時間單調、不跨界；保留原始 provider evidence 100%。
- 標準字幕範例可先採 cue ≤42 可見字、顯示時間至少約 0.8s、閱讀速度依繁中情境目標設定，**不能硬套造成新聲學錯位**；要先用 gold 量測才可做硬門檻。
- 若沒有人工覆聽 gold，不能宣告 CER/WER 或 audio-coverage PASS；可先驗 deterministic parse/lineage/QA。
- 不得以單一「99% coverage」替代按 speech duration weighted 的有聲學根據審查。

## 8. 對 VPS / 代理的最小風險操作建議

目前已觀測的 VPS 大致為 2 vCPU、MemAvailable 約 6 GiB、根分割區曾有約 20 GiB 可用，**這是舊時點快照，不是當下承諾**。VPS 可能為 ARM64，請先用 read-only uname -m / lscpu / free / df 查明；不要直接以已過時規格安裝模型。

### 可在確認既有讀取權限後執行的唯讀 preflight 示例

~~~bash
# 只示範唯讀主機資源/路徑辨識；不得用 sudo 提升權限
uname -m
nproc
free -h
df -h /
test -r /opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite && echo LEDGER_READABLE || echo LEDGER_PROTECTED
# 本機現有公開 repo 的讀取：
git -C /path/to/your/existing/checkout rev-parse HEAD
~~~

**禁止**照著本文件執行 apt/pip/model download、Docker restart、git reset/clean/stash、雲端 POST、GCS upload、Production SQLite 寫入、Drive 發布。任何 secure path、credential、private artifact 操作，需以當前 AGY TASK 與 Owner 核准為準；沒有 permission 就回報 BLOCKED。

### 建議 POC 非敏感 JSON 回報 schema（樣式示意，**不是實際結果**）

~~~json
{
  "schema": "golden-v2-asr-recovery-peer-review-v1",
  "date": "2026-11-08",
  "job_id": "v2p_6c8bfcd842315946abbac8d257f0bb70",
  "source_sha": "8afc970062db3b5eb186daf11f84ab43d84e513ca9442b09c7ea74f34b4fa247",
  "scope": "POC-0 read-only evidence classification",
  "raw_provider_sha_verified": "UNKNOWN",
  "gap_count": 8,
  "classified_count": 0,
  "provenance_conflict_1942840ms": "UNRESOLVED",
  "model_calls": 0,
  "network_requests": 0,
  "production_writes": 0,
  "drive_writes": 0,
  "verdict": "NOT_RUN",
  "details_private": true
}
~~~

## 9. 期望第二位 AI 提交的審查內容

請以**表格＋可執行驗證順序**回覆（可透過已核准的 Issue #173 結果回報機制；未派工不得自行建立 AGY TASK）：

1. **FACT CHECK**：指出本文件與最新 repo / Issue / 真實程式碼不一致的地方；所有數值標註是「已獨立觀測、AGY 報告、假說、UNKNOWN」哪一級。
2. **ROOT CAUSE MATRIX**：對八段缺口建立五種根因分類、要讀的 exact artifact、必要時間戳轉換與已存字數統計；先不觸發新推論。
3. **STACK DECISION**：Silero+Sherpa vs FunASR/FSMN vs native WebRTC+Chirp Standard Repair；比較行為、CPU/RAM/disk、模型 license、國語/台語與佛經、維運風險、是否需要 GPU；如果有更好的 repo 請列版本與來源證據。
4. **FASTEST POC**：按照 POC-0 → POC-1 → POC-2 排列，給出每步所需精確權限、成本、預期報告與 STOP 條件。不承諾未測量的準確率。
5. **MINIMAL CHANGESET**：具體哪些現有函式/接口可重用，哪些 PR 應保留或重新建 branch；不要把 #181 的未合併 DEV 能力當 Production 已具備。
6. **REGRESSION DESIGN**：合成 4 類 raw/merged/cue gap、VAD false positive/negative、中文標點、窗界、長 410 秒 gap、API 429/unknown-operation/crash recovery、費用上限、版控 & rollback。
7. **FINAL RECOMMENDATION**：選 1 個最小且成本可控的先行方案；列依賴、量化驗收／未確認之處，以及需 Owner 作出的**下一個最小授權決策**。不得提出「直接重做全部課程」或「先部署試試」作為第一步。

## 10. 參考原始證據與連結（閱讀順序）

1. [Issue #160｜全系統 G1–G10 驗收](https://github.com/Randytsay/course-transcript-mvp/issues/160)
2. [Issue #173｜ChatGPT / AGY 協作](https://github.com/Randytsay/course-transcript-mvp/issues/173)
3. [AGY-LOCAL-004｜來源/29,769 words/八段異常](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6706738290)
4. [AGY-LOCAL-005｜私有八段 WAV 產出、零 human review](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6709882350)
5. [11/08 provenance conflict｜raw vs merged](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6716758028)
6. [11/08 可檢查草稿版本已發布](https://github.com/Randytsay/course-transcript-mvp/issues/173#issuecomment-6717485807)
7. [PR #181｜離線 timeline QA / progressive candidate / human template](https://github.com/Randytsay/course-transcript-mvp/pull/181)
8. [V1 Completeness Gate](../app/providers/chirp_completeness_gate.py)
9. [V1 Chirp JSON parser](../app/providers/chirp_chunk.py)
10. [V2 正式上線主計畫](GOLDEN_V2_GO_LIVE_MASTER_PLAN.md)
11. [Golden V2 規格與逐項驗收](GOLDEN_V2_ACCEPTANCE_AND_ROADMAP.md)
12. [Google Cloud Chirp 3 官方說明](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3)
13. [Silero VAD](https://github.com/snakers4/silero-vad)；[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)；[FunASR](https://github.com/modelscope/FunASR)；[faster-whisper](https://github.com/SYSTRAN/faster-whisper)；[WhisperX](https://github.com/m-bain/whisperX)；[stable-ts](https://github.com/jianfch/stable-ts)

---

**當前總判定：POC-0 尚未執行；POC-1 / POC-2 尚未授權實測。** 文件僅是可被批判與驗證的技術提案。審查者應先找出原始 Chirp 是否已有可恢復的文字，再決定是否值得下載模型或重新花費 Speech API 成本。