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
      {process.env.COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED === "true" && (
        <p style={{ margin: "12px 0", fontWeight: 700 }}>
          <a href="/golden-v2/paid">進入付費核准管理台（≥1 小時／US$100 上限）</a>
        </p>
      )}
      <GoldenV2AdminPage />
    </AppShell>
  );
}
