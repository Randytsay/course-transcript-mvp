import { proxyLinkBatchGet } from "@/lib/golden-v2-link-proxy.server";
export const dynamic = "force-dynamic";
export async function GET(request: Request, context: { params: Promise<{ batchId: string }> }) {
  return proxyLinkBatchGet(request, (await context.params).batchId);
}
