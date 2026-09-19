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

## 本次實際踩坑對照表

下表把這次遇到的問題和阻斷方式固定下來。任何一項驗證不符合，都應停止切換，
不要用手動改 `.env` 或強制重送來「先讓畫面看起來正常」。

| 坑 | 原因 | 以後的預防與驗證 |
| --- | --- | --- |
| Console 看似換帳號，實際仍查到舊 Project | Google Console 的 `authuser` 分頁、目前登入帳號、Project selector 可能不同步 | 同時確認登入信箱、Project ID、Billing Account ID；再用服務帳戶做 project／billing／API 唯讀 preflight |
| 以為換 Service Account 就換了扣款帳號 | Service Account 與 Cloud Billing Account 是不同層 | 切換前在 Billing Console 確認 Project 的 linked billing；Profile 只保存執行身分與 Project，不會自動改 Billing |
| 內部識別名稱不知道填什麼 | 名稱是本系統 Profile key，不是 Google 顯示名稱 | 使用 `google-<credit>-<days>-<yyyymmdd>`，例如 `google-us300-90d-20260919`；只用英數、點、底線、連字號 |
| JSON 金鑰與 Profile Project 不一致 | 金鑰的 `project_id`、metadata 的 `project_id`、執行環境的 Project 可能各自不同 | 新增時強制比對三者；JSON 只放在本機 Downloads 和 VPS 保護目錄，絕不貼聊天、進 Git、放 Drive 或建立公開連結 |
| 舊桶仍可列出但其實不能再用 | 舊 `course-transcript-mvp` 桶屬於另一個 Project，原 Billing 已關閉 | 每個新 Project 建立自己的桶；確認 bucket IAM、object list、Billing 與 Public access prevention，不以「看得到桶」當作可用證據 |
| API 已切換，pipeline 卻仍用舊設定 | `ai-active.env`、protected `.env`、Compose interpolation 三者優先序不同 | 部署後必須逐一檢查 API 與 pipeline-worker 的 Project／Location／Bucket；兩者不同就判定失敗 |
| API 落回 `shopclaw-ai` | VPS `/home/ubuntu/.env` 留有歷史值，且使用了舊部署工作樹 | 只從同一 SHA 的 `/opt/course-transcript-releases/<SHA>/scripts/deploy_release_safe.sh` 執行；不要直接使用 `/opt/course-transcript-source` 舊工具 |
| 切換後畫面顯示等待重建 | Profile 切換是兩階段提交，尚未代表 consumer 已採用 | `PENDING_RESTART` 期間不送付費工作；只有 API、pipeline generation 一致且 runtime `ACTIVE` 才算完成 |
| 正在轉錄時切換帳戶 | 舊工作可能仍持有舊 Project／Bucket 的 operation 或 lease | 切換前確認 active jobs、live leases、delivery candidates 都是零；不要中途重送或把舊 operation 接到新 Project |
| 部署 SHA 正確但服務仍不是該版本 | VPS live working tree、release archive、Docker image tag 可能不是同一份 | 先 dry-run，再用同 SHA execute；驗證所有服務 image tag、runtime SHA、health 與 rollback tag |
| 看到 timeout 就重送切換或發布 | mutation 可能已成功但 response 遺失 | 先查 `active.json`、runtime status、audit、container env 和服務狀態，再決定是否重試 |
| 外部網址回 302 被誤判為故障 | Cloudflare Access 會把未登入請求導向登入 | 302 是預期的 Access 邊界；管理台登入與校對前台 Google／LINE 登入是兩套 session，不能混為一談 |
| 額度標籤被當成即時餘額 | 管理台的 `credit_status` 是人工標記 | 額度、到期日、Billing Account 以 Google Cloud Billing Console 為準；管理台只作提醒 |
| 完成工作直接刪除 | 工作資料可能仍是 audit、raw evidence 或 rollback 依據 | 先查工作狀態，再跑 retention cleanup dry-run；確認報告後才另行 `--apply`，部署流程不自動刪除 |

## 切換完成的唯一判定

下列條件必須全部成立：

- Google Cloud Console 的登入帳號、Project ID、Billing Account 相互對得上。
- Service Account JSON、Profile metadata、API 容器、pipeline-worker 容器的 Project
  與 Bucket 完全一致。
- Profile preflight 全部通過，沒有 active work、lease 或 delivery candidate。
- 精確 SHA 的 safe release dry-run 與 execute 都通過，Cloudflared 未被重建。
- API health、worker stability、frontend proxy 通過，且沒有 provider call 或 Drive
  mutation 的非預期紀錄。
- 管理台顯示「已生效」，runtime verification 為 `verified=true`，狀態為 `ACTIVE`。

少一項都只算「準備中」，不算新額度已上線。
