import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const source = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

test("review admin exposes versioned canonical Buddhist text management", () => {
  const page = source("app/review-admin/canonical-texts/page.tsx");
  const layout = source("app/review-admin/layout.tsx");
  assert.match(page, /\/api\/v1\/review-admin\/canonical-texts/);
  assert.match(page, /大成佛經正式文字/);
  assert.match(page, /講師一句＋大眾跟一句/);
  assert.match(page, /不靠音近字猜寫/);
  assert.match(page, /scripture_alignment_review/);
  assert.match(layout, /\/review-admin\/canonical-texts/);
  assert.match(layout, /佛典正式文字/);
  const newJob = source("components/new-job-page-drive-api.tsx");
  assert.match(newJob, /前段依正式經文對齊/);
  assert.match(newJob, /講師一句＋大眾跟一句折成一次/);
  assert.match(newJob, /尚未設定，前段共誦只會標人工複核/);
});
