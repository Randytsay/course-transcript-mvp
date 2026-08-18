"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./video.module.css";

type SuggestionStatus = "pending" | "approved" | "rejected" | "withdrawn" | null;

type Segment = {
  id: number;
  segment_index: number;
  start_ms: number;
  end_ms: number;
  working_text: string;
  revision: number;
  my_suggestion_id: string | null;
  my_suggested_text: string | null;
  my_changed_chars: number | null;
  my_suggestion_updated_at: string | null;
  my_suggestion_status: SuggestionStatus;
  my_suggestion_reviewed_at: string | null;
  my_suggestion_withdrawn: number;
};

type VideoDetail = {
  video: {
    youtube_video_id: string;
    title: string;
    duration_ms: number | null;
  };
  segments: Segment[];
  progress: {
    last_playback_ms: number;
    reviewed_until_ms: number;
    last_segment_index: number | null;
    completed: number;
  } | null;
  active_editors: Array<{
    user_id: string;
    display_name: string;
    avatar_url: string | null;
    expires_at: string;
  }>;
  contributors: Array<{
    user_id: string;
    display_name: string;
    avatar_url: string | null;
    suggestions_sent: number;
    approved_suggestions: number;
    last_contributed_at: string;
  }>;
  max_editors: number;
};

type MeResponse = {
  user: { id: string; display_name: string };
  csrf_token: string;
};

type LeaseResponse = {
  lease_token: string;
  expires_at: string;
  heartbeat_after_seconds: number;
  max_editors: number;
};

type PlayerInstance = {
  getCurrentTime: () => number;
  getPlayerState?: () => number;
  pauseVideo?: () => void;
  playVideo?: () => void;
  seekTo: (seconds: number, allowSeekAhead: boolean) => void;
  destroy: () => void;
};

let youtubeApiPromise: Promise<void> | null = null;

function loadYouTubeApi() {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.YT?.Player) return Promise.resolve();
  if (youtubeApiPromise) return youtubeApiPromise;
  youtubeApiPromise = new Promise<void>((resolve) => {
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      resolve();
    };
    if (!document.getElementById("youtube-iframe-api")) {
      const script = document.createElement("script");
      script.id = "youtube-iframe-api";
      script.src = "https://www.youtube.com/iframe_api";
      script.async = true;
      document.head.appendChild(script);
    }
  });
  return youtubeApiPromise;
}

function timestamp(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`
    : `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function percent(value: number, total: number | null) {
  if (!total || total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((value / total) * 100)));
}

function suggestionLabel(status: SuggestionStatus) {
  if (status === "pending") return "待審核";
  if (status === "approved") return "已採用";
  if (status === "rejected") return "未採用";
  if (status === "withdrawn") return "已撤回";
  return null;
}

export default function ReviewVideoPage() {
  const params = useParams<{ videoId: string }>();
  const router = useRouter();
  const videoId = params.videoId;
  const draftStorageKey = `review-drafts:${videoId}`;
  const playerHostRef = useRef<HTMLDivElement | null>(null);
  const playerRef = useRef<PlayerInstance | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [detail, setDetail] = useState<VideoDetail | null>(null);
  const [lease, setLease] = useState<LeaseResponse | null>(null);
  const [activeSegmentId, setActiveSegmentId] = useState<number | null>(null);
  const [selectedSegmentId, setSelectedSegmentId] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [unsavedIds, setUnsavedIds] = useState<Set<number>>(new Set());
  const [savingSegmentId, setSavingSegmentId] = useState<number | null>(null);
  const [progressBusy, setProgressBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentMs, setCurrentMs] = useState(0);
  const [followPlayback, setFollowPlayback] = useState(true);
  const [batchFindText, setBatchFindText] = useState("");
  const [batchReplaceText, setBatchReplaceText] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);

  const mutationHeaders = useCallback(
    (extra: Record<string, string> = {}) => ({
      "Content-Type": "application/json",
      ...(me?.csrf_token ? { "X-Review-CSRF": me.csrf_token } : {}),
      ...extra,
    }),
    [me?.csrf_token],
  );

  const loadStoredDrafts = useCallback((): Record<number, string> => {
    if (typeof window === "undefined") return {};
    try {
      const parsed = JSON.parse(window.localStorage.getItem(draftStorageKey) || "{}");
      if (!parsed || typeof parsed !== "object") return {};
      return Object.fromEntries(
        Object.entries(parsed)
          .filter(([, value]) => typeof value === "string")
          .map(([key, value]) => [Number(key), String(value)]),
      );
    } catch {
      return {};
    }
  }, [draftStorageKey]);

  const persistStoredDrafts = useCallback((values: Record<number, string>) => {
    if (typeof window === "undefined") return;
    if (!Object.keys(values).length) {
      window.localStorage.removeItem(draftStorageKey);
      return;
    }
    window.localStorage.setItem(draftStorageKey, JSON.stringify(values));
  }, [draftStorageKey]);

  const loadDetail = useCallback(async () => {
    const response = await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}`, {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (response.status === 401) {
      router.replace("/review");
      return null;
    }
    if (!response.ok) throw new Error("目前無法讀取這支影片的字幕");
    const next: VideoDetail = await response.json();
    const stored = loadStoredDrafts();
    const baseDrafts = Object.fromEntries(
      next.segments.map((segment) => [
        segment.id,
        segment.my_suggestion_status === "pending" && segment.my_suggested_text
          ? segment.my_suggested_text
          : segment.working_text,
      ]),
    );
    const validStored = Object.fromEntries(
      Object.entries(stored).filter(([id]) => next.segments.some((segment) => segment.id === Number(id))),
    );
    setDetail(next);
    setDrafts({ ...baseDrafts, ...validStored });
    setUnsavedIds(new Set(Object.keys(validStored).map(Number)));
    return next;
  }, [loadStoredDrafts, router, videoId]);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const meResponse = await fetch("/api/v1/review/auth/me", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (meResponse.status === 401) {
          router.replace("/review");
          return;
        }
        if (!meResponse.ok) throw new Error("目前無法確認登入狀態");
        const user: MeResponse = await meResponse.json();
        if (!cancelled) setMe(user);
        const loaded = await loadDetail();
        if (loaded?.progress?.last_playback_ms) {
          setCurrentMs(loaded.progress.last_playback_ms);
        }
      } catch (caught) {
        if (!cancelled) {
          setMessage(caught instanceof Error ? caught.message : "載入失敗");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadDetail, router]);

  useEffect(() => {
    if (!unsavedIds.size) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [unsavedIds.size]);

  useEffect(() => {
    if (!detail || !playerHostRef.current) return;
    let disposed = false;
    void loadYouTubeApi().then(() => {
      if (disposed || !window.YT?.Player || !playerHostRef.current) return;
      const host = playerHostRef.current;
      playerRef.current = new window.YT.Player(host, {
        videoId: detail.video.youtube_video_id,
        playerVars: { playsinline: 1, rel: 0 },
        events: {
          onReady: ({ target }) => {
            const resumeSeconds = (detail.progress?.last_playback_ms ?? 0) / 1000;
            if (resumeSeconds > 0) target.seekTo(resumeSeconds, true);
          },
        },
      });
    });
    return () => {
      disposed = true;
      playerRef.current?.destroy();
      playerRef.current = null;
    };
  }, [detail?.video.youtube_video_id]);

  useEffect(() => {
    if (!detail) return;
    const timer = window.setInterval(() => {
      const player = playerRef.current;
      if (!player) return;
      const milliseconds = Math.round(player.getCurrentTime() * 1000);
      setCurrentMs(milliseconds);
      const active = detail.segments.find(
        (segment) => milliseconds >= segment.start_ms && milliseconds < segment.end_ms,
      );
      if (active && active.id !== activeSegmentId) {
        setActiveSegmentId(active.id);
        if (followPlayback && selectedSegmentId === null) {
          document.getElementById(`review-segment-${active.id}`)?.scrollIntoView({
            block: "nearest",
            behavior: "smooth",
          });
        }
      }
    }, 300);
    return () => window.clearInterval(timer);
  }, [activeSegmentId, detail, followPlayback, selectedSegmentId]);

  useEffect(() => {
    if (!me || !detail) return;
    const timer = window.setInterval(() => {
      const player = playerRef.current;
      if (!player) return;
      const milliseconds = Math.max(0, Math.round(player.getCurrentTime() * 1000));
      void fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/progress`, {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(),
        body: JSON.stringify({ last_playback_ms: milliseconds }),
      });
    }, 10000);
    return () => window.clearInterval(timer);
  }, [detail, me, mutationHeaders, videoId]);

  useEffect(() => {
    if (!lease || !me) return;
    const intervalMs = lease.heartbeat_after_seconds * 1000;
    const timer = window.setInterval(async () => {
      const response = await fetch(
        `/api/v1/review/videos/${encodeURIComponent(videoId)}/lease/heartbeat`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: mutationHeaders(),
          body: JSON.stringify({ lease_token: lease.lease_token }),
        },
      );
      if (!response.ok) {
        setLease(null);
        setSelectedSegmentId(null);
        setMessage("本次校訂已暫停；你仍可繼續觀看，重新按「開始校訂」後就能接著修改。");
        return;
      }
      const heartbeat = await response.json();
      setLease((current) => current ? { ...current, ...heartbeat } : current);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [lease?.lease_token, lease?.heartbeat_after_seconds, me, mutationHeaders, videoId]);

  const selected = useMemo(
    () => detail?.segments.find((segment) => segment.id === selectedSegmentId) ?? null,
    [detail, selectedSegmentId],
  );

  const activeSegment = useMemo(
    () => detail?.segments.find((segment) => segment.id === activeSegmentId) ?? null,
    [activeSegmentId, detail],
  );

  const batchMatches = useMemo(() => {
    if (!detail || !batchFindText.trim() || !batchReplaceText.trim()) return [];
    return detail.segments.flatMap((segment) => {
      const source = segment.my_suggestion_status === "pending" && segment.my_suggested_text
        ? segment.my_suggested_text
        : segment.working_text;
      if (!source.includes(batchFindText)) return [];
      const replacement = source.split(batchFindText).join(batchReplaceText);
      return replacement === source
        ? []
        : [{ segmentId: segment.id, segmentIndex: segment.segment_index, source, replacement }];
    });
  }, [batchFindText, batchReplaceText, detail]);

  const reviewPercent = detail?.progress?.completed
    ? 100
    : percent(detail?.progress?.reviewed_until_ms ?? 0, detail?.video.duration_ms ?? null);

  const contributors = useMemo(() => {
    if (!detail) return [];
    const people = new Map<string, {
      user_id: string;
      display_name: string;
      avatar_url: string | null;
      suggestions_sent: number;
      approved_suggestions: number;
      is_active: boolean;
    }>();
    detail.contributors.forEach((person) => {
      people.set(person.user_id, { ...person, is_active: false });
    });
    detail.active_editors.forEach((person) => {
      const existing = people.get(person.user_id);
      people.set(person.user_id, {
        user_id: person.user_id,
        display_name: person.display_name,
        avatar_url: person.avatar_url,
        suggestions_sent: existing?.suggestions_sent ?? 0,
        approved_suggestions: existing?.approved_suggestions ?? 0,
        is_active: true,
      });
    });
    return Array.from(people.values()).sort(
      (left, right) => Number(right.is_active) - Number(left.is_active)
        || right.suggestions_sent - left.suggestions_sent
        || left.display_name.localeCompare(right.display_name, "zh-Hant"),
    );
  }, [detail]);

  function updateDraft(segment: Segment, value: string) {
    setDrafts((current) => ({ ...current, [segment.id]: value }));
    const base = segment.my_suggestion_status === "pending" && segment.my_suggested_text
      ? segment.my_suggested_text
      : segment.working_text;
    const stored = loadStoredDrafts();
    if (value === base) {
      delete stored[segment.id];
      setUnsavedIds((current) => {
        const next = new Set(current);
        next.delete(segment.id);
        return next;
      });
    } else {
      stored[segment.id] = value;
      setUnsavedIds((current) => new Set(current).add(segment.id));
    }
    persistStoredDrafts(stored);
  }

  function clearStoredDraft(segmentId: number) {
    const stored = loadStoredDrafts();
    delete stored[segmentId];
    persistStoredDrafts(stored);
    setUnsavedIds((current) => {
      const next = new Set(current);
      next.delete(segmentId);
      return next;
    });
  }

  async function startEditing() {
    if (!me) return;
    setMessage(null);
    const response = await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/lease`, {
      method: "POST",
      credentials: "same-origin",
      headers: mutationHeaders(),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setMessage(
        response.status === 409
          ? `目前已有 ${detail?.max_editors ?? 2} 位師兄姐正在校訂這支影片。你仍可先觀看，稍後再按「開始校訂」。`
          : body.detail || "目前暫時無法開始校訂",
      );
      await loadDetail();
      return;
    }
    setLease(body);
    pausePlayback();
    setMessage("已進入校訂模式。直接點字幕文字即可編輯；點時間可跳到該段。共同錯字可用字幕上方的搜尋與取代。");
    await loadDetail();
  }

  async function stopEditing() {
    if (!lease) return;
    await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/lease/release`, {
      method: "POST",
      credentials: "same-origin",
      headers: mutationHeaders(),
      body: JSON.stringify({ lease_token: lease.lease_token }),
    });
    setLease(null);
    setSelectedSegmentId(null);
    setMessage("已結束本次校訂。觀看進度與尚未送出的草稿都會保留。");
    await loadDetail();
  }

  function seek(segment: Segment) {
    playerRef.current?.seekTo(segment.start_ms / 1000, true);
    setActiveSegmentId(segment.id);
  }

  function pausePlayback() {
    playerRef.current?.pauseVideo?.();
  }

  function resumePlayback() {
    playerRef.current?.playVideo?.();
  }

  function editSegment(segment: Segment) {
    pausePlayback();
    setSelectedSegmentId(segment.id);
    setFollowPlayback(false);
    seek(segment);
    window.requestAnimationFrame(() => {
      document.getElementById(`review-editor-${segment.id}`)?.focus({ preventScroll: true });
    });
  }

  function handleSegmentTextClick(segment: Segment) {
    if (lease) {
      editSegment(segment);
      return;
    }
    seek(segment);
  }

  async function saveReviewProgress(reviewedUntilMs: number, completed: boolean) {
    if (!detail || !me) return;
    const bounded = Math.max(0, Math.min(reviewedUntilMs, detail.video.duration_ms ?? reviewedUntilMs));
    const last = [...detail.segments]
      .reverse()
      .find((segment) => segment.start_ms <= bounded) ?? detail.segments[0];
    setProgressBusy(true);
    try {
      const response = await fetch(`/api/v1/review/videos/${encodeURIComponent(videoId)}/progress`, {
        method: "POST",
        credentials: "same-origin",
        headers: mutationHeaders(),
        body: JSON.stringify({
          last_playback_ms: Math.max(0, currentMs),
          reviewed_until_ms: bounded,
          last_segment_index: last?.segment_index ?? null,
          completed,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "校閱進度儲存失敗");
      setMessage(completed ? "感謝共修，這支影片已標記為完成校閱。" : `已記錄：校閱到 ${timestamp(bounded)}。`);
      await loadDetail();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "校閱進度儲存失敗");
    } finally {
      setProgressBusy(false);
    }
  }

  async function markReviewedHere() {
    const target = activeSegment?.end_ms ?? currentMs;
    if (target <= (detail?.progress?.reviewed_until_ms ?? 0)) {
      setMessage("這個位置已經在你的校閱進度內。");
      return;
    }
    await saveReviewProgress(target, false);
  }

  async function completeReview() {
    if (!detail || detail.progress?.completed) return;
    if (!window.confirm("確認你已經完整校閱這支影片？\n\n完成後仍可繼續觀看與提出修改建議。")) return;
    const endMs = detail.video.duration_ms ?? detail.segments.at(-1)?.end_ms ?? currentMs;
    await saveReviewProgress(endMs, true);
  }

  async function reopenReview() {
    if (!detail || !detail.progress?.completed || progressBusy) return;
    if (!window.confirm("要重新開放這支影片的校閱狀態嗎？\n\n已記錄的播放位置與校閱進度會保留。")) return;
    setProgressBusy(true);
    try {
      const response = await fetch(
        `/api/v1/review/videos/${encodeURIComponent(videoId)}/progress/completion`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: mutationHeaders(),
          body: JSON.stringify({ completed: false }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "校閱狀態更新失敗");
      setMessage("已重新開放校閱；原本的播放位置與校閱進度都已保留。");
      await loadDetail();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "校閱狀態更新失敗");
    } finally {
      setProgressBusy(false);
    }
  }

  async function saveSuggestion(segment: Segment) {
    if (!lease) return;
    const text = (drafts[segment.id] ?? "").trim();
    const currentPending = segment.my_suggestion_status === "pending";
    if (!text || (text === segment.working_text && !currentPending)) {
      setMessage("請先修改文字，再送出建議。");
      return;
    }
    setSavingSegmentId(segment.id);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/v1/review/videos/${encodeURIComponent(videoId)}/segments/${segment.id}/suggestion`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: mutationHeaders({ "X-Review-Lease": lease.lease_token }),
          body: JSON.stringify({ text }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "修改建議送出失敗");
      clearStoredDraft(segment.id);
      setMessage(body.created ? "修改建議已送出，等待管理員審核。" : "修改建議已更新。" );
      await loadDetail();
      setSelectedSegmentId(null);
      setFollowPlayback(true);
      resumePlayback();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "修改建議送出失敗");
    } finally {
      setSavingSegmentId(null);
    }
  }

  async function batchReplaceSuggestions() {
    if (!lease || batchBusy) return;
    if (!batchFindText.trim() || !batchReplaceText.trim()) {
      setMessage("請輸入要尋找的文字與正確文字。");
      return;
    }
    if (unsavedIds.size) {
      setMessage("請先送出或取消個別草稿，再使用批次搜尋與取代，避免覆蓋尚未送出的修改。");
      return;
    }
    if (!batchMatches.length) {
      setMessage("目前找不到可套用的字幕文字。");
      return;
    }
    const findText = batchFindText;
    const replaceText = batchReplaceText;
    if (!window.confirm(
      `確認把「${findText}」取代為「${replaceText}」？\n\n會在這支影片建立 ${batchMatches.length} 段待審核修改建議，正式字幕不會立即改變。`,
    )) return;
    setBatchBusy(true);
    setMessage(null);
    pausePlayback();
    try {
      const response = await fetch(
        `/api/v1/review/videos/${encodeURIComponent(videoId)}/batch-suggestion`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: mutationHeaders({ "X-Review-Lease": lease.lease_token }),
          body: JSON.stringify({ find_text: findText, replace_text: replaceText }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "批次修改建議送出失敗");
      const batch = body.batch ?? {};
      setBatchFindText("");
      setBatchReplaceText("");
      setSelectedSegmentId(null);
      setFollowPlayback(true);
      setMessage(
        `已建立 ${batch.matched_count ?? batchMatches.length} 段待審核建議。${batch.revised_count ? `其中 ${batch.revised_count} 段是更新原有建議。` : ""}`,
      );
      await loadDetail();
      resumePlayback();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "批次修改建議送出失敗");
      resumePlayback();
    } finally {
      setBatchBusy(false);
    }
  }

  async function withdrawSuggestion(segment: Segment) {
    if (!segment.my_suggestion_id || segment.my_suggestion_status !== "pending") return;
    if (!window.confirm("撤回這筆尚未審核的修改建議？")) return;
    setSavingSegmentId(segment.id);
    try {
      const response = await fetch(
        `/api/v1/review/suggestions/${encodeURIComponent(segment.my_suggestion_id)}/withdraw`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: mutationHeaders(),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "撤回失敗");
      clearStoredDraft(segment.id);
      setMessage("這筆修改建議已撤回。");
      await loadDetail();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "撤回失敗");
    } finally {
      setSavingSegmentId(null);
    }
  }

  if (loading) {
    return <main className={styles.loading}>正在載入影片與字幕…</main>;
  }

  if (!detail) {
    return (
      <main className={styles.loading}>
        <p>{message ?? "找不到影片"}</p>
        <Link href="/review/videos">返回影片清單</Link>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={styles.topbar}>
        <Link href="/review/videos" className={styles.backLink}>← 影片清單</Link>
        <div className={styles.titleBlock}>
          <p>佛學字幕共修</p>
          <h1>{detail.video.title}</h1>
        </div>
        {lease ? (
          <button className={styles.stopButton} onClick={() => void stopEditing()} type="button">
            結束校訂
          </button>
        ) : (
          <button className={styles.startButton} onClick={() => void startEditing()} type="button">
            開始校訂
          </button>
        )}
      </header>

      {message ? <div className={styles.message} role="status">{message}</div> : null}

      <div className={styles.workspace}>
        <section className={styles.playerColumn}>
          <div className={styles.playerFrame}>
            <div ref={playerHostRef} className={styles.playerHost} />
          </div>

          <div className={styles.sessionCard}>
            <div>
              <span className={styles.sessionLabel}>目前時間</span>
              <strong>{timestamp(currentMs)}</strong>
            </div>
            <div>
              <span className={styles.sessionLabel}>我的校閱</span>
              <strong>{reviewPercent}%</strong>
            </div>
            <div>
              <span className={styles.sessionLabel}>正在校訂</span>
              <strong>{detail.active_editors.length} 人</strong>
            </div>
            <div className={styles.editorNames}>
              {detail.active_editors.length ? (
                detail.active_editors.map((editor) => <span key={editor.user_id}>{editor.display_name}</span>)
              ) : (
                <span>目前沒有人在修改</span>
              )}
            </div>
          </div>

          <section className={styles.contributorCard} aria-label="本片共修夥伴">
            <div className={styles.contributorHeading}>
              <div>
                <span className={styles.contributorEyebrow}>一起把字幕校準</span>
                <strong>本片共修夥伴</strong>
              </div>
              <span className={styles.contributorCount}>{contributors.length} 位</span>
            </div>
            {contributors.length ? (
              <div className={styles.contributorList}>
                {contributors.map((person) => (
                  <div className={styles.contributor} key={person.user_id}>
                    {person.avatar_url ? (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <img src={person.avatar_url} alt="" className={styles.contributorAvatar} />
                    ) : (
                      <span className={styles.contributorAvatarFallback} aria-hidden="true">
                        {person.display_name.slice(0, 1)}
                      </span>
                    )}
                    <span className={styles.contributorCopy}>
                      <strong>{person.display_name}</strong>
                      <small>{person.is_active ? "現在正在校訂" : `已貢獻 ${person.suggestions_sent} 處字幕`}</small>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className={styles.contributorEmpty}>還沒有共修夥伴，歡迎從這支影片開始。</p>
            )}
            <p className={styles.contributorNote}>送出字幕修改建議後，就會出現在這裡；頭像只用來辨識共修夥伴。</p>
          </section>

          <div className={styles.reviewActions}>
            <button disabled={progressBusy || Boolean(detail.progress?.completed)} onClick={() => void markReviewedHere()} type="button">
              ✓ 我已校閱到這裡
            </button>
            {detail.progress?.completed ? (
              <button className={styles.reopenButton} disabled={progressBusy} onClick={() => void reopenReview()} type="button">
                ↶ 重新開放校閱
              </button>
            ) : (
              <button className={styles.completeButton} disabled={progressBusy} onClick={() => void completeReview()} type="button">
                標記為完成本片校閱
              </button>
            )}
          </div>

          <p className={styles.helperText}>
            {detail.progress?.completed
              ? "目前狀態：已完成校閱。若發現漏看或想再檢查，可重新開放校閱；原本進度會保留。"
              : "觀看不限人數。進入校訂模式後，直接點字幕文字即可編輯；送出建議後影片會接續播放。"}
          </p>
        </section>

        <section className={styles.subtitlePanel} aria-label="同步字幕">
          <div className={styles.subtitleHeader}>
            <div className={styles.subtitleHeading}>
              <div className={styles.subtitleTitle}>
                <span>同步字幕</span>
                <strong>{detail.segments.length} 段</strong>
              </div>
              <div className={styles.interactionHint} role="note">
                <span><b>時間碼</b> 跳到該段</span>
                <span><b>字幕文字</b> {lease ? "點擊編輯" : "開始校訂後可編輯"}</span>
              </div>
            </div>
            <div className={styles.subtitleTools}>
              <button
                className={styles.followButton}
                onClick={() => setFollowPlayback((value) => !value)}
                type="button"
              >
                {followPlayback ? "📍 跟隨播放" : "跟隨播放：關"}
              </button>
              <span className={lease ? styles.editingBadge : styles.readonlyBadge}>
                {lease ? "校訂模式" : "觀看模式"}
              </span>
            </div>
          </div>
          {unsavedIds.size ? (
            <div className={styles.draftNotice}>已自動保存 {unsavedIds.size} 筆尚未送出的草稿。</div>
          ) : null}
          {lease ? (
            <form
              className={styles.replacePanel}
              onSubmit={(event) => {
                event.preventDefault();
                void batchReplaceSuggestions();
              }}
            >
              <div className={styles.replacePanelHeading}>
                <div>
                  <span className={styles.replaceEyebrow}>共同錯字</span>
                  <strong>搜尋與取代</strong>
                </div>
                <span className={styles.replaceSafety}>只建立待審核建議</span>
              </div>
              <p className={styles.replaceDescription}>找出這支影片中重複出現的錯字，一次送出多段修改；正式字幕仍由管理員審核。</p>
              <div className={styles.replaceFields}>
                <label>
                  <span>尋找</span>
                  <input
                    aria-label="批次尋找文字"
                    autoComplete="off"
                    onChange={(event) => setBatchFindText(event.target.value)}
                    placeholder="例如：彌勒大成佛今"
                    type="text"
                    value={batchFindText}
                  />
                </label>
                <span className={styles.replaceArrow} aria-hidden="true">→</span>
                <label>
                  <span>取代為</span>
                  <input
                    aria-label="批次取代文字"
                    autoComplete="off"
                    onChange={(event) => setBatchReplaceText(event.target.value)}
                    placeholder="正確文字"
                    type="text"
                    value={batchReplaceText}
                  />
                </label>
              </div>
              <div className={styles.replaceSummary} aria-live="polite">
                {!batchFindText.trim() || !batchReplaceText.trim()
                  ? "輸入兩欄文字後，這裡會顯示預計修改的段落數。"
                  : batchMatches.length
                    ? <>找到 <strong>{batchMatches.length}</strong> 段，送出後會各自成為待審核建議。</>
                    : "目前找不到可套用的字幕文字。"}
              </div>
              {unsavedIds.size ? <p className={styles.replaceWarning}>請先處理上方尚未送出的個別草稿。</p> : null}
              <button
                className={styles.replaceButton}
                disabled={batchBusy || Boolean(unsavedIds.size) || !batchMatches.length}
                type="submit"
              >
                {batchBusy ? "建立中…" : `建立 ${batchMatches.length} 段待審核建議`}
              </button>
            </form>
          ) : null}
          <div className={styles.segmentList}>
            {detail.segments.map((segment) => {
              const active = segment.id === activeSegmentId;
              const selectedForEdit = segment.id === selectedSegmentId;
              const status = segment.my_suggestion_withdrawn ? "withdrawn" : segment.my_suggestion_status;
              const statusText = suggestionLabel(status);
              const hasPendingSuggestion = status === "pending" && Boolean(segment.my_suggestion_id);
              const shownText = hasPendingSuggestion && segment.my_suggested_text
                ? segment.my_suggested_text
                : segment.working_text;
              return (
                <article
                  className={`${styles.segment} ${active ? styles.activeSegment : ""} ${selectedForEdit ? styles.segmentEditing : ""}`}
                  id={`review-segment-${segment.id}`}
                  key={segment.id}
                >
                  <button className={styles.timeButton} onClick={() => seek(segment)} type="button">
                    {timestamp(segment.start_ms)}
                  </button>
                  <div className={styles.segmentBody}>
                    {selectedForEdit && lease ? (
                      <>
                        <p className={styles.originalHint}>目前字幕：{segment.working_text}</p>
                        <textarea
                          autoFocus
                          aria-label={`第 ${segment.segment_index} 段字幕修改`}
                          id={`review-editor-${segment.id}`}
                          value={drafts[segment.id] ?? ""}
                          onChange={(event) => updateDraft(segment, event.target.value)}
                          rows={3}
                        />
                        <div className={styles.editActions}>
                          <button
                            className={styles.saveButton}
                            disabled={savingSegmentId === segment.id}
                            onClick={() => void saveSuggestion(segment)}
                            type="button"
                          >
                            {savingSegmentId === segment.id ? "送出中…" : hasPendingSuggestion ? "更新建議" : "送出修改"}
                          </button>
                          <button
                            className={styles.cancelButton}
                            onClick={() => {
                              setSelectedSegmentId(null);
                              setFollowPlayback(true);
                            }}
                            type="button"
                          >
                            先保留草稿
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <button
                          aria-label={lease ? `編輯第 ${segment.segment_index} 段字幕` : `跳到第 ${segment.segment_index} 段字幕`}
                          className={styles.segmentTextButton}
                          onClick={() => handleSegmentTextClick(segment)}
                          title={lease ? "點擊編輯這段字幕" : "觀看模式：點擊跳到這段；開始校訂後可編輯"}
                          type="button"
                        >
                          {shownText}
                        </button>
                        <div className={styles.segmentMeta}>
                          <span>
                            {statusText ? (
                              <b className={`${styles.suggestionStatus} ${styles[`status_${status}`] ?? ""}`}>{statusText}</b>
                            ) : `第 ${segment.segment_index} 段`}
                          </span>
                          <div className={styles.segmentButtons}>
                            {hasPendingSuggestion ? (
                              <button disabled={savingSegmentId === segment.id} onClick={() => void withdrawSuggestion(segment)} type="button">
                                撤回
                              </button>
                            ) : null}
                            {lease ? (
                              <button
                                onClick={() => editSegment(segment)}
                                type="button"
                              >
                                {hasPendingSuggestion ? "調整建議" : "修改"}
                              </button>
                            ) : null}
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </div>
    </main>
  );
}
