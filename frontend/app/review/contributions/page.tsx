"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
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
  approved_suggestions: number;
  pending_suggestions: number;
  is_me: boolean;
};

type ContributionDetail = {
  user: { display_name: string };
  suggestions_sent: number;
  changed_chars: number;
  videos_contributed: number;
  completed_videos: number;
  approved_suggestions: number;
  pending_suggestions: number;
  videos: Array<{
    youtube_video_id: string;
    title: string;
    suggestions_sent: number;
    changed_chars: number;
    approved_suggestions: number;
    pending_suggestions: number;
    completed: number;
  }>;
};

type MySuggestion = {
  id: string;
  youtube_video_id: string;
  video_title: string;
  segment_index: number;
  start_ms: number;
  original_text_snapshot: string;
  suggested_text: string;
  current_text: string;
  display_status: "pending" | "approved" | "rejected" | "withdrawn";
  review_reason: string | null;
  updated_at: string;
};

type HistoryFilter = "all" | "pending" | "approved" | "rejected" | "withdrawn";

function timecode(milliseconds: number) {
  const seconds = Math.floor(Math.max(0, milliseconds) / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}`
    : `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function statusLabel(status: MySuggestion["display_status"]) {
  if (status === "pending") return "待審核";
  if (status === "approved") return "已採用";
  if (status === "rejected") return "未採用";
  return "已撤回";
}

export default function ContributionPage() {
  const router = useRouter();
  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[]>([]);
  const [detail, setDetail] = useState<ContributionDetail | null>(null);
  const [history, setHistory] = useState<MySuggestion[]>([]);
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [boardResponse, meResponse, historyResponse] = await Promise.all([
          fetch("/api/v1/review/contributions", { cache: "no-store", credentials: "same-origin" }),
          fetch("/api/v1/review/contributions/me", { cache: "no-store", credentials: "same-origin" }),
          fetch("/api/v1/review/suggestions/me", { cache: "no-store", credentials: "same-origin" }),
        ]);
        if ([boardResponse, meResponse, historyResponse].some((response) => response.status === 401)) {
          router.replace("/review");
          return;
        }
        if (!boardResponse.ok || !meResponse.ok || !historyResponse.ok) {
          throw new Error("目前無法讀取共修紀錄");
        }
        const board = await boardResponse.json();
        const mine = await meResponse.json();
        const suggestions = await historyResponse.json();
        if (!cancelled) {
          setLeaderboard(board.leaderboard ?? []);
          setDetail(mine);
          setHistory(suggestions.suggestions ?? []);
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

  const filteredHistory = useMemo(
    () => history.filter((item) => historyFilter === "all" || item.display_status === historyFilter),
    [history, historyFilter],
  );

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>佛學字幕共修</p>
            <h1>我的共修紀錄</h1>
            <p>每一筆校訂建議都會留下紀錄；同一筆建議後續調整，不會重複計算。完成整支影片的校閱，也會記錄在這裡。</p>
          </div>
          <Link href="/review/videos" className={styles.backLink}>返回影片</Link>
        </header>

        {loading ? <div className={styles.stateCard}>正在整理共修紀錄…</div> : null}
        {error ? <div className={styles.errorCard}>{error}</div> : null}

        {detail ? (
          <section className={styles.mySection}>
            <div className={styles.myHeading}>
              <div>
                <span>我的共修</span>
                <strong>{detail.user.display_name}</strong>
              </div>
            </div>
            <div className={styles.statsGrid}>
              <div><strong>{detail.completed_videos}</strong><span>完成校閱</span></div>
              <div><strong>{detail.videos_contributed}</strong><span>參與影片</span></div>
              <div><strong>{detail.suggestions_sent}</strong><span>修改建議</span></div>
              <div><strong>{detail.approved_suggestions}</strong><span>已採用</span></div>
              <div><strong>{detail.pending_suggestions}</strong><span>待審核</span></div>
              <div><strong>{detail.changed_chars}</strong><span>協助修正字數</span></div>
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
                    <small>
                      {video.completed ? "✓ 已完成校閱・" : ""}
                      {video.suggestions_sent} 筆建議・{video.approved_suggestions} 筆採用
                    </small>
                  </Link>
                ))}
              </div>
            ) : null}
          </section>
        ) : null}

        {!loading && !error ? (
          <section className={styles.historySection}>
            <div className={styles.boardHeading}>
              <div>
                <h2>我的修改紀錄</h2>
                <span>可回到原影片再次查看或調整尚未審核的建議</span>
              </div>
              <select value={historyFilter} onChange={(event) => setHistoryFilter(event.target.value as HistoryFilter)}>
                <option value="all">全部</option>
                <option value="pending">待審核</option>
                <option value="approved">已採用</option>
                <option value="rejected">未採用</option>
                <option value="withdrawn">已撤回</option>
              </select>
            </div>

            {filteredHistory.length ? (
              <div className={styles.historyList}>
                {filteredHistory.map((item) => (
                  <Link
                    className={styles.historyItem}
                    href={`/review/videos/${encodeURIComponent(item.youtube_video_id)}`}
                    key={item.id}
                  >
                    <div className={styles.historyTopline}>
                      <strong>{item.video_title}</strong>
                      <span className={`${styles.statusBadge} ${styles[`status_${item.display_status}`]}`}>{statusLabel(item.display_status)}</span>
                    </div>
                    <small>{timecode(item.start_ms)}・第 {item.segment_index} 段</small>
                    <div className={styles.historyDiff}>
                      <span>{item.original_text_snapshot}</span>
                      <b>→</b>
                      <span>{item.suggested_text}</span>
                    </div>
                    {item.display_status === "rejected" && item.review_reason ? (
                      <p className={styles.reason}>未採用說明：{item.review_reason}</p>
                    ) : null}
                  </Link>
                ))}
              </div>
            ) : (
              <div className={styles.stateCard}>目前沒有符合這個狀態的修改紀錄。</div>
            )}
          </section>
        ) : null}

        {!loading && !error ? (
          <section className={styles.boardSection}>
            <div className={styles.boardHeading}>
              <div>
                <h2>共修夥伴</h2>
                <span>以完成校閱與參與影片為優先，不鼓勵為了排名增加修改數量</span>
              </div>
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
                      <span>參與 {row.videos_contributed} 部・完成 {row.completed_videos} 部</span>
                    </div>
                    <div className={styles.score}>
                      <strong>{row.approved_suggestions}</strong>
                      <span>已採用</span>
                    </div>
                    <div className={styles.characters}>
                      <strong>{row.suggestions_sent}</strong>
                      <span>筆建議</span>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className={styles.stateCard}>還沒有共修紀錄。第一筆校訂，就從你開始。</div>
            )}
          </section>
        ) : null}
      </div>
    </main>
  );
}
