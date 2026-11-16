import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import {
  classifyReviewHostRequest,
  normalizeHostname,
} from "@/lib/review-host-boundary";

function normalizeHostnameSafe(value: string | null | undefined): string {
  return normalizeHostname(value);
}

const REVIEW_PUBLIC_HOSTNAME =
  process.env.REVIEW_PUBLIC_HOSTNAME?.trim().toLowerCase() ||
  "review.randy88.ccwu.cc";

const ADMIN_HOSTNAMES = new Set(
  (process.env.ADMIN_PUBLIC_HOSTNAMES?.trim().toLowerCase() || "transcript.randy88.ccwu.cc")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);

const REVIEW_PUBLIC_ORIGIN =
  process.env.REVIEW_PUBLIC_ORIGIN?.trim().replace(/\/$/, "") ||
  "https://review.randy88.ccwu.cc";

export function proxy(request: NextRequest) {
  // Prefer the actual HTTP Host. A client-controlled X-Forwarded-Host must not
  // be able to make the public reviewer hostname look like the protected admin
  // hostname. The forwarded value is only a fallback for unusual local proxies.
  const hostname =
    request.headers.get("host") ||
    request.headers.get("x-forwarded-host") ||
    request.nextUrl.hostname;
  // Dedicated V2 console intentionally cannot reach V1 review/admin routes
  // or the generic /api/v1 reverse proxy, even though its server has Owner
  // credentials. This restriction does not apply to the V1 frontend stack.
  if (process.env.COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ONLY === "true") {
    const path = request.nextUrl.pathname;
    const allowed = path === "/" || path === "/golden-v2/paid"
      || path === "/api/golden-v2/paid-console"
      || path === "/api/golden-v2/paid-console/authorize"
      || path === "/favicon.ico"
      || path === "/images/cisheng-lotus-seal.png";
    if (!allowed) return new NextResponse("Not Found", {
      status: 404, headers: { "Cache-Control":"no-store" },
    });
  }
  const action = classifyReviewHostRequest(
    hostname,
    request.nextUrl.pathname,
    REVIEW_PUBLIC_HOSTNAME,
  );

  if (action === "deny_api") {
    return new NextResponse("Not Found", {
      status: 404,
      headers: { "Cache-Control": "no-store" },
    });
  }

  // Reviewer pages opened from an admin hostname must continue on the review
  // origin, otherwise the reviewer auth Origin check would reject the login.
  if (
    ADMIN_HOSTNAMES.has(normalizeHostnameSafe(hostname)) &&
    (request.nextUrl.pathname === "/review" ||
      request.nextUrl.pathname.startsWith("/review/"))
  ) {
    const target = new URL(
      request.nextUrl.pathname + request.nextUrl.search,
      REVIEW_PUBLIC_ORIGIN,
    );
    return NextResponse.redirect(target, 308);
  }

  if (action === "redirect_review") {
    const target = request.nextUrl.clone();
    target.pathname = "/review";
    target.search = "";
    return NextResponse.redirect(target, 307);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
