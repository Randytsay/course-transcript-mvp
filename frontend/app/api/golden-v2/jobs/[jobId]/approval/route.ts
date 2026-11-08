import {
  goldenV2OwnerApiBase,
  goldenV2OwnerToken,
  isGoldenV2AdminEnabled,
  isGoldenV2AdminRequestAllowed,
  isGoldenV2MutationOriginAllowed,
} from "@/lib/golden-v2-admin.server";
import {
  isGoldenV2JobId,
  parseGoldenV2ApprovalPayload,
} from "@/lib/golden-v2-admin-contract";

export const dynamic = "force-dynamic";

function jsonNoStore(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

export async function POST(
  request: Request,
  context: { params: Promise<{ jobId: string }> },
) {
  if (!isGoldenV2AdminEnabled() || !isGoldenV2AdminRequestAllowed(request.headers)
      || !isGoldenV2MutationOriginAllowed(request)) {
    return jsonNoStore({ detail: "Golden V2 admin route unavailable" }, 404);
  }
  const { jobId } = await context.params;
  if (!isGoldenV2JobId(jobId)) {
    return jsonNoStore({ detail: "Golden V2 job not found" }, 404);
  }
  const token = goldenV2OwnerToken("write");
  if (!token) {
    return jsonNoStore({ detail: "Golden V2 owner actions are not configured" }, 503);
  }
  const input = await request.json().catch(() => null);
  const body = parseGoldenV2ApprovalPayload(input);
  if (!body) {
    return jsonNoStore({ detail: "Explicit budget and source confirmation is required" }, 422);
  }
  try {
    const upstream = await fetch(
      `${goldenV2OwnerApiBase()}/api/v1/golden-v2/jobs/${jobId}/approval`,
      { method: "POST", headers: {
        Authorization: `Bearer ${token}`, "Content-Type": "application/json",
      }, body: JSON.stringify(body), cache: "no-store",
        signal: AbortSignal.timeout(15000) },
    );
    const payload = await upstream.json().catch(() => null);
    if (!upstream.ok) {
      const status = upstream.status >= 500 ? 503 : upstream.status;
      const detail = status === 503 ? "Golden V2 owner service is unavailable"
        : typeof payload?.detail === "string" ? payload.detail : "Golden V2 approval failed";
      return jsonNoStore({ detail }, status);
    }
    return jsonNoStore(payload);
  } catch {
    return jsonNoStore({ detail: "Golden V2 owner service could not be reached" }, 503);
  }
}
