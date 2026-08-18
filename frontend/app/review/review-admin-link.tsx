"use client";

import styles from "./review.module.css";

const REVIEW_HOSTNAME = "review.randy88.ccwu.cc";
const ADMIN_ORIGIN = "https://transcript.randy88.ccwu.cc";

function openAdminPortal(event: React.MouseEvent<HTMLAnchorElement>) {
  if (window.location.hostname !== REVIEW_HOSTNAME) return;
  event.currentTarget.href = `${ADMIN_ORIGIN}/review-admin`;
}

export default function ReviewAdminLink() {
  return (
    <a
      className={`${styles.portalLink} ${styles.adminPortalLink}`}
      href="/review-admin"
      onClick={openAdminPortal}
    >
      管理員入口 ↗
    </a>
  );
}
