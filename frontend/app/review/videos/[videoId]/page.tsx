"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./video.module.css";

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
  seekTo: (seconds: number, allowSeekAhead: boolean) => void;
  destroy: () => void;
};

type YouTubeNamespace = {
  Player: new (
    element: HTMLElement,
    config: {
      videoId: string;
      playerVars?: Record<string, number>;
      events?: { onReady?: (event: { target: PlayerInstance }) => void };
    },
  ) => PlayerInstance;
};

declare global {
  interface Window {
    YT?: YouTubeNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

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

export default function ReviewVideoPage() {
  const params = useParams<{ videoId: string }>();
  const router = useRouter();
  const videoId = params.videoId;
  const playerHostRef = useRef<HTMLDivElement | null>(null);
  const playerRef = useRef<PlayerInstance | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [detail, setDetail] = useState<VideoDetail | null>(null);
  const [lease, setLease] = useState<LeaseResponse | null>(null);
  const [activeSegmentId, setActiveSegmentId] = useState<number | null>(null);
  const [selectedSegmentId, setSelectedSegmentId] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [savingSegmentId, setSavingSegmentId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentMs, setCurrentMs] = useState(0);

  const mutationHeaders = useCallback(
    (extra: Record<string, string> = {}) => ({
      "Content-Type": "application/json",
      ...(me?.csrf_token ? { "X-Review-CSRF": me.csrf_token } : {}),
      ...extra,
    }),
    [me?.csrf_token],
  );

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
    setDetail(next);
    setDrafts(
      Object.fromEntries(
        next.segments.map((segment) => [
          segment.id,
          segment.my_suggested_text ?? segment.working_text,
        ]),
      ),
    );
    return next;
  }, [router, videoId]);

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
        document.getElementById(`review-segment-${active.id}`)?.scrollIntoView({
          block: "nearest",
          behavior: "smooth",
        });
      }
    }, 300);
    return () => window.clearInterval(timer);
  }, [activeSegmentId, detail]);

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
        setMessage("校訂席位已失效；目前仍可觀看，重新取得席位後可繼續修改。");
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
          ? `目前已有 ${detail?.max_editors ?? 2} 位師兄姐正在校訂。你仍可先觀看，稍後再取得席位。`
          : body.detail || "目前無法取得校訂席位",
      );
      await loadDetail();
      return;
    }
    setLease(body);
    setMessage("已取得校訂席位，現在可以提交字幕修改。");
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
    setMessage("已釋放校訂席位。");
    await loadDetail();
  }

  function seek(segment: Segment) {
    playerRef.current?.seekTo(segment.start_ms / 1000, true);
    setActiveSegmentId(segment.id);
  }

  async function saveSuggestion(segment: Segment) {
    if (!lease) return;
    const text = (drafts[segment.id] ?? "").trim();
    if (!text || text === segment.working_text && !segment.my_suggestion_id) {
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
      setMessage(body.created ? "修改建議已送出，已立即計入校訂紀錄。" : "修改建議已更新。" );
      await loadDetail();
      setSelectedSegmentId(null);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "修改建議送出失敗");
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
              <span className={styles.sessionLabel}>校訂席位</span>
              <strong>{detail.active_editors.length}/{detail.max_editors}</strong>
            </div>
            <div className={styles.editorNames}>
              {detail.active_editors.length ? (
                detail.active_editors.map((editor) => <span key={editor.user_id}>{editor.display_name}</span>)
              ) : (
                <span>目前無人校訂</span>
              )}
            </div>
          </div>
          <p className={styles.helperText}>
            觀看影片不佔席位。字幕會跟著播放位置自動捲動；點時間碼可直接跳到該句。
          </p>
        </section>

        <section className={styles.subtitlePanel} aria-label="同步字幕">
          <div className={styles.subtitleHeader}>
            <div>
              <span>同步字幕</span>
              <strong>{detail.segments.length} 段</strong>
            </div>
            <span className={lease ? styles.editingBadge : styles.readonlyBadge}>
              {lease ? "校訂模式" : "觀看模式"}
            </span>
          </div>
          <div className={styles.segmentList}>
            {detail.segments.map((segment) => {
              const active = segment.id === activeSegmentId;
              const selectedForEdit = segment.id === selectedSegmentId;
              const hasSuggestion = Boolean(segment.my_suggestion_id);
              return (
                <article
                  className={`${styles.segment} ${active ? styles.activeSegment : ""}`}
                  id={`review-segment-${segment.id}`}
                  key={segment.id}
                >
                  <button className={styles.timeButton} onClick={() => seek(segment)} type="button">
                    {timestamp(segment.start_ms)}
                  </button>
                  <div className={styles.segmentBody}>
                    {selectedForEdit && lease ? (
                      <>
                        <textarea
                          aria-label={`第 ${segment.segment_index} 段字幕修改`}
                          value={drafts[segment.id] ?? ""}
                          onChange={(event) =>
                            setDrafts((current) => ({ ...current, [segment.id]: event.target.value }))
                          }
                          rows={3}
                        />
                        <div className={styles.editActions}>
                          <button
                            className={styles.saveButton}
                            disabled={savingSegmentId === segment.id}
                            onClick={() => void saveSuggestion(segment)}
                            type="button"
                          >
                            {savingSegmentId === segment.id ? "送出中…" : hasSuggestion ? "更新建議" : "送出修改"}
                          </button>
                          <button
                            className={styles.cancelButton}
                            onClick={() => setSelectedSegmentId(null)}
                            type="button"
                          >
                            取消
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <p>{segment.my_suggested_text ?? segment.working_text}</p>
                        <div className={styles.segmentMeta}>
                          {hasSuggestion ? <span className={styles.suggested}>✓ 已提出修改</span> : <span>第 {segment.segment_index} 段</span>}
                          {lease ? (
                            <button
                              onClick={() => {
                                setSelectedSegmentId(segment.id);
                                seek(segment);
                              }}
                              type="button"
                            >
                              修改
                            </button>
                          ) : null}
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
