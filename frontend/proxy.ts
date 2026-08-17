import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { classifyReviewHostRequest } from "@/lib/review-host-boundary";

const REVIEW_PUBLIC_HOSTNAME =
  process.env.REVIEW_PUBLIC_HOSTNAME?.trim().toLowerCase() ||
  "review.randy88.ccwu.cc";

export function proxy(request: NextRequest) {
  const hostname =
    request.headers.get("x-forwarded-host") ||
    request.headers.get("host") ||
    request.nextUrl.hostname;
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
