"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./review-admin.module.css";

type Tab = "sync" | "suggestions" | "batch" | "versions" | "publish" | "audit";

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

type SuggestionContext = {
  suggestion_id: string;
  youtube_video_id: string;
  video_title: string;
  segment_index: number;
  start_ms: number;
  end_ms: number;
  current_text: string;
  previous_text: string | null;
  previous_start_ms: number | null;
  next_text: string | null;
  next_start_ms: number | null;
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
  video_count: number;
  reviewer_count: number;
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

type SyncItem = {
  youtube_video_id: string;
  title: string;
  status: "ready" | "existing" | "no_matching_caption" | "imported" | "failed";
  caption_track_id?: string;
  caption_language?: string;
  caption_name?: string | null;
  caption_track_kind?: string;
  segment_count?: number;
  duration_ms?: number;
  note?: string;
  error?: string;
};

type SyncResponse = {
  playlist_id: string;
  apply: boolean;
  playlist_items: number;
  imported: number;
  skipped_existing: number;
  missing_caption: number;
  failed: number;
  requested_video_ids?: string[];
  missing_requested_video_ids?: string[];
  results: SyncItem[];
};

type PublishPreview = {
  version: Version;
  is_latest: boolean;
  is_already_published: boolean;
  caption_track_configured: boolean;
  caption_track_id: string | null;
  reference_version: Version | null;
  changed_segments: number;
  changed_characters: number;
  timing_policy: "fixed";
};

type AuditItem = {
  id: number;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  payload: Record<string, unknown>;
  created_at: string;
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
    import_baseline: "初始匯入",
  };
  return labels[source] ?? source;
}

function syncStatusLabel(status: SyncItem["status"]) {
  const labels: Record<SyncItem["status"], string> = {
    ready: "可匯入",
    existing: "已在共修系統",
    no_matching_caption: "找不到符合語言的字幕",
    imported: "匯入完成",
    failed: "處理失敗",
  };
  return labels[status];
}

function auditActionLabel(action: string) {
  const labels: Record<string, string> = {
    suggestion_approved: "核准字幕建議",
    suggestion_rejected: "未採用字幕建議",
    suggestion_conflict: "發現建議衝突",
    batch_created: "建立批次修正預覽",
    batch_applied: "套用批次修正",
    version_created: "建立字幕版本",
    version_restored: "還原歷史版本",
    youtube_publish_succeeded: "發布到 YouTube",
    youtube_publish_failed: "YouTube 發布失敗",
    import_baseline_created: "建立初始版本",
  };
  return labels[action] ?? action;
}

function auditSummary(item: AuditItem) {
  const payload = item.payload ?? {};
  if (item.action === "suggestion_approved") {
    return "字幕建議已核准並進入新版本。";
  }
  if (item.action === "suggestion_rejected") {
    const reason = String(payload.reason ?? "").trim();
    return reason ? `原因：${reason}` : "字幕建議未採用。";
  }
  if (item.action === "batch_created") {
    return `查找「${String(payload.find_text ?? "")}」→「${String(payload.replace_text ?? "")}」，命中 ${String(payload.match_count ?? 0)} 處。`;
  }
  if (item.action === "batch_applied") {
    return `套用 ${String(payload.applied_count ?? 0)} 處，衝突 ${String(payload.conflict_count ?? 0)} 處，略過 ${String(payload.skipped_count ?? 0)} 處。`;
  }
  if (item.action === "version_created") {
    return `建立 v${String(payload.version_number ?? "?")}，來源：${sourceLabel(String(payload.source ?? ""))}。`;
  }
  if (item.action === "version_restored") {
    return `還原後建立新版本，變更 ${String(payload.changed_segments ?? 0)} 段。`;
  }
  if (item.action === "youtube_publish_succeeded") return "YouTube 字幕發布成功。";
  if (item.action === "youtube_publish_failed") return "YouTube 字幕發布失敗，錯誤已保留供追查。";
  return `${item.entity_type} · ${item.entity_id.slice(0, 12)}`;
}

function commonDiff(before: string, after: string) {
  let prefix = 0;
  const maxPrefix = Math.min(before.length, after.length);
  while (prefix < maxPrefix && before[prefix] === after[prefix]) prefix += 1;

  let suffix = 0;
  const maxSuffix = Math.min(before.length - prefix, after.length - prefix);
  while (
    suffix < maxSuffix
    && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  ) suffix += 1;

  return {
    prefix: before.slice(0, prefix),
    removed: before.slice(prefix, before.length - suffix),
    added: after.slice(prefix, after.length - suffix),
    suffix: suffix ? before.slice(before.length - suffix) : "",
  };
}

function InlineDiff({ before, after }: { before: string; after: string }) {
  const diff = commonDiff(before, after);
  if (before === after) return <span>{before}</span>;
  return (
    <span className={styles.inlineDiff}>
      {diff.prefix}
      {diff.removed ? <del>{diff.removed}</del> : null}
      {diff.added ? <ins>{diff.added}</ins> : null}
      {diff.suffix}
    </span>
  );
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

  const [syncLimit, setSyncLimit] = useState(10);
  const [syncPreview, setSyncPreview] = useState<SyncResponse | null>(null);
  const [selectedSyncItems, setSelectedSyncItems] = useState<Set<string>>(new Set());

  const [contexts, setContexts] = useState<Record<string, SuggestionContext>>({});
  const [openContextId, setOpenContextId] = useState<string | null>(null);

  const [publishPreview, setPublishPreview] = useState<PublishPreview | null>(null);
  const [publishConfirmed, setPublishConfirmed] = useState(false);

  const [audit, setAudit] = useState<AuditItem[]>([]);
  const [auditLoaded, setAuditLoaded] = useState(false);

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

  useEffect(() => {
    if (tab !== "audit" || auditLoaded) return;
    void (async () => {
      setBusy("audit");
      try {
        const body = await jsonOrThrow(
          await fetch("/api/v1/review-admin/audit?limit=150", {
            cache: "no-store",
            credentials: "same-origin",
          }),
        );
        setAudit(body.audit ?? []);
        setAuditLoaded(true);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "目前無法讀取操作紀錄");
      } finally {
        setBusy(null);
      }
    })();
  }, [auditLoaded, tab]);

  const latestVersionByVideo = useMemo(() => {
    const result = new Map<string, string>();
    for (const version of versions) {
      if (!result.has(version.youtube_video_id)) result.set(version.youtube_video_id, version.id);
    }
    return result;
  }, [versions]);

  const versionsByVideo = useMemo(() => {
    const result = new Map<string, Version[]>();
    for (const version of versions) {
      const rows = result.get(version.youtube_video_id) ?? [];
      rows.push(version);
      result.set(version.youtube_video_id, rows);
    }
    return result;
  }, [versions]);

  async function approve(item: Suggestion) {
    if (item.conflict) return;
    if (!window.confirm(`核准這筆字幕修改？\n\n${item.original_text_snapshot}\n→\n${item.suggested_text}\n\n核准會建立新的本地字幕版本，但不會自動發布到 YouTube。`)) return;
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
      setMessage(`已核准 ${item.reviewer_name} 的修改並建立新版本；YouTube 尚未變更。`);
      await load();
      setAuditLoaded(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "核准失敗");
      await load();
    } finally {
      setBusy(null);
    }
  }

  async function reject(item: Suggestion) {
    const reason = window.prompt("未採用原因（建議填寫，師兄姐之後可以看到）：", "");
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
      setMessage(`已將 ${item.reviewer_name} 的建議標記為未採用。`);
      await load();
      setAuditLoaded(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "處理失敗");
    } finally {
      setBusy(null);
    }
  }

  async function toggleContext(item: Suggestion) {
    if (openContextId === item.id) {
      setOpenContextId(null);
      return;
    }
    setOpenContextId(item.id);
    if (contexts[item.id]) return;
    setBusy(`context:${item.id}`);
    try {
      const body = await jsonOrThrow(
        await fetch(`/api/v1/review-admin/suggestions/${item.id}/context`, {
          cache: "no-store",
          credentials: "same-origin",
        }),
      );
      setContexts((current) => ({ ...current, [item.id]: body.context }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "目前無法讀取字幕上下文");
    } finally {
      setBusy(null);
    }
  }

  async function previewSync() {
    setBusy("sync-preview");
    setError(null);
    setMessage(null);
    try {
      const body: SyncResponse = await jsonOrThrow(
        await fetch("/api/v1/review-admin/youtube/sync", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ apply: false, max_videos: syncLimit }),
        }),
      );
      setSyncPreview(body);
      setSelectedSyncItems(
        new Set(body.results.filter((item) => item.status === "ready").map((item) => item.youtube_video_id)),
      );
      setMessage(`檢查完成：找到 ${body.results.filter((item) => item.status === "ready").length} 支可匯入影片。這一步沒有下載字幕，也沒有修改資料庫。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "播放清單檢查失敗");
    } finally {
      setBusy(null);
    }
  }

  function toggleSyncItem(videoId: string) {
    setSelectedSyncItems((current) => {
      const next = new Set(current);
      if (next.has(videoId)) next.delete(videoId);
      else next.add(videoId);
      return next;
    });
  }

  async function importSelected() {
    if (!selectedSyncItems.size) {
      setError("請至少選一支可匯入的影片。");
      return;
    }
    const ids = Array.from(selectedSyncItems);
    const names = syncPreview?.results
      .filter((item) => ids.includes(item.youtube_video_id))
      .map((item) => `• ${item.title}`)
      .join("\n") ?? "";
    if (!window.confirm(`確定匯入 ${ids.length} 支影片到共修系統？\n\n${names}\n\n這會下載所選 YouTube 字幕並建立本地共修資料，但不會修改 YouTube。`)) return;

    setBusy("sync-import");
    setError(null);
    setMessage(null);
    try {
      const body: SyncResponse = await jsonOrThrow(
        await fetch("/api/v1/review-admin/youtube/sync", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            apply: true,
            max_videos: ids.length,
            youtube_video_ids: ids,
          }),
        }),
      );
      setSyncPreview(body);
      setSelectedSyncItems(new Set());
      setMessage(`匯入完成：成功 ${body.imported} 支、既有 ${body.skipped_existing} 支、失敗 ${body.failed} 支。YouTube 沒有被修改。`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "影片匯入失敗");
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
      setMessage(`找到 ${(body.items as BatchItem[]).length} 個命中位置；請逐筆確認後再套用。`);
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
    if (!window.confirm(`確定套用 ${selectedBatchItems.size} 處批次修正？\n\n「${batch.batch.find_text}」→「${batch.batch.replace_text}」\n\n這會建立新的本地字幕版本，但不會自動發布到 YouTube。`)) return;
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
        `批次修正完成：套用 ${body.applied_count} 處、衝突 ${body.conflict_count} 處、略過 ${body.skipped_count} 處。YouTube 尚未變更。`,
      );
      await load();
      setAuditLoaded(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "批次套用失敗");
    } finally {
      setBusy(null);
    }
  }

  async function restore(version: Version) {
    if (!window.confirm(`將「${version.video_title}」目前工作字幕還原成 v${version.version_number}？\n\n歷史版本不會被刪除，系統會另外建立一個新的還原版本。這個動作不會修改 YouTube。`)) return;
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
      setMessage(`已從 v${version.version_number} 還原工作字幕，建立新版本 v${body.version.version_number}。YouTube 尚未變更。`);
      await load();
      setAuditLoaded(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "版本還原失敗");
    } finally {
      setBusy(null);
    }
  }

  async function preparePublish(version: Version) {
    setBusy(`publish-preview:${version.id}`);
    setPublishConfirmed(false);
    setError(null);
    try {
      const body = await jsonOrThrow(
        await fetch(`/api/v1/review-admin/versions/${version.id}/publish-preview`, {
          cache: "no-store",
          credentials: "same-origin",
        }),
      );
      setPublishPreview(body);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "發布前檢查失敗");
    } finally {
      setBusy(null);
    }
  }

  async function publishPrepared() {
    const preview = publishPreview;
    if (!preview || !publishConfirmed) return;
    const version = preview.version;
    const warning = preview.is_latest
      ? `確定發布「${version.video_title}」v${version.version_number} 到 YouTube？`
      : `你選的是較舊的 v${version.version_number}。確定要發布這個歷史版本？這會把 YouTube 線上字幕回復到舊版本內容。`;
    if (!window.confirm(`${warning}\n\n這一步會真正覆蓋目前 YouTube 字幕軌。`)) return;

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
      setPublishPreview(null);
      setPublishConfirmed(false);
      await load();
      setAuditLoaded(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "YouTube 發布失敗");
      await load();
    } finally {
      setBusy(null);
    }
  }

  async function refreshAudit() {
    setBusy("audit");
    setError(null);
    try {
      const body = await jsonOrThrow(
        await fetch("/api/v1/review-admin/audit?limit=150", {
          cache: "no-store",
          credentials: "same-origin",
        }),
      );
      setAudit(body.audit ?? []);
      setAuditLoaded(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "目前無法讀取操作紀錄");
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
            <h1>影片同步、字幕審核、版本與發布</h1>
            <p>師兄姐只提出建議；本地字幕版本與 YouTube 線上字幕分開管理，所有正式變更都有明確確認與操作紀錄。</p>
          </div>
          <a href="/review/videos" className={styles.reviewerLink}>查看共修畫面 ↗</a>
        </header>

        {overview ? (
          <section className={styles.stats}>
            <div><strong>{overview.video_count}</strong><span>共修影片</span></div>
            <div><strong>{overview.reviewer_count}</strong><span>共修夥伴</span></div>
            <div><strong>{overview.pending_suggestions}</strong><span>待審建議</span></div>
            <div><strong>{overview.conflicting_suggestions}</strong><span>衝突待處理</span></div>
            <div><strong>{overview.version_count}</strong><span>字幕版本</span></div>
            <div><strong>{overview.published_video_count}</strong><span>YouTube 已發布</span></div>
          </section>
        ) : null}

        <nav className={styles.tabs} aria-label="字幕管理功能">
          <button className={tab === "sync" ? styles.activeTab : ""} onClick={() => setTab("sync")} type="button">影片同步</button>
          <button className={tab === "suggestions" ? styles.activeTab : ""} onClick={() => setTab("suggestions")} type="button">
            待審建議 {overview?.pending_suggestions ? `(${overview.pending_suggestions})` : ""}
          </button>
          <button className={tab === "batch" ? styles.activeTab : ""} onClick={() => setTab("batch")} type="button">批次修正</button>
          <button className={tab === "versions" ? styles.activeTab : ""} onClick={() => setTab("versions")} type="button">版本管理</button>
          <button className={tab === "publish" ? styles.activeTab : ""} onClick={() => setTab("publish")} type="button">YouTube 發布</button>
          <button className={tab === "audit" ? styles.activeTab : ""} onClick={() => setTab("audit")} type="button">操作紀錄</button>
        </nav>

        {loading ? <div className={styles.stateCard}>正在讀取字幕共修資料…</div> : null}
        {message ? <div className={styles.successCard} role="status">{message}</div> : null}
        {error ? <div className={styles.errorCard} role="alert">{error}</div> : null}

        {!loading && tab === "sync" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div>
                <h2>從 YouTube 播放清單準備共修影片</h2>
                <p>先「檢查播放清單」只讀取影片與字幕狀態；確認後再勾選要匯入的影片。匯入不會改動 YouTube。</p>
              </div>
              <div className={styles.syncControls}>
                <label>
                  <span>每次檢查</span>
                  <select value={syncLimit} onChange={(event) => setSyncLimit(Number(event.target.value))}>
                    <option value={1}>1 支</option>
                    <option value={10}>10 支</option>
                    <option value={20}>20 支</option>
                  </select>
                </label>
                <button className={styles.previewButton} disabled={busy !== null} onClick={() => void previewSync()} type="button">
                  {busy === "sync-preview" ? "檢查中…" : "檢查播放清單"}
                </button>
              </div>
            </div>

            {syncPreview ? (
              <>
                <div className={styles.syncSummary}>
                  <div><strong>{syncPreview.results.length}</strong><span>已檢查</span></div>
                  <div><strong>{syncPreview.results.filter((item) => item.status === "ready").length}</strong><span>可匯入</span></div>
                  <div><strong>{syncPreview.results.filter((item) => item.status === "existing").length}</strong><span>已存在</span></div>
                  <div><strong>{selectedSyncItems.size}</strong><span>已選取</span></div>
                  <button className={styles.applyBatchButton} disabled={busy !== null || selectedSyncItems.size === 0} onClick={() => void importSelected()} type="button">
                    {busy === "sync-import" ? "匯入中…" : `匯入選取 ${selectedSyncItems.size} 支`}
                  </button>
                </div>
                <div className={styles.syncList}>
                  {syncPreview.results.map((item) => {
                    const selectable = item.status === "ready";
                    return (
                      <label className={`${styles.syncItem} ${!selectable ? styles.disabledSyncItem : ""}`} key={item.youtube_video_id}>
                        <input
                          checked={selectedSyncItems.has(item.youtube_video_id)}
                          disabled={!selectable || busy !== null}
                          onChange={() => toggleSyncItem(item.youtube_video_id)}
                          type="checkbox"
                        />
                        <div className={styles.syncThumb}>
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={`https://i.ytimg.com/vi/${item.youtube_video_id}/mqdefault.jpg`} alt="" />
                        </div>
                        <div className={styles.syncBody}>
                          <strong>{item.title}</strong>
                          <span>{item.youtube_video_id}</span>
                          <div className={styles.syncBadges}>
                            <b className={`${styles.syncBadge} ${styles[`sync_${item.status}`]}`}>{syncStatusLabel(item.status)}</b>
                            {item.caption_language ? <b className={styles.languageBadge}>{item.caption_language}</b> : null}
                            {item.caption_track_kind ? <b className={styles.languageBadge}>{item.caption_track_kind}</b> : null}
                          </div>
                          {item.error ? <small className={styles.itemError}>{item.error}</small> : null}
                          {item.note ? <small>{item.note}</small> : null}
                        </div>
                      </label>
                    );
                  })}
                </div>
              </>
            ) : (
              <div className={styles.emptyState}>尚未檢查播放清單。建議先從 10 支開始，只讀預覽後再選擇匯入。</div>
            )}
          </section>
        ) : null}

        {!loading && tab === "suggestions" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div><h2>師兄姐提出的修改</h2><p>先看差異，需要時展開前後文與影片片段，再決定核准或未採用。</p></div>
              <button className={styles.refreshButton} onClick={() => void load()} type="button">重新整理</button>
            </div>
            {suggestions.length ? (
              <div className={styles.suggestionList}>
                {suggestions.map((item) => {
                  const context = contexts[item.id];
                  const showContext = openContextId === item.id;
                  const startSeconds = Math.max(0, Math.floor(item.start_ms / 1000) - 8);
                  return (
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

                      <div className={styles.inlineDiffBox}>
                        <span>文字差異</span>
                        <p><InlineDiff before={item.original_text_snapshot} after={item.suggested_text} /></p>
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

                      {showContext ? (
                        <div className={styles.contextCard}>
                          {context ? (
                            <>
                              <div className={styles.contextGrid}>
                                <div><span>上一句</span><p>{context.previous_text ?? "—"}</p></div>
                                <div className={styles.currentContext}><span>目前這句</span><p>{context.current_text}</p></div>
                                <div><span>下一句</span><p>{context.next_text ?? "—"}</p></div>
                              </div>
                              <div className={styles.contextMedia}>
                                <iframe
                                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                                  allowFullScreen
                                  src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(item.youtube_video_id)}?start=${startSeconds}&rel=0`}
                                  title={`播放 ${item.video_title} 建議位置`}
                                />
                                <div>
                                  <strong>從建議位置前約 8 秒開始</strong>
                                  <span>播放後可直接聽上下文，再回來核准。</span>
                                </div>
                              </div>
                            </>
                          ) : (
                            <div className={styles.stateCard}>{busy === `context:${item.id}` ? "正在載入上下文…" : "正在準備上下文…"}</div>
                          )}
                        </div>
                      ) : null}

                      <div className={styles.cardActions}>
                        <button className={styles.contextButton} disabled={busy !== null && busy !== `context:${item.id}`} onClick={() => void toggleContext(item)} type="button">
                          {showContext ? "收合影音上下文" : "查看影音上下文"}
                        </button>
                        <button className={styles.rejectButton} disabled={busy !== null} onClick={() => void reject(item)} type="button">未採用</button>
                        <button className={styles.approveButton} disabled={busy !== null || item.conflict} onClick={() => void approve(item)} type="button">
                          {busy === `approve:${item.id}` ? "核准中…" : "核准並建立版本"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className={styles.emptyState}>目前沒有待審修改。</div>
            )}
          </section>
        ) : null}

        {!loading && tab === "batch" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div><h2>跨影片批次查找／取代</h2><p>先預覽每一個命中位置，再勾選真正要套用的段落；預覽後若字幕已被修改，系統會自動排除衝突。</p></div>
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
                          <p><InlineDiff before={item.original_text_snapshot} after={item.proposed_text} /></p>
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
              <div><h2>本地字幕版本</h2><p>這裡只管理版本與本地還原。YouTube 發布已獨立到下一個分頁，避免把「本地完成」誤認為「已上線」。</p></div>
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
                          {version.publish_status === "published" ? <span className={styles.publishedBadge}>YouTube 現行</span> : null}
                          {version.publish_status === "superseded" ? <span className={styles.supersededBadge}>曾發布</span> : null}
                          {version.publish_status === "publish_failed" ? <span className={styles.failedBadge}>發布失敗</span> : null}
                        </div>
                        <div className={styles.versionMeta}>
                          <span>{sourceLabel(version.source)}</span>
                          <span>{dateTime(version.created_at)}</span>
                          <span>SHA {version.content_sha256.slice(0, 12)}</span>
                          <span>建立者 {version.created_by_actor}</span>
                        </div>
                        {version.publish_error ? <p className={styles.publishError}>{version.publish_error}</p> : null}
                      </div>
                      <div className={styles.versionActions}>
                        <button className={styles.restoreButton} disabled={busy !== null || latest} onClick={() => void restore(version)} type="button">還原成新的工作版</button>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className={styles.emptyState}>尚未建立字幕版本；核准第一筆修改後，系統會先保留 v1 初始版本，再建立新的修改版本。</div>
            )}
          </section>
        ) : null}

        {!loading && tab === "publish" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div><h2>YouTube 發布</h2><p>先做發布前檢查，再明確確認。這裡是唯一會真正更新 YouTube 字幕的操作區。</p></div>
              <button className={styles.refreshButton} onClick={() => void load()} type="button">重新整理</button>
            </div>

            <div className={styles.publishWarning}>
              <strong>⚠ 本地核准、批次修正、版本還原都不會自動發布。</strong>
              <span>只有在這裡完成「發布前檢查」並再次確認後，系統才會呼叫 YouTube caption update。</span>
            </div>

            {versions.length ? (
              <div className={styles.publishGroups}>
                {Array.from(versionsByVideo.entries()).map(([videoId, rows]) => {
                  const latestId = latestVersionByVideo.get(videoId);
                  const publishedVersion = rows.find((version) => version.publish_status === "published");
                  return (
                    <section className={styles.publishGroup} key={videoId}>
                      <div className={styles.publishGroupHeading}>
                        <div>
                          <strong>{rows[0]?.video_title}</strong>
                          <span>
                            本地最新 {rows.find((version) => version.id === latestId) ? `v${rows.find((version) => version.id === latestId)?.version_number}` : "—"}
                            ・YouTube 現行 {publishedVersion ? `v${publishedVersion.version_number}` : "尚未由系統發布"}
                          </span>
                        </div>
                      </div>
                      <div className={styles.publishVersions}>
                        {rows.map((version) => (
                          <button
                            className={`${styles.publishVersionButton} ${version.id === latestId ? styles.publishLatest : ""}`}
                            disabled={busy !== null}
                            key={version.id}
                            onClick={() => void preparePublish(version)}
                            type="button"
                          >
                            <strong>v{version.version_number}</strong>
                            <span>{sourceLabel(version.source)}</span>
                            <small>{version.id === latestId ? "最新工作版" : "歷史版本"}</small>
                          </button>
                        ))}
                      </div>
                    </section>
                  );
                })}
              </div>
            ) : (
              <div className={styles.emptyState}>尚無可發布的字幕版本。</div>
            )}

            {publishPreview ? (
              <div className={styles.publishPreflight}>
                <div className={styles.preflightHeading}>
                  <div>
                    <span>發布前檢查</span>
                    <h3>{publishPreview.version.video_title}・v{publishPreview.version.version_number}</h3>
                  </div>
                  <button onClick={() => { setPublishPreview(null); setPublishConfirmed(false); }} type="button">關閉</button>
                </div>
                {!publishPreview.is_latest ? (
                  <div className={styles.rollbackWarning}>你選的是歷史版本。發布後，YouTube 線上字幕會回到這個舊版本內容。</div>
                ) : null}
                <div className={styles.preflightGrid}>
                  <div><span>版本 SHA</span><strong>{publishPreview.version.content_sha256.slice(0, 16)}</strong></div>
                  <div><span>參考版本</span><strong>{publishPreview.reference_version ? `v${publishPreview.reference_version.version_number}` : "無"}</strong></div>
                  <div><span>變更字幕段</span><strong>{publishPreview.changed_segments}</strong></div>
                  <div><span>變更字元</span><strong>{publishPreview.changed_characters}</strong></div>
                  <div><span>時間碼</span><strong>✓ 固定不變</strong></div>
                  <div><span>字幕軌</span><strong>{publishPreview.caption_track_configured ? "✓ 已綁定" : "⚠ 未綁定"}</strong></div>
                </div>
                <label className={styles.publishConfirm}>
                  <input checked={publishConfirmed} onChange={(event) => setPublishConfirmed(event.target.checked)} type="checkbox" />
                  <span>我已確認版本、變更數與時間碼，了解這一步會覆蓋目前 YouTube 字幕。</span>
                </label>
                <button
                  className={styles.dangerPublishButton}
                  disabled={!publishConfirmed || !publishPreview.caption_track_configured || busy !== null}
                  onClick={() => void publishPrepared()}
                  type="button"
                >
                  {busy === `publish:${publishPreview.version.id}` ? "正在發布…" : publishPreview.is_latest ? "發布這個版本到 YouTube" : "發布歷史版本（線上回復）"}
                </button>
              </div>
            ) : null}
          </section>
        ) : null}

        {!loading && tab === "audit" ? (
          <section className={styles.panel}>
            <div className={styles.panelHeading}>
              <div><h2>操作紀錄</h2><p>保留核准、批次修正、版本建立／還原與 YouTube 發布等重要操作，方便日後追查。</p></div>
              <button className={styles.refreshButton} disabled={busy !== null} onClick={() => void refreshAudit()} type="button">重新整理</button>
            </div>
            {busy === "audit" && !auditLoaded ? <div className={styles.stateCard}>正在讀取操作紀錄…</div> : null}
            {audit.length ? (
              <div className={styles.auditList}>
                {audit.map((item) => (
                  <article className={styles.auditItem} key={item.id}>
                    <div className={styles.auditTime}>{dateTime(item.created_at)}</div>
                    <div className={styles.auditBody}>
                      <strong>{auditActionLabel(item.action)}</strong>
                      <span>{auditSummary(item)}</span>
                      <small>{item.actor}・{item.entity_type}・{item.entity_id.slice(0, 16)}</small>
                    </div>
                  </article>
                ))}
              </div>
            ) : auditLoaded ? (
              <div className={styles.emptyState}>目前沒有操作紀錄。</div>
            ) : null}
          </section>
        ) : null}
      </div>
    </main>
  );
}
