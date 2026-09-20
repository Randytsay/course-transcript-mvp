import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const newJob = readFileSync(new URL("../new-job-page-drive-api.tsx", import.meta.url), "utf8");
const dashboard = readFileSync(new URL("../dashboard-page.tsx", import.meta.url), "utf8");
const shell = readFileSync(new URL("../app-shell.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");
const liveJob = readFileSync(new URL("../live-job-page.tsx", import.meta.url), "utf8");
const jobControls = readFileSync(new URL("../job-controls.tsx", import.meta.url), "utf8");
const jobControlsCss = readFileSync(new URL("../job-controls.module.css", import.meta.url), "utf8");

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

test("failed job page is task-oriented and hides noisy waiting-chunk actions", () => {
  assert.match(liveJob, /Google Speech 權限不足，Chirp 尚未開始/);
  assert.match(liveJob, /權限修正後重新送出/);
  assert.match(liveJob, /chunk\.status === "FAILED"/);
  assert.match(liveJob, /\["SUCCEEDED", "EMPTY_SILENCE"\]\.includes\(chunk\.status\)/);
  assert.doesNotMatch(liveJob, /第一層｜分段進度/);
  assert.doesNotMatch(liveJob, /第二層｜即時 Chirp 原始稿/);
});

test("chunk progress explains course-relative speech density instead of raw counts alone", () => {
  assert.match(liveJob, /語速一致/);
  assert.match(liveJob, /需確認有聲空檔/);
  assert.match(liveJob, /同課中位/);
  assert.match(liveJob, /扣除長空檔/);
  assert.match(liveJob, /時間戳修復/);
  assert.match(liveJob, /局部辨識已修復/);
  assert.match(liveJob, /局部重辨仍無可辨語詞/);
});

test("task controls default to a compact bottom-right launcher", () => {
  assert.match(jobControls, /useState\(true\)/);
  assert.match(jobControls, /任務操作/);
  assert.doesNotMatch(jobControls, /重試失敗階段/);
  assert.match(jobControlsCss, /width: min\(390px, calc\(100vw - 32px\)\)/);
  assert.doesNotMatch(jobControlsCss, /left: max\(18px/);
});
