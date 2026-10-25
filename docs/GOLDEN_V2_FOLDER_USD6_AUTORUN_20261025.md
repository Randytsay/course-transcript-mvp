# Golden V2 — 資料夾一次性 US$6 限額流程

2026-10-25：使用者授權以每次明確貼出的 Google Drive 資料夾為範圍，全部新錄音合計可信付費報價不超過 US$6.00 時，不必再次逐檔確認。此授權不含未提交的新資料夾、不適用舊案件或任意新的付費修復。

## 安全流程

1. 先核對正式服務、Drive 連接與 Worker 心跳，沿用既有受保護 Owner CLI。
2. Preview 收集完整來源 ID、版本、MD5、大小與 preview SHA；預設不搜尋子資料夾。
3. 每筆都必須取得最新 COMPLETE quote、source SHA、quote SHA、價格版本與效期。
4. 第二次 Preview 核對來源沒有異動；**整批可信估價合計不得超過 600 cents**。若某支缺報價或全批超額，所有新付費授權一律停止，不可先送局部檔案規避上限。
5. 保留每次來源集合、預算決策的不可變稽核檔；以既有 Owner CLI 對個別新 job 做來源綁定授權，絕不重送已完成 provider 操作。
6. 繼續 Gemini 3.8 Flash、Chirp 3、語意與聲學 QA，無法確證的原音集中標記待複核，不能宣稱 Golden PASS。
7. 正式 AI-limited 交付必須另備完整 SRT/TXT 與同來源 QA，並由 Owner 端點簽章；保留舊檔，Drive 雙檔讀回 SHA，獨立帳本 DELIVERED 才算完成。

程式：app/pipeline/golden_v2_folder_budget.py、scripts/golden_v2_folder_autorun.py。

本次 US$6 係提供者模型的保守預估額度，不是最終 Google 發票；GCS、網路等周邊費用不在既有 quote 模型內，額外成本須單獨追蹤。使用者提供新連結才啟動新一次授權，不能背景自行掃描所有資料夾。

## 現階段完成度

付費授權前的整批預算檢查已實作並通過離線測試。Production 的 paid_execution 仍是 false；且從全新音檔到 QA/AI-limited DELIVERED 的端到端 Smoke 尚未做，不能宣稱貼連結即已保證全自動交付。新版未完成正式部署前不得打開 paid API 繞過安全閘。每次遇到故障應說明明確阻塞步驟，而不是重複要求使用者核准同一筆 US$6 內的新請求。
