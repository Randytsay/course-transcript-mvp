"use client";

import AppShell from "./app-shell";
import Link from "next/link";
import { AlertTriangle, Check, LoaderCircle, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1").replace(/\/$/, "");

type Cue = { source_segment_ids: string[]; text: string };

type Candidate = {
  change_id: string;
  change_type: string;
  source_segment_ids: string[];
  before: Array<{ segment_id: string; text: string }>;
  after: Cue[];
  reason: string;
  confidence: number;
  risk: string;
  high_review_required: boolean;
  status: "pending" | "accepted" | "rejected";
};

type RevisionSummary = {
  revision: number;
  created_at: string;
  source: string;
  content_sha256: string;
  cue_count: number;
  active: boolean;
};

type ReviewState = {
  subtitle_id: string;
  revision: number;
  active_revision: number | null;
  counts: Record<string, number>;
  total_candidates: number;
  candidates: Candidate[];
  revisions: RevisionSummary[];
};

type BaselineSegment = {
  segment_id: string;
  start_ms: number;
  end_ms: number;
  raw_text: string;
  working_text?: string;
};

function apiErrorMessage(payload: unknown, status: number): string {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) return `API 回應 ${status}`;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  return `API 回應 ${status}`;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    cache: "no-store",
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) throw new Error(apiErrorMessage(payload, response.status));
  return payload as T;
}

function time(ms: number) {
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

const TYPE_LABELS: Record<string, string> = {
  asr_typo: "錯字",
  proper_noun: "專有名詞",
  semantic_asr_error: "語義辨識",
  punctuation: "標點",
  line_break: "換行",
  repetition_cleanup: "口誤 / 重複",
  obvious_speech_correction: "口誤整理",
  cross_segment_reflow: "跨段重新整理",
  merge_adjacent: "合併",
  split_for_readability: "重切",
  mixed: "混合",
};

const FILTERS = ["all", "text", "cross_segment", "high_risk", "pending"] as const;
type Filter = (typeof FILTERS)[number];

export default function AIReviewPanel({ subtitleId }: { subtitleId: string }) {
  const [state, setState] = useState<ReviewState | null>(null);
  const [baseline, setBaseline] = useState<BaselineSegment[]>([]);
  const [filter, setFilter] = useState<Filter>("pending");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, Cue[]>>({});

  const load = useCallback(async () => {
    try {
      const result = await fetchJson<ReviewState>(`/subtitles/${encodeURIComponent(subtitleId)}/ai-review`);
      setState(result);
      const base = await fetchJson<{ segments: BaselineSegment[] }>(`/subtitles/${encodeURIComponent(subtitleId)}/ai-review/baseline`);
      setBaseline(base.segments);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法載入審核狀態");
    }
  }, [subtitleId]);

  useEffect(() => { void load(); }, [load]);

  const baselineById = useMemo(
    () => Object.fromEntries(baseline.map((item) => [item.segment_id, item])),
    [baseline],
  );

  function cueRange(candidate: Candidate): string {
    const times = candidate.source_segment_ids
      .map((id) => baselineById[id])
      .filter(Boolean)
      .flatMap((item) => [item.start_ms, item.end_ms]);
    if (!times.length) return "";
    return `${time(Math.min(...times))} → ${time(Math.max(...times))}`;
  }

  const visible = useMemo(() => {
    if (!state) return [];
    return state.candidates.filter((candidate) => {
      if (filter === "text") return !["cross_segment_reflow", "merge_adjacent", "split_for_readability"].includes(candidate.change_type);
      if (filter === "cross_segment") return ["cross_segment_reflow", "merge_adjacent", "split_for_readability"].includes(candidate.change_type);
      if (filter === "high_risk") return candidate.risk === "high" || candidate.high_review_required;
      if (filter === "pending") return candidate.status === "pending";
      return true;
    });
  }, [state, filter]);

  const counts = state?.counts ?? {};
  const pendingCount = counts.pending ?? 0;

  async function decide(candidate: Candidate, decision: "accept" | "reject") {
    setBusy(candidate.change_id);
    try {
      await fetchJson(`/subtitles/${encodeURIComponent(subtitleId)}/ai-review/candidates/decide`, {
        method: "POST",
        body: JSON.stringify({
          change_id: candidate.change_id,
          decision,
          ...(decision === "accept" && edits[candidate.change_id] ? { edited_after: edits[candidate.change_id] } : {}),
        }),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失敗");
    } finally {
      setBusy(null);
    }
  }

  async function publish() {
    if (!state) return;
    setBusy("publish");
    try {
      await fetchJson(`/subtitles/${encodeURIComponent(subtitleId)}/ai-review/publish`, {
        method: "POST",
        body: JSON.stringify({ base_revision: state.revision }),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "發布失敗");
    } finally {
      setBusy(null);
    }
  }

  async function rollback(revision: number) {
    setBusy(`rollback-${revision}`);
    try {
      await fetchJson(`/subtitles/${encodeURIComponent(subtitleId)}/ai-review/revisions/${revision}/rollback`, { method: "POST" });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "回復失敗");
    } finally {
      setBusy(null);
    }
  }

  function editCue(changeId: string, index: number, text: string) {
    setEdits((current) => {
      const source = current[changeId] ?? state?.candidates.find((c) => c.change_id === changeId)?.after ?? [];
      const next = source.map((cue, position) => (position === index ? { ...cue, text } : cue));
      return { ...current, [changeId]: next };
    });
  }

  return (
    <AppShell
      title="AI 整理字幕"
      description={state ? `建議 ${state.total_candidates} 項 · 已接受 ${counts.accepted ?? 0} · 已拒絕 ${counts.rejected ?? 0}` : "載入中"}
      actions={<Link href={`/subtitles/${encodeURIComponent(subtitleId)}`} className="button button--secondary"><RotateCcw size={18} />返回編輯器</Link>}
    >
      {error && <div className="empty-state empty-state--error" style={{ marginBottom: 14 }}>{error}</div>}
      {!state ? (
        <div className="empty-state"><LoaderCircle className="spin" size={22} />正在載入…</div>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          <section style={{ padding: 16, border: "1px solid var(--border)", borderRadius: 14, background: "var(--surface)" }}>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              {FILTERS.map((value) => (
                <button key={value} type="button" className={`button ${filter === value ? "button--primary" : "button--secondary"}`} onClick={() => setFilter(value)}>
                  {value === "all" ? "全部" : value === "text" ? "只看文字修正" : value === "cross_segment" ? "只看跨段" : value === "high_risk" ? "只看高風險" : `未審核 (${pendingCount})`}
                </button>
              ))}
              <div style={{ flex: 1 }} />
              {Object.entries(state.revisions.reduce<Record<string, number>>((acc, item) => {
                acc[TYPE_LABELS[item.source] ?? item.source] = (acc[TYPE_LABELS[item.source] ?? item.source] ?? 0) + 1;
                return acc;
              }, {})).length > 0 && null}
            </div>
            <div style={{ marginTop: 12, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <strong>
                已接受 {counts.accepted ?? 0} · 已拒絕 {counts.rejected ?? 0} · 未處理 {pendingCount}
              </strong>
              <div style={{ flex: 1 }} />
              <button type="button" className="button button--primary" disabled={busy !== null || pendingCount > 0 || (counts.accepted ?? 0) === 0} onClick={() => void publish()}>
                {busy === "publish" ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}建立新字幕版本（Revision {(state.revision ?? 0) + 1}）
              </button>
            </div>
          </section>

          {(state.revisions.length > 0) && (
            <section style={{ padding: 16, border: "1px solid var(--border)", borderRadius: 14, background: "var(--surface)" }}>
              <h2 style={{ margin: "0 0 10px", fontSize: "1.05rem" }}>字幕版本</h2>
              <div style={{ display: "grid", gap: 8 }}>
                {state.revisions.map((item) => (
                  <div key={item.revision} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <strong>Revision {item.revision}</strong>
                    <span>{item.cue_count} cues · {item.created_at.slice(0, 19)}</span>
                    {item.active && <span className="status-badge status-badge--completed">ACTIVE</span>}
                    {!item.active && (
                      <button type="button" className="button button--secondary" disabled={busy !== null} onClick={() => void rollback(item.revision)}>
                        <RotateCcw size={16} />回復此版本
                      </button>
                    )}
                  </div>
                ))}
              </div>
              {state.active_revision != null && (
                <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {(["srt", "vtt", "txt", "docx"] as const).map((kind) => (
                    <a key={kind} className="button button--secondary" href={`${apiBase}/subtitles/${encodeURIComponent(subtitleId)}/ai-review/export/${kind}`}>
                      下載 {kind.toUpperCase()}（Active）
                    </a>
                  ))}
                </div>
              )}
            </section>
          )}

          <section style={{ display: "grid", gap: 10 }}>
            {visible.length === 0 ? (
              <div className="empty-state">目前篩選條件沒有建議。</div>
            ) : visible.map((candidate) => {
              const isCross = ["cross_segment_reflow", "merge_adjacent", "split_for_readability"].includes(candidate.change_type);
              const edited = edits[candidate.change_id];
              const afterCues = edited ?? candidate.after;
              return (
                <article key={candidate.change_id} style={{ padding: 16, border: `1px solid ${candidate.risk === "high" ? "#f59e0b" : "var(--border)"}`, borderRadius: 14, background: "var(--surface)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                    <strong>{TYPE_LABELS[candidate.change_type] ?? candidate.change_type} · {cueRange(candidate)}</strong>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {candidate.risk === "high" && <span className="status-badge status-badge--warning"><AlertTriangle size={14} />高風險</span>}
                      {candidate.status !== "pending" && <span className="status-badge status-badge--completed">{candidate.status === "accepted" ? "已接受" : "已拒絕"}</span>}
                      <span className="status-badge">信心 {(candidate.confidence * 100).toFixed(0)}%</span>
                    </div>
                  </div>

                  <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    <div style={{ padding: 10, borderRadius: 10, background: "#fef2f2" }}>
                      <strong>修改前</strong>
                      {candidate.before.map((item) => (
                        <div key={item.segment_id} style={{ marginTop: 6 }}><code>[{item.segment_id}]</code> {item.text}</div>
                      ))}
                    </div>
                    <div style={{ padding: 10, borderRadius: 10, background: "#f0fdf4" }}>
                      <strong>修改後</strong>
                      {afterCues.map((cue, index) => (
                        <div key={index} style={{ marginTop: 6 }}>
                          <code>[cue {index + 1} · {cue.source_segment_ids.join("+")}]</code>{" "}
                          {edited ? (
                            <input value={cue.text} onChange={(event) => editCue(candidate.change_id, index, event.target.value)} style={{ width: "80%", border: "1px solid var(--border-strong)", borderRadius: 6, padding: "2px 6px" }} />
                          ) : (
                            <span>{cue.text}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div style={{ marginTop: 8, color: "var(--text-muted)", fontSize: ".9rem" }}>原因：{candidate.reason}</div>

                  {candidate.status === "pending" && (
                    <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
                      <button type="button" className="button button--secondary" disabled={busy !== null} onClick={() => setEdits((c) => ({ ...c, [candidate.change_id]: afterCues }))}>
                        自行調整
                      </button>
                      <button type="button" className="button button--secondary" disabled={busy !== null} onClick={() => void decide(candidate, "reject")}>
                        <X size={16} />拒絕
                      </button>
                      <button type="button" className="button button--primary" disabled={busy !== null} onClick={() => void decide(candidate, "accept")}>
                        <Check size={16} />接受
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        </div>
      )}
    </AppShell>
  );
}
