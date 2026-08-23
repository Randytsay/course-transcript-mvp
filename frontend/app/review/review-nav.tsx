"use client";

import { useEffect, useState } from "react";
import styles from "./review-nav.module.css";

const REVIEW_HOSTNAME = "review.randy88.ccwu.cc";
const ADMIN_ORIGIN = "https://transcript.randy88.ccwu.cc";
const LEARN_ORIGIN = "https://review.randy88.ccwu.cc";

export type ReviewNavActive =
  | "learn"
  | "videos"
  | "review"
  | "search"
  | "contributions"
  | "help"
  | "account";

export type ReviewNavMode = "full" | "minimal";

const NAV_LINKS: Array<{ key: ReviewNavActive; href: string; label: string }> = [
  { key: "learn", href: "/review/learn", label: "學習中心" },
  { key: "videos", href: "/review/videos", label: "字幕共修" },
  { key: "review", href: "/review/learn/review", label: "複習中心" },
  { key: "search", href: "/review/learn/search", label: "知識搜尋" },
  { key: "contributions", href: "/review/contributions", label: "共修紀錄" },
  { key: "help", href: "/review/help", label: "使用說明" },
];

function openAdminPortal(event: React.MouseEvent<HTMLAnchorElement>) {
  if (window.location.hostname !== REVIEW_HOSTNAME) return;
  event.currentTarget.href = `${ADMIN_ORIGIN}/review-admin`;
}

function openLearnPlatform(event: React.MouseEvent<HTMLAnchorElement>) {
  if (window.location.hostname === REVIEW_HOSTNAME) return;
  event.currentTarget.href = `${LEARN_ORIGIN}/review/learn`;
}

/**
 * Shared reviewer top navigation.
 *
 * - `full`: the six primary reviewer sections plus utility actions.
 * - `minimal`: login/entry pages — keeps 使用說明 without the full six links,
 *   so unauthenticated users are not pushed into redirect loops.
 *
 * 管理員入口 renders only when `/api/v1/review/auth/me` reports role "owner".
 */
export default function ReviewNav({
  active,
  mode = "full",
}: {
  active: ReviewNavActive;
  mode?: ReviewNavMode;
}) {
  const [role, setRole] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/api/v1/review/auth/me", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const body = await response.json();
        if (!cancelled && body?.user?.role) setRole(String(body.user.role));
      } catch {
        // Unauthenticated or offline: keep nav functional without owner actions.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const isOwner = role === "owner";
  const links = mode === "full" ? NAV_LINKS : NAV_LINKS.filter((link) => link.key === "help");

  return (
    <nav className={styles.nav} aria-label="共學平台導覽">
      <div className={styles.primary}>
        {links.map((link) => (
          <a
            aria-current={active === link.key ? "page" : undefined}
            className={active === link.key ? styles.active : undefined}
            href={link.href}
            key={link.key}
          >
            {link.label}
          </a>
        ))}
      </div>
      <div className={styles.utility}>
        <a className={styles.utilityLink} href="/review">帳號</a>
        {isOwner ? (
          <a
            className={`${styles.utilityLink} ${styles.adminLink}`}
            href="/review-admin"
            onClick={openAdminPortal}
          >
            管理員入口 ↗
          </a>
        ) : null}
      </div>
    </nav>
  );
}

/** Top banner used by the admin host to return to the reviewer platform. */
export function BackToPlatformLink() {
  return (
    <a className={styles.utilityLink} href="/review/learn" onClick={openLearnPlatform}>
      回到學習平台 ↗
    </a>
  );
}
