"use client";

import { usePathname } from "next/navigation";
import styles from "./review.module.css";
import ReviewAdminLink from "./review-admin-link";

export default function ReviewPortalDock() {
  const pathname = usePathname();

  // Focused lesson playback has its own compact help/menu affordance. Keeping
  // the global dock off that route prevents accidental taps over subtitles.
  const fullPageRoutes = new Set([
    "/review/learn/notes",
    "/review/learn/review",
    "/review/learn/search",
  ]);
  if (pathname.startsWith("/review/learn/") && !fullPageRoutes.has(pathname)) return null;

  return (
    <nav className={styles.portalDock} aria-label="平台入口">
      <a className={styles.portalLink} href="/review/help" aria-label="開啟佛學共學平台使用說明">
        <span aria-hidden="true">?</span>
        <span>使用說明</span>
      </a>
      <ReviewAdminLink />
    </nav>
  );
}
