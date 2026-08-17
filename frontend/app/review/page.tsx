"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "./review.module.css";

type ProviderName = "google" | "line";

type ProviderStatus = {
  configured: boolean;
};

type ProvidersResponse = {
  providers: Record<ProviderName, ProviderStatus>;
};

type MeResponse = {
  user: {
    id: string;
    display_name: string;
    avatar_url: string | null;
    role: "owner" | "reviewer";
  };
  identities: Array<{
    provider: ProviderName;
    email: string | null;
    created_at: string;
    last_login_at: string;
  }>;
  csrf_token: string;
  session_expires_at: string;
};

const providerLabels: Record<ProviderName, string> = {
  google: "Google",
  line: "LINE",
};

export default function ReviewPortalPage() {
  const [providers, setProviders] = useState<ProvidersResponse["providers"] | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<ProviderName | "logout" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [providerResponse, meResponse] = await Promise.all([
        fetch("/api/v1/review/auth/providers", { cache: "no-store" }),
        fetch("/api/v1/review/auth/me", { cache: "no-store", credentials: "same-origin" }),
      ]);
      if (providerResponse.ok) {
        setProviders((await providerResponse.json()).providers);
      }
      if (meResponse.ok) {
        setMe(await meResponse.json());
      } else if (meResponse.status === 401) {
        setMe(null);
      }
    } catch {
      setMessage("目前無法連線到登入服務，請稍後重新整理。 ");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function startAuth(provider: ProviderName, action: "login" | "link") {
    setBusy(provider);
    setMessage(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (action === "link" && me?.csrf_token) {
        headers["X-Review-CSRF"] = me.csrf_token;
      }
      const response = await fetch(`/api/v1/review/auth/${provider}/start`, {
        method: "POST",
        credentials: "same-origin",
        headers,
        body: JSON.stringify({ action, return_to: "/review" }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || typeof body.authorization_url !== "string") {
        throw new Error(body.detail || "無法啟動登入流程");
      }
      window.location.assign(body.authorization_url);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "無法啟動登入流程");
      setBusy(null);
    }
  }

  async function logout() {
    if (!me?.csrf_token) return;
    setBusy("logout");
    setMessage(null);
    try {
      const response = await fetch("/api/v1/review/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-Review-CSRF": me.csrf_token },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "登出失敗");
      }
      setMe(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "登出失敗");
    } finally {
      setBusy(null);
    }
  }

  const linkedProviders = new Set(me?.identities.map((identity) => identity.provider) ?? []);

  return (
    <main className={styles.page}>
      <section className={styles.shell} aria-labelledby="review-title">
        <div className={styles.brandMark} aria-hidden="true">校</div>
        <p className={styles.eyebrow}>佛學字幕共修</p>
        <h1 id="review-title">一起把每一句法語校得更準確</h1>
        <p className={styles.lead}>
          登入後即可保存自己的觀看與校訂進度，手機、平板、電腦都能接著上次的位置繼續。
        </p>

        {loading ? (
          <div className={styles.statusCard}>正在確認登入狀態…</div>
        ) : me ? (
          <div className={styles.accountCard}>
            <div className={styles.identityRow}>
              {me.user.avatar_url ? (
                // Provider avatar URLs are supplied by verified Google/LINE identity claims.
                // eslint-disable-next-line @next/next/no-img-element
                <img src={me.user.avatar_url} alt="" className={styles.avatar} />
              ) : (
                <div className={styles.avatarFallback} aria-hidden="true">
                  {me.user.display_name.slice(0, 1)}
                </div>
              )}
              <div>
                <p className={styles.signedIn}>已登入</p>
                <h2>{me.user.display_name}</h2>
              </div>
            </div>

            <a className={styles.enterReviewButton} href="/review/videos">
              <span>進入影片校訂</span>
              <strong>選影片、同步觀看字幕、接續上次進度 →</strong>
            </a>

            <div className={styles.linkedSection}>
              <p className={styles.sectionLabel}>已綁定登入方式</p>
              <div className={styles.providerChips}>
                {me.identities.map((identity) => (
                  <span className={styles.providerChip} key={identity.provider}>
                    <span className={styles.check}>✓</span>
                    {providerLabels[identity.provider]}
                    {identity.email ? <small>{identity.email}</small> : null}
                  </span>
                ))}
              </div>
            </div>

            <div className={styles.actions}>
              {(["google", "line"] as ProviderName[]).map((provider) => {
                const configured = providers?.[provider]?.configured ?? false;
                const linked = linkedProviders.has(provider);
                if (linked) return null;
                return (
                  <button
                    className={styles.secondaryButton}
                    disabled={!configured || busy !== null}
                    key={provider}
                    onClick={() => void startAuth(provider, "link")}
                    type="button"
                  >
                    {busy === provider ? "連結中…" : `綁定 ${providerLabels[provider]}`}
                  </button>
                );
              })}
              <button
                className={styles.textButton}
                disabled={busy !== null}
                onClick={() => void logout()}
                type="button"
              >
                {busy === "logout" ? "登出中…" : "登出"}
              </button>
            </div>
          </div>
        ) : (
          <div className={styles.loginCard}>
            <p className={styles.sectionLabel}>選擇一種方式登入</p>
            <div className={styles.loginButtons}>
              <button
                className={styles.googleButton}
                disabled={!providers?.google?.configured || busy !== null}
                onClick={() => void startAuth("google", "login")}
                type="button"
              >
                <span className={styles.providerInitial}>G</span>
                {busy === "google" ? "前往 Google…" : "使用 Google 登入"}
              </button>
              <button
                className={styles.lineButton}
                disabled={!providers?.line?.configured || busy !== null}
                onClick={() => void startAuth("line", "login")}
                type="button"
              >
                <span className={styles.providerInitial}>L</span>
                {busy === "line" ? "前往 LINE…" : "使用 LINE 登入"}
              </button>
            </div>
            <p className={styles.privacyNote}>
              系統只使用登入身分建立校訂帳號，不會把 Google 或 LINE 的存取權杖存進瀏覽器。
            </p>
          </div>
        )}

        {message ? <p className={styles.errorMessage} role="alert">{message}</p> : null}

        <footer className={styles.footer}>
          <span>固定時間碼</span>
          <span>跨裝置進度</span>
          <span>校訂功德紀錄</span>
        </footer>
      </section>
    </main>
  );
}
