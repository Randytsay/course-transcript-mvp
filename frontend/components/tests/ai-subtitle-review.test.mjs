import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("../ai-subtitle-review.tsx", import.meta.url), "utf8");
const backend = readFileSync(
  new URL("../../../app/subtitles/ai_review.py", import.meta.url),
  "utf8",
);

test("review UI offers accept / reject / manual edit per candidate", () => {
  assert.match(panel, /拒絕/);
  assert.match(panel, /接受/);
  assert.match(panel, /自行調整/);
});

test("review UI provides required filters", () => {
  for (const label of ["全部", "只看文字修正", "只看跨段", "只看高風險", "未審核"]) {
    assert.ok(panel.includes(label), `missing filter label ${label}`);
  }
});

test("review UI shows before/after with source lineage and reason", () => {
  assert.match(panel, /修改前/);
  assert.match(panel, /修改後/);
  assert.match(panel, /原因：/);
  assert.match(panel, /source_segment_ids/);
});

test("publish button requires zero pending candidates", () => {
  assert.match(panel, /pendingCount > 0/);
  assert.match(panel, /建立新字幕版本/);
});

test("rollback never destructive — creates a new revision via POST", () => {
  assert.match(panel, /回復此版本/);
  assert.doesNotMatch(backend, /shutil\.rmtree|os\.remove/);
  assert.match(backend, /rolled_back_from/);
});

test("backend forbids invented timestamps in candidate payload", () => {
  assert.match(backend, /禁止發明 timestamp/);
});

test("backend rejects invented segment ids and enforces adjacency", () => {
  assert.match(backend, /發明不存在的 segment/);
  assert.match(backend, /跨段整理必須是相鄰 segments/);
});

test("backend keeps every candidate review_required (no auto-apply)", () => {
  assert.match(backend, /"high_review_required": True/);
  assert.doesNotMatch(backend, /"status": "accepted"/.source.replace("\\ ", " ") && /auto_accept/);
});

test("high-risk filter uses risk only — high_review_required is a different concept", () => {
  assert.match(panel, /candidate\.risk === "high";/);
  assert.doesNotMatch(panel, /risk === "high" \|\| candidate\.high_review_required/);
});

test("exports render from active revision with lineage", () => {
  assert.match(backend, /def render_srt/);
  assert.match(backend, /def render_vtt/);
  assert.match(backend, /WEBVTT/);
  assert.match(backend, /def render_docx_bytes/);
  assert.match(backend, /active_revision/);
});
