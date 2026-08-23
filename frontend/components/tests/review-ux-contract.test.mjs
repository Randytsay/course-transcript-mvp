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
  const workspaceStyles = source("app/review/videos/[videoId]/video.module.css");
  assert.match(library, /觀看進度/);
  assert.match(library, /校閱進度/);
  assert.match(workspace, /我已校閱到這裡/);
  assert.match(workspace, /完成本片校閱/);
  assert.match(workspace, /跟隨播放/);
  assert.match(workspace, /review-drafts:/);
  assert.match(workspace, /撤回/);
  assert.match(workspace, /直接點字幕文字/);
  assert.match(workspace, /時間碼<\/b> 跳到並播放；再按同一時間碼暫停／繼續/);
  assert.match(workspace, /字幕文字<\/b> \{lease \? "點擊編輯" : "開始校訂後可編輯"\}/);
  assert.match(workspace, /點擊編輯這段字幕/);
  assert.match(workspace, /progress\/completion/);
  assert.match(workspace, /batch-suggestion/);
  assert.match(workspace, /搜尋與取代/);
  assert.match(workspace, /建立 .*待審核建議/);
  assert.match(workspace, /本片共修夥伴/);
  assert.match(workspace, /contributors/);
  assert.match(workspace, /contributorAvatar/);
  assert.match(workspace, /pauseVideo/);
  assert.match(workspace, /playVideo/);
  assert.match(workspace, /toggleTimecode/);
  assert.match(workspace, /onStateChange/);
  assert.match(workspace, /aria-pressed=\{active\}/);
  assert.match(workspaceStyles, /grid-template-columns: minmax\(0, 1\.25fr\) minmax\(360px, 0\.75fr\)/);
  assert.match(workspaceStyles, /overflow-wrap: anywhere/);
  assert.match(workspaceStyles, /overflow-x: hidden/);
  assert.match(workspaceStyles, /@media \(max-width: 1180px\)/);
  assert.doesNotMatch(library, /席位/);
  assert.doesNotMatch(workspace, /席位/);
});

test("admin UI keeps import, versioning and YouTube publication as explicit separate stages", () => {
  const page = source("app/review-admin/page.tsx");
  const layout = source("app/review-admin/layout.tsx");
  const reviewerLayout = source("app/review/layout.tsx");
  const reviewerLink = source("app/review/review-admin-link.tsx");
  assert.match(page, />影片同步</);
  assert.match(page, />版本管理</);
  assert.match(page, />YouTube 發布</);
  assert.match(page, />操作紀錄</);
  assert.match(page, /apply: false/);
  assert.match(page, /youtube_video_ids: ids/);
  assert.match(page, /publish-preview/);
  assert.match(page, /這一步會真正覆蓋目前 YouTube 字幕軌/);
  assert.match(layout, /進入校訂入口/);
  assert.match(layout, /\/review\/videos/);
  assert.match(reviewerLayout, /ReviewAdminLink/);
  assert.match(reviewerLink, /ADMIN_ORIGIN = "https:\/\/transcript\.randy88\.ccwu\.cc"/);
  assert.match(reviewerLink, /\$\{ADMIN_ORIGIN\}\/review-admin/);
  assert.match(reviewerLink, /管理員入口/);
});
