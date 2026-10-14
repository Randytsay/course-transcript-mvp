import {
  goldenV2OwnerApiBase, goldenV2OwnerToken, isGoldenV2AdminEnabled,
  isGoldenV2AdminRequestAllowed, isGoldenV2MutationOriginAllowed,
} from "./golden-v2-admin.server";
import { parseLinkBatch, parseLinkPreview } from "./golden-v2-admin-contract";

const json = (body: unknown, status = 200) => Response.json(body, {
  status, headers: { "Cache-Control": "no-store" },
});

export async function proxyLinkPost(request: Request, action: "link-preview" | "link-batches") {
  if (!isGoldenV2AdminEnabled() || !isGoldenV2AdminRequestAllowed(request.headers)
      || !isGoldenV2MutationOriginAllowed(request)) return json({ detail: "V2 route unavailable" }, 404);
  const token = goldenV2OwnerToken(action === "link-preview" ? "status" : "write");
  if (!token) return json({ detail: "V2 owner service not configured" }, 503);
  const reader = request.body?.getReader();
  if (!reader) return json({ detail: "Request body required" }, 422);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 65536) { await reader.cancel(); return json({ detail: "Request too large" }, 413); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const input: unknown = JSON.parse(new TextDecoder().decode(bytes));
    const body = action === "link-preview" ? parseLinkPreview(input) : parseLinkBatch(input);
    if (!body) return json({ detail: "Invalid Drive link request" }, 422);
    const response = await fetch(`${goldenV2OwnerApiBase()}/api/v1/golden-v2/${action}`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body), cache: "no-store", signal: AbortSignal.timeout(180000),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) return json({ detail: response.status >= 500 ? "V2 Drive service unavailable"
      : typeof payload?.detail === "string" ? payload.detail : "Drive request rejected" }, response.status >= 500 ? 503 : response.status);
    return json(payload, action === "link-preview" ? 200 : 201);
  } catch (error) {
    return json({ detail: error instanceof SyntaxError ? "Invalid request JSON" : "V2 service unavailable; refresh batch status before retrying" }, error instanceof SyntaxError ? 422 : 503);
  }
}

export async function proxyLinkBatchGet(request: Request, id: string) {
  if (!isGoldenV2AdminEnabled() || !isGoldenV2AdminRequestAllowed(request.headers)
      || !/^v2b_[0-9a-f]{32}$/.test(id)) return json({ detail: "V2 route unavailable" }, 404);
  const token = goldenV2OwnerToken("status");
  if (!token) return json({ detail: "V2 owner service not configured" }, 503);
  try {
    const response = await fetch(`${goldenV2OwnerApiBase()}/api/v1/golden-v2/link-batches/${id}`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    const payload = await response.json().catch(() => null);
    return json(response.ok ? payload : { detail: "Batch status unavailable" }, response.status >= 500 ? 503 : response.status);
  } catch { return json({ detail: "Batch status unavailable" }, 503); }
}
