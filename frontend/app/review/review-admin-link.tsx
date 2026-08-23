"use client";

import { useEffect, useState } from "react";
import styles from "./review.module.css";

const REVIEW_HOSTNAME = "review.randy88.ccwu.cc";
const ADMIN_ORIGIN = "https://transcript.randy88.ccwu.cc";

function openAdminPortal(event: React.MouseEvent<HTMLAnchorElement>) {
  if (window.location.hostname !== REVIEW_HOSTNAME) return;
  event.currentTarget.href = `${ADMIN_ORIGIN}/review-admin`;
}

export default function ReviewAdminLink({ className = "" }: { className?: string }) {
  const [isOwner, setIsOwner] = useState(false);

  useEffect(() => {
    let active = true;
    void fetch("/api/v1/review/auth/me", { cache: "no-store", credentials: "same-origin" })
      .then((response) => response.ok ? response.json() : null)
      .then((payload: { user?: { role?: string } } | null) => {
        if (active) setIsOwner(payload?.user?.role === "owner");
      })
      .catch(() => {
        if (active) setIsOwner(false);
      });
    return () => { active = false; };
  }, []);

  if (!isOwner) return null;

  return (
    <a
      className={`${styles.portalLink} ${styles.adminPortalLink} ${className}`}
      href="/review-admin"
      onClick={openAdminPortal}
    >
      管理員入口 ↗
    </a>
  );
}
