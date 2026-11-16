import { createPublicKey, verify as verifySignature } from "node:crypto";
import {
  isGoldenV2AdminEnabled,
  isGoldenV2AdminRequestAllowed,
} from "./golden-v2-admin.server.ts";

type HeaderReader = { get(name: string): string | null };
type Environment = Record<string, string | undefined>;

function parseSegment(value: string): Record<string, unknown> | null {
  try {
    const json = Buffer.from(value, "base64url").toString("utf8");
    const parsed: unknown = JSON.parse(json);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function isSignedClaimValid(
  claim: Record<string, unknown>, teamDomain: string,
  audience: string, allowedEmails: Set<string>, now: number,
): boolean {
  const audiences = Array.isArray(claim.aud) ? claim.aud : [claim.aud];
  const email = typeof claim.email === "string" ? claim.email.toLowerCase() : "";
  return claim.iss === "https://" + teamDomain
    && audiences.includes(audience)
    && allowedEmails.has(email)
    && typeof claim.sub === "string" && claim.sub.length > 0
    && typeof claim.exp === "number" && Number.isSafeInteger(claim.exp)
    && claim.exp > now && claim.exp <= now + 86400
    && (claim.nbf === undefined || (typeof claim.nbf === "number" && claim.nbf <= now))
    && (claim.iat === undefined || (typeof claim.iat === "number" && claim.iat <= now + 90));
}

/** Verify Access JWT RSA signature, application audience, issuer and operator email.
 * A spoofed cf-access-authenticated-user-email header never authorizes payment.
 * Absence of any required configuration or trusted key fails closed.
 */
export async function isVerifiedGoldenV2PaidOperator(
  headers: HeaderReader,
  env: Environment = process.env,
  fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
  if (env.NODE_ENV !== "production" ||
      env.COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED !== "true" ||
      !isGoldenV2AdminEnabled(env) ||
      !isGoldenV2AdminRequestAllowed(headers, env)) return false;

  const teamDomain = env.COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ACCESS_TEAM_DOMAIN ?? "";
  const audience = env.COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ACCESS_AUD ?? "";
  const allowedEmails = new Set((env.COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ALLOWED_EMAILS ?? "")
    .split(",").map((entry) => entry.trim().toLowerCase()).filter(Boolean));
  if (!/^[a-z0-9-]+\.cloudflareaccess\.com$/.test(teamDomain) ||
      !/^[a-zA-Z0-9_-]{16,256}$/.test(audience) || allowedEmails.size === 0) return false;

  const jwt = headers.get("cf-access-jwt-assertion") ?? "";
  if (jwt.length < 80 || jwt.length > 8192) return false;
  const parts = jwt.split(".");
  if (parts.length !== 3) return false;
  const [encodedHeader, encodedClaim, encodedSignature] = parts;
  if (!encodedHeader || !encodedClaim || !encodedSignature) return false;
  const jwtHeader = parseSegment(encodedHeader);
  const claim = parseSegment(encodedClaim);
  if (!jwtHeader || jwtHeader.alg !== "RS256" ||
      typeof jwtHeader.kid !== "string" || !claim) return false;
  if (!isSignedClaimValid(
    claim, teamDomain, audience, allowedEmails, Math.floor(Date.now() / 1000)
  )) return false;

  try {
    const response = await fetchImpl("https://" + teamDomain + "/cdn-cgi/access/certs", {
      method: "GET", cache: "no-store", signal: AbortSignal.timeout(6000),
    });
    if (!response.ok) return false;
    const jwks: unknown = await response.json();
    if (!jwks || typeof jwks !== "object" || !("keys" in jwks) ||
        !Array.isArray(jwks.keys) || jwks.keys.length > 30) return false;

    const jwk = jwks.keys.find((key: unknown) =>
      key && typeof key === "object" && "kid" in key && key.kid === jwtHeader.kid);
    if (!jwk || jwk.kty !== "RSA" || jwk.use !== "sig") return false;
    return verifySignature(
      "RSA-SHA256",
      Buffer.from(encodedHeader + "." + encodedClaim, "utf8"),
      createPublicKey({ key: jwk, format: "jwk" }),
      Buffer.from(encodedSignature, "base64url"),
    );
  } catch {
    return false;
  }
}
