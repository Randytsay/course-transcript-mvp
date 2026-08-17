export type ReviewHostAction = "allow" | "redirect_review" | "deny_api";

export function normalizeHostname(value: string | null | undefined): string {
  const first = (value ?? "").split(",", 1)[0]?.trim().toLowerCase() ?? "";
  if (!first) return "";
  if (first.startsWith("[")) {
    const end = first.indexOf("]");
    return end >= 0 ? first.slice(0, end + 1) : first;
  }
  return first.split(":", 1)[0] ?? "";
}

function hasPathPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function classifyReviewHostRequest(
  hostname: string,
  pathname: string,
  reviewHostname: string,
): ReviewHostAction {
  if (normalizeHostname(hostname) !== normalizeHostname(reviewHostname)) {
    return "allow";
  }

  if (
    hasPathPrefix(pathname, "/review") ||
    hasPathPrefix(pathname, "/api/v1/review") ||
    hasPathPrefix(pathname, "/_next") ||
    pathname === "/favicon.ico" ||
    pathname === "/robots.txt"
  ) {
    return "allow";
  }

  if (hasPathPrefix(pathname, "/api")) {
    return "deny_api";
  }

  return "redirect_review";
}
