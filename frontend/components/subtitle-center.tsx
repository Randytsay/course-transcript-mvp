"use client";

import AppShell from "./app-shell";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, FileUp, LoaderCircle, PencilLine } from "lucide-react";
import { useEffect, useState } from "react";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

type SubtitleSummary = {
  id: string;
  kind: "job" | "imported";
  name: string;
  status: string;
  revision: number;
  segment_count: number;
  suspected_count: number;
  edited_count: number;
  updated_at: string;
  can_publish_to_source: boolean;
};

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
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) throw new Error(payload?.detail ?? `API 回應 ${response.status}`);
  return payload as T;
}

export default function SubtitleCenter() {
  const [items, setItems] = useState<SubtitleSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const result = await fetchJson<{ subtitles: SubtitleSummary[] }>("/subtitles");
      setItems(result.subtitles);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法載入字幕");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function importFile(file: File) {
    setImporting(true);
    try {
      const srtText = await file.text();
      await fetchJson<SubtitleSummary>("/subtitles/import", {
        method: "POST",
        body: JSON.stringify({ name: file.name, srt_text: srtText }),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "匯入失敗");
    } finally {
      setImporting(false);
    }
  }

  return (
    <AppShell
      title="字幕校訂中心"
      description="辨識任務完成後直接輸出；需要時再集中修正疑似錯字、批次替換並安全回寫 Drive。"
      actions={
        <label className="button button--primary" style={{ cursor: importing ? "wait" : "pointer" }}>
          {importing ? <LoaderCircle className="spin" size={18} /> : <FileUp size={18} />}
          匯入 SRT
          <input
            type="file"
            accept=".srt,application/x-subrip,text/plain"
            hidden
            disabled={importing}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importFile(file);
              event.target.value = "";
            }}
          />
        </label>
      }
    >
      {error && <div className="empty-state empty-state--error">{error}</div>}
      {loading ? (
        <div className="empty-state"><LoaderCircle className="spin" size={22} />正在載入字幕資料…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">尚無可編輯字幕。完成一個辨識任務，或從右上角匯入 SRT。</div>
      ) : (
        <div style={{ display: "grid", gap: "12px" }}>
          {items.map((item) => (
            <Link
              key={item.id}
              href={`/subtitles/${encodeURIComponent(item.id)}`}
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) auto",
                gap: "16px",
                alignItems: "center",
                padding: "18px",
                border: "1px solid var(--border)",
                borderRadius: "14px",
                background: "var(--surface)",
                boxShadow: "var(--shadow-sm)",
                color: "inherit",
                textDecoration: "none",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <strong style={{ display: "block", fontSize: "1.05rem", overflow: "hidden", textOverflow: "ellipsis" }}>{item.name}</strong>
                <span style={{ display: "block", color: "var(--text-muted)", marginTop: "5px" }}>
                  {item.kind === "job" ? "系統辨識字幕" : "外部匯入字幕"} · {item.segment_count.toLocaleString()} 段 · 版本 {item.revision}
                </span>
                <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginTop: "10px" }}>
                  <span className={`status-badge ${item.suspected_count ? "status-badge--warning" : "status-badge--completed"}`}>
                    {item.suspected_count ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
                    疑似問題 {item.suspected_count}
                  </span>
                  <span className="status-badge status-badge--queued"><PencilLine size={14} />人工修改 {item.edited_count}</span>
                  {item.can_publish_to_source && <span className="status-badge status-badge--completed">可安全回寫原資料夾</span>}
                </div>
              </div>
              <span className="button button--secondary">開啟編輯</span>
            </Link>
          ))}
        </div>
      )}
    </AppShell>
  );
}
