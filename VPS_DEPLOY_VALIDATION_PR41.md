# VPS Deploy Validation — PR #41

日期：2026-08-17（Asia/Taipei）
PR：#41（Draft，未 merge）
驗證／部署 exact head：`2675d32e8568f6e3c8e01cf52eed191d1e4f4701`

## 結論

**PASS — 原始 PR #41 exact head 已完成非付費部署閘門並上線。**

本次沒有啟動新的 Chirp、Gemini、MiniMax，也沒有 Drive mutation。原始 raw evidence、manifest、usage metadata 與 operation name 均保留。

## 部署前閘門

| 項目 | 結果 |
|---|---|
| active / leased job | PASS；`active_or_leased=0` |
| 未過期 pipeline lease | PASS；`unexpired_leases=0` |
| SQLite / jobs backup | PASS；`/opt/course-transcript-backups/20260817T184327Z-pr41` |
| DB backup SHA256 | PASS；`5811822f63804e3627c0d5bcac3e924cdd4618a931aba951488b1c2665269368` |
| jobs backup file count | PASS；11,824 |
| api / pipeline-worker / delivery-worker `/app/data` | PASS；皆為同一 host path `/opt/course-transcript-source/data` |
| compose web config | PASS |
| compose billing config | PASS |
| ARM64 image build | PASS；五個服務均為 `linux/arm64` |

第一次 build 因 VPS 磁碟剩餘空間不足而中止，沒有切換 live container；只清理了可回收 Docker build cache 與該次失敗的 exact PR 暫存 image，保留 live image、rollback tag、資料與 backup。第二次 build 成功。

## Non-paid validation

- `python -m compileall -q app tests`：PASS
- container import：`app.api_hardened`、`app.pipeline`、`app.jobs.delivery_worker`：PASS
- Python unittest：202 tests，PASS
- 變更後 observability/concurrency regression：5 tests，PASS
- `npm --prefix frontend ci`：PASS
- `npm --prefix frontend run build`：PASS
- `npm --prefix frontend audit --omit=dev --audit-level=high`：PASS，0 vulnerabilities
- CI（PR #41 原始 head）：5 checks 全 PASS

## Live container readback

| service | image revision | architecture | status |
|---|---|---|---|
| api | `2675d32e8568f6e3c8e01cf52eed191d1e4f4701` | arm64 | running / healthy |
| worker | `2675d32e8568f6e3c8e01cf52eed191d1e4f4701` | arm64 | running |
| pipeline-worker | `2675d32e8568f6e3c8e01cf52eed191d1e4f4701` | arm64 | running |
| delivery-worker | `2675d32e8568f6e3c8e01cf52eed191d1e4f4701` | arm64 | running |
| frontend | `2675d32e8568f6e3c8e01cf52eed191d1e4f4701` | arm64 | running / healthy |

另外，health-monitor、retention-monitor、cloudflared 沒有被本次 release 替換；health-monitor 維持 healthy。

- API `/api/v1/health`：HTTP 200
- frontend `127.0.0.1:3300`：HTTP 200
- pipeline-worker heartbeat：持續更新
- delivery-worker heartbeat：持續更新
- pipeline-worker / delivery-worker restart persistence：PASS
- restart 後 `active_or_leased=0`：PASS
- cutover 後 Drive/provider evidence 新增檔案：0
- cutover 後五個新服務 error marker：0
- cutover 前後 SQLite job status：未變更（`awaiting_review=26`、`cancelled=6`、`completed=10`）

## Scope note

後續 observability 欄位 patch 已在隔離 worktree 完成並通過測試；它不是上述 exact head，因此沒有冒用原始 SHA 宣稱已部署。未 merge `main`，也未直接 merge PR #41。
