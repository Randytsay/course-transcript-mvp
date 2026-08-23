import type { ReactNode } from "react";
import ReviewPortalDock from "./review-portal-dock";

export default function ReviewLayout({ children }: { children: ReactNode }) {
  return (
    <>
      {children}
      <ReviewPortalDock />
    </>
  );
}
