"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "./ai-accounts.module.css";

type Profile = {
  name: string;
  client_email?: string;
  project_id?: string;
  location?: string;
  gcs_bucket?: string;
  uploaded_at?: string;
  is_active: boolean;
  credential_valid?: boolean;
  invalid?: boolean;
};

type ListResponse = {
  profiles: Profile[];
  active: string | null;
  previous: string | null;
  runtime: {
    credential_ok?: boolean;
    env_matches?: boolean;
    mounted_client_email?: string;
    mounted_project_id?: string;
    credential_error?: string;
  };
  restart_required_hint: string;
  billing_note: string;
};

type PreflightResponse = {
  ok: boolean;
  checks: Record<string, unknown>;
  candidate: Profile;
  current: string | null;
  same_project_warning: boolean;
};

function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat("zh-TW", {
      year: "numeric", month: "numeric", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export default function AIAccountsPage() {
  const [data, setData] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [saText, setSaText] = useState("");
  const [location, setLocation] = useState("global");
  const [bucket, setBucket] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [showGuide, setShowGuide] = useState(false);

  // preflight confirmation state
  const [preflight, setPreflight] = useState<PreflightResponse | null>(null);
  const [pendingSwitch, setPendingSwitch] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts", {
        cache: "no-store", credentials: "same-origin",
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

  useEffect(() => { void load(); }, [load]);

  async function handleFile(file: File | null) {
    if (!file) return;
    setSaText(await file.text());
    if (!name) setName(file.name.replace(/\.json$/i, ""));
  }

  async function addAccount() {
    if (!name.trim()) return setError("請輸入帳戶名稱。");
    let parsed: unknown;
    try { parsed = JSON.parse(saText); }
    catch { return setError("服務帳戶 JSON 格式錯誤，請確認貼上完整的 key 檔內容。"); }
    setBusy("add"); setMessage(null); setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), sa_json: parsed,
                               location, gcs_bucket: bucket }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `新增失敗 (${r.status})`);
      setMessage(`已登記帳戶「${body.name}」（Project：${body.project_id}）。`);
      setSaText(""); setShowForm(false); setBucket("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失敗");
    } finally { setBusy(null); }
  }

  async function startPreflight(item: Profile) {
    setBusy(`switch:${item.name}`); setMessage(null); setError(null); setPreflight(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts/preflight", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: item.name }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `Preflight 失敗 (${r.status})`);
      setPreflight(body as PreflightResponse);
      setPendingSwitch(body.ok ? item.name : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preflight 失敗");
    } finally { setBusy(null); }
  }

  async function confirmSwitch(nameToSwitch: string) {
    if (pendingSwitch !== nameToSwitch) return;
    setBusy(`confirm:${nameToSwitch}`); setMessage(null); setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts/switch", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nameToSwitch, confirm: true }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `切換失敗 (${r.status})`);
      setMessage(
        `已切換到「${body.name}」（Project ${body.project_id} / ${body.location}）。` +
        `請重啟 api 與 pipeline-worker 容器載入新憑證。`,
      );
      setPreflight(null); setPendingSwitch(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "切換失敗");
    } finally { setBusy(null); }
  }

  async function rollback() {
    if (!window.confirm("回滾到上一個帳戶？將還原憑證、Project、Location 與 Bucket。")) return;
    setBusy("rollback"); setMessage(null); setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts/rollback", {
        method: "POST", credentials: "same-origin",
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `回滾失敗 (${r.status})`);
      setMessage(`已回滾到「${body.name}」。請重啟 api 與 pipeline-worker 容器。`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "回滾失敗");
    } finally { setBusy(null); }
  }

  async function removeAccount(item: Profile) {
    if (!window.confirm(`刪除帳戶「${item.name}」？不影響目前掛載中的憑證檔。`)) return;
    setBusy(`delete:${item.name}`); setMessage(null); setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-accounts/delete", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: item.name, confirm: true }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `刪除失敗 (${r.status})`);
      setMessage(`已刪除「${item.name}」。`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "刪除失敗");
    } finally { setBusy(null); }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>AI 學習內容</p>
            <h1>AI 帳戶管理</h1>
            <p>
              管理 Vertex AI 帳號設定檔（Service Account + GCP Project + Region + Bucket）。
              切換時四項資訊會一併更新；額度主要跟 Project 綁定。
              第一次操作請先看{" "}
              <button className={styles.linkButton}
                      onClick={() => setShowGuide((v) => !v)} type="button">
                {showGuide ? "收起操作說明 ▲" : "操作說明 ▼"}
              </button>
            </p>
          </div>
          <div className={styles.headerActions}>
            {data?.previous && data.active ? (
              <button className={styles.rollbackButton}
                      disabled={busy !== null} onClick={() => void rollback()} type="button">
                {busy === "rollback" ? "回滾中…" : `↩ 回滾到 ${data.previous}`}
              </button>
            ) : null}
            <button className={styles.addButton}
                    onClick={() => setShowForm((v) => !v)} type="button">
              {showForm ? "收起表單" : "+ 新增帳戶"}
            </button>
          </div>
        </header>

        {showGuide ? (
          <section aria-label="操作說明" className={styles.guideCard}>
            <h2>新增與切換（一步步來）</h2>
            <ol className={styles.guideSteps}>
              <li><strong>取得新的服務帳戶金鑰</strong>
                <span>Google Cloud Console → IAM → 服務帳戶 → 金鑰 → 新增 JSON 金鑰。</span></li>
              <li><strong>確認專案已啟用 Vertex AI</strong>
                <span>同一個 GCP 專案啟用 Vertex AI API，SA 需有「Vertex AI 使用者」權限。</span></li>
              <li><strong>點右上「+ 新增帳戶」</strong>
                <span>取名、上傳 JSON，並填寫 Region（預設 global）與 Pipeline 用的 GCS Bucket。</span></li>
              <li><strong>需要切換時先跑 Preflight</strong>
                <span>點「切換為使用中」會先做唯讀唯讀驗證（憑證有效、可存取 Project／Bucket），不通過就不會切換。</span></li>
              <li><strong>確認差異後生效</strong>
                <span>Preflight 通過後會顯示目前與目標的 Project 差異，明確確認才會寫入。</span></li>
              <li><strong>重啟容器完成切換</strong>
                <span>重啟 api 與 pipeline-worker 後，新 Project／Region／Bucket 即全面生效。出問題可用「回滾」一步還原。</span></li>
            </ol>
            <p className={styles.hint}>
              💡 安全提醒：私鑰只存伺服器受保護目錄，頁面與 API 都不會再顯示；
              所有操作記錄在稽核日誌。建議定期在 Google Cloud 輪替金鑰。
            </p>
          </section>
        ) : null}

        {loading ? <div className={styles.stateCard}>正在讀取…</div> : null}
        {message ? <div className={styles.successCard} role="status">{message}</div> : null}
        {error ? <div className={styles.errorCard} role="alert">{error}</div> : null}

        {!loading && data ? (
          <>
            <section className={styles.statusRow}>
              <div><span>目前使用中</span><strong>{data.active ?? "（未設定）"}</strong></div>
              <div><span>掛載憑證狀態</span>
                <strong className={data.runtime.credential_ok ? styles.okText : styles.badText}>
                  {data.runtime.credential_ok ? "✓ 正常" : "⚠ 讀取失敗"}
                </strong></div>
              <div><span>運行中 Project</span>
                <small>{data.runtime.mounted_project_id ?? data.runtime.credential_error ?? "—"}</small></div>
            </section>

            <div className={`${styles.billingNote}`}>
              ℹ️ {data.billing_note}
            </div>

            {showForm ? (
              <section className={styles.formCard}>
                <label><span>帳戶名稱（英文／數字）</span>
                  <input value={name} onChange={(e) => setName(e.target.value)} /></label>
                <label><span>服務帳戶金鑰（JSON 檔）</span>
                  <input accept=".json,application/json" type="file"
                         onChange={(e) => void handleFile(e.target.files?.[0] ?? null)} /></label>
                <textarea rows={6} value={saText}
                          onChange={(e) => setSaText(e.target.value)}
                          placeholder="或直接貼上 JSON 內容" />
                <label><span>GCP Region（例如 global、us-central1、asia-east1）</span>
                  <input value={location} onChange={(e) => setLocation(e.target.value)} /></label>
                <label><span>Pipeline 用 GCS Bucket（選填，跨專案時建議填）</span>
                  <input value={bucket} onChange={(e) => setBucket(e.target.value)}
                         placeholder="course-transcript-mvp" /></label>
                <button disabled={busy !== null || !name.trim() || !saText.trim()}
                        onClick={() => void addAccount()} type="button">
                  {busy === "add" ? "儲存中…" : "儲存帳戶"}
                </button>
                <p className={styles.hint}>
                  Project ID 一律以 JSON 內容為準（頁面無法覆寫）；金鑰存保護目錄權限 600，不再顯示。
                </p>
              </section>
            ) : null}

            {/* preflight confirmation panel */}
            {preflight ? (
              <section className={`${styles.formCard} ${preflight.ok ? "" : styles.preflightFail}`}>
                <h3 style={{margin:"0 0 8px"}}>
                  {preflight.ok ? "✓ Preflight 通過 — 確認切換" : "✗ Preflight 未通過"}
                </h3>
                <div className={styles.switchDiff}>
                  <div><span>從</span><strong>{preflight.current ?? "（未設定）"}</strong></div>
                  <div><span>切換到</span><strong>{preflight.candidate.name}</strong></div>
                  <div><span>目標 Project</span><strong>{preflight.candidate.project_id ?? "—"}</strong></div>
                  <div><span>目標 Region</span><strong>{preflight.candidate.location ?? "global"}</strong></div>
                  {preflight.candidate.gcs_bucket ? (
                    <div><span>目標 Bucket</span><strong>{preflight.candidate.gcs_bucket}</strong></div>
                  ) : null}
                </div>
                {preflight.same_project_warning ? (
                  <div className={styles.warnBox}>
                    ⚠️ 這兩個帳號使用相同 GCP Project，切換通常不會改變 Vertex project 額度。
                  </div>
                ) : null}
                {!preflight.ok ? (
                  <div className={styles.errorCard}>{JSON.stringify(preflight.checks)}</div>
                ) : null}
                <div className={styles.accountActions}>
                  <button disabled={!preflight.ok || busy !== null}
                          onClick={() => pendingSwitch && void confirmSwitch(pendingSwitch)}
                          type="button" className={styles.switchButton}>
                    {busy ? "處理中…" : "確認切換"}
                  </button>
                  <button disabled={busy !== null}
                          onClick={() => { setPreflight(null); setPendingSwitch(null); }}
                          type="button" className={styles.deleteButton}>取消</button>
                </div>
              </section>
            ) : null}

            {data.profiles.length ? (
              <ul className={styles.accountList}>
                {data.profiles.map((item) => (
                  <li key={item.name}
                      className={`${styles.accountCard} ${item.is_active ? styles.activeCard : ""}`}>
                    <div className={styles.accountMain}>
                      <strong>{item.name}
                        {item.is_active ? <b className={styles.activeBadge}>使用中</b> : null}
                        {item.credential_valid === false ? <b className={styles.badText}>格式錯誤</b> : null}
                      </strong>
                      <small>{item.client_email ?? "—"}</small>
                      <span>
                        Project {item.project_id ?? "—"} ・ Region {item.location ?? "global"}
                        {item.gcs_bucket ? ` ・ Bucket ${item.gcs_bucket}` : ""}
                        {" "}・ 上傳 {dateTime(item.uploaded_at)}
                      </span>
                    </div>
                    <div className={styles.accountActions}>
                      {!item.is_active && item.credential_valid !== false ? (
                        <button className={styles.switchButton} disabled={busy !== null}
                                onClick={() => void startPreflight(item)} type="button">
                          {busy === `switch:${item.name}` ? "Preflight 中…" : "切換為使用中"}
                        </button>
                      ) : null}
                      {!item.is_active ? (
                        <button className={styles.deleteButton} disabled={busy !== null}
                                onClick={() => void removeAccount(item)} type="button">刪除</button>
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
