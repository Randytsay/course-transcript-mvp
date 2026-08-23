"use client";

import { getJob, getJobChunkTranscript } from "@/lib/api-client";
import {
  createRetranscription,
  getAsrQuality,
  getRetranscription,
  listRetranscriptions,
  previewRetranscription,
  rejectRetranscription,
  type AsrQualityChunk,
  type AsrQualityReport,
  type CandidateComparison,
  type RetranscriptionCandidate,
  type RetranscriptionPreview,
} from "@/lib/retranscription-client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

const reasonLabels: Record<string, string> = {
  provider_or_chunk_status_not_successful: "辨識服務或分段狀態異常",
  expected_asr_artifact_missing: "辨識結果檔案不完整",
  density_far_below_course_baseline: "文字量遠低於同一堂課的基準",
  density_below_course_baseline: "文字量低於同一堂課的基準",
  density_mildly_below_course_baseline: "文字量略低於同一堂課的基準",
  density_far_below_neighbor_chunks: "文字量明顯低於前後段",
  recognized_timeline_span_low: "有辨識到文字的時間範圍偏少",
  long_unrecognized_timeline_gap: "中間有較長時間沒有辨識到文字",
  high_repeated_word_pattern: "辨識結果有偏高的重複字詞",
};

const statusLabels: Record<string, string> = {
  queued: "已確認，等待重辨識",
  submitted: "已送出 Chirp 3",
  processing: "Chirp 3 處理中",
  completed: "候選版本已完成",
  failed: "候選重辨識失敗",
  stale: "原始資料已變更，候選失效",
  rejected: "已決定不採用",
  applied: "已採用",
};

const severityLabels: Record<string, string> = {
  normal: "正常",
  low: "低度提醒",
  medium: "建議檢查",
  high: "優先檢查",
};

function metric(metrics: Record<string, number | string | null>, key: string, digits = 2) {
  const raw = metrics[key];
  if (typeof raw !== "number" || !Number.isFinite(raw)) return "—";
  return raw.toFixed(digits);
}

const panelStyle = {
  background: "#fff",
  border: "1px solid #d9e0e8",
  borderRadius: "18px",
  padding: "20px",
  boxShadow: "0 10px 30px rgba(15,23,42,.06)",
} as const;

const buttonStyle = {
  minHeight: "44px",
  border: 0,
  borderRadius: "11px",
  padding: "0 16px",
  fontWeight: 750,
  cursor: "pointer",
} as const;

export default function AsrRetranscriptionPanel({ jobId }: { jobId: string }) {
  const [jobRevision, setJobRevision] = useState(0);
  const [report, setReport] = useState<AsrQualityReport | null>(null);
  const [candidates, setCandidates] = useState<RetranscriptionCandidate[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [selectedText, setSelectedText] = useState("");
  const [preview, setPreview] = useState<RetranscriptionPreview | null>(null);
  const [comparison, setComparison] = useState<CandidateComparison | null>(null);
  const [comparisonCandidate, setComparisonCandidate] = useState<RetranscriptionCandidate | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [job, quality, rows] = await Promise.all([
        getJob(jobId),
        getAsrQuality(jobId),
        listRetranscriptions(jobId),
      ]);
      setJobRevision(job.revision);
      setReport(quality);
      setCandidates(rows);
      setSelectedIndex((current) => {
        if (current !== null && quality.chunks.some((chunk) => chunk.chunk_index === current)) {
          return current;
        }
        const suspicious = quality.chunks.find((chunk) => chunk.quality?.suspicious);
        return suspicious?.chunk_index ?? quality.chunks[0]?.chunk_index ?? null;
      });
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法取得 ASR 品質資料");
    }
  }, [jobId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const hasActiveCandidate = candidates.some((candidate) =>
    ["queued", "submitted", "processing"].includes(candidate.status),
  );

  useEffect(() => {
    if (!hasActiveCandidate) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveCandidate, refresh]);

  useEffect(() => {
    setPreview(null);
    setComparison(null);
    setComparisonCandidate(null);
    if (selectedIndex === null) {
      setSelectedText("");
      return;
    }
    void getJobChunkTranscript(jobId, selectedIndex)
      .then((value) => setSelectedText(value.rawText))
      .catch(() => setSelectedText(""));
  }, [jobId, selectedIndex]);

  const selected = useMemo<AsrQualityChunk | null>(() => {
    if (!report || selectedIndex === null) return null;
    return report.chunks.find((chunk) => chunk.chunk_index === selectedIndex) ?? null;
  }, [report, selectedIndex]);

  const candidatesForSelected = useMemo(() => {
    if (selectedIndex === null) return [];
    return candidates.filter((candidate) => candidate.chunk_index === selectedIndex);
  }, [candidates, selectedIndex]);

  async function openPreview() {
    if (selectedIndex === null || jobRevision < 1) return;
    setBusy("preview");
    setError(null);
    try {
      setPreview(await previewRetranscription(jobId, jobRevision, selectedIndex));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法取得重辨識估價");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function confirmCreate() {
    if (!preview) return;
    if (!preview.retranscription_enabled) {
      setError("單段付費重辨識尚在 production live gate 階段，目前不會送出 Chirp 3 請求。");
      return;
    }
    const message = [
      `只重新辨識第 ${preview.chunk_index + 1} 段。`,
      `預估費用：約 NT$${preview.estimate.estimated_cost_twd}（US$${preview.estimate.estimated_cost_usd}）。`,
      "這一步會送出一筆付費 Chirp 3 請求，但不會覆蓋目前字幕。",
      "確定建立候選版本？",
    ].join("\n\n");
    if (!window.confirm(message)) return;
    setBusy("create");
    setError(null);
    try {
      await createRetranscription(jobId, preview);
      setPreview(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法建立重辨識候選版本");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function showComparison(candidate: RetranscriptionCandidate) {
    setBusy(`compare:${candidate.id}`);
    setError(null);
    try {
      const detail = await getRetranscription(jobId, candidate.id);
      setComparisonCandidate(detail.candidate);
      setComparison(detail.comparison);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法取得候選比較");
    } finally {
      setBusy(null);
    }
  }

  async function reject(candidate: RetranscriptionCandidate) {
    if (!window.confirm("確定不採用這個候選版本？目前正式字幕不會被修改。")) return;
    setBusy(`reject:${candidate.id}`);
    setError(null);
    try {
      await rejectRetranscription(jobId, candidate.id, jobRevision);
      setComparison(null);
      setComparisonCandidate(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "無法更新候選決定");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  if (!report && !error) {
    return <main style={{ maxWidth: "1180px", margin: "0 auto", padding: "32px 20px" }}>載入 ASR 品質資料中…</main>;
  }

  return (
    <main style={{ maxWidth: "1180px", margin: "0 auto", padding: "28px 20px 80px", color: "#172033" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "16px", flexWrap: "wrap", marginBottom: "20px" }}>
        <div>
          <p style={{ margin: "0 0 6px", color: "#64748b", fontWeight: 700 }}>Chirp 3 品質檢查</p>
          <h1 style={{ margin: 0, fontSize: "clamp(1.7rem, 4vw, 2.4rem)" }}>ASR 品質與單段重辨識</h1>
          <p style={{ color: "#64748b", marginBottom: 0 }}>先找異常，再決定是否花費重跑。候選完成前後都不會覆蓋正式字幕。</p>
        </div>
        <Link href={`/jobs/${jobId}`} style={{ ...buttonStyle, display: "inline-flex", alignItems: "center", color: "#334155", background: "#eef2f7", textDecoration: "none" }}>返回任務</Link>
      </div>

      {error ? <div role="alert" style={{ ...panelStyle, borderColor: "#fecaca", background: "#fff7f7", color: "#991b1b", marginBottom: "18px" }}>{error}</div> : null}

      {report ? (
        <section style={{ ...panelStyle, marginBottom: "18px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: "12px" }}>
            <Summary label="全部分段" value={report.summary.chunk_count} />
            <Summary label="需要檢查" value={report.summary.suspicious_chunk_count} />
            <Summary label="高優先" value={report.summary.high_count} />
            <Summary label="中優先" value={report.summary.medium_count} />
            <Summary label="本次品質分析費用" value="NT$0" />
            <Summary label="付費重辨識" value={report.retranscription_enabled ? "已啟用" : "實機驗收中"} />
          </div>
        </section>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(250px, 360px) minmax(0, 1fr)", gap: "18px", alignItems: "start" }}>
        <section style={panelStyle}>
          <h2 style={{ marginTop: 0, fontSize: "1.15rem" }}>分段品質</h2>
          <div style={{ display: "grid", gap: "8px", maxHeight: "68vh", overflow: "auto" }}>
            {report?.chunks.map((chunk) => {
              const active = chunk.chunk_index === selectedIndex;
              const severity = chunk.quality?.severity ?? "normal";
              return (
                <button
                  key={chunk.chunk_index}
                  type="button"
                  onClick={() => setSelectedIndex(chunk.chunk_index)}
                  style={{
                    textAlign: "left",
                    border: active ? "2px solid #2563eb" : "1px solid #dbe3ec",
                    background: active ? "#eff6ff" : "#fff",
                    borderRadius: "12px",
                    padding: "12px",
                    cursor: "pointer",
                  }}
                >
                  <strong>第 {chunk.chunk_index + 1} 段</strong>
                  <span style={{ float: "right", fontWeight: 750, color: severity === "high" ? "#b91c1c" : severity === "medium" ? "#b45309" : "#64748b" }}>{severityLabels[severity]}</span>
                  <div style={{ clear: "both", marginTop: "6px", color: "#64748b", fontSize: ".9rem" }}>
                    密度 {metric(chunk.metrics, "density_chars_per_min", 0)} 字/分 · 分數 {chunk.quality?.score ?? 0}
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section style={{ display: "grid", gap: "18px" }}>
          {selected ? (
            <div style={panelStyle}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                <div>
                  <p style={{ margin: "0 0 5px", color: "#64748b" }}>目前選擇</p>
                  <h2 style={{ margin: 0 }}>第 {selected.chunk_index + 1} 段 · {severityLabels[selected.quality?.severity ?? "normal"]}</h2>
                </div>
                <div style={{ textAlign: "right", color: "#64748b" }}>
                  <div>辨識覆蓋 {metric(selected.metrics, "recognized_span_ratio")}</div>
                  <div>相對本課密度 {metric(selected.metrics, "relative_density")}×</div>
                </div>
              </div>

              {selected.quality?.reasons?.length ? (
                <ul style={{ paddingLeft: "20px", lineHeight: 1.7 }}>
                  {selected.quality.reasons.map((reason) => <li key={reason}>{reasonLabels[reason] ?? reason}</li>)}
                </ul>
              ) : <p style={{ color: "#047857", fontWeight: 700 }}>目前沒有品質異常訊號。</p>}

              <div style={{ background: "#f8fafc", borderRadius: "12px", padding: "14px", whiteSpace: "pre-wrap", lineHeight: 1.65, maxHeight: "220px", overflow: "auto" }}>
                {selectedText || "此段目前沒有可顯示的原始逐字稿。"}
              </div>

              <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginTop: "16px" }}>
                {selected.quality?.suspicious ? (
                  <button type="button" disabled={busy !== null} onClick={() => void openPreview()} style={{ ...buttonStyle, background: "#1d4ed8", color: "#fff", opacity: busy ? .65 : 1 }}>
                    {busy === "preview" ? "重新計算中…" : "查看重辨識費用"}
                  </button>
                ) : (
                  <span style={{ color: "#64748b", alignSelf: "center" }}>此段目前不建議付費重辨識。</span>
                )}
              </div>
            </div>
          ) : null}

          {preview ? (
            <div style={{ ...panelStyle, borderColor: "#bfdbfe", background: "#f8fbff" }}>
              <h2 style={{ marginTop: 0 }}>重辨識前確認</h2>
              <p>只處理第 <strong>{preview.chunk_index + 1}</strong> 段，不重跑整堂課，也不覆蓋目前字幕。</p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: "10px", margin: "16px 0" }}>
                <Summary label="預估費用" value={`NT$${preview.estimate.estimated_cost_twd}`} />
                <Summary label="美元估價" value={`US$${preview.estimate.estimated_cost_usd}`} />
                <Summary label="計費分鐘" value={preview.estimate.billable_minutes} />
                <Summary label="模式" value={preview.estimate.processing_strategy === "DYNAMIC_BATCHING" ? "動態批次" : "標準批次"} />
              </div>
              {preview.existing_candidate ? (
                <p style={{ color: "#7c3aed", fontWeight: 700 }}>這個相同版本已經有候選任務，不會重複保留費用或建立第二筆。</p>
              ) : null}
              {!preview.retranscription_enabled ? (
                <p style={{ color: "#92400e", fontWeight: 750, background: "#fffbeb", borderRadius: "10px", padding: "12px" }}>
                  目前只開放品質分析與估價。付費單段重辨識仍在 ARM64/VPS production live gate，尚未送出任何新 Chirp 3 費用。
                </p>
              ) : (
                <button type="button" disabled={busy !== null} onClick={() => void confirmCreate()} style={{ ...buttonStyle, background: "#b45309", color: "#fff" }}>
                  {busy === "create" ? "建立中…" : preview.existing_candidate ? "查看既有候選" : "確認費用並建立候選"}
                </button>
              )}
            </div>
          ) : null}

          {candidatesForSelected.length ? (
            <div style={panelStyle}>
              <h2 style={{ marginTop: 0 }}>這一段的候選紀錄</h2>
              <div style={{ display: "grid", gap: "10px" }}>
                {candidatesForSelected.map((candidate) => (
                  <div key={candidate.id} style={{ border: "1px solid #e2e8f0", borderRadius: "12px", padding: "13px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "10px", flexWrap: "wrap" }}>
                      <strong>{statusLabels[candidate.status] ?? candidate.status}</strong>
                      <span style={{ color: "#64748b" }}>US${candidate.confirmed_cost_usd}</span>
                    </div>
                    {candidate.error_safe_message ? <p style={{ color: "#b91c1c" }}>{candidate.error_safe_message}</p> : null}
                    {candidate.status === "completed" || candidate.status === "rejected" ? (
                      <button type="button" disabled={busy !== null} onClick={() => void showComparison(candidate)} style={{ ...buttonStyle, marginTop: "10px", background: "#e0e7ff", color: "#3730a3" }}>
                        {busy === `compare:${candidate.id}` ? "讀取中…" : "查看原版 / 候選比較"}
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {comparison && comparisonCandidate ? (
            <div style={{ ...panelStyle, borderColor: "#c7d2fe" }}>
              <h2 style={{ marginTop: 0 }}>原版 / 候選比較</h2>
              <p style={{ color: "#64748b" }}>文字相似度 {(comparison.comparison.text_similarity_ratio * 100).toFixed(1)}% · 正式資料仍保持原版</p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: "12px" }}>
                <TextCompare title="目前正式原文" text={comparison.original.raw_text} />
                <TextCompare title="重辨識候選" text={comparison.candidate.raw_text} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: "10px", marginTop: "14px" }}>
                <Summary label="原版字數" value={comparison.original.word_count} />
                <Summary label="候選字數" value={comparison.candidate.word_count} />
                <Summary label="覆蓋變化" value={`${(comparison.comparison.metric_deltas.recognized_span_ratio ?? 0) >= 0 ? "+" : ""}${(comparison.comparison.metric_deltas.recognized_span_ratio ?? 0).toFixed(3)}`} />
                <Summary label="最長空白變化" value={(comparison.comparison.metric_deltas.longest_gap_ratio ?? 0).toFixed(3)} />
              </div>
              <p style={{ marginBottom: 0, color: "#92400e", fontWeight: 700 }}>目前版本只支援比較與「不採用」。採用候選要等安全的 transcript revision / downstream rebuild 流程完成後才會開放。</p>
              {comparisonCandidate.status === "completed" ? (
                <button type="button" disabled={busy !== null} onClick={() => void reject(comparisonCandidate)} style={{ ...buttonStyle, marginTop: "14px", background: "#fee2e2", color: "#991b1b" }}>
                  {busy === `reject:${comparisonCandidate.id}` ? "處理中…" : "不採用這個候選"}
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}

function Summary({ label, value }: { label: string; value: string | number }) {
  return (
    <div style={{ background: "#f8fafc", borderRadius: "12px", padding: "12px" }}>
      <div style={{ color: "#64748b", fontSize: ".85rem", marginBottom: "4px" }}>{label}</div>
      <strong style={{ fontSize: "1.12rem" }}>{value}</strong>
    </div>
  );
}

function TextCompare({ title, text }: { title: string; text: string }) {
  return (
    <div style={{ background: "#f8fafc", borderRadius: "12px", padding: "14px" }}>
      <strong>{title}</strong>
      <div style={{ marginTop: "9px", whiteSpace: "pre-wrap", lineHeight: 1.65, maxHeight: "320px", overflow: "auto" }}>{text || "（無文字）"}</div>
    </div>
  );
}
