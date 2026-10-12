import {
  goldenV2OwnerApiBase,
  goldenV2OwnerToken,
  isGoldenV2AdminEnabled,
  isGoldenV2AdminRequestAllowed,
} from "@/lib/golden-v2-admin.server";

export const dynamic = "force-dynamic";

function jsonNoStore(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

export async function GET(request: Request) {
  if (!isGoldenV2AdminEnabled() || !isGoldenV2AdminRequestAllowed(request.headers)) {
    return jsonNoStore({ detail: "Golden V2 admin route unavailable" }, 404);
  }
  const token = goldenV2OwnerToken("status");
  if (!token) {
    return jsonNoStore({ detail: "Golden V2 admin service is not configured" }, 503);
  }
  try {
    const upstream = await fetch(
      `${goldenV2OwnerApiBase()}/api/v1/golden-v2/jobs?limit=100`,
      { headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        signal: AbortSignal.timeout(15000) },
    );
    const payload = await upstream.json().catch(() => null);
    if (!upstream.ok) {
      const status = upstream.status >= 500 ? 503 : upstream.status;
      const detail = status === 503 ? "Golden V2 ledger is unavailable"
        : typeof payload?.detail === "string" ? payload.detail : "Golden V2 request failed";
      return jsonNoStore({ detail }, status);
    }
    return jsonNoStore(payload);
  } catch {
    return jsonNoStore({ detail: "Golden V2 service could not be reached" }, 503);
  }
}
