# MiniMax M3 Early Fallback Analysis

分析對象：job `260815-20260816-152635-39ffbc`，source hash `af83491752148e7e`。
只讀取既有 `correction-m3-v1/*.json`、`correction-routing.json` 與 fallback evidence，沒有重新呼叫 MiniMax。

## 確定的 root cause

1. M3 correction 的兩次同一 source hash evidence 都是 `input_tokens=584`、`output_tokens=4096`，`response_valid=false`，error 為 `MiniMax response is not valid correction JSON`。
2. raw provider wrapper JSON 本身可解析，內層 `choices[0].message.content` 以 `<think>` 開頭；套用 `_as_json_text` 移除 reasoning block 後，兩次都剩下空字串，JSON parser 在 position 0 失敗。
3. 兩次 output 都精確等於 `MINIMAX_M3_MAX_OUTPUT_TOKENS` 的 default 4096；這是 output ceiling hit 的強 evidence。儲存的 provider payload 沒有 `finish_reason`，因此不能聲稱 provider 明確回傳了 `finish_reason=length`；但「4096 ceiling + reasoning-only content + 無 final JSON」已足以確定是輸出預算被 reasoning 消耗，沒有留下可解析的 correction JSON。
4. 這不是 missing/mismatched segment ID：parser 在進入 schema、segment count、ID set 檢查前就因 empty/invalid JSON 失敗。
5. 同一 job 的 M3 evidence 共 17 個 audit records；其中 6 個精確等於 4096 且全部 invalid，包含 2 個 correction、4 個 terminology。5 個在移除 `<think>` 後為空，另 1 個只留下 20 個字元但仍不是合法 JSON。
6. routing evidence 顯示：開始時 `m3_available`，在 `seg-0024` 因 `invalid_response` 一次性切到 `gemini-3.7-flash`；之後不重新進入 M3。

因此，584 input tokens 不是造成失敗的原因；真正問題是 M3 在很小輸入上仍花滿 4096 output tokens 做 reasoning，最後沒有產生 final structured answer。terminology 同樣受到「無候選數上限、仍可長篇 reasoning、output cap」的組合影響。

## 方案比較（尚未啟用）

| 方案 | 可靠度 | 速度 | Token Plan 消耗 | 複雜度 | fallback 風險 |
|---|---|---|---|---|---|
| A. provider 支援時啟用 structured output / thinking control；system prompt 明確禁止 explanation，只回必要 schema；校驗 output budget | 高 | 快 | 較低 | 中 | 最低；仍保留 raw evidence |
| B. correction window 減少 segment 數，限制 `corrected_text` 長度與 `uncertain_terms`；terminology chunk 由 250 降至 50–100，並限制最多候選 terms | 高 | 每次較快、request 數增加 | 單次較低、總 request 可能增加 | 中 | 低；可維持 immutable segment IDs |
| C. invalid response 先把該 window 二分，各自 bounded retry；仍失敗才整 job switch Gemini | 中高 | 失敗時較慢 | 失敗時增加 1–2 次 M3 request | 中高 | 比整 job 立即切換低，但需防止重入與 rate-limit |

第一解不應只是把 4096 拉到 8192/16384；那會延後失敗、增加 Token Plan 消耗，不能解決 reasoning 沒有收斂到 final JSON。production M3 尚未因本分析新增任何設定。

## 已完成的程式防護

隔離 follow-up patch 已加入：

- M3 single-flight 與 Gemini effective concurrency 的實際 inflight/max 計數。
- `minimaxInvalidResponseCount`、`minimaxOutputLimitHitCount`。
- requested/initial/final provider、switch reason、switch segment。
- runtime git SHA、Docker image revision 與 provider/retry counts。
- 所有欄位 backward compatible；沒有 credential 欄位；5 個 targeted regression 與完整 202 tests 均通過。
