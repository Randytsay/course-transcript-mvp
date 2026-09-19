import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const newJob = readFileSync(new URL("../new-job-page-drive-api.tsx", import.meta.url), "utf8");
const dashboard = readFileSync(new URL("../dashboard-page.tsx", import.meta.url), "utf8");
const shell = readFileSync(new URL("../app-shell.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

test("primary transcription flow is user intent first", () => {
  assert.match(newJob, /title="開始辨識"/);
  assert.match(newJob, /開始辨識/);
  assert.match(newJob, /<details className="advanced-settings">/);
  assert.match(newJob, /一般任務不再需要第二次費用確認/);
  assert.doesNotMatch(newJob, /Drive 混合架構/);
  assert.doesNotMatch(newJob, /單次前端請求最多等待 15 秒/);
});

test("dashboard focuses on tasks rather than cloud diagnostics", () => {
  assert.match(dashboard, /label: "處理中"/);
  assert.match(dashboard, /label: "需要處理"/);
  assert.match(dashboard, /label: "待審查"/);
  assert.match(dashboard, /label: "已完成"/);
  assert.doesNotMatch(dashboard, /Google Cloud 費用/);
  assert.doesNotMatch(dashboard, /管線連線狀態/);
});

test("shell removes persistent engineering and font rescue chrome", () => {
  assert.doesNotMatch(shell, /font-size-switcher/);
  assert.doesNotMatch(shell, /私人工作區已啟用/);
  assert.doesNotMatch(shell, /私人連線/);
  assert.doesNotMatch(shell, /AI TRANSCRIPTION WORKSPACE/);
});

test("typography is Traditional Chinese first and readable by default", () => {
  assert.match(css, /font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei"/);
  assert.match(css, /--font-xs: 13px/);
  assert.match(css, /--font-sm: 14px/);
  assert.match(css, /--font-base: 16px/);
  assert.match(css, /Final typography fence/);
});

test("operator workspace follows the formal course and proofreading brand", () => {
  assert.match(css, /Formal course \/ review brand bridge/);
  assert.match(css, /--bg: #f7f3eb/);
  assert.match(css, /--surface: #fffdf6/);
  assert.match(css, /\.sidebar \{ background: #203f34/);
  assert.match(css, /#d8a62e/);
  assert.match(css, /#4e5b4a/);
});
