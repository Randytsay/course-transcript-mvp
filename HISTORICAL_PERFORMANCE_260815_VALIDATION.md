# Historical Performance Validation

Job：`260815-20260816-152635-39ffbc`
資料來源：既有 VPS job artifacts；沒有重新執行付費辨識。

## Effective stage accounting

| stage | effective duration | share of active duration |
|---|---:|---:|
| correction | 1,959,068 ms | 84.8% |
| chirp | 297,230 ms | 12.9% |
| normalize | 20,141 ms | 0.9% |
| download | 13,201 ms | 0.6% |
| qa | 6,561 ms | 0.3% |
| validation | 5,627 ms | 0.2% |
| cleanup | 2,573 ms | 0.1% |
| export | 2,554 ms | 0.1% |
| segment | 2,021 ms | 0.1% |

舊 download attempt #1 的原始 `activeDurationMs=5,245,994` 仍保留為 `observedActiveDurationMs`，但標為 `reportingStatus=superseded_unclosed`、`excludedFromEffectiveDuration=true`，所以不再污染 download bottleneck。有效 active stage duration 為 2,308,976ms；`activeRealTimeFactor=0.2123`，整體 `realTimeFactor=0.4824`。

## Provider accounting

- Google Vertex / Gemini 3.7 Flash：177 calls，retry 0。
- MiniMax M3：18 provider evidence，retry 1；`24210570799bbd95.bf425abbe942` 的 `attemptCount=2`、`retryCount=1` 維持歸屬 MiniMax。
- MiniMax 顯示 `Token Plan`，不誤標成 Gemini API NT$0。
- 估算成本：Chirp NT$17.62、Gemini NT$36.92、MiniMax NT$0（Token Plan）；Cloud Billing 仍是正式帳務來源。

## UI verification

`frontend/components/performance-page.tsx` 已以實際 provider 映射：

- `provider=minimax` → `MiniMax M3`
- `provider=google-vertex-ai` → `Gemini / Vertex AI`
- `billingMode=token_plan` → `Token Plan`

重新產生的報告位於 production job directory 的 `performance-report.json`、`performance-report.csv`、`performance-report.html`。
