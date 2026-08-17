# Chirp Recovery Poll Analysis

分析對象：`260815-20260816-152635-39ffbc`；13 個已完成 chunk。
目前 production：`CHIRP_RECOVERY_POLL_SECONDS=120`。
本文件只做離線 simulation，沒有新的 Chirp request。

## 已觀測 evidence

13 個 chunk 的 `recoveryDelayMs`：平均約 81.3 秒、中位數 107.9 秒、最大 132.3 秒；其中多筆落在約 97–132 秒，符合目前 120 秒 poll 週期造成的等待尾端。

Simulation 使用既有 `provider_completed_at`、`recovery_started_at`，以本次批次第一次 recovery sweep 作為 phase，計算下一次 operation status check。數字是估算，不是 provider quota 結算。

| poll interval | 模擬 operation status checks | 平均 recovery delay | median | P95 | 最後 provider completion 後 tail |
|---:|---:|---:|---:|---:|---:|
| 120 秒 | 23 | 86.4 秒 | 93.5 秒 | 117.8 秒 | 82.1 秒 |
| 60 秒 | 23 | 40.2 秒 | 38.3 秒 | 58.5 秒 | 22.1 秒 |
| **45 秒** | **23** | **28.7 秒** | **23.3 秒** | **43.5 秒** | **7.1 秒** |
| 30 秒 | 26 | 24.1 秒 | 22.1 秒 | 28.8 秒 | 22.1 秒 |

## 建議

**首選 `45` 秒。** 以這批 3 小時級任務的 phase 模型，相對 120 秒約節省 57.7 秒平均 recovery delay，最後完成 tail 約少 75 秒；30 秒只再改善約 4.6 秒平均值，卻增加 polling/wake-up 次數，收益不成比例。

多 job 時，operation status / GCS result check 的量近似與 `waiting_jobs × chunks × 1/poll_interval` 成正比；30 秒相對 45 秒約增加 50% steady-state polling，亦提高 VPS wake-up、Google API rate-limit 與 GCS list request 壓力。現有 evidence 沒有 quota 或 rate-limit 失敗證據，所以不選 30 秒。

注意：這批資料中有一個早於第一次 sweep 完成的 operation，其 delay 受 initial sweep phase 主導，單純縮短 interval 不會完全消除；若要進一步改善，應另行評估「首次 recovery 立即檢查」的 worker 排程，不應把它誤判成 provider 變慢。

## Production 操作界線

- 本次沒有修改 production env，現值仍為 120 秒。
- 建議下一步先以 45 秒做一個非付費排程／read-only simulation review，再在明確核准後變更。
- rollback 值：`CHIRP_RECOVERY_POLL_SECONDS=120`。
- 若出現 operation API 429、GCS list rate-limit、VPS CPU wake-up 明顯升高或 waiting jobs 堆積，立即 rollback 到 120 秒。
