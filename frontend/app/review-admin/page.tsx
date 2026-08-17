"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./review-admin.module.css";

type Tab = "suggestions" | "batch" | "versions";

type Suggestion = {
  id: string;
  youtube_video_id: string;
  video_title: string;
  segment_id: number;
  segment_index: number;
  start_ms: number;
  end_ms: number;
  reviewer_name: string;
  created_at: string;
  original_text_snapshot: string;
  suggested_text: string;
  current_text: string;
  base_segment_revision: number;
  current_revision: number;
  conflict: boolean;
};

type Version = {
  id: string;
  youtube_video_id: string;
  video_title: string;
  version_number: number;
  parent_version_id: string | null;
  source: string;
  source_ref: string | null;
  content_sha256: string;
  created_by_actor: string;
  created_at: string;
  publish_status: "draft" | "published" | "superseded" | "publish_failed";
  published_at: string | null;
  youtube_caption_track_id: string | null;
  publish_error: string | null;
};

type Overview = {
  pending_suggestions: number;
  conflicting_suggestions: number;
  version_count: number;
  published_video_count: number;
};

type BatchItem = {
  id: number;
  youtube_video_id: string;
  video_title: string;
  segment_id: number;
  segment_index: number;
  start_ms: number;
  end_ms: number;
  original_text_snapshot: string;
  proposed_text: string;
  current_text: string;
  base_revision: number;
  current_revision: number;
  status: "pending" | "applied" | "conflict" | "skipped";
  conflict: boolean;
};

type BatchPreview = {
  batch: {
    id: string;
    find_text: string;
    replace_text: string;
    status: "draft" | "applied" | "cancelled";
    created_at: string;
  };
  items: BatchItem[];
};

function timecode(milliseconds: number) {
  const totalSeconds = Math.floor(Math.max(0, milliseconds) / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`
    : `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function dateTime(value: string | null) {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat("zh-TW", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    suggestion_approval: "單筆核准",
    batch_replacement: "批次修正",
    restore_version: "版本還原",
    import_baseline: "初始版本",
  };
  return labels[source] ?? source;
}

async function jsonOrThrow(response: Response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `操作失敗 (${response.status})`);
  }
  return body;
}

export default function ReviewAdminPage() {
  const [tab, setTab] = useState<Tab>("suggestions");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [batch, setBatch] = useState<BatchPreview | null>(null);
  const [selectedBatchItems, setSelectedBatchItems] = useState<Set<number>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [overviewResponse, suggestionResponse, versionsResponse] = await Promise.all([
        fetch("/api/v1/review-admin/overview", { cache: "no-store", credentials: "same-origin" }),
        fetch("/api/v1/review-admin/suggestions?status=pending", { cache: "no-store", credentials: "same-origin" }),
        fetch("/api/v1/review-admin/versions", { cache: "no-store", credentials: "same-origin" }),
      ]);
      const [overviewBody, suggestionBody, versionBody] = await Promise.all([
        jsonOrThrow(overviewResponse),
        jsonOrThrow(suggestionResponse),
        jsonOrThrow(versionsResponse),
      ]);
      setOverview(overviewBody);
      setSuggestions(suggestionBody.suggestions ?? []);
      setVersions(versionBody.versions ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "目前無法讀取管理員校訂資料");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const latestVersionByVideo = useMemo(() => {
    const result = new Map<string, string>();
    for (const version of versions) {
      if (!result.has(version.youtube_video_id)) result.set(version.youtube_video_id, version.id);
    }
    return result;
  }, [versions]);

  async function approve(item: Suggestion) {
    if (item.conflict) return;
    if (!window.confirm(`核准這筆字幕修改？\n\n${item.original_text_snapshot}\n→\n${item.suggested_text}`)) return;
    setBusy(`approve:${item.id}`);
    setMessage(null);
    setError(null);
    try {
      await jsonOrThrow(
        await fetch(`/api/v1/review-admin/suggestions/${item.id}/approve`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        }),
      );
      setMessage(`已核准 ${item.reviewer_name} 的修改，並建立新的字幕版本。`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "核准失敗");
      await load();
    } finally {
      setBusy(null);
    }
  }

  async function reject(item: Suggestion) {
    const reason = window.prompt("拒絕原因（可留空）：", "");
    if (reason === null) return;
    setBusy(`reject:${item.id}`);
    setMessage(null);
    setError(null);
    try {
      await jsonOrThrow(
        await fetch(`/api/v1/review-admin/suggestions/${item.id}/reject`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        }),
      );
      setMessage(`已拒絕 ${item.reviewer_name} 的修改建議。`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "拒絕失敗");
    } finally {
      setBusy(null);
    }
  }

  async function previewBatch() {
    if (!findText.trim()) {
      setError("請輸入要查找的文字。");
      return;
    }
    setBusy("batch-preview");
    setError(null);
    setMessage(null);
    try {
      const body = await jsonOrThrow(
        await fetch("/api/v1/review-admin/batches", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ find_text: findText, replace_text: replaceText }),
        }),
      );
      setBatch(body);
      setSelectedBatchItems(
        new Set(
          (body.items as BatchItem[])
            .filter((item) => item.status === "pending" && !item.conflict)
            .map((item) => item.id),
        ),
      );
      setMessage(`找到 ${(body.items as BatchItem[]).length} 個命中位置；請確認後再套用。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "批次查找失敗");
    } finally {
      setBusy(null);
    }
  }

  function toggleBatchItem(item: BatchItem) {
    if (item.status !== "pending" || item.conflict) return;
    setSelectedBatchItems((current) => {
      const next = new Set(current);
      if (next.has(item.id)) next.delete(item.id);
      else next.add(item.id);
      return next;
    });
  }

  async function applyBatch() {
    if (!batch || batch.batch.status !== "draft") return;
    if (!selectedBatchItems.size) {
      setError("至少選一個命中位置才能套用。");
      return;
    }
    if (!window.confirm(`確定套用 ${selectedBatchItems.size} 處批次修正？\n\n「${batch.batch.find_text}」→「${batch.batch.replace_text}」`)) return;
    setBusy("batch-apply");
    setError(null);
    setMessage(null);
    try {
      const body = await jsonOrThrow(
        await fetch(`/api/v1/review-admin/batches/${batch.batch.id}/apply`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            confirm: true,
            item_ids: Array.from(selectedBatchItems),
          }),
        }),
      );
      setBatch(body.batch);
      setSelectedBatchItems(new Set());
      setMessage(
        `批次修正完成：套用 ${body.applied_count} 處、衝突 ${body.conflict_count} 處、略過 ${body.skipped_count} 處。`,
      );
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "批次套用失敗");
    } finally {
      setBusy(null);
    }
  }

  async function publish(version: Version) {
    const latest = latestVersionByVideo.get(version.youtube_video_id) === version.id;
    const prompt = latest
      ? `發布「${version.video_title}」v${version.version_number} 到 YouTube 字幕？\n\n這會更新整條字幕軌並消耗 YouTube API quota。`
      : `你選的是較舊的 v${version.version_number}。確定要重新發布這個版本到 YouTube？\n\n這等同線上字幕 rollback。`;
    if (!window.confirm(prompt)) return;
    setBusy(`publish:${version.id}`);
    setError(null);
    setMessage(null);
    try {
      const body = await jsonOrThrow(
        await fetch(`/api/v1/review-admin/versions/${version.id}/publish`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        }),
      );
      setMessage(
        body.already_published
          ? `v${version.version_number} 已經是 YouTube 上的發布版本，沒有重複呼叫 API。`
          : `已發布「${version.video_title}」v${version.version_number} 到 YouTube。`,
      );
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "YouTube 發布失敗");
      await load();
    } finally {
      setBusy(null);
    }
  }

  async function restore(version: Version) {
    if (!window.confirm(`將「${version.video_title}」目前工作字幕還原成 v${version.version_number}？\n\n歷史版本不會被刪除，系統會建立一個新的還原版本。`)) return;
    setBusy(`restore:${version.id}`);
    setError(null);
    setMessage(null);
    try {
      const body = await jsonOrThrow(
        await fetch(`/api/v1/review-admin/versions/${version.id}/restore`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        }),
      );
      setMessage(`已從 v${version.version_number} 還原工作字幕，建立新版本 v${body.version.version_number}。`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "版本還原失敗");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>字幕共修管理</p>
            <h1>審核、批次修正、版本發布</h1>
            <p>師兄姐只提建議；所有正式文字變更與 YouTube 發布，最後都由這裡確認。</p>
          </div>
          <a href="/review/videos" className={styles.reviewerLink}>Reviewer 畫面 ↗</a>
        </header>

        {overview ? (
          <section className={styles.stats}>
            <div><strong>{overview.pending_suggestions}</strong><span>待審建議</span></div>
            <div><strong>{overview.conflicting_suggestions}</strong><span>衝突待處理</span></div>
            <div><strong>{overview.version_count}</strong><span>字幕版本</span></div>
            <div><strong>{overview.published_video_count}</strong><span>已發布影片</span></div>
          </section>
        ) : null}

        <nav className={styles.tabs} aria-label="字幕管理功能">
          <button className={tab === "suggestions" ? styles.activeTab : ""} onClick={() => setTab("suggestions")} type="button">
            待審建議 {overview?.pending_suggestions ? `(${overview.pending_suggestions})` : ""}
          </button>
          <button className={tab === "batch" ? styles.activeTab : ""} onClick={() => setTab("batch")} type="button">
            批次修正
          </button>
          <button className={tab === "versions" ? styles.activeTab : ""} onClick={() => setTab("versions")} type="button">
            版本與發布
          </button>
        </nav>

        {loading ? <div className={styles.stateCard}>正在讀取字幕校訂資料…</div> : null}
        {message ? <div className={styles.successCard} role="status">{message}</div> : null}
        {error ? <div className={styles.errorCard} role="alert">{error}</div> : null}

        {!loading && tab === "suggestions" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div><h2>師兄姐提出的修改</h2><p>衝突表示字幕在建議提出後已被改過，不能直接套用。</p></div>
              <button className={styles.refreshButton} onClick={() => void load()} type="button">重新整理</button>
            </div>
            {suggestions.length ? (
              <div className={styles.suggestionList}>
                {suggestions.map((item) => (
                  <article className={`${styles.suggestionCard} ${item.conflict ? styles.conflictCard : ""}`} key={item.id}>
                    <div className={styles.suggestionTop}>
                      <div>
                        <strong>{item.video_title}</strong>
                        <span>{timecode(item.start_ms)}・第 {item.segment_index} 段</span>
                      </div>
                      <div className={styles.reviewerMeta}>
                        <strong>{item.reviewer_name}</strong>
                        <span>{dateTime(item.created_at)}</span>
                      </div>
                    </div>
                    <div className={styles.diffGrid}>
                      <div><span>提出時原文</span><p>{item.original_text_snapshot}</p></div>
                      <div className={styles.arrow}>→</div>
                      <div><span>建議改為</span><p className={styles.proposed}>{item.suggested_text}</p></div>
                    </div>
                    {item.conflict ? (
                      <div className={styles.conflictNotice}>
                        <strong>⚠ 內容已變更，不能直接核准</strong>
                        <span>目前字幕：{item.current_text}（revision {item.current_revision}）</span>
                      </div>
                    ) : null}
                    <div className={styles.cardActions}>
                      <a href={`/review/videos/${encodeURIComponent(item.youtube_video_id)}`} target="_blank" rel="noreferrer">看影片位置 ↗</a>
                      <button className={styles.rejectButton} disabled={busy !== null} onClick={() => void reject(item)} type="button">拒絕</button>
                      <button className={styles.approveButton} disabled={busy !== null || item.conflict} onClick={() => void approve(item)} type="button">
                        {busy === `approve:${item.id}` ? "核准中…" : "核准並建立版本"}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className={styles.emptyState}>目前沒有待審修改。很清靜，像早課前五分鐘。</div>
            )}
          </section>
        ) : null}

        {!loading && tab === "batch" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div><h2>跨影片批次查找／取代</h2><p>先預覽所有命中，再勾選真正要套用的段落。</p></div>
            </div>
            <div className={styles.batchForm}>
              <label><span>查找文字</span><input value={findText} onChange={(event) => setFindText(event.target.value)} placeholder="例如：彌勒大成佛今" /></label>
              <div className={styles.replaceArrow}>→</div>
              <label><span>取代為</span><input value={replaceText} onChange={(event) => setReplaceText(event.target.value)} placeholder="例如：彌勒大成佛經" /></label>
              <button className={styles.previewButton} disabled={busy !== null} onClick={() => void previewBatch()} type="button">
                {busy === "batch-preview" ? "查找中…" : "查找並預覽"}
              </button>
            </div>

            {batch ? (
              <div className={styles.batchPreview}>
                <div className={styles.batchSummary}>
                  <div><strong>{batch.items.length}</strong><span>命中</span></div>
                  <div><strong>{selectedBatchItems.size}</strong><span>準備套用</span></div>
                  <div><strong>{batch.items.filter((item) => item.conflict || item.status === "conflict").length}</strong><span>衝突</span></div>
                  <button className={styles.applyBatchButton} disabled={busy !== null || batch.batch.status !== "draft" || selectedBatchItems.size === 0} onClick={() => void applyBatch()} type="button">
                    {busy === "batch-apply" ? "套用中…" : `套用選取 ${selectedBatchItems.size} 處`}
                  </button>
                </div>
                <div className={styles.batchItems}>
                  {batch.items.map((item) => {
                    const disabled = item.status !== "pending" || item.conflict;
                    return (
                      <label className={`${styles.batchItem} ${disabled ? styles.disabledBatchItem : ""}`} key={item.id}>
                        <input type="checkbox" checked={selectedBatchItems.has(item.id)} disabled={disabled} onChange={() => toggleBatchItem(item)} />
                        <div>
                          <strong>{item.video_title}</strong>
                          <span>{timecode(item.start_ms)}・第 {item.segment_index} 段</span>
                          <p>{item.original_text_snapshot}</p>
                          <p className={styles.proposed}>→ {item.proposed_text}</p>
                          {item.conflict ? <small>⚠ 預覽後字幕已改動，這筆會排除</small> : item.status !== "pending" ? <small>{item.status}</small> : null}
                        </div>
                      </label>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </section>
        ) : null}

        {!loading && tab === "versions" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div><h2>版本歷史與 YouTube 發布</h2><p>發布只認版本 ID；較舊版本也可以重新發布，作為線上 rollback。</p></div>
              <button className={styles.refreshButton} onClick={() => void load()} type="button">重新整理</button>
            </div>
            {versions.length ? (
              <div className={styles.versionList}>
                {versions.map((version) => {
                  const latest = latestVersionByVideo.get(version.youtube_video_id) === version.id;
                  return (
                    <article className={styles.versionCard} key={version.id}>
                      <div className={styles.versionMain}>
                        <div className={styles.versionTitleRow}>
                          <strong>{version.video_title}</strong>
                          <span className={styles.versionNumber}>v{version.version_number}</span>
                          {latest ? <span className={styles.latestBadge}>目前工作版</span> : null}
                          {version.publish_status === "published" ? <span className={styles.publishedBadge}>YouTube 已發布</span> : null}
                          {version.publish_status === "publish_failed" ? <span className={styles.failedBadge}>發布失敗</span> : null}
                        </div>
                        <div className={styles.versionMeta}>
                          <span>{sourceLabel(version.source)}</span>
                          <span>{dateTime(version.created_at)}</span>
                          <span>SHA {version.content_sha256.slice(0, 10)}</span>
                        </div>
                        {version.publish_error ? <p className={styles.publishError}>{version.publish_error}</p> : null}
                      </div>
                      <div className={styles.versionActions}>
                        <button className={styles.restoreButton} disabled={busy !== null || latest} onClick={() => void restore(version)} type="button">還原成工作版</button>
                        <button className={styles.publishButton} disabled={busy !== null} onClick={() => void publish(version)} type="button">
                          {busy === `publish:${version.id}` ? "發布中…" : version.publish_status === "published" ? "已發布" : latest ? "發布 YouTube" : "重新發布此版"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className={styles.emptyState}>尚未建立任何審核版本；核准第一筆建議後就會出現。</div>
            )}
          </section>
        ) : null}
      </div>
    </main>
  );
}
