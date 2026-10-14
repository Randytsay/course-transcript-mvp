import { proxyLinkPost } from "@/lib/golden-v2-link-proxy.server";
export const dynamic = "force-dynamic";
export const POST = (request: Request) => proxyLinkPost(request, "link-batches");
