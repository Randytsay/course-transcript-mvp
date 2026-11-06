# Golden V2｜Chirp Operation 多份 Ledger 唯讀對帳

適用於 **AGY-LOCAL-002 v2**、Issue #160 G4 與 Issue #173 的 Ledger 證據衝突。

## 核心問題

Issue #160 的歷史基線記錄：2022/12/17 課程曾有 **34 筆外部回報 SUCCEEDED、136¢**。AGY 在 2026-11-06 以正式 VPS 唯讀查詢回報：該 Job 目前 **16 筆本機 DONE、78¢、rev 5**。兩者並未驗證是相同作業範圍；**不能將 16 筆當成已完成 34 筆雲端 LRO 的對帳證據**。GCP 實際帳單繼續保持 UNKNOWN。

本工具由 ChatGPT 於**開發分支**提供，只做 SQLite 現有檔案的比較；不可用來復原／修補 Ledger 或核銷費用。

## 用法（由已有正式主機唯讀權限的 AGY 自行核對）

先在隔離開發 checkout 檢閱程式、使用真實路徑並確認授權。以下以完整 Job ID 指定來源：

```bash
python3 scripts/golden_v2_compare_chirp_ledger_snapshots.py \
  --current /opt/course-transcript-v2-chat/data/golden-v2/ledger.sqlite \
  --baseline PR159=/opt/course-transcript-v2-chat/backups/pr159-20261029T071713Z/ledger.sqlite \
  --baseline PR158=/opt/course-transcript-v2-chat/backups/pr158-20261027T074517Z-600487/ledger.sqlite \
  --job-id v2p_cce7a9147a495027bd16f5619ad17b8d
```

**上述是唯讀檢查範例，不是可以任意提升權限的命令。** 僅在當前 AGY 工作票明確容許且已有適當權限時執行；無權讀取就回報 BLOCKED，不要 `chmod`／`chown`、改 ACL、複製生產資料庫或附加新 sudo 範圍。

工具：

- 僅開啟已存在的 SQLite 檔；使用 URI `mode=ro`、`PRAGMA query_only=ON`，每份 DB 的多個查詢都包在單一 `BEGIN` 讀取 transaction 內。
- 只讀取指定 Job 的狀態、Chirp stage、Operation 身分、來源 SHA 與已存成本。對比當前與各備份的共有／僅舊版／僅當前 Operation **數量**。
- 比對相同 Operation 的金額和 result SHA 是否改變；不輸出真實 Operation 名稱、雲端 URI、音檔內容、token、路徑或完整 source SHA。
- 不允許缺失 Operation name、未知成本、重複 window、來源不一致等情況被自動認定為「零費用」。
- 歷史的 34/136 是待驗證的**外部回報**，而非某份 DB 保證具有 34 筆；沒有相應快照時，結果應為 `UNRECONCILED`。
- 永遠輸出 `NO_GO`、`cloud_billing_reconciled=false`、`paid_or_delivery_authorized=false`，預期返回碼 2 是安全阻擋，**不是運行失敗或准許修復**。
- 對正式 DB 的 transaction 是**該資料庫內**一致性，不代表不同日期 DB 與雲端服務具有跨系統原子快照；即使全部操作共有，仍需獨立驗證 GCP LRO、計費與不可變來源錄音。

## 剩餘的必要證據

這份工具不讀 `jobs.tar.gz`、不驗證檔案是否可冷啟動、不做 GCP 只讀查詢、不動 Worker。若 PR159/PR158 均找不到 34 筆，請回報「歷史 34 筆出處尚未確定」，勿臆測 18 筆遺失、勿再次提交模型請求。

**正式磁碟剩餘容量不足以在根目錄展開既有約 12.18 GB 壓縮備份。** 不得以這份對帳工作為理由解壓或清除備份。

追蹤：[Issue #173](https://github.com/Randytsay/course-transcript-mvp/issues/173)、[Issue #160](https://github.com/Randytsay/course-transcript-mvp/issues/160)、[Issue #170](https://github.com/Randytsay/course-transcript-mvp/issues/170)。
