import assert from "node:assert/strict";
import test from "node:test";

import {
  GOLDEN_V2_STAGES,
  formatUsdCents,
  isGoldenV2JobId,
  patchGoldenV2ApprovalForm,
  patchGoldenV2SourceForm,
  parseGoldenV2ApprovalPayload,
  parseGoldenV2SourcePreflightPayload,
} from "../../lib/golden-v2-admin-contract.ts";
import {
  goldenV2OwnerApiBase,
  goldenV2OwnerToken,
  isGoldenV2AdminEnabled,
  isGoldenV2AdminRequestAllowed,
  isGoldenV2MutationOriginAllowed,
} from "../../lib/golden-v2-admin.server.ts";

function headers(values = {}) {
  return { get(name) { return values[name.toLowerCase()] ?? null; } };
}

test("owner approval payload requires exact fields, true confirmation and bounded values", () => {
  const valid = {
    revision: 3, approved_cents: 125, reference: "reviewed source v19", confirm: true,
  };
  assert.deepEqual(parseGoldenV2ApprovalPayload(valid), valid);
  assert.equal(parseGoldenV2ApprovalPayload({ ...valid, confirm: false }), null);
  assert.equal(parseGoldenV2ApprovalPayload({ ...valid, approved_cents: -1 }), null);
  assert.equal(parseGoldenV2ApprovalPayload({ ...valid, revision: Number.MAX_SAFE_INTEGER + 1 }), null);
  assert.equal(parseGoldenV2ApprovalPayload({ ...valid, reference: "  " }), null);
  assert.equal(parseGoldenV2ApprovalPayload({ ...valid, extra: "rejected" }), null);
  assert.deepEqual(parseGoldenV2ApprovalPayload({ ...valid, repeat_source_confirmed: true }),
    { ...valid, repeat_source_confirmed: true });
  assert.equal(parseGoldenV2ApprovalPayload({ ...valid, repeat_source_confirmed: false }), null);
});

test("editing approval amount or reference requires fresh confirmation", () => {
  const confirmed = { amount: "125", reference: "checked source", confirmed: true,
    repeatSourceConfirmed: true };
  assert.deepEqual(patchGoldenV2ApprovalForm(confirmed, { amount: "150" }), {
    amount: "150", reference: "checked source", confirmed: false,
    repeatSourceConfirmed: false,
  });
  assert.deepEqual(patchGoldenV2ApprovalForm(confirmed, { reference: "new basis" }), {
    amount: "125", reference: "new basis", confirmed: false,
    repeatSourceConfirmed: false,
  });
  assert.equal(patchGoldenV2ApprovalForm(confirmed, { confirmed: false }).confirmed, false);
  assert.equal(patchGoldenV2ApprovalForm(confirmed, { repeatSourceConfirmed: true }).confirmed, false);
});

test("Drive source preflight requires exact path, estimates and explicit confirmation", () => {
  const valid = {
    source_path: "gdrive:課程/第一課.m4a", mode: "FLASH_CHIRP",
    estimated_stage_cents: Object.fromEntries(GOLDEN_V2_STAGES.map((stage) => [stage, 0])),
    confirm: true,
  };
  assert.deepEqual(parseGoldenV2SourcePreflightPayload(valid), valid);
  assert.equal(parseGoldenV2SourcePreflightPayload({ ...valid, confirm: false }), null);
  assert.equal(parseGoldenV2SourcePreflightPayload({ ...valid, source_path: "/tmp/audio.wav" }), null);
  assert.equal(parseGoldenV2SourcePreflightPayload({ ...valid, mode: "UNSAFE" }), null);
  assert.equal(parseGoldenV2SourcePreflightPayload({
    ...valid, estimated_stage_cents: { ...valid.estimated_stage_cents, extra: 1 },
  }), null);
  assert.equal(parseGoldenV2SourcePreflightPayload({
    ...valid, estimated_stage_cents: { ...valid.estimated_stage_cents, gemini_primary: -1 },
  }), null);
});

test("changing source, mode or estimates resets preflight confirmation", () => {
  const confirmed = {
    sourcePath: "gdrive:lesson.mp3", mode: "FLASH_CHIRP",
    estimates: Object.fromEntries(GOLDEN_V2_STAGES.map((stage) => [stage, "0"])),
    confirmed: true,
  };
  assert.equal(patchGoldenV2SourceForm(confirmed, { sourcePath: "gdrive:other.mp3" }).confirmed, false);
  assert.equal(patchGoldenV2SourceForm(confirmed, { mode: "SMART_2PLUS" }).confirmed, false);
  assert.equal(patchGoldenV2SourceForm(confirmed, {
    estimates: { ...confirmed.estimates, gemini_primary: "5" },
  }).confirmed, false);
  assert.equal(patchGoldenV2SourceForm(confirmed, { confirmed: false }).confirmed, false);
});

test("owner route accepts only real V2 job identities", () => {
  assert.equal(isGoldenV2JobId(`v2_${"a".repeat(32)}`), true);
  assert.equal(isGoldenV2JobId(`v2p_${"b".repeat(32)}`), true);
  assert.equal(isGoldenV2JobId("v2_not-a-job"), false);
  assert.equal(isGoldenV2JobId(`v2p_${"A".repeat(32)}`), false);
});

test("production UI is disabled by default and requires admin host plus Access identity", () => {
  const production = { NODE_ENV: "production", ADMIN_PUBLIC_HOSTNAMES: "transcript.example.test" };
  assert.equal(isGoldenV2AdminEnabled(production), false);
  assert.equal(isGoldenV2AdminEnabled({ ...production, COURSE_TRANSCRIPT_V2_ADMIN_UI_ENABLED: "true" }), true);
  assert.equal(isGoldenV2AdminRequestAllowed(headers({ host: "review.example.test" }), production), false);
  assert.equal(isGoldenV2AdminRequestAllowed(headers({ host: "transcript.example.test" }), production), false);
  assert.equal(isGoldenV2AdminRequestAllowed(headers({
    host: "transcript.example.test",
    "cf-access-authenticated-user-email": "owner@example.test",
    "cf-access-jwt-assertion": "signed-assertion",
  }), production), true);
});

test("development permits localhost but approval POSTs remain same-origin", () => {
  const development = { NODE_ENV: "development" };
  assert.equal(isGoldenV2AdminRequestAllowed(headers({ host: "localhost:3000" }), development), true);
  const request = new Request("http://localhost:3000/api/golden-v2/jobs/test/approval", {
    method: "POST", headers: { host: "localhost:3000", origin: "http://localhost:3000" },
  });
  assert.equal(isGoldenV2MutationOriginAllowed(request, development), true);
  const crossOrigin = new Request("http://localhost:3000/api/golden-v2/jobs/test/approval", {
    method: "POST", headers: { host: "localhost:3000", origin: "https://attacker.example" },
  });
  assert.equal(isGoldenV2MutationOriginAllowed(crossOrigin, development), false);
  assert.equal(isGoldenV2MutationOriginAllowed(new Request("https://transcript.example.test"), development), false);
});

test("owner token stays server configured and API base defaults by runtime", () => {
  assert.equal(goldenV2OwnerToken("write", {
    COURSE_TRANSCRIPT_V2_WRITE_TOKEN: "short",
  }), null);
  assert.equal(goldenV2OwnerToken("status", {
    COURSE_TRANSCRIPT_V2_STATUS_TOKEN: "s".repeat(32),
  }), "s".repeat(32));
  assert.equal(goldenV2OwnerApiBase({ NODE_ENV: "production" }), "http://api:8000");
  assert.equal(goldenV2OwnerApiBase({ API_INTERNAL_BASE_URL: "http://api.internal:8000/" }), "http://api.internal:8000");
  assert.equal(formatUsdCents(125), "$1.25");
  assert.equal(formatUsdCents(null), "—");
});

import { parseLinkPreview, parseLinkBatch } from "../../lib/golden-v2-admin-contract.ts";

test("Drive link contract rejects foreign hosts, resource keys and ambiguous IDs", () => {
  const link = "https://drive.google.com/drive/folders/folder0123456789";
  assert.deepEqual(parseLinkPreview({ link, recursive: false }), { link, recursive: false });
  for (const value of [link.replace("drive.google.com", "drive.google.com.evil.test"), link + "#x", link + "?resourcekey=secret",
    "https://drive.google.com/open?id=file0123456789&id=other0123456789", link + "\n"]) {
    assert.equal(parseLinkPreview({ link: value, recursive: false }), null);
  }
  assert.equal(parseLinkPreview({ link, recursive: "false" }), null);
  assert.equal(parseLinkPreview({ link, recursive: false, extra: true }), null);
});

test("batch contract requires explicit complete per-file estimates and confirmation", () => {
  const valid = { link: "https://drive.google.com/file/d/file0123456789/view", recursive: false,
    mode: "FLASH_CHIRP", preview_sha256: "a".repeat(64), confirm: true,
    estimated_stage_cents_by_file: { file0123456789: Object.fromEntries(GOLDEN_V2_STAGES.map((s) => [s, 0])) } };
  assert.deepEqual(parseLinkBatch(valid), valid);
  assert.equal(parseLinkBatch({ ...valid, confirm: false }), null);
  assert.equal(parseLinkBatch({ ...valid, estimated_stage_cents_by_file: {} }), null);
  assert.equal(parseLinkBatch({ ...valid, estimated_stage_cents_by_file: { file0123456789: {} } }), null);
  assert.equal(parseLinkBatch({ ...valid, unexpected: true }), null);
  const nan = structuredClone(valid); nan.estimated_stage_cents_by_file.file0123456789.gemini_primary = NaN;
  assert.equal(parseLinkBatch(nan), null);
});
