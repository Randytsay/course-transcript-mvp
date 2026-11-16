import { headers } from "next/headers";
import { notFound } from "next/navigation";
import AppShell from "@/components/app-shell";
import GoldenV2PaidConsole from "@/components/golden-v2-paid-console";
import { isVerifiedGoldenV2PaidOperator } from "@/lib/golden-v2-paid-console-auth.server";

export const dynamic = "force-dynamic";

export default async function Page() {
  if (!await isVerifiedGoldenV2PaidOperator(await headers())) notFound();
  return (
    <AppShell title="Golden V2｜付費核准管理" description="僅允許 ≥1 小時的 MP3；US$100 累計上限">
      <GoldenV2PaidConsole />
    </AppShell>
  );
}
