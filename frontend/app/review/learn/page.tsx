"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  Bookmark,
  ChevronRight,
  CircleCheck,
  CircleUserRound,
  Clock3,
  Cpu,
  Flower2,
  PlayCircle,
  Sparkles,
  Star
} from "lucide-react";
import styles from "./learning.module.css";
import brandStyles from "./learning-brand.module.css";

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

type MeResponse = {
  user: { display_name: string; avatar_url: string | null };
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

function courseHref(video: Video | null | undefined) {
  return video ? `/review/learn/${video.youtube_video_id}` : "/review/videos";
}

export default function LearningDashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("recent");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [response, meResponse] = await Promise.all([
        fetch("/api/v1/review/learning/dashboard", { cache: "no-store", credentials: "same-origin" }),
        fetch("/api/v1/review/auth/me", { cache: "no-store", credentials: "same-origin" })
      ]);
      if (response.status === 401 || meResponse.status === 401) {
        window.location.assign("/review");
        return;
      }
      if (!response.ok) throw new Error("目前無法讀取學習進度");
      setData(await response.json());
      if (meResponse.ok) setMe(await meResponse.json());
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

  if (loading && !data) return <main className={`${styles.page} ${brandStyles.dashboard}`}><div className={styles.shell}><p className={styles.status}>正在整理你的學習進度…</p></div></main>;
  if (error && !data) return <main className={`${styles.page} ${brandStyles.dashboard}`}><div className={styles.shell}><div className={styles.error} role="alert"><p>{error}</p><button className={styles.primaryButton} onClick={() => void load()} type="button">重新載入</button></div></div></main>;
  if (!data) return null;

  const resume = data.continue_learning;
  const continueTarget = resume || data.videos[0] || null;
  const hasHistory = data.summary.completed_count > 0 || data.summary.in_progress_count > 0;
  const reviewTarget = data.review_due[0] || data.videos.find((item) => item.artifact_generated_at) || null;

  return (
    <main className={`${styles.page} ${brandStyles.dashboard}`}>
      <div className={`${styles.shell} ${brandStyles.shell}`}>
        <header className={`${styles.topbar} ${brandStyles.topbar}`}>
          <a className={`${styles.brand} ${brandStyles.brand}`} href="/review/learn" aria-label="回到學習中心">
            <span className={`${styles.brandMark} ${brandStyles.brandMark}`}><img src="/images/cisheng-lotus-seal.webp" alt="" decoding="async" /></span>
            <span className={`${styles.brandText} ${brandStyles.brandText}`}><strong>慈聖佛堂・佛學共學平台</strong><span>看課・複習・共修・留下自己的學習歷程</span></span>
          </a>
          <nav className={`${styles.nav} ${brandStyles.nav}`} aria-label="學習功能">
            <a className={brandStyles.navActive} aria-current="page" href="/review/learn"><Flower2 aria-hidden="true" size={18} strokeWidth={1.8} />學習中心</a>
            <a className={brandStyles.navLink} href="/review/videos">字幕共修</a>
            <a className={brandStyles.navLink} href="/review/learn/review">複習中心</a>
            <a className={brandStyles.navLink} href="/review/learn/search">知識搜尋</a>
            <a className={brandStyles.navLink} href="/review/contributions">共修紀錄</a>
            <a className={brandStyles.navLink} href="/review/help">使用說明</a>
          </nav>
          {me ? <a className={brandStyles.profileChip} href="/review/learn" aria-label="目前登入身分">
            {me.user.avatar_url ? <img src={me.user.avatar_url} alt="" /> : <span aria-hidden="true">{me.user.display_name.slice(0, 1)}</span>}
            <strong>{me.user.display_name}</strong>
            <span aria-hidden="true">⌄</span>
          </a> : <span className={brandStyles.profilePlaceholder} aria-hidden="true"><span><CircleUserRound size={19} /></span></span>}
        </header>

        <section className={brandStyles.learningHero} aria-label="本次學習與繼續學習">
          <div className={brandStyles.focusPanel}>
            <div className={brandStyles.sectionRibbon}><Sparkles aria-hidden="true" size={17} />本次學習重點<Sparkles aria-hidden="true" size={17} /></div>
            <div className={brandStyles.steps}>
              <div className={brandStyles.stepCard}><span className={brandStyles.stepNumber}>1</span><BookOpen aria-hidden="true" className={brandStyles.stepIcon} size={34} strokeWidth={1.6} /><strong>先看使用說明</strong><span>了解平台怎麼用</span></div>
              <ChevronRight aria-hidden="true" className={brandStyles.stepArrow} size={25} />
              <div className={brandStyles.stepCard}><span className={brandStyles.stepNumber}>2</span><PlayCircle aria-hidden="true" className={brandStyles.stepIcon} size={34} strokeWidth={1.6} /><strong>選一堂課開始學習</strong><span>找到想學的內容</span></div>
              <ChevronRight aria-hidden="true" className={brandStyles.stepArrow} size={25} />
              <div className={brandStyles.stepCard}><span className={brandStyles.stepNumber}>3</span><Bookmark aria-hidden="true" className={brandStyles.stepIcon} size={34} strokeWidth={1.6} /><strong>從上次位置繼續</strong><span>接著上次進度學習</span></div>
              <ChevronRight aria-hidden="true" className={brandStyles.stepArrow} size={25} />
              <div className={brandStyles.stepCard}><span className={brandStyles.stepNumber}>4</span><Sparkles aria-hidden="true" className={brandStyles.stepIcon} size={34} strokeWidth={1.6} /><strong>用 AI 重點複習</strong><span>快速掌握重點內容</span></div>
            </div>
          </div>

          <aside className={brandStyles.continueCard}>
            <div className={brandStyles.continueEyebrow}><Flower2 aria-hidden="true" size={17} />繼續學習</div>
            <h1>{resume ? resume.title : hasHistory ? "回到你的學習進度" : "選一堂課開始學習"}</h1>
            <p className={brandStyles.continueMeta}>已學習 {resume ? time(resume.last_playback_ms) : "0:00"} / 全長 {resume ? time(resume.duration_ms) : "—"} ・ {resume?.watch_percent || 0}%</p>
            <div className={brandStyles.continueProgress} aria-label={`觀看進度 ${resume?.watch_percent || 0}%`} aria-valuemax={100} aria-valuemin={0} aria-valuenow={resume?.watch_percent || 0} role="progressbar"><span style={{ width: `${resume?.watch_percent || 0}%` }} /></div>
            <div className={brandStyles.continueActions}>
              <a className={brandStyles.goldButton} href={courseHref(continueTarget)}><PlayCircle aria-hidden="true" size={18} />{resume ? "從上次位置繼續" : "開始第一堂"}</a>
              <a className={brandStyles.outlineGoldButton} href={courseHref(continueTarget)}><BookOpen aria-hidden="true" size={17} />查看課程詳情</a>
            </div>
          </aside>
        </section>

        <section className={brandStyles.metricGrid} aria-label="學習摘要">
          <article className={brandStyles.metricCard}><span className={brandStyles.metricIcon}><BookOpen aria-hidden="true" size={31} /></span><div><span>正在學習</span><strong>{data.summary.in_progress_count}<small> 堂課</small></strong><a href="#courses">查看進度 <ChevronRight aria-hidden="true" size={14} /></a></div></article>
          <article className={brandStyles.metricCard}><span className={brandStyles.metricIcon}><Clock3 aria-hidden="true" size={31} /></span><div><span>待複習</span><strong>{data.summary.review_due_count}<small> 堂課</small></strong><a href="/review/learn/review">去複習 <ChevronRight aria-hidden="true" size={14} /></a></div></article>
          <article className={brandStyles.metricCard}><span className={brandStyles.metricIcon}><CircleCheck aria-hidden="true" size={31} /></span><div><span>已學完</span><strong>{data.summary.completed_count}<small> 堂課</small></strong><a href="#courses">查看紀錄 <ChevronRight aria-hidden="true" size={14} /></a></div></article>
          <article className={brandStyles.metricCard}><span className={brandStyles.metricIcon}><Star aria-hidden="true" size={31} /></span><div><span>收藏</span><strong>{data.summary.saved_count}<small> 堂課</small></strong><a href="#courses">查看收藏 <ChevronRight aria-hidden="true" size={14} /></a></div></article>
        </section>

        <section className={brandStyles.recommendSection} aria-labelledby="recommend-title">
          <div className={brandStyles.recommendHeading}><h2 id="recommend-title">推薦先做這三件事</h2><span>新手必看</span></div>
          <div className={brandStyles.recommendGrid}>
            <article className={brandStyles.recommendCard}><span className={brandStyles.recommendIcon}><BookOpen aria-hidden="true" size={31} /></span><div><strong>1. 先看使用說明</strong><p>了解平台功能與學習流程</p><a href="/review/help">查看看 <ChevronRight aria-hidden="true" size={14} /></a></div></article>
            <article className={brandStyles.recommendCard}><span className={brandStyles.recommendIcon}><PlayCircle aria-hidden="true" size={31} /></span><div><strong>2. 選一堂課開始學習</strong><p>挑選適合的課程開始學習</p><a href="#courses">瀏覽課程 <ChevronRight aria-hidden="true" size={14} /></a></div></article>
            <article className={brandStyles.recommendCard}><span className={brandStyles.recommendIcon}><Cpu aria-hidden="true" size={31} /></span><div><strong>3. 用 AI 重點複習</strong><p>AI 為您整理重點，學得更快</p><a href={reviewTarget ? `/review/learn/${reviewTarget.youtube_video_id}?tab=review` : "/review/learn/review"}>去複習 <ChevronRight aria-hidden="true" size={14} /></a></div></article>
          </div>
        </section>

        {error ? <p className={styles.error} role="alert">{error}；目前顯示上一次成功載入的資料。<button onClick={() => void load()} type="button">重新整理</button></p> : null}

        <section className={brandStyles.courseSection} id="courses" aria-labelledby="courses-title">
          <div className={brandStyles.courseHeader}>
            <div><h2 id="courses-title">全部課程</h2><span>觀看進度不等於學習完成・您的進度排序</span></div>
            <div className={brandStyles.courseTools}>
              <div className={brandStyles.filterPills} aria-label="課程篩選">
                {([['all', "全部"], ['in_progress', "學習中"], ['not_started', "尚未開始"], ['completed', "已學完"], ['saved', "收藏"]] as Array<[Filter, string]>).map(([key, label]) => (
                  <button aria-pressed={filter === key} className={filter === key ? brandStyles.filterActive : brandStyles.filterButton} key={key} onClick={() => setFilter(key)} type="button">{label}</button>
                ))}
              </div>
              <label className={brandStyles.sortLabel}>排序：<select aria-label="課程排序" onChange={(event) => setSort(event.target.value as Sort)} value={sort}><option value="recent">最近學習</option><option value="title">課程名稱</option><option value="progress">觀看進度</option></select></label>
              <input aria-label="搜尋課程名稱" className={brandStyles.courseSearch} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋課程名稱…" type="search" value={query} />
            </div>
          </div>

          {videos.length ? <section className={brandStyles.courseGrid}>
            {videos.map((video) => {
              const subtitleProgress = reviewPercent(video);
              return <article className={`${styles.lessonCard} ${brandStyles.courseCard}`} key={video.youtube_video_id}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img loading="lazy" decoding="async" className={`${styles.thumb} ${brandStyles.courseThumb}`} src={`https://i.ytimg.com/vi/${video.youtube_video_id}/mqdefault.jpg`} alt={`${video.title} 縮圖`} />
                <div className={`${styles.lessonBody} ${brandStyles.courseBody}`}>
                  <div className={brandStyles.courseStatus}>{statusLabel(video.learning_status)}{video.saved ? <span>已收藏</span> : null}</div>
                  <h3 className={`${styles.lessonTitle} ${brandStyles.courseTitle}`}>{video.title}</h3>
                  <div className={`${styles.progressLine} ${brandStyles.courseProgress}`} aria-label={`觀看進度 ${video.watch_percent}%`} aria-valuemax={100} aria-valuemin={0} aria-valuenow={video.watch_percent} role="progressbar"><span style={{ width: `${video.watch_percent}%` }} /></div>
                  <div className={brandStyles.courseMeta}><span>已學習 {time(video.last_playback_ms)} / {time(video.duration_ms)}</span><span>・ {video.watch_percent}%</span></div>
                  <div className={brandStyles.courseTags}><span>{video.watch_percent ? "學習中" : "尚未開始"}</span>{video.artifact_generated_at ? <span>AI 筆記</span> : null}{subtitleProgress > 0 ? <span>字幕共修進度 {subtitleProgress}%</span> : null}</div>
                  <div className={`${styles.cardActions} ${brandStyles.courseActions}`}>
                    <a className={`${styles.openButton} ${brandStyles.goldSmallButton}`} href={`/review/learn/${video.youtube_video_id}${video.last_playback_ms ? `?t=${Math.floor(video.last_playback_ms / 1000)}` : ""}`}><PlayCircle aria-hidden="true" size={16} />{video.watch_percent ? "繼續學習" : "開始學習"}</a>
                    {video.artifact_generated_at ? <a className={`${styles.reviewButton} ${brandStyles.lightSmallButton}`} href={`/review/learn/${video.youtube_video_id}?tab=review`}>快速複習</a> : null}
                  </div>
                </div>
              </article>;
            })}
          </section> : <div className={styles.empty}>{query.trim() ? `找不到包含「${query.trim()}」的課程。` : "這個分類目前沒有課程。"}</div>}
        </section>

        <div className={brandStyles.recentGrid}>
          <section className={brandStyles.recentCard}><div className={brandStyles.recentHeading}><h2>最近筆記</h2><a href="/review/learn/notes">查看全部 <ChevronRight aria-hidden="true" size={14} /></a></div><p>自己的理解與提醒，和 AI 筆記分開保存。</p><div className={`${styles.panel} ${brandStyles.recentPanel}`}>{data.recent_notes.length ? <div className={styles.list}>{data.recent_notes.map((note) => <div className={styles.listItem} key={note.id}><strong>{note.title || note.video_title}</strong><p>{note.body.slice(0, 160)}</p><a href={`/review/learn/${note.youtube_video_id}${note.start_ms != null ? `?t=${Math.floor(note.start_ms / 1000)}&tab=personal` : "?tab=personal"}`}>回到這則筆記 →</a></div>)}</div> : <div className={styles.empty}>還沒有自己的筆記。</div>}</div></section>
          <section className={brandStyles.recentCard}><div className={brandStyles.recentHeading}><h2>最近書籤</h2><a href="#courses">查看全部 <ChevronRight aria-hidden="true" size={14} /></a></div><p>看到重要段落時，先留下時間點。</p><div className={`${styles.panel} ${brandStyles.recentPanel}`}>{data.recent_bookmarks.length ? <div className={styles.list}>{data.recent_bookmarks.map((mark) => <div className={styles.listItem} key={mark.id}><strong>{mark.label || mark.video_title}</strong><p>{time(mark.start_ms)} {mark.note || ""}</p><a href={`/review/learn/${mark.youtube_video_id}?t=${Math.floor(mark.start_ms / 1000)}`}>跳到影片 →</a></div>)}</div> : <div className={styles.empty}>還沒有書籤。</div>}</div></section>
        </div>
      </div>
    </main>
  );
}
