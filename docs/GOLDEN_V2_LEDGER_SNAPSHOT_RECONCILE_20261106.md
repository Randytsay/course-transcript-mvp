# Golden V2：歷史 Chirp Ledger 快照唯讀對帳（開發工具）

**狀態：DEV ONLY。** 本工具不能重啟 Worker、不能重送付費 Operation，且不會取得 GCP 帳務真值。配合 [Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173) 的 `AGY-LOCAL-002 v2` 與 [Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160) G4 使用。

## 已知衝突，不能推定為筆數遺失

- **2026-11-06 AGY 本機報告**：20221217 `v2p_cce7a9147a495027bd16f5619ad17b8d`，當前 SQLite 記錄 16 筆 `DONE`，本地 `actual_cents` 合計 78¢，Job rev 5。
- **Issue #160 歷史記錄**：34 筆外部 `SUCCEEDED`，136¢，Job rev 4，尚未與現存 Ledger／provider 帳單對齊。
- 兩份可能代表不同時間、不同來源或 Operation／window 定義；**不得自動主張 18 筆遺失或 58¢ 應沖銷**。

## 在已授權的 VPS DEV 工作樹使用

```bash
python3 scripts/golden_v2_ledger_snapshot_reconcile.py \
  --job-id v2p_cce7a9147a495027bd16f5619ad17b8d \
  --current /opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite \
  --historical pr159=/opt/course-transcript-v2-chat/backups/pr159-20261029T071713Z/ledger.sqlite \
  --historical pr158=/opt/course-transcript-v2-chat/backups/pr158-20261027T074517Z-600487/ledger.sqlite \
  --claimed-count 34 \
  --claimed-cents 136
```

上面的範例是**待執行指令，不是執行證據**；AGY 必須先確認來源路徑和現有獲准的唯讀權限。若權限不足，請回 `BLOCKED`，不要自行提權或修改 ACL。不得執行任何本地備份的複製、解壓、覆蓋或 Production DB 寫入。

### 自動讀取保護和產出

1. 所有來源必須是已存在的 SQLite regular file。連線使用 `mode=ro`、`query_only=ON`，而且每一份 SQLite 檔案內的所有 SELECT 使用**同一個明確 `BEGIN` 讀取交易**。
2. 資料庫之間並非同一個原子快照（`cross_file_atomic=false`）。本工具不帶 `immutable=1` 以避免在可能仍運作的 DB 上繞過正常鎖與 WAL 處理。
3. 核對每份 Job 的 source SHA、revision、操作筆數、各操作 state、已記錄費用，以及 Chirp stage 費用。
4. 有完整 Operation 名稱時只在程序記憶體內比較交集與差集的**數量**。輸出不包含原始 Operation 名稱／window ID，也不揭露 DB 檔案絕對路徑。整組操作集合的 HMAC fingerprint 由每次執行暫存的隨機密鑰產生，**只能在同一次報告內比較，不是跨執行的穩定識別碼**。
5. 若舊檔沒有 Job／欄位、某筆 Operation 沒有 ID 或費用未知，結果保持不確定，不會當作零筆或零元。
6. 永遠回傳 `gcp_billing=UNKNOWN`、`reconciliation_complete=false`、`paid_replay_authorized=false`，即使新舊 SQLite 快照完全相符。**此工具不能取代真正的 GCP LRO 與帳單核對。**

## 測試

`python3 -m unittest tests.test_golden_v2_ledger_snapshot_reconcile -v`

共 8 個合成資料測試：34 vs 16 筆、136 vs 78¢、完全一致、缺少 Operation 名稱、費用未知、不同來源 SHA、舊 schema 缺失與唯讀安全邊界。

## 最終 Gate

只在 [Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173) 留公開可分享的聚合證據；禁止上傳完整 SQLite、Operation names、GCP token、音訊和逐字稿。對帳完成前 #160 G4 保持 OPEN，正式恢復與 Paid/Drive 一律 NO-GO。
