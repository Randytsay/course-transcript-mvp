import {
  goldenV2OwnerApiBase, goldenV2OwnerToken,
} from "@/lib/golden-v2-admin.server";
import { isVerifiedGoldenV2PaidOperator } from "@/lib/golden-v2-paid-console-auth.server";

export const dynamic = "force-dynamic";

function reply(body: unknown, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

export async function GET(request: Request) {
  if (!await isVerifiedGoldenV2PaidOperator(request.headers)) {
    return reply({ detail: "Golden V2 paid console unavailable" }, 404);
  }
  const token = goldenV2OwnerToken("status");
  if (!token) return reply({ detail: "Missing server-side Owner status credential" }, 503);
  try {
    const upstream = await fetch(
      goldenV2OwnerApiBase() + "/api/v1/golden-v2/paid-console",
      { headers: { Authorization: "Bearer " + token }, cache: "no-store",
        signal: AbortSignal.timeout(12000) },
    );
    const body = await upstream.json().catch(() => null);
    if (!upstream.ok) {
      return reply({ detail: upstream.status === 503 ? "付費管理帳本不可用"
        : "付費管理功能尚未在後端啟用" }, upstream.status >= 500 ? 503 : 404);
    }
    return reply(body);
  } catch {
    return reply({ detail: "無法連線 Golden V2 管理服務" }, 503);
  }
}
