# Golden V2 四筆未結案｜原音聲學抽查與交付接續紀錄

2026-10-24 20:54 後由獨立代理針對正式 VPS ledger、既有 Chirp raw/
acoustic QA、原音 FLAC 及 Drive 來源執行**唯讀**查核。此文件只記錄
確定的待解決工作，不把候選字幕或音量/VAD 判定稱為已完成覆聽。

## 機器與授權現況

- 正式 Worker 運行 `17d33faf6239ca5a1fcdfe91f18cef202f271b0f`。
- 自動救援／AI 草稿 PR [#149](https://github.com/Randytsay/course-transcript-mvp/pull/149)
  為 open/draft、未合併、未部署。不得把 PR 測試通過說成 production 已修復。
- 任務帳本維持：
  - 250503: `PROCESSING`，Gemini `REVIEW_REQUIRED`、Chirp `RUNNING`。
  - 251025 / 251018 / 261017: `NEEDS_REVIEW`，Gemini `COMPLETE`、
    Chirp `REVIEW_REQUIRED`、最終 QA 未 PASS。
- 已完成 Chirp/文字不可刪除或重新付費送出。原來源授權不自動擴張。
- 修復的既有 `scripts/golden_v2_lossless_repair_drive.py` 的主入口
  **刻意 `EXECUTION_DISABLED`**（缺可審查的 lexical target plan），
  不可把 VAD 單獨檢測作為付費語音補辨識的授權。
- 本次檢查未提交任何新的 Gemini/Chirp operation、未修改 production
  ledger/Drive/Cloud IAM、未擴增費用授權。

## 各筆目前 Drive 產物（確實存在，但不代表 Golden QA PASS）

| 原音 | Google Drive 現有檔案 | 任務完成度 |
| --- | --- | --- |
| 250503 | `語音 250503_.srt`、`語音 250503_待校對_術語修正版.srt`、`語音 250503_待複核清單.txt` | 2860 cues，33 處詞彙校正；仍有語音漏段 |
| 251025 | `251025_.srt`、`251025_修復校字版_待全檔驗收.srt`、對應 TXT | 未通過聲學 QA |
| 251018 | `251018_.srt`、`251018_修復校字版_待全檔驗收.srt`、對應 TXT | 未通過聲學 QA |
| 261017 | `261017_.srt`、對應逐字稿 | 未通過聲學 QA |

## 來源 FLAC 局部抽查：只推導「需複核」，不能直接補字

抽樣使用 provider-free `_probe_nonspeech`（RMS + local VAD +
ffmpeg peak），每段僅約 12 秒。正面結果代表局部有與語音相容的
聲學活動，不能代替辨識文字或聲稱整段都是漏講。

| job | 候選空窗 | 局部抽樣結果 | 本輪處置 |
| --- | --- | --- | --- |
| 261017 | 02:58:24.760–02:59:00.040 | 12s SPEECH_CONFIRMED | 定點補辨識候選 |
| 261017 | 01:00:47.720–01:01:09.480 | 12s NONSPEECH_CONFIRMED | 僅此 12s，**不是**整段 PASS |
| 251018 | 00:15:42.760–00:20:19.120 | 12s NONSPEECH_CONFIRMED | 尚需檢查後續 264s |
| 251018 | 00:37:49.640–00:40:34.800 | 12s SPEECH_CONFIRMED | 定點補辨識候選 |
| 251025 | 02:04:17.120–02:09:47.600 | 12s SPEECH_CONFIRMED | 定點補辨識候選 |
| 251025 | 01:10:19.440–01:14:15.360 | 12s SPEECH_CONFIRMED | 定點補辨識候選 |

另有 250503 高優先空窗：
- 00:43:20.160–00:44:30.080：連續聲學切片發現與語音相容活動，
  且同目錄參考逐字稿有相應敘述，需核對整段與局部補辨識。
- 02:21:39.320–02:23:08.360：前兩個 30s 聲學活動陽性；
  口述／板書需分離。
- 00:03:59.320–00:04:22.240 與 00:12:25.520–00:12:42.920：
  需釐清板書期間原音。

## 必要下一步，不再從頭做

1. 在新的受控 QA 工作中對剩餘未檢測長空窗做完整、可中斷恢復的
   **無付費** VAD/RMS 區段抽查；各樣本的結論不可擴張成全段判定。
2. 將來源有語音的區段，結合原始逐字稿／現有 Gemini／Chirp
   字詞和精確音訊，做 **source-bound lexical target plan**；
   無證據的字不插入正式 SRT。
3. 經有效原始 provider 授權及新補辨識 quote/ledger reserve 條件
   審核後，只提交尚未辨識的局部片段；不重送既有操作。
4. 將定點修復結果、同音佛學術語、原音時間碼逐段 cross-check。
   最後以保留舊檔的版本化交付契約核對新 SRT/TXT 的 Drive ID/hash。
5. Golden 人耳覆聽與 AI_REVIEWED 機器品質等級分開，不假造
   `confirm_listened`，正式發布需對應的有效 owner grant。

此紀錄不代表任務完成。**四筆正式帳本都未結案**。
