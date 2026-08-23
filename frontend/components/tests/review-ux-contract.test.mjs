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
  const sharedNav = source("app/review/review-nav.tsx");
  assert.match(page, />影片同步</);
  assert.match(page, />版本管理</);
  assert.match(page, />YouTube 發布</);
  assert.match(page, />操作紀錄</);
  assert.match(page, /apply: false/);
  assert.match(page, /youtube_video_ids: ids/);
  assert.match(page, /publish-preview/);
  assert.match(page, /這一步會真正覆蓋目前 YouTube 字幕軌/);
  assert.match(layout, /BackToPlatformLink/);
  assert.match(sharedNav, /回到學習平台/);
  assert.match(sharedNav, /\/review\/learn/);
});

test("floating portal dock is removed from reviewer layout", () => {
  const reviewerLayout = source("app/review/layout.tsx");
  const css = source("app/review/review.module.css");
  assert.doesNotMatch(reviewerLayout, /portalDock|ReviewAdminLink/);
  assert.doesNotMatch(css, /\.portalDock/);
  assert.ok(!exists("app/review/review-admin-link.tsx"), "review-admin-link.tsx should be deleted");
});

function exists(relativePath) {
  try {
    readFileSync(new URL(`../../${relativePath}`, import.meta.url));
    return true;
  } catch {
    return false;
  }
}

test("shared ReviewNav provides six primary links and owner-only admin entry", () => {
  const nav = source("app/review/review-nav.tsx");
  for (const label of ["學習中心", "字幕共修", "複習中心", "知識搜尋", "共修紀錄", "使用說明"]) {
    assert.match(nav, new RegExp(label));
  }
  // owner-only rendering
  assert.match(nav, /role === "owner"/);
  assert.match(nav, /管理員入口 ↗/);
  assert.match(nav, /\/api\/v1\/review\/auth\/me/);
  // production admin origin behaviour preserved
  assert.match(nav, /ADMIN_ORIGIN = "https:\/\/transcript\.randy88\.ccwu\.cc"/);
  assert.match(nav, /\$\{ADMIN_ORIGIN\}\/review-admin/);
  // REVIEW_HOSTNAME guard kept
  assert.match(nav, /REVIEW_HOSTNAME = "review\.randy88\.ccwu\.cc"/);
  // minimal mode keeps only help link
  assert.match(nav, /mode === "full" \? NAV_LINKS : NAV_LINKS\.filter/);
  assert.match(nav, /key === "help"/);
});

test("learner pages use the shared ReviewNav instead of per-page copies", () => {
  const learn = source("app/review/learn/page.tsx");
  const videos = source("app/review/videos/page.tsx");
  const lesson = source("app/review/learn/[videoId]/page.tsx");
  const workspace = source("app/review/videos/[videoId]/page.tsx");
  const contributions = source("app/review/contributions/page.tsx");
  const help = source("app/review/help/page.tsx");
  const reviewCenter = source("app/review/learn/review/page.tsx");
  const search = source("app/review/learn/search/page.tsx");
  const notes = source("app/review/learn/notes/page.tsx");
  for (const [name, src] of [["learn", learn], ["videos", videos], ["lesson", lesson], ["workspace", workspace], ["contributions", contributions], ["help", help], ["review", reviewCenter], ["search", search], ["notes", notes]]) {
    assert.match(src, /ReviewNav/, `${name} should use shared nav`);
  }
  assert.doesNotMatch(videos, /headerActions/);
});

test("login page hides full nav and admin entry when logged out", () => {
  const login = source("app/review/page.tsx");
  assert.match(login, /mode=\{me \? "full" : "minimal"\}/);
});
