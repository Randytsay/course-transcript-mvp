import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyReviewHostRequest,
  normalizeHostname,
} from "../../lib/review-host-boundary.ts";

const REVIEW = "review.randy88.ccwu.cc";

test("normalizes forwarded host values and ports", () => {
  assert.equal(normalizeHostname("review.randy88.ccwu.cc:443"), REVIEW);
  assert.equal(
    normalizeHostname("review.randy88.ccwu.cc, frontend:3000"),
    REVIEW,
  );
});

test("review hostname allows only reviewer pages and APIs", () => {
  for (const path of [
    "/review",
    "/review/videos",
    "/review/contributions",
    "/api/v1/review/auth/google/callback",
    "/api/v1/review/videos",
    "/favicon.ico",
  ]) {
    assert.equal(classifyReviewHostRequest(REVIEW, path, REVIEW), "allow", path);
  }
});

test("review hostname denies non-review APIs instead of forwarding them", () => {
  for (const path of [
    "/api/v1/jobs",
    "/api/v1/review-admin/overview",
    "/api/v1/subtitles/example/publish-status",
  ]) {
    assert.equal(
      classifyReviewHostRequest(REVIEW, path, REVIEW),
      "deny_api",
      path,
    );
  }
});

test("review hostname redirects non-review pages to the reviewer landing page", () => {
  for (const path of ["/", "/jobs", "/review-admin", "/settings"]) {
    assert.equal(
      classifyReviewHostRequest(REVIEW, path, REVIEW),
      "redirect_review",
      path,
    );
  }
});

test("transcript and local development hosts remain untouched", () => {
  for (const host of ["transcript.randy88.ccwu.cc", "localhost:3000"])
    assert.equal(classifyReviewHostRequest(host, "/api/v1/jobs", REVIEW), "allow");
});
