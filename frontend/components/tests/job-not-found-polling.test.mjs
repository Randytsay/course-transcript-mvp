import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("../job-detail-page.tsx", import.meta.url), "utf8");
const apiClient = readFileSync(new URL("../../lib/api-client.ts", import.meta.url), "utf8");

test("job detail resolves the canonical job before dependent polling", () => {
  assert.match(source, /const nextJob = await getJob\(jobId\)/);
  assert.match(source, /const \[nextSegments, nextTerms, nextArtifacts, nextEvents, nextChunks, nextCost\] = await Promise\.all/);
});

test("404 becomes a terminal stale-job state instead of an endless polling loop", () => {
  assert.match(apiClient, /export class ApiClientError extends Error/);
  assert.match(source, /cause instanceof ApiClientError && cause\.status === 404/);
  assert.match(source, /setJobNotFound\(true\)/);
  assert.match(source, /if \(jobNotFound\) return;/);
  assert.match(source, /系統已停止重複查詢/);
});
