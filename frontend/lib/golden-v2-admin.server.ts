type HeaderReader = { get(name: string): string | null };
type Environment = Record<string, string | undefined>;

export function isGoldenV2AdminEnabled(env: Environment = process.env): boolean {
  return env.COURSE_TRANSCRIPT_V2_ADMIN_UI_ENABLED === "true";
}

function normalizedHost(value: string | null): string {
  const first = (value ?? "").split(",", 1)[0]?.trim().toLowerCase() ?? "";
  if (first.startsWith("[")) {
    const end = first.indexOf("]");
    return end >= 0 ? first.slice(0, end + 1) : first;
  }
  return first.split(":", 1)[0] ?? "";
}

export function isGoldenV2AdminRequestAllowed(
  headers: HeaderReader,
  env: Environment = process.env,
): boolean {
  const host = normalizedHost(headers.get("host"));
  const allowed = (env.ADMIN_PUBLIC_HOSTNAMES || "transcript.randy88.ccwu.cc")
    .split(",").map((item) => item.trim().toLowerCase()).filter(Boolean);
  const localDevelopmentHost = env.NODE_ENV !== "production"
    && ["localhost", "127.0.0.1", "[::1]"].includes(host);
  if (!allowed.includes(host) && !localDevelopmentHost) return false;
  if (env.NODE_ENV === "production") {
    return Boolean(
      headers.get("cf-access-authenticated-user-email")
      && headers.get("cf-access-jwt-assertion"),
    );
  }
  return true;
}

export function isGoldenV2MutationOriginAllowed(
  request: Request,
  env: Environment = process.env,
): boolean {
  const host = normalizedHost(request.headers.get("host"));
  const origin = request.headers.get("origin");
  if (!host || !origin) return false;
  try {
    const parsed = new URL(origin);
    if (normalizedHost(parsed.host) !== host) return false;
    return env.NODE_ENV !== "production" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export function goldenV2OwnerApiBase(env: Environment = process.env): string {
  return (env.API_INTERNAL_BASE_URL
    || (env.NODE_ENV === "production" ? "http://api:8000" : "http://127.0.0.1:8000"))
    .replace(/\/+$/, "");
}

export function goldenV2OwnerToken(
  scope: "status" | "write",
  env: Environment = process.env,
): string | null {
  const value = env[scope === "status"
    ? "COURSE_TRANSCRIPT_V2_STATUS_TOKEN"
    : "COURSE_TRANSCRIPT_V2_WRITE_TOKEN"];
  return value && value.length >= 32 ? value : null;
}
