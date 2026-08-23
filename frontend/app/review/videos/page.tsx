"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import styles from "./videos.module.css";

type VideoSummary = {
  youtube_video_id: string;
  title: string;
  duration_ms: number | null;
  segment_count: number;
  last_playback_ms: number;
  reviewed_until_ms: number;
  completed: boolean;
  active_editor_count: number;
  my_suggestion_count: number;
  my_approved_count: number;
  my_pending_count: number;
};

type VideosResponse = {
  videos: VideoSummary[];
  resume: {
    youtube_video_id: string;
    title: string;
    last_playback_ms: number;
  } | null;
  max_editors_per_video: number;
};

type Filter = "all" | "not-started" | "reviewing" | "completed" | "mine";
type Sort = "default" | "title" | "review-progress";

function formatDuration(milliseconds: number | null) {
  if (!milliseconds) return "--:--";
  const seconds = Math.round(milliseconds / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remaining = seconds % 60;
  return hours > 0
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${remaining.toString().padStart(2, "0")}`
    : `${minutes}:${remaining.toString().padStart(2, "0")}`;
}

function percent(value: number, total: number | null) {
  if (!total || total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((value / total) * 100)));
}

export default function ReviewVideosPage() {
  const router = useRouter();
  const [data, setData] = useState<VideosResponse | null>(null);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("default");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [meResponse, videosResponse] = await Promise.all([
          fetch("/api/v1/review/auth/me", { cache: "no-store", credentials: "same-origin" }),
          fetch("/api/v1/review/videos", { cache: "no-store", credentials: "same-origin" }),
        ]);
        if (meResponse.status === 401 || videosResponse.status === 401) {
          router.replace("/review");
          return;
        }
        if (!meResponse.ok || !videosResponse.ok) {
          throw new Error("目前無法讀取校訂影片清單");
        }
        const me = await meResponse.json();
        const videos = await videosResponse.json();
        if (!cancelled) {
          setName(me.user.display_name);
          setData(videos);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "目前無法讀取校訂影片清單");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const filteredVideos = useMemo(() => {
    if (!data) return [];
    const normalized = query.trim().toLocaleLowerCase("zh-TW");
    const rows = data.videos.filter((video) => {
      if (normalized && !video.title.toLocaleLowerCase("zh-TW").includes(normalized)) return false;
      const watched = percent(video.last_playback_ms, video.duration_ms);
      const reviewed = percent(video.reviewed_until_ms, video.duration_ms);
      if (filter === "not-started") return watched === 0 && reviewed === 0 && !video.completed;
      if (filter === "reviewing") return !video.completed && (watched > 0 || reviewed > 0);
      if (filter === "completed") return video.completed;
      if (filter === "mine") return video.my_suggestion_count > 0 || reviewed > 0 || video.completed;
      return true;
    });
    if (sort === "title") {
      return [...rows].sort((a, b) => a.title.localeCompare(b.title, "zh-TW"));
    }
    if (sort === "review-progress") {
      return [...rows].sort(
        (a, b) => percent(b.reviewed_until_ms, b.duration_ms) - percent(a.reviewed_until_ms, a.duration_ms),
      );
    }
    return rows;
  }, [data, filter, query, sort]);

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>佛學字幕共修</p>
            <h1>選一支影片，接著校訂</h1>
            <p className={styles.subhead}>
              {name ? `${name}，` : ""}任何時候都能先觀看；要修改字幕時再按「開始校訂」即可。
            </p>
          </div>
          <div className={styles.headerActions}>
            <Link href="/review/contributions" className={styles.accountLink}>我的共修</Link>
            <Link href="/review" className={styles.accountLink}>帳號</Link>
          </div>
        </header>

        {loading ? <div className={styles.stateCard}>正在整理影片…</div> : null}
        {error ? <div className={styles.errorCard}>{error}</div> : null}

        {data?.resume ? (
          <Link
            href={`/review/videos/${encodeURIComponent(data.resume.youtube_video_id)}`}
            className={styles.resumeCard}
          >
            <div>
              <span>繼續上次進度</span>
              <strong>{data.resume.title}</strong>
            </div>
            <b>繼續觀看 →</b>
          </Link>
        ) : null}

        {data ? (
          <section className={styles.library} aria-label="校訂影片清單">
            <div className={styles.libraryHeading}>
              <div>
                <h2>影片清單</h2>
                <span>{filteredVideos.length} / {data.videos.length} 部</span>
              </div>
            </div>

            {data.videos.length ? (
              <div className={styles.controls}>
                <label className={styles.searchBox}>
                  <span>搜尋</span>
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="輸入經名、集數或關鍵字"
                    type="search"
                  />
                </label>
                <label className={styles.selectBox}>
                  <span>狀態</span>
                  <select value={filter} onChange={(event) => setFilter(event.target.value as Filter)}>
                    <option value="all">全部影片</option>
                    <option value="not-started">尚未開始</option>
                    <option value="reviewing">校閱中</option>
                    <option value="completed">已完成校閱</option>
                    <option value="mine">我參與過</option>
                  </select>
                </label>
                <label className={styles.selectBox}>
                  <span>排序</span>
                  <select value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
                    <option value="default">依匯入順序</option>
                    <option value="title">依影片名稱</option>
                    <option value="review-progress">依校閱進度</option>
                  </select>
                </label>
              </div>
            ) : null}

            {data.videos.length === 0 ? (
              <div className={styles.stateCard}>
                目前還沒有可校訂的影片。影片準備完成後會自動出現在這裡，請稍後再回來看看。
              </div>
            ) : filteredVideos.length === 0 ? (
              <div className={styles.stateCard}>目前沒有符合條件的影片，換個搜尋字或篩選條件試試看。</div>
            ) : (
              <div className={styles.videoGrid}>
                {filteredVideos.map((video) => {
                  const watched = percent(video.last_playback_ms, video.duration_ms);
                  const reviewed = video.completed ? 100 : percent(video.reviewed_until_ms, video.duration_ms);
                  const editingNow = video.active_editor_count;
                  return (
                    <Link
                      href={`/review/videos/${encodeURIComponent(video.youtube_video_id)}`}
                      className={styles.videoCard}
                      key={video.youtube_video_id}
                    >
                      <div className={styles.thumbnail}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={`https://i.ytimg.com/vi/${video.youtube_video_id}/hqdefault.jpg`}
                          alt=""
                        />
                        <span>{formatDuration(video.duration_ms)}</span>
                        {video.completed ? <b className={styles.completedBadge}>✓ 已完成</b> : null}
                      </div>
                      <div className={styles.videoBody}>
                        <h3>{video.title}</h3>
                        <div className={styles.metaRow}>
                          <span>{video.segment_count} 段字幕</span>
                          <span>{editingNow ? `目前 ${editingNow} 人正在校訂` : "目前可開始校訂"}</span>
                        </div>

                        <div className={styles.progressGroup}>
                          <div className={styles.progressLabel}><span>觀看進度</span><b>{watched}%</b></div>
                          <div className={styles.progressTrack} aria-label={`觀看進度 ${watched}%`}>
                            <i style={{ width: `${watched}%` }} />
                          </div>
                          <div className={styles.progressLabel}><span>校閱進度</span><b>{reviewed}%</b></div>
                          <div className={`${styles.progressTrack} ${styles.reviewTrack}`} aria-label={`校閱進度 ${reviewed}%`}>
                            <i style={{ width: `${reviewed}%` }} />
                          </div>
                        </div>

                        <div className={styles.metaRow}>
                          <span>{video.completed ? "本片已完成校閱" : reviewed ? `已校閱 ${reviewed}%` : "尚未校閱"}</span>
                          {video.my_suggestion_count ? (
                            <strong>
                              我提 {video.my_suggestion_count} 處
                              {video.my_pending_count ? `・${video.my_pending_count} 待審` : ""}
                            </strong>
                          ) : null}
                        </div>
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </section>
        ) : null}
      </div>
    </main>
  );
}
