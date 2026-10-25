# Golden V2 AI 限制揭露交付：受信任 Owner CLI + 來源版本綁定

背景：PR #154 Commit `4f7d706` 已完成 API-only Production 部署；4/4 獨立 `PREPARED`，`DELIVERED=0`。
現場發現 Drive 來源內容相同（MD5、大小一致），但 251025 版本 7→9、261017 9→10。
且既有私有 Owner CLI 未支援新的 `/ai-limited/*` 路由。不能自行讀取 Owner Token 或繞過 API。

## 本修補的兩項安全改進

1. 在原有受保護且會驗證來源/路由/私有配置權限的 `golden_v2_chat_client`
   新增 `ai-limited-status`、`ai-limited-prepare`、`ai-limited-authorize-publication`、
   `ai-limited-publish`，繼續使用既有憑證管理函式；沒有新憑證儲存路徑。
2. 新版 `authorize-publication` 在 owner grant 簽署前從 Drive API 唯讀抓取
   **當下來源版本**，檢查相同 Drive ID、MD5、位元組大小、有效 parent、
   非已刪除、版本不倒退，然後將 `source_version_at_approval` 納入 HMAC。
   發布前、寫入中及讀回後都必須檢查當前 Drive 版本仍與簽章完全一致。
   若音檔內容改變，即使版本號更新也一律拒絕，沒有自動覆寫任何既有 SRT/TXT。

## 證據與准入

- 在沒有實際 Owner API token 的純離線 Mock 上測試新路由與 token 防輸出。
- 目前已通過 25 項針對性測試，包括新版「版本有變、內容未變 + 新 owner HMAC」與
  「MD5 改變仍要拒絕」，原 4f 嚴格檢查測試不退步。
- PR #154 原版 Production 仍在服務，4/4 正式獨立帳本僅 PREPARED，
  正式 Golden 仍 NEEDS_REVIEW。
- 此變更會產生**新 Git SHA**。沒有 Owner 對該 SHA 的正式部署核准，不能更換已部署的 4f。
- 發布時使用現有 CLI 私有配置，不要輸出私有 Token，不要讓代理讀取機密。
  新建雙檔命名必須明確含 `_AI校對_含待複核`，發布前檢查目標沒有同名檔。
- 成功應有獨立 `DELIVERED` receipt（Drive ID + 雙檔 readback SHA），
  不能假稱 Golden PASS 或人工聽音；原 Golden stages 保持 NEEDS_REVIEW。

## 後續安全上線次序

CI/Release Compose PASS → 刷新 SQLite 備份與還原 → 核對四筆來源 MD5/版本及已結算
Chirp → 建置精確 SHA image、API only canary → Owner 使用原有私密 CLI 對 4 個已
`PREPARED` manifest 做逐筆簽章 → 每筆新檔名與來源再檢 → Drive 雙檔上傳 →
讀回 SHA → 正式獨立帳本 DELIVERED → 核對 Golden 不變。
不能重送已完成付費操作，不能重啟有不明付費狀態 worker。
