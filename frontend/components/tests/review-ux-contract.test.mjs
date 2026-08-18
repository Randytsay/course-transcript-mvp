import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(relativePath) {
  return readFileSync(new URL(`../../${relativePath}`, import.meta.url), "utf8");
}

test("reviewer onboarding explains the three-step nontechnical flow", () => {
  const page = source("app/review/page.tsx");
  assert.match(page, /選一支影片/);
  assert.match(page, /邊看邊校對/);
  assert.match(page, /送出修改建議/);
  assert.match(page, /不用調整時間碼/);
  assert.match(page, /不會直接改到 YouTube/);
});

test("reviewer workspace separates watched and reviewed progress without seat jargon", () => {
  const library = source("app/review/videos/page.tsx");
  const workspace = source("app/review/videos/[videoId]/page.tsx");
  assert.match(library, /觀看進度/);
  assert.match(library, /校閱進度/);
  assert.match(workspace, /我已校閱到這裡/);
  assert.match(workspace, /完成本片校閱/);
  assert.match(workspace, /跟隨播放/);
  assert.match(workspace, /review-drafts:/);
  assert.match(workspace, /撤回/);
  assert.doesNotMatch(library, /席位/);
  assert.doesNotMatch(workspace, /席位/);
});

test("admin UI keeps import, versioning and YouTube publication as explicit separate stages", () => {
  const page = source("app/review-admin/page.tsx");
  assert.match(page, />影片同步</);
  assert.match(page, />版本管理</);
  assert.match(page, />YouTube 發布</);
  assert.match(page, />操作紀錄</);
  assert.match(page, /apply: false/);
  assert.match(page, /youtube_video_ids: ids/);
  assert.match(page, /publish-preview/);
  assert.match(page, /這一步會真正覆蓋目前 YouTube 字幕軌/);
});
