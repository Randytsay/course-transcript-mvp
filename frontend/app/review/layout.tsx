import type { ReactNode } from "react";
import styles from "./review.module.css";
import ReviewAdminLink from "./review-admin-link";

export default function ReviewLayout({ children }: { children: ReactNode }) {
  return (
    <>
      {children}
      <nav className={styles.portalDock} aria-label="平台入口">
        <a className={styles.portalLink} href="/review/help" aria-label="開啟佛學共學平台使用說明">
          <span aria-hidden="true">?</span>
          <span>使用說明</span>
        </a>
        <ReviewAdminLink />
      </nav>
    </>
  );
}
