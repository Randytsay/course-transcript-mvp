import {
  goldenV2OwnerApiBase,
  goldenV2OwnerToken,
  isGoldenV2AdminEnabled,
  isGoldenV2AdminRequestAllowed,
  isGoldenV2MutationOriginAllowed,
} from "@/lib/golden-v2-admin.server";
import { parseGoldenV2SourcePreflightPayload } from "@/lib/golden-v2-admin-contract";

export const dynamic = "force-dynamic";

function jsonNoStore(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

async function readBoundedBody(request: Request, maxBytes: number): Promise<string | null> {
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > maxBytes) return null;
  const reader = request.body?.getReader();
  if (!reader) return "";
  const decoder = new TextDecoder();
  let text = "";
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        return null;
      }
      text += decoder.decode(value, { stream: true });
    }
    return text + decoder.decode();
  } catch {
    await reader.cancel().catch(() => undefined);
    return "";
  }
}

export async function POST(request: Request) {
  if (!isGoldenV2AdminEnabled() || !isGoldenV2AdminRequestAllowed(request.headers)
      || !isGoldenV2MutationOriginAllowed(request)) {
    return jsonNoStore({ detail: "Golden V2 admin route unavailable" }, 404);
  }
  const token = goldenV2OwnerToken("write");
  if (!token) {
    return jsonNoStore({ detail: "Golden V2 owner actions are not configured" }, 503);
  }
  const rawBody = await readBoundedBody(request, 8192);
  if (rawBody === null) {
    return jsonNoStore({ detail: "Golden V2 source request is too large" }, 413);
  }
  let input: unknown = null;
  try { input = JSON.parse(rawBody); } catch { /* invalid body is rejected below */ }
  const body = parseGoldenV2SourcePreflightPayload(input);
  if (!body) {
    return jsonNoStore({ detail: "A Drive path, bounded estimate and explicit confirmation are required" }, 422);
  }
  try {
    const upstream = await fetch(
      `${goldenV2OwnerApiBase()}/api/v1/golden-v2/source-preflight`,
      { method: "POST", headers: {
        Authorization: `Bearer ${token}`, "Content-Type": "application/json",
      }, body: JSON.stringify(body), cache: "no-store",
        signal: AbortSignal.timeout(3 * 60 * 60 * 1000) },
    );
    const payload = await upstream.json().catch(() => null);
    if (!upstream.ok) {
      const status = upstream.status >= 500 ? 503 : upstream.status;
      const detail = status === 503 ? "Golden V2 source service is unavailable"
        : typeof payload?.detail === "string" ? payload.detail : "Golden V2 source preflight failed";
      return jsonNoStore({ detail }, status);
    }
    return jsonNoStore(payload, 201);
  } catch {
    return jsonNoStore({ detail: "Golden V2 source service could not be reached" }, 503);
  }
}
