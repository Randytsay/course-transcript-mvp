"use client";

import { useCallback, useEffect, useState } from "react";
import styles from "../ai-accounts/ai-accounts.module.css";

type ProviderProfile = {
  id: string;
  name?: string;
  provider?: string;
  default_model?: string;
  key_configured?: boolean;
  validation_status?: string;
  last_validated_at?: string | null;
};

type ListResponse = {
  profiles: ProviderProfile[];
  supported_providers: string[];
  capabilities: Record<string, { realtime: boolean; batch: boolean; batch_note?: string }>;
  security_note: string;
};

const PROVIDER_LABELS: Record<string, string> = {
  openrouter: "OpenRouter",
  minimax: "MiniMax",
};

export default function AIProvidersPage() {
  const [data, setData] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [profileId, setProfileId] = useState("");
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("openrouter");
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-providers",
                            { cache: "no-store", credentials: "same-origin" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `讀取失敗 (${r.status})`);
      setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "無法讀取供應商設定");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function create() {
    if (!profileId.trim() || !name.trim() || !apiKey || !defaultModel.trim())
      return setError("請填齊 ID、名稱、API Key 與預設模型。");
    setBusy("create"); setMessage(null); setError(null);
    try {
      const r = await fetch("/api/v1/review-admin/ai-providers", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: profileId.trim(), name: name.trim(),
                               provider, api_key: apiKey,
                               default_model: defaultModel.trim() }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `新增失敗 (${r.status})`);
      setMessage(`已登記「${body.name}」。請用「測試連線」驗證。`);
      setApiKey(""); setShowForm(false); setDefaultModel("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "新增失敗");
    } finally { setBusy(null); }
  }

  async function testConnection(id: string) {
    setBusy(`test:${id}`); setMessage(null); setError(null);
    try {
      const r = await fetch(`/api/v1/review-admin/ai-providers/${id}/test`,
                            { method: "POST", credentials: "same-origin" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `測試失敗 (${r.status})`);
      setMessage(`「${id}」連線測試通過（唯讀驗證，未產生費用）。`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "測試失敗");
    } finally { setBusy(null); }
  }

  async function removeProfile(id: string) {
    if (!window.confirm(`刪除供應商設定「${id}」？`)) return;
    setBusy(`delete:${id}`); setMessage(null); setError(null);
    try {
      const r = await fetch(`/api/v1/review-admin/ai-providers/${id}/delete`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || `刪除失敗 (${r.status})`);
      setMessage(`已刪除「${id}」。`);
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
            <h1>AI 模型供應商</h1>
            <p>
              管理 OpenRouter／MiniMax API Key 設定檔（與「AI 帳戶管理」的
              Google Cloud 帳戶分開）。Key 只存伺服器保護目錄，不會顯示。
            </p>
          </div>
          <button className={styles.addButton}
                  onClick={() => setShowForm((v) => !v)} type="button">
            {showForm ? "收起表單" : "+ 新增供應商"}
          </button>
        </header>

        {loading ? <div className={styles.stateCard}>正在讀取…</div> : null}
        {message ? <div className={styles.successCard} role="status">{message}</div> : null}
        {error ? <div className={styles.errorCard} role="alert">{error}</div> : null}

        {!loading && data ? (
          <>
            {showForm ? (
              <section className={styles.formCard}>
                <label><span>設定檔 ID（小寫英文／數字／連字號）</span>
                  <input value={profileId} onChange={(e) => setProfileId(e.target.value)}
                         placeholder="openrouter-main" /></label>
                <label><span>顯示名稱</span>
                  <input value={name} onChange={(e) => setName(e.target.value)}
                         placeholder="OpenRouter Main" /></label>
                <label><span>供應商</span>
                  <select value={provider}
                          onChange={(e) => { setProvider(e.target.value);
                                             setDefaultModel(
                                               e.target.value === "minimax"
                                                 ? "MiniMax-M3"
                                                 : "google/gemini-3.7-flash"); }}>
                    {data.supported_providers.map((pv) => (
                      <option key={pv} value={pv}>{PROVIDER_LABELS[pv] ?? pv}</option>
                    ))}
                  </select>
                </label>
                <label><span>API Key（只會送達伺服器，不會儲存在瀏覽器）</span>
                  <input type="password" autoComplete="off"
                         value={apiKey} onChange={(e) => setApiKey(e.target.value)} /></label>
                <label><span>預設模型</span>
                  <input value={defaultModel} onChange={(e) => setDefaultModel(e.target.value)}
                         placeholder={provider === "minimax" ? "MiniMax-M3"
                                      : "google/gemini-3.7-flash"} /></label>
                <button disabled={busy !== null} onClick={() => void create()} type="button">
                  {busy === "create" ? "儲存中…" : "儲存"}
                </button>
                <p className={styles.hint}>{data.security_note}</p>
              </section>
            ) : null}

            {data.profiles.length ? (
              <ul className={styles.accountList}>
                {data.profiles.map((item) => {
                  const caps = data.capabilities[item.provider ?? ""] ?? {};
                  return (
                    <li key={item.id} className={styles.accountCard}>
                      <div className={styles.accountMain}>
                        <strong>{item.name || item.id}</strong>
                        <small>
                          {PROVIDER_LABELS[item.provider ?? ""] ?? item.provider}
                          {" "}・ 模型 {item.default_model ?? "—"}
                        </small>
                        <span>
                          API Key：{item.key_configured ? "已設定" : "未設定"} ・
                          連線：{item.validation_status ?? "UNKNOWN"}
                          {" "}・ 即時：{caps.realtime === false ? "NO" : "YES"} ・
                          Batch：{caps.batch ? "YES" : "NO"}
                        </span>
                        {caps.batch === false && caps.batch_note ? (
                          <span className={styles.creditStatusLine}>{caps.batch_note}</span>
                        ) : null}
                      </div>
                      <div className={styles.accountActions}>
                        <button className={styles.switchButton} disabled={busy !== null}
                                onClick={() => void testConnection(item.id)} type="button">
                          {busy === `test:${item.id}` ? "測試中…" : "測試連線"}
                        </button>
                        <button className={styles.deleteButton} disabled={busy !== null}
                                onClick={() => void removeProfile(item.id)} type="button">刪除</button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : !showForm ? (
              <div className={styles.emptyState}>
                尚未登記任何供應商。點右上「+ 新增供應商」。
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </main>
  );
}
