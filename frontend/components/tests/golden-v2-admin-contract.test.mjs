import assert from "node:assert/strict";
import test from "node:test";

import {
  formatUsdCents,
  isGoldenV2JobId,
  patchGoldenV2ApprovalForm,
  parseGoldenV2ApprovalPayload,
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
});

test("editing approval amount or reference requires fresh confirmation", () => {
  const confirmed = { amount: "125", reference: "checked source", confirmed: true };
  assert.deepEqual(patchGoldenV2ApprovalForm(confirmed, { amount: "150" }), {
    amount: "150", reference: "checked source", confirmed: false,
  });
  assert.deepEqual(patchGoldenV2ApprovalForm(confirmed, { reference: "new basis" }), {
    amount: "125", reference: "new basis", confirmed: false,
  });
  assert.equal(patchGoldenV2ApprovalForm(confirmed, { confirmed: false }).confirmed, false);
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
