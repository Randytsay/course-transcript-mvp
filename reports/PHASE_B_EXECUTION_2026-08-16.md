# Phase B 執行報告（2026-08-16）

## 結論

```text
PHASE_B_STATUS = PARTIAL
PRODUCTION_CUTOVER = NOT_APPROVED
```

本次未啟用生產 M3。生產環境維持 fail-closed 安全設定：

```text
CORRECTION_DEFAULT_POLICY=GEMINI_FIRST
MINIMAX_M3_ENABLED=false
MINIMAX_M3_QUOTA_CHECK_ENABLED=false
```

## 版本與生產 readback

- `main_sha`: `3747b7652fccc518d57292007e5b11ff45d3d304`
- production release image: `049486a3234a37ae5d022eeb1229987c1250dd6f`
- M3 terminology fix staging image / PR #35: `7e17debe7e2fb9e3755ed25e8d3303483443ace6`
- production host: `ubuntu@161.33.193.39`
- API health readback: `status=ok`, API `0.4.0`
- production M3 flags and default policy were read back from the live API/container; no production config was changed.

## Gate evidence

### Live quota

PASS。使用 production secret 讀取 MiniMax Token Plan API，未輸出 key 或 response body：初始 readback 為 HTTP `200`、text pool `general`、interval `100.0`、weekly `68.0`；所有隔離 staging/A-B 完成後再次 live readback 仍為 HTTP `200`、`available/general`、interval `64.0`、weekly `65.0`。production container 本身的 quota live check flag 仍為 `false`。

### Fixed-segment M3 canary

PASS。隔離 ARM64 API image，以 2 段固定繁中字幕測試，segment IDs 保留、無 fallback，產出 2 個術語；production flags 未變更。

### Terminology canary

- main `3747b765` 的整課程 one-shot：FAIL。3,369 段、約 172,561 prompt chars，MiniMax HTTP `200` 但回應 schema 無法解析；稽核顯示 `<think>` 包裝與內層 fenced JSON，且 response 過長。
- fix `7e17debe` 分塊版：PASS。fixture `260801-1934-20260801-205446-9e6ecc` 的 3,369 段以 250 段窗口分成 14 chunks，14/14 audit valid、HTTP `200`、ID set 完整且無重複，合併 304 terms；input/output/total tokens=`82878/27980/110858`，latency=`219890ms`。
- 修正內容已放在 PR #35，包含 bounded terminology chunking、`<think>`/fence JSON normalization、raw audit preservation 與 regression tests。

### Controlled quota exhaustion E2E

PASS。quota preflight 使用真實 Token Plan API；只在隔離 runtime 注入受控 `USAGE_LIMIT`，不偽造 production quota、不耗盡真實帳戶。結果：初始 M3、一次切到 Gemini、後續不重返 M3、segment IDs 保留。隔離 manifest 位於 VPS `/tmp/course-transcript-phaseb-m3-state/quota-exhaustion-e2e-20260816/`。

### Next-source recheck

PASS。受控 failure 的 source A 切換後，建立新的 source B 並再次強制 live quota refresh；quota=`available`，source B 初始 route 回到 M3，未沿用前一個 source 的 switch 狀態。隔離 manifest 位於 VPS `/tmp/course-transcript-phaseb-m3-state/next-source-e2e-20260816/`。

### Full M3_FIRST staging pipeline

PASS（含一次真實 M3 invalid-response → Gemini fallback）。

- v1 staging run 在 M3 invalid response 後正確嘗試切 Gemini，但啟動 command 未帶 `GOOGLE_CLOUD_PROJECT`，因此在 staging harness 以 `KeyError` 結束；這是環境 preflight 缺漏，不是 production mutation。證據：VPS `/tmp/course-transcript-phaseb-m3-state/full-m3-20260816/full-m3.log`。
- v2 已補 production-equivalent Vertex 設定與唯讀 credential mount，使用相同 3,369 段 raw fixture；exit `0`，完成 3,369 段 corrected artifacts。M3=`1,134`、Gemini=`2,235`、raw fallback=`0`，只發生一次單向 switch：`seg-1135` 的 `invalid_response`。
- output 驗證：corrected segment count=`3,369`，IDs/order/start/end timestamps 與 raw 完全一致，non-empty=`true`，changed=`2,008`，fallback=`0`；`subtitles-corrected.json/.srt/.vtt` 與 corrected txt/md 均存在且非空。production raw 與 staging raw SHA256 都是 `ac208616488b5baafaf2fd6a64abc47ab79fff3d2df7cb40bea258f17af3dbf4`。
- M3 correction audit：88 files、74 valid、14 invalid attempts，89 attempts，total tokens=`1,289,956`；terminology audit：15 files、14 valid、1 invalid attempt，total tokens=`127,430`。Gemini fallback audit：131/131 valid、2,235 segments，total tokens=`1,587,065`，latency=`1,181,055ms`。證據：VPS `/tmp/course-transcript-phaseb-m3-state/full-m3-20260816-v2/`。

### Long-course A/B

3 門真實長課程各取前 60 段完成 M3/Gemini 實際 A/B；三門均保留完整 segment IDs、且兩個 provider response 都通過 schema：

| fixture | M3 changed | Gemini changed | exact agreement | M3 latency / total tokens | Gemini latency / total tokens |
|---|---:|---:|---:|---:|---:|
| `260801-1934-20260801-205446-9e6ecc` | 5 | 52 | 9/60 | 119,829 ms / 18,886 | 19,019 ms / 6,166 |
| `260718-20260801-100405-f76e03` | 4 | 55 | 5/60 | 22,425 ms / 6,238 | 17,039 ms / 5,654 |
| `260620-20260801-132242-bc1903` | 3 | 6 | 54/60 | 35,648 ms / 9,237 | 21,986 ms / 6,533 |

A/B data capture PASS，但 quality gate 尚未通過：兩門 exact agreement 很低，且第一門 M3 有 1 次 invalid retry；需要人工逐句判定是 M3 保守或 Gemini 過度修正，不能用 changed count 自動選勝者。A/B audit 位於 VPS `/tmp/course-transcript-phaseb-m3-state/ab-20260816/`。raw Chirp/Drive source 未被修改，也未重新執行付費 Chirp。

## CI / PR

- PR #34 `codex/phase-b-health-monitor`: CI 全綠，修正 approval-waiting job 被誤判 stale heartbeat。
- PR #35 `codex/phase-b-m3-terminology`: CI 全綠，修正 M3 terminology 分塊與 parser。
- 兩個 PR 目前為 draft、尚未合併；在 full pipeline、A/B 與 rollback gate 通過前，不進行 production cutover。
- Production health monitor readback 仍是 `unhealthy`（舊 image `2afd77f5…`），logs 仍將 approval-waiting job 誤判為 stale heartbeat；API health 本身為 `ok`。因此 production health gate 也未宣告全綠，需合併/部署 PR #34 後重新 readback。

## 審查策略與 remaining risks

1. terminology 一律 bounded chunks；保留每次 raw provider response、usage、attempt、source hash。
2. correction response 嚴格檢查 segment IDs；invalid response 只做 bounded retry，失敗後 source-job 只允許單向切 Gemini，禁止 silent auth fallback、禁止重新進入 M3。
3. 每個新 source 強制 live quota refresh；quota unknown/unavailable 直接走 Gemini-first。
4. 產出前逐段比對 raw source 的 segment ID、順序與 timestamps；raw Chirp 結果永不覆寫。
5. M3 目前仍有非決定性的 invalid-response/retry 現象；本次 full run 雖成功 fallback 並完成 artifact，但 M3-only quality 與跨課程一致性尚未證明，不足以直接開啟 production M3-first。

## Recommendation

維持兩個 M3 flags 為 `false`。下一步只剩人工術語/字幕品質審查、成本/usage review 與 rollback/canary gate；所有 gate 通過後才可做小流量 production canary。
