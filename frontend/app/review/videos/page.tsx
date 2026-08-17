"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
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

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>佛學字幕共修</p>
            <h1>選一支影片，接著校訂</h1>
            <p className={styles.subhead}>
              {name ? `${name}，` : ""}觀看不佔校訂名額；開始修改時才會取得席位。
            </p>
          </div>
          <div className={styles.headerActions}>
            <Link href="/review/contributions" className={styles.accountLink}>功德榜</Link>
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
            <b>繼續播放 →</b>
          </Link>
        ) : null}

        {data ? (
          <section className={styles.library} aria-label="校訂影片清單">
            <div className={styles.libraryHeading}>
              <h2>影片清單</h2>
              <span>{data.videos.length} 部</span>
            </div>
            {data.videos.length === 0 ? (
              <div className={styles.stateCard}>
                還沒有匯入可校訂的 YouTube 字幕；管理員完成第一次 playlist 同步後，影片會直接出現在這裡。
              </div>
            ) : (
              <div className={styles.videoGrid}>
                {data.videos.map((video) => {
                  const watched = percent(video.last_playback_ms, video.duration_ms);
                  return (
                    <Link
                      href={`/review/videos/${encodeURIComponent(video.youtube_video_id)}`}
                      className={styles.videoCard}
                      key={video.youtube_video_id}
                    >
                      <div className={styles.thumbnail}>
                        {/* YouTube thumbnail is public media for the selected video. */}
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={`https://i.ytimg.com/vi/${video.youtube_video_id}/hqdefault.jpg`}
                          alt=""
                        />
                        <span>{formatDuration(video.duration_ms)}</span>
                      </div>
                      <div className={styles.videoBody}>
                        <h3>{video.title}</h3>
                        <div className={styles.metaRow}>
                          <span>{video.segment_count} 段字幕</span>
                          <span>
                            {video.active_editor_count}/{data.max_editors_per_video} 人校訂中
                          </span>
                        </div>
                        <div className={styles.progressTrack} aria-label={`觀看進度 ${watched}%`}>
                          <i style={{ width: `${watched}%` }} />
                        </div>
                        <div className={styles.metaRow}>
                          <span>{video.completed ? "已完成校閱" : watched ? `已看 ${watched}%` : "尚未開始"}</span>
                          {video.my_suggestion_count ? (
                            <strong>已提 {video.my_suggestion_count} 處</strong>
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
