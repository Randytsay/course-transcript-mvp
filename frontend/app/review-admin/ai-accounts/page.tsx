"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "./ai-accounts.module.css";

type AIAccount = {
  name: string;
  client_email?: string;
  project_id?: string;
  uploaded_at?: string;
  size_bytes?: number;
  is_active: boolean;
  invalid?: boolean;
};

type AIAccountsResponse = {
  accounts: AIAccount[];
  active: string | null;
  verify: { ok: boolean; client_email?: string; error?: string };
  restart_required_hint: string;
};

function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat("zh-TW", {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export default function AIAccountsPage() {
  const [data, setData] = useState<AIAccountsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [saText, setSaText] = useState("");
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts", {
        cache: "no-store",
        credentials: "same-origin",
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `讀取失敗 (${r.status})`);
      setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "無法讀取 AI 帳戶資料");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleFile(file: File | null) {
    if (!file) return;
    setSaText(await file.text());
    if (!name) setName(file.name.replace(/\.json$/i, ""));
  }

  async function addAccount() {
    if (!name.trim()) return setError("請輸入帳戶名稱。");
    let parsed: unknown;
    try {
      parsed = JSON.parse(saText);
    } catch {
      return setError("服務帳戶 JSON 格式錯誤，請確認貼上完整的 key 檔內容。");
    }
    setBusy("add");
    setMessage(null);
    setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), sa_json: parsed }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `新增失敗 (${r.status})`);
      setMessage(`已新增帳戶「${body.name}」（${body.client_email}）。`);
      setSaText("");
      setShowForm(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失敗");
    } finally {
      setBusy(null);
    }
  }

  async function switchTo(item: AIAccount) {
    if (
      !window.confirm(
        `切換使用中的 Vertex 帳戶？\n\n${item.name}\n${item.client_email ?? ""}\n\n切換後需要重啟 api 與 pipeline-worker 容器才會生效，之後的 AI 工作會用新帳戶的額度。`,
      )
    )
      return;
    setBusy(`switch:${item.name}`);
    setMessage(null);
    setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts/switch", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: item.name, confirm: true }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `切換失敗 (${r.status})`);
      setMessage(
        `已切換到「${body.name}」。請重啟 api / pipeline-worker 容器載入新憑證（可用下方按鈕或聯絡系統管理者）。`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "切換失敗");
    } finally {
      setBusy(null);
    }
  }

  async function removeAccount(item: AIAccount) {
    if (!window.confirm(`刪除帳戶「${item.name}」？此動作不會影響目前掛載中的憑證檔。`)) return;
    setBusy(`delete:${item.name}`);
    setMessage(null);
    setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts/delete", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: item.name, confirm: true }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `刪除失敗 (${r.status})`);
      setMessage(`已刪除「${item.name}」。`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "刪除失敗");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>AI 學習內容</p>
            <h1>AI 帳戶管理</h1>
            <p>
              管理影片處理使用的 Google Vertex AI 服務帳戶。可以預先登記多組帳戶，
              某個帳戶額度用完時一鍵切換；切換後重啟容器即生效。
            </p>
          </div>
          <button
            className={styles.addButton}
            onClick={() => setShowForm((v) => !v)}
            type="button"
          >
            {showForm ? "收起表單" : "+ 新增帳戶"}
          </button>
        </header>

        {loading ? <div className={styles.stateCard}>正在讀取…</div> : null}
        {message ? <div className={styles.successCard} role="status">{message}</div> : null}
        {error ? <div className={styles.errorCard} role="alert">{error}</div> : null}

        {!loading && data ? (
          <>
            <section className={styles.statusRow}>
              <div>
                <span>目前使用中</span>
                <strong>{data.active ?? "（未設定）"}</strong>
              </div>
              <div>
                <span>掛載憑證狀態</span>
                <strong className={data.verify.ok ? styles.okText : styles.badText}>
                  {data.verify.ok ? "✓ 正常" : "⚠ 讀取失敗"}
                </strong>
              </div>
              <div>
                <span>服務帳戶</span>
                <small>{data.verify.client_email ?? data.verify.error ?? "—"}</small>
              </div>
            </section>

            {showForm ? (
              <section className={styles.formCard}>
                <label>
                  <span>帳戶名稱（英文／數字，例如 vertex-account-b）</span>
                  <input value={name} onChange={(e) => setName(e.target.value)} />
                </label>
                <label>
                  <span>服務帳戶金鑰（JSON 檔）</span>
                  <input
                    accept=".json,application/json"
                    onChange={(e) => void handleFile(e.target.files?.[0] ?? null)}
                    type="file"
                  />
                </label>
                <textarea
                  onChange={(e) => setSaText(e.target.value)}
                  placeholder="或直接貼上 JSON 內容"
                  rows={6}
                  value={saText}
                />
                <button disabled={busy !== null || !name.trim() || !saText.trim()} onClick={() => void addAccount()} type="button">
                  {busy === "add" ? "儲存中…" : "儲存帳戶"}
                </button>
                <p className={styles.hint}>
                  金鑰只會儲存在伺服器受保護目錄（權限 600），頁面與 API 都不會再顯示私鑰內容。
                </p>
              </section>
            ) : null}

            {data.accounts.length ? (
              <ul className={styles.accountList}>
                {data.accounts.map((item) => (
                  <li
                    className={`${styles.accountCard} ${item.is_active ? styles.activeCard : ""}`}
                    key={item.name}
                  >
                    <div className={styles.accountMain}>
                      <strong>
                        {item.name}
                        {item.is_active ? <b className={styles.activeBadge}>使用中</b> : null}
                        {item.invalid ? <b className={styles.badText}>格式錯誤</b> : null}
                      </strong>
                      <small>{item.client_email ?? "—"}</small>
                      <span>
                        專案 {item.project_id ?? "—"} ・ 上傳 {dateTime(item.uploaded_at)}
                      </span>
                    </div>
                    <div className={styles.accountActions}>
                      {!item.is_active && !item.invalid ? (
                        <button
                          className={styles.switchButton}
                          disabled={busy !== null}
                          onClick={() => void switchTo(item)}
                          type="button"
                        >
                          {busy === `switch:${item.name}` ? "切換中…" : "切換為使用中"}
                        </button>
                      ) : null}
                      {!item.is_active ? (
                        <button
                          className={styles.deleteButton}
                          disabled={busy !== null}
                          onClick={() => void removeAccount(item)}
                          type="button"
                        >
                          刪除
                        </button>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            ) : !showForm ? (
              <div className={styles.emptyState}>
                尚未登記任何帳戶。點右上「+ 新增帳戶」上傳第一組服務帳戶金鑰。
              </div>
            ) : null}

            <footer className={styles.footerNote}>{data.restart_required_hint}</footer>
          </>
        ) : null}
      </div>
    </main>
  );
}
