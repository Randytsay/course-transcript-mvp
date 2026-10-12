import { headers } from "next/headers";
import { notFound } from "next/navigation";

import AppShell from "@/components/app-shell";
import GoldenV2AdminPage from "@/components/golden-v2-admin-page";
import {
  isGoldenV2AdminEnabled,
  isGoldenV2AdminRequestAllowed,
} from "@/lib/golden-v2-admin.server";

export const dynamic = "force-dynamic";

export default async function Page() {
  const requestHeaders = await headers();
  if (!isGoldenV2AdminEnabled() || !isGoldenV2AdminRequestAllowed(requestHeaders)) {
    notFound();
  }
  return (
    <AppShell title="Golden V2" description="來源版本、辨識階段與核准記錄">
      <GoldenV2AdminPage />
    </AppShell>
  );
}
