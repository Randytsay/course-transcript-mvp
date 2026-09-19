# Google AI 額度帳戶切換標準流程

這份流程把「Google Cloud 免費額度換帳戶」拆成可重複、可回滾的步驟。每個
Profile 同時保存四個執行設定：Service Account、GCP Project、Location、GCS
Bucket。真正的扣款與額度仍由該 Project 連結的 Cloud Billing Account 決定；
管理台顯示的 credit status 只是管理員標記，不是 Google 即時帳務查詢。

## 快速路徑

下次只需要準備下列資料，依序完成即可：

| 欄位 | 範例 | 規則 |
| --- | --- | --- |
| 內部識別名稱 | `google-us300-90d-20260919` | 只用英文、數字、點、底線、連字號 |
| Service Account JSON | 下載到本機 `Downloads` | 不進 Git、不貼到聊天、不寄出 |
| Project ID | `xenon-chain-506409-c3` | 必須與 JSON 的 `project_id` 一致 |
| Location | `global` | Vertex AI 位置；沒有特別需求就用 `global` |
| GCS Bucket | `xenon-chain-506409-c3-course-transcript` | 建議每個 Project 一個專用桶 |
| 額度類型／日期 | `free_trial`、開始日、到期日 | 只作營運提示，實際以 Billing Console 為準 |

1. 在 Google Cloud Console 選定新帳號與 Project，確認 Project 已連結正確的
   Billing Account。啟用 Vertex AI、Speech-to-Text、Cloud Resource Manager、
   Cloud Billing、Service Usage API。
2. 建立或確認專用 GCS Bucket：Location 使用 `US` 或既定區域、Uniform bucket-
   level access 開啟、Public access prevention 保持啟用。
3. 建立 Service Account，授予專案實際需要的最小角色：Vertex AI User（Console
   可能顯示為 Agent Platform 使用者）、Service Usage Viewer、Service Usage
   Consumer、Storage Viewer、Storage Object Admin。
   下載新的 JSON 金鑰到本機 `Downloads`。
4. 開啟管理台的 [AI 帳戶管理](/review-admin/ai-accounts)，新增 Profile，匯入
   JSON，填入上表的 Project／Location／Bucket／額度資訊。
5. 對新 Profile 執行 **唯讀 preflight**。所有 project、billing、API、Vertex、
   bucket 檢查都必須通過；這一步不會產生模型請求或費用。
6. 按確認切換。切換只寫入下一代設定，畫面出現「等待服務重建」是正常的。
7. 先確認沒有進行中的轉錄、租約或交付工作，再使用目前 `main` 的精確 SHA
   執行不可變 VPS release。部署必須從該 SHA 的 release 工具目錄執行，不能依賴
   `/opt/course-transcript-source` 的舊工作樹：

   ```bash
   SHA=<merged-main-sha>
   sudo bash /opt/course-transcript-releases/$SHA/scripts/deploy_release_safe.sh \
     --release-sha "$SHA" --dry-run
   sudo bash /opt/course-transcript-releases/$SHA/scripts/deploy_release_safe.sh \
     --release-sha "$SHA" --execute --confirm-sha "$SHA"
   ```

8. 完成後確認：API 與 pipeline-worker 的 `GOOGLE_CLOUD_PROJECT`、
   `GOOGLE_CLOUD_LOCATION`、`GCS_BUCKET` 相同；管理台顯示「已生效」；
   `runtime_status()` 為 `ACTIVE` 且 verification 全部為 true。Cloudflare Access
   外部未登入請求回 `302` 是預期行為。

## 回滾與保留

- 新 Profile 驗證失敗時不要強制切換，先修正 Project、IAM、API 或 Bucket。
- 切換後若 runtime 沒有變成 `ACTIVE`，先停止後續付費工作，使用管理台回滾，
  再用精確 SHA 重建 API 與 pipeline-worker。
- 舊 Profile、舊映像、rollback tag、部署 evidence 與金鑰備份在觀察期內保留；
  不要因為畫面 timeout 就重送切換或其他 mutation。
- 已完成工作可做 retention cleanup，但先 dry-run、檢視報告，再另行使用
  `--apply`；切換部署本身不刪除工作證據。

## 這次花時間的原因與改進

這次不是單純改一個環境變數，而是同時處理：新 Google 帳號與 Project、Billing、
API 啟用、Bucket、IAM、JSON 金鑰、Cloudflare Access、管理台 Profile、唯讀
preflight、工作 quiescence、ARM64 映像、精確 SHA release，以及 API／worker
實際採用設定的驗證。

中途還發現兩個容易漏掉的邊界：VPS 保護的 `/home/ubuntu/.env` 留著舊 Project，
以及部署工作樹可能不是目前 `main` 的工具版本。結果第一次 release 的 pipeline-
worker 已讀到新設定，但 API 仍落回舊 Project；後來補上部署工具修正，改從同一個
release 目錄執行 safe wrapper，才讓 API 與 pipeline-worker 同時達到 `ACTIVE`。

這份 runbook 把資料欄位、最小 IAM、唯讀檢查、精確 SHA 部署、驗證證據和回滾順序
固定下來。下一次換帳戶不需要重新探索流程，通常只剩 Google Console 操作、管理台
登記／preflight，以及一次 dry-run／execute release。
