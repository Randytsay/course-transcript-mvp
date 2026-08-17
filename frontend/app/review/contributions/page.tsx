"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import styles from "./contributions.module.css";

type LeaderboardRow = {
  rank: number;
  user_id: string;
  display_name: string;
  avatar_url: string | null;
  suggestions_sent: number;
  changed_chars: number;
  videos_contributed: number;
  completed_videos: number;
  is_me: boolean;
};

type ContributionDetail = {
  user: { display_name: string };
  suggestions_sent: number;
  changed_chars: number;
  videos_contributed: number;
  completed_videos: number;
  videos: Array<{
    youtube_video_id: string;
    title: string;
    suggestions_sent: number;
    changed_chars: number;
    completed: number;
  }>;
};

export default function ContributionPage() {
  const router = useRouter();
  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[]>([]);
  const [detail, setDetail] = useState<ContributionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [boardResponse, meResponse] = await Promise.all([
          fetch("/api/v1/review/contributions", { cache: "no-store", credentials: "same-origin" }),
          fetch("/api/v1/review/contributions/me", { cache: "no-store", credentials: "same-origin" }),
        ]);
        if (boardResponse.status === 401 || meResponse.status === 401) {
          router.replace("/review");
          return;
        }
        if (!boardResponse.ok || !meResponse.ok) throw new Error("目前無法讀取校訂功德紀錄");
        const board = await boardResponse.json();
        const mine = await meResponse.json();
        if (!cancelled) {
          setLeaderboard(board.leaderboard ?? []);
          setDetail(mine);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "載入失敗");
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
            <h1>校訂功德榜</h1>
            <p>每一處送出的字幕建議，都立即留下共修紀錄；後續修正同一筆建議不重複灌件數。</p>
          </div>
          <Link href="/review/videos" className={styles.backLink}>返回影片</Link>
        </header>

        {loading ? <div className={styles.stateCard}>正在整理校訂紀錄…</div> : null}
        {error ? <div className={styles.errorCard}>{error}</div> : null}

        {detail ? (
          <section className={styles.mySection}>
            <div className={styles.myHeading}>
              <div>
                <span>我的校訂</span>
                <strong>{detail.user.display_name}</strong>
              </div>
            </div>
            <div className={styles.statsGrid}>
              <div><strong>{detail.suggestions_sent}</strong><span>修改建議</span></div>
              <div><strong>{detail.changed_chars}</strong><span>修改字數</span></div>
              <div><strong>{detail.videos_contributed}</strong><span>參與影片</span></div>
              <div><strong>{detail.completed_videos}</strong><span>完成校閱</span></div>
            </div>
            {detail.videos.length ? (
              <div className={styles.myVideos}>
                {detail.videos.map((video) => (
                  <Link
                    key={video.youtube_video_id}
                    href={`/review/videos/${encodeURIComponent(video.youtube_video_id)}`}
                    className={styles.myVideoRow}
                  >
                    <span>{video.title}</span>
                    <small>{video.suggestions_sent} 處・{video.changed_chars} 字{video.completed ? "・已完成" : ""}</small>
                  </Link>
                ))}
              </div>
            ) : null}
          </section>
        ) : null}

        {!loading && !error ? (
          <section className={styles.boardSection}>
            <div className={styles.boardHeading}>
              <h2>共修排行</h2>
              <span>依修改建議數排序</span>
            </div>
            {leaderboard.length ? (
              <div className={styles.board}>
                {leaderboard.map((row) => (
                  <article className={`${styles.boardRow} ${row.is_me ? styles.meRow : ""}`} key={row.user_id}>
                    <div className={styles.rank}>{row.rank}</div>
                    {row.avatar_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={row.avatar_url} alt="" className={styles.avatar} />
                    ) : (
                      <div className={styles.avatarFallback}>{row.display_name.slice(0, 1)}</div>
                    )}
                    <div className={styles.person}>
                      <strong>{row.display_name}{row.is_me ? "（我）" : ""}</strong>
                      <span>{row.videos_contributed} 部影片・完成 {row.completed_videos} 部</span>
                    </div>
                    <div className={styles.score}>
                      <strong>{row.suggestions_sent}</strong>
                      <span>處修改</span>
                    </div>
                    <div className={styles.characters}>
                      <strong>{row.changed_chars}</strong>
                      <span>字</span>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className={styles.stateCard}>還沒有校訂紀錄。第一筆修改，就從你開始。</div>
            )}
          </section>
        ) : null}
      </div>
    </main>
  );
}
