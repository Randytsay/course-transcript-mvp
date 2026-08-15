import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
const source=readFileSync(new URL("../new-job-page-drive-api.tsx",import.meta.url),"utf8");
test("active UI gates M3 on feature and credentials",()=>{assert.match(source,/const m3Ready = m3Enabled && m3Configured/);assert.match(source,/disabled={!m3StatusLoaded \|\| !m3Ready}/);assert.match(source,/MiniMax 憑證尚未完成設定/);});
test("active UI has one preflight action",()=>{assert.match(source,/檢查檔案與估價/);assert.doesNotMatch(source,/>建立唯讀批次預覽/);assert.doesNotMatch(source,/>建立 preflight 工作/);assert.match(source,/router\.push\(`\/batches\/\$\{nextBatch\.batchId\}`\)/);});
