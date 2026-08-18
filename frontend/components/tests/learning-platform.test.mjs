import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(relativePath) {
  return readFileSync(new URL(`../../${relativePath}`, import.meta.url), "utf8");
}

test("learning home exposes continue-learning, explicit completion and four distinct progress concepts", () => {
  const landing = source("app/review/page.tsx");
  const dashboard = source("app/review/learn/page.tsx");
  const lesson = source("app/review/learn/[videoId]/page.tsx");
  assert.match(landing, /佛學共學平台/);
  assert.match(landing, /觀看進度、學習完成、複習與字幕共修分開記錄/);
  assert.match(dashboard, /繼續學習/);
  assert.match(dashboard, /觀看進度不等於學習完成/);
  assert.match(lesson, /我已學完/);
  assert.match(lesson, /協助校字幕/);
  assert.match(lesson, /觀看進度、學習完成、複習與字幕共修分開記錄/);
});

test("lesson workspace provides traceable AI notes, review, flashcards, quiz and personal notes", () => {
  const lesson = source("app/review/learn/[videoId]/page.tsx");
  for (const label of ["影片＋字幕", "AI 筆記", "快速複習", "問答", "Flashcards", "自我測驗", "我的筆記"]) {
    assert.match(lesson, new RegExp(label.replace(/[+]/g, "\\+")));
  }
  assert.match(lesson, /source_segment_indexes/);
  assert.match(lesson, /seek\(c\.start_ms\)/);
  assert.match(lesson, /收藏此刻/);
  assert.match(lesson, /review-complete/);
  assert.match(lesson, /quiz-attempts/);
  assert.match(lesson, /flashcards\/review/);
});

test("review center and knowledge search stay grounded in learning evidence", () => {
  const review = source("app/review/learn/review/page.tsx");
  const search = source("app/review/learn/search/page.tsx");
  assert.match(review, /1、3、7、14、30 天/);
  assert.match(review, /開始快速複習/);
  assert.match(search, /核准字幕與 AI 學習整理/);
  assert.match(search, /這裡不讓 AI 憑記憶猜答案/);
  assert.match(search, /回到影片/);
});

test("learner help explains the whole journey without engineering jargon", () => {
  const landing = source("app/review/page.tsx");
  const dashboard = source("app/review/learn/page.tsx");
  const help = source("app/review/help/page.tsx");
  assert.match(landing, /完整使用說明/);
  assert.match(dashboard, /\/review\/help/);
  for (const label of ["選一堂課開始", "收藏此刻", "AI 筆記", "我已學完", "1、3、7、14、30 天", "協助校字幕"]) {
    assert.match(help, new RegExp(label));
  }
  assert.match(help, /四種進度/);
  assert.match(help, /不會直接覆蓋正式字幕或 YouTube/);
});

test("owner AI console requires formal learning-source approval before paid generation", () => {
  const admin = source("app/review-admin/learning/page.tsx");
  assert.match(admin, /核定為正式學習來源/);
  assert.match(admin, /核定.*為學習版/);
  assert.match(admin, /付費 LLM/);
  assert.match(admin, /不會修改 YouTube/);
  assert.match(admin, /approve-source/);
  assert.match(admin, /generate/);
  assert.match(admin, /confirm:true/);
});

test("owner help keeps import, review, AI generation and YouTube publish as separate gates", () => {
  const layout = source("app/review-admin/layout.tsx");
  const help = source("app/review-admin/help/page.tsx");
  assert.match(layout, /管理員說明/);
  for (const label of ["影片同步", "字幕共修", "版本管理", "YouTube 發布", "AI 學習內容", "操作紀錄"]) {
    assert.match(help, new RegExp(label));
  }
  assert.match(help, /先核定來源，再產生/);
  assert.match(help, /不要因畫面 timeout 就直接重送 mutation/);
});
