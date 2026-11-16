import {
  goldenV2OwnerApiBase, goldenV2OwnerToken, isGoldenV2MutationOriginAllowed,
} from "@/lib/golden-v2-admin.server";
import { isVerifiedGoldenV2PaidOperator } from "@/lib/golden-v2-paid-console-auth.server";

export const dynamic = "force-dynamic";

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  if (!isGoldenV2MutationOriginAllowed(request)
      || !await isVerifiedGoldenV2PaidOperator(request.headers)) {
    return reply({ detail: "Golden V2 paid console unavailable" }, 404);
  }
  if (Number(request.headers.get("content-length") || 0) > 4096) {
    return reply({ detail: "Request too large" }, 413);
  }
  const token = goldenV2OwnerToken("write");
  const statusToken = goldenV2OwnerToken("status");
  if (!token || !statusToken) return reply({ detail: "Owner credentials unavailable" }, 503);
  const raw: unknown = await request.json().catch(() => null);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return reply({ detail: "Invalid confirmation" }, 422);
  }
  const body = raw as Record<string, unknown>;
  if (Object.keys(body).sort().join(",") !==
      "approved_cents,confirm,job_id,quote_sha256,reference,revision,source_sha256") {
    return reply({ detail: "Invalid approval fields" }, 422);
  }
  const id = body.job_id;
  const amount = body.approved_cents;
  const revision = body.revision;
  if (typeof id !== "string" || !/^v2p_[0-9a-f]{32}$/.test(id)
      || !Number.isSafeInteger(amount) || Number(amount) <= 0 || Number(amount) > 500
      || !Number.isSafeInteger(revision) || Number(revision) < 0
      || typeof body.quote_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(body.quote_sha256)
      || typeof body.source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(body.source_sha256)
      || typeof body.reference !== "string"
      || body.reference.trim().length < 10 || body.reference.length > 256
      || body.confirm !== true) return reply({ detail: "Invalid signed-grant request" }, 422);

  // A genuine, enabled backend console must independently confirm eligibility.
  // Never allow an unprotected fallback to the legacy authorization route.
  try {
    const policyResponse = await fetch(
      goldenV2OwnerApiBase() + "/api/v1/golden-v2/paid-console",
      { headers: { Authorization: "Bearer " + statusToken },
        cache: "no-store", signal: AbortSignal.timeout(12000) },
    );
    if (!policyResponse.ok) return reply({ detail: "Paid policy unavailable" }, 503);
    const policy = await policyResponse.json();
    const job = Array.isArray(policy.jobs)
      ? policy.jobs.find((entry: {job_id?: string}) => entry?.job_id === id) : null;
    if (!job || !job.can_approve || job.revision !== revision
        || job.source_sha256 !== body.source_sha256
        || job.quote_sha256 !== body.quote_sha256
        || job.estimated_cents > Number(amount)
        || job.duration_seconds < 3600
        || Number(policy.reserved_exposure_cents) + Number(amount) > 10000) {
      return reply({ detail: "Source, duration or batch budget is not eligible" }, 409);
    }
    const upstream = await fetch(
      goldenV2OwnerApiBase() + "/api/v1/golden-v2/jobs/" + id + "/authorize-paid",
      { method: "POST", headers: {
          Authorization: "Bearer " + token, "Content-Type": "application/json",
        }, body: JSON.stringify({
          revision, quote_sha256: body.quote_sha256,
          source_sha256: body.source_sha256, approved_cents: amount,
          reference: body.reference.trim(), confirm: true,
        }), cache: "no-store", signal: AbortSignal.timeout(180000) },
    );
    const result = await upstream.json().catch(() => null);
    if (!upstream.ok) {
      return reply({ detail: upstream.status === 409 || upstream.status === 422
        ? "核准條件已變更，請重新整理後確認" : "核准服務拒絕，請先查詢狀態，不要重送" },
        upstream.status === 409 ? 409 : 503);
    }
    return reply(result);
  } catch {
    // Network errors are ambiguous: the POST may have succeeded upstream.
    return reply({ detail: "核准結果未確認。請重新整理狀態，禁止直接重試。" }, 503);
  }
}
