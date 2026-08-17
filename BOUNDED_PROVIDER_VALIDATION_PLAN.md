# Bounded Provider Validation Plan

本次驗證已取得 operator 的條件式預先授權；只要維持以下固定樣本、成本上限與 stop gates，就不需要在執行 Gemini / MiniMax provider call 前再次停下來詢問。PR #42 本身仍未執行新的付費 provider call，因此此文件同時作為後續 VPS/runtime Agent 的可直接執行 runbook。

## 固定樣本

- 使用既有 job 的 Chirp raw segments，先取連續 5 分鐘；若結果穩定再擴到 10 分鐘。
- 不重新送 Chirp，不修改 raw words、segment IDs、ordering、timestamps 或 boundaries。
- 不做 Drive upload、rename、delete 或 publish；結果只寫隔離 local validation directory。
- 同一份 segments、同一 terminology snapshot、同一 prompt/schema，分別測 Gemini 3.7 Flash 與 MiniMax M3。

## 兩個 provider runs

1. Gemini 3.7 Flash control：effective concurrency 2、固定 window、保留 raw response/usage。
2. MiniMax M3 treatment：先 M3；invalid structured response 依 bounded policy 處理，不自動擴大整個 job；保留每次 raw response、usage、attempt、switch evidence。

每個 run 量測：專有名詞正確率、漏字、新增資訊、semantic drift、content_guard fallback、structured-response validity、uncertain_terms、latency P50/P95、input/output tokens、provider retry、M3→Gemini switch、effective concurrency，以及人工抽查修訂數。

## 成本上限估算

目前 application estimate 使用 USD/TWD=32、Gemini input US$1.50/M、output US$7.50/M；MiniMax 為 Token Plan，不列為邊際 API 金額。以下只估校正，不重送已存在的 Chirp：

| 樣本 | Gemini 估計 token（依本次 10.87 分鐘 evidence 等比例） | Gemini 估計 | 含 25% contingency |
|---|---:|---:|---:|
| 5 分鐘 | 約 61k input + 58k output | 約 US$0.53 / NT$17 | 約 NT$21 |
| 10 分鐘 | 約 122k input + 116k output | 約 US$1.05 / NT$34 | 約 NT$42 |

MiniMax 5 分鐘可先抓約 8–12 次 bounded request、約 40k–50k input 與 20k–30k output Token Plan 用量；現金 API estimate 為 NT$0，但 quota/weekly usage 仍需列入判斷。若 M3 invalid 而 fallback 到 Gemini，應以實際保存的 usage 加總，不以猜測扣款。

## Stop gates

- 任一 provider 出現 credential/auth、rate-limit、unexpected model、raw evidence 缺失：停止該 provider run 並保留 evidence。
- M3 structured invalid、output limit hit、segment ID mismatch、semantic drift 或 content_guard fallback 超過預設門檻：停止擴大樣本；先分析 root cause，不直接擴大到整個 job。
- 預估 Gemini 新增費用超過 NT$50 或 Token Plan 用量超出上述事前上限：停止。
- 不可因為 stop gate 失敗而強行進 production；production enable 仍須依完整 readiness gate 判定。
- 只有遇到不可逆操作、credential replacement、刪資料、額外付費資源或重大 production architecture change 才需要再次要求人工確認。
