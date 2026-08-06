"use client";

import AppShell from "./app-shell";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, Check, CloudUpload, LoaderCircle, RotateCcw, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

type Segment = {
  segment_id: string;
  start_ms: number;
  end_ms: number;
  raw_text: string;
  ai_text: string;
  current_text: string;
  manually_edited: boolean;
  suspected: boolean;
  suspected_reasons: string[];
  uncertain_terms: string[];
};

type SubtitleDetail = {
  id: string;
  name: string;
  kind: "job" | "imported";
  status: string;
  revision: number;
  segment_count: number;
  suspected_count: number;
  edited_count: number;
  can_publish_to_source: boolean;
  segments: Segment[];
};

type ReplacePreview = {
  match_count: number;
  segment_count: number;
  subtitle_count: number;
  expected_revisions: Record<string, number>;
  matches: Array<{ subtitle_id: string; segment_id: string; count: number; before: string; after: string }>;
  truncated: boolean;
};

function apiErrorMessage(payload: unknown, status: number): string {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) {
    return `API 回應 ${status}`;
  }
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const record = item as { msg?: unknown; loc?: unknown };
        const message = typeof record.msg === "string" ? record.msg : null;
        if (!message) return null;
        const location = Array.isArray(record.loc)
          ? record.loc.map(String).filter((part) => part !== "body").join(".")
          : "";
        return location ? `${location}：${message}` : message;
      })
      .filter((message): message is string => Boolean(message));
    if (messages.length > 0) return messages.join("；");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return `API 回應 ${status}`;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) {
    const message = apiErrorMessage(payload, response.status);
    const error = new Error(message);
    (error as any).status = response.status;
    throw error;
  }
  return payload as T;
}

function time(ms: number) {
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

type PublishStatus = {
  status: "idle" | "publishing" | "completed" | "failed" | "ambiguous";
  job_status?: string | null;
  batch_status?: string | null;
  published_revision?: number | null;
  current_revision?: number;
  revision_changed_during_publish?: boolean;
  zero_edit_review?: boolean;
  drive_publish_status?: string | null;
  editor_publish_event_count?: number;
  can_publish?: boolean;
  can_retry?: boolean;
};

export default function SubtitleEditor({ subtitleId }: { subtitleId: string }) {
  const [detail, setDetail] = useState<SubtitleDetail | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<"all" | "suspected" | "edited">("suspected");
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchText, setSearchText] = useState("");
  const [replacement, setReplacement] = useState("");
  const [preview, setPreview] = useState<ReplacePreview | null>(null);
  const [replaceBusy, setReplaceBusy] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishStatus, setPublishStatus] = useState<PublishStatus | null>(null);
  const [publishMessage, setPublishMessage] = useState<string | null>(null);
  const publishingRef = useRef(false);

  async function load() {
    try {
      const result = await fetchJson<SubtitleDetail>(`/subtitles/${encodeURIComponent(subtitleId)}`);
      setDetail(result);
      setDrafts(Object.fromEntries(result.segments.map((item) => [item.segment_id, item.current_text])));
      setError(null);

      if (result.can_publish_to_source) {
        const statusResult = await fetchJson<PublishStatus>(`/subtitles/${encodeURIComponent(subtitleId)}/publish-status`);
        setPublishStatus(statusResult);
      } else {
        setPublishStatus(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法載入字幕");
    }
  }

  useEffect(() => { void load(); }, [subtitleId]);

  // Polling verification loop
  useEffect(() => {
    if (!publishStatus || publishStatus.status !== "publishing") {
      if (publishStatus?.status !== "completed") {
        setPublishMessage(null);
      }
      return;
    }

    setPublishing(true);
    setPublishMessage("正在發布至 Google Drive，請勿重複操作");

    let attempts = 0;
    const maxAttempts = 120; // 10 minutes (5 seconds interval)
    let active = true;

    const interval = setInterval(async () => {
      if (!active) return;
      attempts++;
      try {
        const res = await fetchJson<PublishStatus>(`/subtitles/${encodeURIComponent(subtitleId)}/publish-status`);
        if (!active) return;

        if (res.status === "completed") {
          clearInterval(interval);
          setPublishing(false);
          setPublishMessage(null);
          setPublishStatus(res);
          window.alert("字幕已成功發布至 Google Drive");
          await load();
        } else if (res.status === "failed") {
          clearInterval(interval);
          setPublishing(false);
          setPublishMessage(null);
          setPublishStatus(res);
          setError("發布失敗，請重試");
          await load();
        } else if (attempts >= maxAttempts) {
          clearInterval(interval);
          setPublishing(false);
          setPublishMessage(null);
          setPublishStatus(res);
          setError("發布仍可能在背景處理，請稍後重新開啟此頁確認，請勿再次送出");
        } else if (res.status === "ambiguous") {
          // Keep polling until 10 minutes timeout
        }
      } catch (err) {
        if (attempts >= maxAttempts) {
          clearInterval(interval);
          setPublishing(false);
          setPublishMessage(null);
          setError("發布仍可能在背景處理，請稍後重新開啟此頁確認，請勿再次送出");
        }
      }
    }, 5000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [publishStatus?.status, subtitleId]);

  // Page unload warning
  useEffect(() => {
    if (!publishing) return;
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "字幕發布正在背景處理中，關閉頁面不影響發布，但您將無法在頁面上即時確認結果。確定要離開嗎？";
      return e.returnValue;
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [publishing]);

  const visible = useMemo(() => {
    if (!detail) return [];
    const keyword = query.trim().toLocaleLowerCase("zh-Hant");
    return detail.segments.filter((item) => {
      if (filter === "suspected" && !item.suspected) return false;
      if (filter === "edited" && !item.manually_edited) return false;
      if (!keyword) return true;
      return [item.current_text, item.raw_text, item.ai_text].some((value) => value.toLocaleLowerCase("zh-Hant").includes(keyword));
    });
  }, [detail, filter, query]);

  async function saveSegment(segment: Segment) {
    if (!detail) return;
    const value = (drafts[segment.segment_id] ?? segment.current_text).trim();
    if (!value || value === segment.current_text) return;
    setSaving(segment.segment_id);
    try {
      await fetchJson(`/subtitles/${encodeURIComponent(subtitleId)}/segments/${encodeURIComponent(segment.segment_id)}`, {
        method: "PATCH",
        body: JSON.stringify({ text: value, expected_revision: detail.revision }),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "儲存失敗");
      await load();
    } finally {
      setSaving(null);
    }
  }

  async function previewReplacement() {
    if (!searchText || !replacement) return;
    setReplaceBusy(true);
    try {
      const result = await fetchJson<ReplacePreview>("/subtitles/replace/preview", {
        method: "POST",
        body: JSON.stringify({ search: searchText, replacement, subtitle_ids: [subtitleId] }),
      });
      setPreview(result);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法預覽批次修正");
    } finally {
      setReplaceBusy(false);
    }
  }

  async function applyReplacement() {
    if (!preview) return;
    setReplaceBusy(true);
    try {
      await fetchJson("/subtitles/replace/apply", {
        method: "POST",
        body: JSON.stringify({
          search: searchText,
          replacement,
          subtitle_ids: [subtitleId],
          expected_revisions: preview.expected_revisions,
        }),
      });
      setPreview(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "批次修正失敗");
      setPreview(null);
      await load();
    } finally {
      setReplaceBusy(false);
    }
  }

  function isMaybeTimeoutOrNetworkError(error: unknown, status?: number): boolean {
    if (status && status >= 500) return true;
    const msg = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
    return msg.includes("timeout") ||
           msg.includes("abort") ||
           msg.includes("hang up") ||
           msg.includes("econnreset") ||
           msg.includes("failed to fetch") ||
           msg.includes("network");
  }

  async function publishEdited() {
    if (!detail || publishingRef.current) return;
    if (
      detail.revision === 0
      && !window.confirm("已確認目前字幕內容無需修改，並要將 SRT 與 TXT 發布回原始 Drive 資料夾嗎？")
    ) return;
    publishingRef.current = true;
    setPublishing(true);
    setPublishMessage("正在發布至 Google Drive，請勿重複操作");
    try {
      const result = await fetchJson<{ backup_count: number }>(`/subtitles/${encodeURIComponent(subtitleId)}/publish`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: detail.revision, output_formats: ["srt", "txt"] }),
      });
      window.alert(`回寫完成；原資料夾中同名舊檔已安全備份 ${result.backup_count} 個。`);
      publishingRef.current = false;
      setPublishing(false);
      setPublishMessage(null);
      await load();
    } catch (cause) {
      const isTimeoutOrNetwork = isMaybeTimeoutOrNetworkError(cause, (cause as any).status);
      if (isTimeoutOrNetwork) {
        try {
          const res = await fetchJson<PublishStatus>(`/subtitles/${encodeURIComponent(subtitleId)}/publish-status`);
          setPublishStatus(res);
        } catch (e) {
          setPublishStatus({ status: "publishing" });
        }
      } else {
        publishingRef.current = false;
        setError(cause instanceof Error ? cause.message : "Drive 回寫失敗");
        setPublishing(false);
        setPublishMessage(null);
      }
    }
  }

  return (
    <AppShell
      title={detail?.name ?? "字幕編輯"}
      description={detail ? `${detail.segment_count.toLocaleString()} 段 · 疑似問題 ${detail.suspected_count} · 人工修改 ${detail.edited_count} · 版本 ${detail.revision}` : "載入字幕中"}
      actions={<Link href="/subtitles" className="button button--secondary"><ArrowLeft size={18} />返回字幕中心</Link>}
    >
      {publishMessage && (
        <div style={{ padding: "12px", borderRadius: "10px", background: "#eff6ff", border: "1px solid #bfdbfe", color: "#1e40af", marginBottom: "14px", fontWeight: 600 }}>
          {publishMessage}
        </div>
      )}
      {error && <div className="empty-state empty-state--error" style={{ marginBottom: "14px" }}>{error}</div>}
      {!detail ? (
        <div className="empty-state"><LoaderCircle className="spin" size={22} />正在載入…</div>
      ) : (
        <div style={{ display: "grid", gap: "16px" }}>
          <section style={{ padding: "16px", border: "1px solid var(--border)", borderRadius: "14px", background: "var(--surface)" }}>
            <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" }}>
              <div className="search-box" style={{ flex: "1 1 280px" }}><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋目前文字、Chirp 原文或 AI 版本" /></div>
              {(["suspected", "edited", "all"] as const).map((value) => (
                <button key={value} type="button" className={`button ${filter === value ? "button--primary" : "button--secondary"}`} onClick={() => setFilter(value)}>
                  {value === "suspected" ? `疑似問題 (${detail.suspected_count})` : value === "edited" ? `已修改 (${detail.edited_count})` : "全部"}
                </button>
              ))}
              {detail.can_publish_to_source && (
                publishStatus?.status === "completed" && publishStatus.published_revision === detail.revision ? (
                  <span className="status-badge status-badge--completed" style={{ padding: "8px 12px", fontSize: "0.95rem" }}>
                    字幕已成功發布至 Google Drive (版本 {publishStatus.published_revision ?? 0})
                  </span>
                ) : (
                  <button
                    type="button"
                    className="button button--primary"
                    disabled={
                      publishing
                      || publishStatus?.status === "publishing"
                      || publishStatus?.status === "ambiguous"
                      || (publishStatus ? !publishStatus.can_publish : false)
                    }
                    onClick={() => void publishEdited()}
                  >
                    {publishing || publishStatus?.status === "publishing" ? <LoaderCircle className="spin" size={18} /> : <CloudUpload size={18} />}
                    {publishing || publishStatus?.status === "publishing" ? "正在發布…" : detail.revision === 0 ? "確認無需修改並回寫" : "安全回寫 SRT＋TXT"}
                  </button>
                )
              )}
            </div>
          </section>

          <section style={{ padding: "16px", border: "1px solid var(--border)", borderRadius: "14px", background: "var(--surface)" }}>
            <h2 style={{ margin: "0 0 12px", fontSize: "1.05rem" }}>同樣錯字批次修復</h2>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(160px,1fr) minmax(160px,1fr) auto", gap: "10px" }}>
              <input value={searchText} onChange={(event) => { setSearchText(event.target.value); setPreview(null); }} placeholder="錯誤文字，例如：代師兄" />
              <input value={replacement} onChange={(event) => { setReplacement(event.target.value); setPreview(null); }} placeholder="正確文字，例如：戴師兄" />
              <button className="button button--secondary" type="button" disabled={replaceBusy || !searchText || !replacement} onClick={() => void previewReplacement()}>
                {replaceBusy ? <LoaderCircle className="spin" size={18} /> : <Search size={18} />}預覽
              </button>
            </div>
            {preview && (
              <div style={{ marginTop: "12px", padding: "12px", borderRadius: "10px", background: "#fff7ed", border: "1px solid #fed7aa" }}>
                <strong>預計修改 {preview.match_count} 處，分布於 {preview.segment_count} 段。</strong>
                <div style={{ maxHeight: "180px", overflow: "auto", marginTop: "8px", fontSize: ".9rem" }}>
                  {preview.matches.slice(0, 30).map((match) => <div key={`${match.subtitle_id}-${match.segment_id}`}>{match.before} → {match.after}</div>)}
                </div>
                <button className="button button--primary" type="button" style={{ marginTop: "10px" }} disabled={replaceBusy || preview.match_count === 0} onClick={() => void applyReplacement()}>
                  <Check size={18} />確認批次套用
                </button>
              </div>
            )}
          </section>

          <section style={{ display: "grid", gap: "10px" }}>
            {visible.length === 0 ? <div className="empty-state">目前篩選條件沒有字幕段落。</div> : visible.map((segment) => {
              const draft = drafts[segment.segment_id] ?? segment.current_text;
              const changed = draft.trim() !== segment.current_text;
              return (
                <article key={segment.segment_id} style={{ padding: "16px", border: `1px solid ${segment.suspected ? "#f59e0b" : "var(--border)"}`, borderRadius: "14px", background: "var(--surface)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                    <strong>{time(segment.start_ms)} → {time(segment.end_ms)}</strong>
                    <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                      {segment.suspected && <span className="status-badge status-badge--warning"><AlertTriangle size={14} />{segment.suspected_reasons.join("、")}</span>}
                      {segment.manually_edited && <span className="status-badge status-badge--completed">已人工修改</span>}
                    </div>
                  </div>
                  <details style={{ marginTop: "10px" }}>
                    <summary style={{ cursor: "pointer", color: "var(--text-muted)" }}>比較 Chirp 原文與 Gemini 版本</summary>
                    <div style={{ marginTop: "8px", display: "grid", gap: "8px", fontSize: ".92rem" }}>
                      <div><strong>Chirp：</strong>{segment.raw_text}</div>
                      <div><strong>Gemini：</strong>{segment.ai_text}</div>
                    </div>
                  </details>
                  <textarea
                    value={draft}
                    onChange={(event) => setDrafts((current) => ({ ...current, [segment.segment_id]: event.target.value }))}
                    style={{ width: "100%", minHeight: "88px", marginTop: "12px", padding: "12px", borderRadius: "10px", border: "1px solid var(--border-strong)", font: "inherit", lineHeight: 1.6 }}
                  />
                  <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "8px" }}>
                    {changed && <button type="button" className="button button--secondary" onClick={() => setDrafts((current) => ({ ...current, [segment.segment_id]: segment.current_text }))}><RotateCcw size={17} />還原</button>}
                    <button type="button" className="button button--primary" disabled={!changed || saving !== null} onClick={() => void saveSegment(segment)}>
                      {saving === segment.segment_id ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}儲存本段
                    </button>
                  </div>
                </article>
              );
            })}
          </section>
        </div>
      )}
    </AppShell>
  );
}
