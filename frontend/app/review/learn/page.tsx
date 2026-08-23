"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./learning.module.css";
import ReviewNav from "../review-nav";

type Video = {
  youtube_video_id: string;
  title: string;
  duration_ms: number | null;
  last_playback_ms: number;
  review_progress_ms: number;
  subtitle_review_completed: number;
  learning_status: "not_started" | "in_progress" | "completed";
  saved: number;
  watch_percent: number;
  note_count: number;
  bookmark_count: number;
  suggestion_count: number;
  next_due_at: string | null;
  artifact_generated_at: string | null;
  last_interaction_at?: string | null;
};

type Dashboard = {
  summary: {
    video_count: number;
    completed_count: number;
    in_progress_count: number;
    not_started_count: number;
    review_due_count: number;
    saved_count: number;
  };
  continue_learning: Video | null;
  videos: Video[];
  review_due: Array<Video & { next_due_at: string }>;
  recent_notes: Array<{ id: string; youtube_video_id: string; video_title: string; title: string | null; body: string; start_ms: number | null }>;
  recent_bookmarks: Array<{ id: string; youtube_video_id: string; video_title: string; label: string | null; note: string | null; start_ms: number }>;
};

type Filter = "all" | "in_progress" | "not_started" | "completed" | "saved";
type Sort = "recent" | "title" | "progress";

function time(ms: number | null | undefined) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function statusLabel(value: Video["learning_status"]) {
  if (value === "completed") return "已學完";
  if (value === "in_progress") return "學習中";
  return "尚未開始";
}

function reviewPercent(video: Video) {
  if (video.subtitle_review_completed) return 100;
  const duration = Number(video.duration_ms || 0);
  return duration > 0 ? Math.min(100, Math.round(Number(video.review_progress_ms || 0) * 100 / duration)) : 0;
}

export default function LearningDashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("recent");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/v1/review/learning/dashboard", { cache: "no-store", credentials: "same-origin" });
      if (response.status === 401) {
        window.location.assign("/review");
        return;
      }
      if (!response.ok) throw new Error("目前無法讀取學習進度");
      setData(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "目前無法讀取學習進度");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const videos = useMemo(() => {
    if (!data) return [];
    let rows = data.videos;
    if (filter === "saved") rows = rows.filter((item) => Boolean(item.saved));
    else if (filter !== "all") rows = rows.filter((item) => item.learning_status === filter);
    const keyword = query.trim().toLocaleLowerCase("zh-TW");
    if (keyword) rows = rows.filter((item) => item.title.toLocaleLowerCase("zh-TW").includes(keyword));
    return [...rows].sort((a, b) => {
      if (sort === "title") return a.title.localeCompare(b.title, "zh-TW", { numeric: true });
      if (sort === "progress") return b.watch_percent - a.watch_percent || a.title.localeCompare(b.title, "zh-TW", { numeric: true });
      const at = a.last_interaction_at ? Date.parse(a.last_interaction_at) : 0;
      const bt = b.last_interaction_at ? Date.parse(b.last_interaction_at) : 0;
      return bt - at || a.title.localeCompare(b.title, "zh-TW", { numeric: true });
    });
  }, [data, filter, query, sort]);

  if (loading && !data) return <main className={styles.page}><div className={styles.shell}><p className={styles.status}>正在整理你的學習進度…</p></div></main>;
  if (error && !data) return <main className={styles.page}><div className={styles.shell}><div className={styles.error} role="alert"><p>{error}</p><button className={styles.primaryButton} onClick={()=>void load()} type="button">重新載入</button></div></div></main>;
  if (!data) return null;

  const completion = data.summary.video_count ? Math.round(data.summary.completed_count * 100 / data.summary.video_count) : 0;
  const resume = data.continue_learning;
  const hasHistory = data.summary.completed_count > 0 || data.summary.in_progress_count > 0;

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.topbar}>
          <a className={styles.brand} href="/review/learn">
            <span className={styles.brandMark}>慈</span>
            <span className={styles.brandText}><strong>慈聖佛堂・佛學共學平台</strong><span>看課・複習・共修・留下自己的學習歷程</span></span>
          </a>
          <ReviewNav active="learn" />
        </header>

        <section className={styles.hero}>
          <div className={styles.heroMain}>
            <p className={styles.eyebrow}>我的學習中心</p>
            <h1>一眼知道學到哪裡，回來就能接著學。</h1>
            <p>觀看、學習完成、複習與字幕共修分開記錄；AI 筆記只根據已核准的字幕版本整理，每個重點都能回到影片原始時間。</p>
            <div className={styles.progressSummary}>
              <div><span className={styles.bigNumber}>{data.summary.completed_count}/{data.summary.video_count}</span><div className={styles.muted}>已完成課程</div></div>
              <div><span className={styles.bigNumber}>{completion}%</span><div className={styles.muted}>整體完成度</div></div>
              <div><span className={styles.bigNumber}>{data.summary.review_due_count}</span><div className={styles.muted}>今天待複習</div></div>
            </div>
          </div>
          <aside className={styles.continueCard}>
            {resume ? <>
              <div><p>繼續學習</p><h2>{resume.title}</h2><p>上次看到 {time(resume.last_playback_ms)} ・ {resume.watch_percent}%</p></div>
              <a className={styles.primaryButton} href={`/review/learn/${resume.youtube_video_id}?t=${Math.floor((resume.last_playback_ms || 0) / 1000)}`}>從上次位置繼續 →</a>
            </> : hasHistory ? <>
              <div><p>目前沒有進行中的課程</p><h2>{data.summary.completed_count ? `已完成 ${data.summary.completed_count} 堂，可以開始下一堂或回頭複習。` : "選一堂課繼續學習。"}</h2><p>已學完的課仍可隨時重看，不會因此取消完成狀態。</p></div>
              {data.summary.review_due_count ? <a className={styles.primaryButton} href="/review/learn/review">前往今天的複習 →</a> : data.videos[0] ? <a className={styles.primaryButton} href={`/review/learn/${data.videos[0].youtube_video_id}`}>查看全部課程 →</a> : null}
            </> : <>
              <div><p>開始第一堂</p><h2>目前還沒有觀看紀錄</h2><p>選一堂課開始，系統會自動記住你看到哪裡。</p></div>
              {data.videos[0] ? <a className={styles.primaryButton} href={`/review/learn/${data.videos[0].youtube_video_id}`}>開始學習 →</a> : null}
            </>}
          </aside>
        </section>

        <section className={styles.stats} aria-label="學習摘要">
          <div className={styles.statCard}><strong>{data.summary.in_progress_count}</strong><span>正在學習</span></div>
          <div className={styles.statCard}><strong>{data.summary.not_started_count}</strong><span>尚未開始</span></div>
          <div className={styles.statCard}><strong>{data.summary.completed_count}</strong><span>已學完</span></div>
          <div className={styles.statCard}><strong>{data.summary.review_due_count}</strong><span>待複習</span></div>
          <div className={styles.statCard}><strong>{data.summary.saved_count}</strong><span>已收藏</span></div>
        </section>

        {error ? <p className={styles.error} role="alert">{error}；目前顯示上一次成功載入的資料。<button onClick={()=>void load()} type="button">重新整理</button></p> : null}

        {data.review_due.length ? <>
          <div className={styles.sectionHeader}><div><h2>今天複習</h2><p>用幾分鐘把之前學過的內容重新叫回來。</p></div><a href="/review/learn/review">查看全部 →</a></div>
          <section className={styles.panel}>
            <div className={styles.list}>
              {data.review_due.slice(0, 3).map((item) => <div className={styles.listItem} key={item.youtube_video_id}>
                <strong>{item.title}</strong>
                <p>已到複習時間。{item.artifact_generated_at ? "AI 重點、問答與 Flashcards 已可使用。" : "目前可先回看影片與自己的筆記。"}</p>
                <a href={`/review/learn/${item.youtube_video_id}?tab=review`}>開始複習 →</a>
              </div>)}
            </div>
          </section>
        </> : null}

        <div className={styles.sectionHeader}><div><h2>全部課程</h2><p>觀看進度不等於學習完成；你可以自己決定何時標記「已學完」。</p></div></div>
        <div className={styles.toolbar} aria-label="課程篩選與排序">
          <input aria-label="搜尋課程名稱" className={styles.input} onChange={(event)=>setQuery(event.target.value)} placeholder="搜尋課程名稱…" style={{maxWidth:280,marginBottom:0}} type="search" value={query}/>
          {([['all','全部'],['in_progress','學習中'],['not_started','尚未開始'],['completed','已學完'],['saved','收藏']] as Array<[Filter,string]>).map(([key,label]) => (
            <button aria-pressed={filter === key} className={filter === key ? styles.active : ""} key={key} onClick={() => setFilter(key)} type="button">{label}</button>
          ))}
          <label className={styles.muted}>排序： <select aria-label="課程排序" onChange={(event)=>setSort(event.target.value as Sort)} value={sort}><option value="recent">最近學習</option><option value="title">課程名稱</option><option value="progress">觀看進度</option></select></label>
        </div>
        {videos.length ? <section className={styles.lessonGrid}>
          {videos.map((video) => {const subtitleProgress=reviewPercent(video);return <article className={styles.lessonCard} key={video.youtube_video_id}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img className={styles.thumb} src={`https://i.ytimg.com/vi/${video.youtube_video_id}/hqdefault.jpg`} alt={`${video.title} 縮圖`} />
            <div className={styles.lessonBody}>
              <h3 className={styles.lessonTitle}>{video.title}</h3>
              <div className={styles.metaRow}>
                <span className={video.learning_status === "completed" ? `${styles.badge} ${styles.badgeDone}` : styles.badge}>{statusLabel(video.learning_status)}</span>
                {video.saved ? <span className={styles.badge}>已收藏</span> : null}
                {video.next_due_at && new Date(video.next_due_at) <= new Date() ? <span className={`${styles.badge} ${styles.badgeDue}`}>待複習</span> : null}
                {video.artifact_generated_at ? <span className={styles.badge}>AI 筆記</span> : null}
              </div>
              <div aria-label={`觀看進度 ${video.watch_percent}%`} aria-valuemax={100} aria-valuemin={0} aria-valuenow={video.watch_percent} className={styles.progressLine} role="progressbar"><span style={{width:`${video.watch_percent}%`}} /></div>
              <div className={styles.progressLabel}><span>觀看 {video.watch_percent}%</span><span>{time(video.last_playback_ms)} / {time(video.duration_ms)}</span></div>
              {subtitleProgress > 0 ? <p className={styles.muted}>字幕共修進度 {subtitleProgress}%{video.subtitle_review_completed ? "・已完成校閱" : ""}</p> : null}
              {(video.note_count || video.bookmark_count) ? <p className={styles.muted}>我的筆記 {video.note_count} ・ 書籤 {video.bookmark_count}</p> : null}
              <div className={styles.cardActions}>
                <a className={styles.openButton} href={`/review/learn/${video.youtube_video_id}${video.last_playback_ms ? `?t=${Math.floor(video.last_playback_ms/1000)}` : ""}`}>{video.watch_percent ? "繼續學習" : "開始學習"}</a>
                {video.artifact_generated_at ? <a className={styles.reviewButton} href={`/review/learn/${video.youtube_video_id}?tab=review`}>快速複習</a> : null}
              </div>
            </div>
          </article>})}
        </section> : <div className={styles.empty}>{query.trim()?`找不到包含「${query.trim()}」的課程。`:"這個分類目前沒有課程。"}</div>}

        <div className={styles.twoColumn}>
          <section>
            <div className={styles.sectionHeader}><div><h2>最近筆記</h2><p>自己的理解與提醒，和 AI 筆記分開保存。</p></div></div>
            <div className={styles.panel}>{data.recent_notes.length ? <div className={styles.list}>{data.recent_notes.map((note) => <div className={styles.listItem} key={note.id}><strong>{note.title || note.video_title}</strong><p>{note.body.slice(0,160)}</p><a href={`/review/learn/${note.youtube_video_id}${note.start_ms != null ? `?t=${Math.floor(note.start_ms/1000)}&tab=personal` : "?tab=personal"}`}>回到這則筆記 →</a></div>)}</div> : <div className={styles.empty}>還沒有自己的筆記。</div>}</div>
          </section>
          <section>
            <div className={styles.sectionHeader}><div><h2>最近書籤</h2><p>看到重要段落時，先留下時間點。</p></div></div>
            <div className={styles.panel}>{data.recent_bookmarks.length ? <div className={styles.list}>{data.recent_bookmarks.map((mark) => <div className={styles.listItem} key={mark.id}><strong>{mark.label || mark.video_title}</strong><p>{time(mark.start_ms)} {mark.note || ""}</p><a href={`/review/learn/${mark.youtube_video_id}?t=${Math.floor(mark.start_ms/1000)}`}>跳到影片 →</a></div>)}</div> : <div className={styles.empty}>還沒有書籤。</div>}</div>
          </section>
        </div>
      </div>
    </main>
  );
}
