"use client";

import { useCallback, useEffect, useState } from "react";

type CanonicalDocument = {
  document_key: "dacheng_scripture" | "dacheng_mantra";
  title: string;
  active: boolean;
  active_version: number | null;
  active_checksum: string | null;
  body_text: string;
  updated_at?: string;
  updated_by?: string;
};

type CanonicalResponse = { documents?: CanonicalDocument[] };

function labelFor(key: CanonicalDocument["document_key"]) {
  return key === "dacheng_scripture" ? "前段正式經文" : "結尾正式咒語";
}

export default function CanonicalTextsPage() {
  const [documents, setDocuments] = useState<CanonicalDocument[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await fetch("/api/v1/review-admin/canonical-texts", {
        cache: "no-store",
        credentials: "same-origin",
      });
      const body = (await response.json().catch(() => ({}))) as CanonicalResponse & { detail?: string };
      if (!response.ok) throw new Error(body.detail || `讀取失敗 (${response.status})`);
      const items = body.documents ?? [];
      setDocuments(items);
      setDrafts(Object.fromEntries(items.map((item) => [item.document_key, item.body_text || ""])));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法讀取佛典正式文字");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function save(document: CanonicalDocument) {
    const bodyText = (drafts[document.document_key] || "").trim();
    if (!bodyText) return setError("正式文字不可空白。");
    if (!window.confirm(`建立「${document.title}」的新 canonical 版本並立即啟用？`)) return;
    setBusy(document.document_key); setError(null); setMessage(null);
    try {
      const response = await fetch(`/api/v1/review-admin/canonical-texts/${document.document_key}`, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: document.title,
          body_text: bodyText,
          note: notes[document.document_key] || "",
          confirm: true,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `儲存失敗 (${response.status})`);
      setMessage(`「${document.title}」已建立新版本並啟用。`);
      setNotes((current) => ({ ...current, [document.document_key]: "" }));
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "儲存失敗");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main style={{ maxWidth: 1120, margin: "32px auto", padding: "0 20px 60px", display: "grid", gap: 20 }}>
      <section style={{ display: "grid", gap: 8 }}>
        <h1 style={{ margin: 0 }}>大成佛經正式文字</h1>
        <p style={{ margin: 0, lineHeight: 1.7, color: "#53635d" }}>
          這裡是大成佛經專用辨識流程的唯一 canonical source。前段共誦只會依《佛說彌勒大成佛經》正式文字對齊；
          結尾《得見彌勒根本大明神咒》會依正式咒語做「講師一句＋大眾跟一句」成對折疊。無法確認時只標人工複核，不靠音近字猜寫。
        </p>
      </section>

      {error && <div style={{ padding: 14, borderRadius: 10, background: "#fff0f0", color: "#9f1d1d" }}>{error}</div>}
      {message && <div style={{ padding: 14, borderRadius: 10, background: "#edf7ef", color: "#285c37" }}>{message}</div>}

      {documents.map((document) => (
        <section key={document.document_key} style={{ border: "1px solid #dbe4df", borderRadius: 14, padding: 20, display: "grid", gap: 12, background: "#fff" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 13, color: "#66756f", marginBottom: 4 }}>{labelFor(document.document_key)}</div>
              <h2 style={{ margin: 0 }}>{document.title}</h2>
            </div>
            <div style={{ textAlign: "right", fontSize: 13, color: "#66756f" }}>
              <div>{document.active ? `Active v${document.active_version}` : "尚未設定正式版本"}</div>
              {document.active_checksum && <code>{document.active_checksum.slice(0, 16)}…</code>}
            </div>
          </div>

          {document.document_key === "dacheng_scripture" && !document.active && (
            <div style={{ padding: 12, borderRadius: 10, background: "#fff5dc", color: "#76510a" }}>
              尚未提供正式經文。大成佛經任務遇到前段共誦時會標記 scripture_alignment_review，不會自動用音近字代替。
            </div>
          )}

          <label style={{ display: "grid", gap: 6 }}>
            <strong>正式文字</strong>
            <textarea
              value={drafts[document.document_key] || ""}
              onChange={(event) => setDrafts((current) => ({ ...current, [document.document_key]: event.target.value }))}
              rows={document.document_key === "dacheng_scripture" ? 22 : 18}
              spellCheck={false}
              style={{ width: "100%", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", lineHeight: 1.7, padding: 12, borderRadius: 10, border: "1px solid #cdd8d2", resize: "vertical" }}
            />
          </label>

          <label style={{ display: "grid", gap: 6 }}>
            <strong>版本備註（選填）</strong>
            <input
              value={notes[document.document_key] || ""}
              onChange={(event) => setNotes((current) => ({ ...current, [document.document_key]: event.target.value }))}
              placeholder="例如：依精舍後台正式版本 2026-09-20 更新"
              style={{ padding: 10, borderRadius: 10, border: "1px solid #cdd8d2" }}
            />
          </label>

          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void save(document)}
              style={{ border: 0, borderRadius: 999, padding: "10px 18px", background: "#203f34", color: "#fff", fontWeight: 700, cursor: busy ? "wait" : "pointer" }}
            >
              {busy === document.document_key ? "儲存中…" : "建立新版本並啟用"}
            </button>
          </div>
        </section>
      ))}
    </main>
  );
}
