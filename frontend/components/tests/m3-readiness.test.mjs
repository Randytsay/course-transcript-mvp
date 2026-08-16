import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const source=readFileSync(new URL("../new-job-page-drive-api.tsx",import.meta.url),"utf8");
test("active UI gates M3 on feature, credentials, and live quota checks",()=>{assert.match(source,/const m3SelectionAvailable = m3StatusLoaded && m3Enabled && m3Configured && m3QuotaLiveCheck/);assert.match(source,/disabled={!m3StatusLoaded \|\| !m3SelectionAvailable}/);assert.match(source,/MiniMax key 尚未掛載/);assert.match(source,/M3 quota 檢查尚未開啟/);});
test("active UI names the M3 switch visibly",()=>{assert.match(source,/可切換：\$\{m3Model\}/);assert.match(source,/開關可選 MiniMax M3 人工抽查模式/);});
test("active UI has one preflight action",()=>{assert.match(source,/檢查檔案與估價/);assert.doesNotMatch(source,/>建立唯讀批次預覽/);assert.doesNotMatch(source,/>建立 preflight 工作/);assert.match(source,/router\.push\(`\/batches\/\$\{nextBatch\.batchId\}`\)/);});
