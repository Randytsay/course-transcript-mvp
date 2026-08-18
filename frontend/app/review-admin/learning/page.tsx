"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./learning-admin.module.css";

type VideoRow={youtube_video_id:string;title:string;duration_ms:number|null;latest_version_id:string|null;latest_version_number:number|null;latest_source_sha256:string|null;artifact_id:string|null;artifact_source_sha256:string|null;artifact_generated_at:string|null;artifact_model:string|null;artifact_prompt_version:string|null;artifact_stale:boolean|null};
type Job={id:string;youtube_video_id:string;video_title:string;subtitle_version_id:string;artifact_type:string;prompt_version:string;model:string;status:"running"|"completed"|"failed";actor:string;error:string|null;started_at:string;finished_at:string|null};
type Overview={videos:VideoRow[];generation_jobs:Job[]};
function dateTime(value:string|null){if(!value)return"—";try{return new Intl.DateTimeFormat("zh-TW",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}).format(new Date(value))}catch{return value}}
export default function LearningAdminPage(){
 const [data,setData]=useState<Overview|null>(null);const [busy,setBusy]=useState<string|null>(null);const [message,setMessage]=useState<string|null>(null);const [error,setError]=useState<string|null>(null);
 const load=useCallback(async()=>{try{const r=await fetch("/api/v1/review-admin/learning/overview",{cache:"no-store",credentials:"same-origin"});if(!r.ok){const b=await r.json().catch(()=>({}));throw new Error(b.detail||"目前無法讀取 AI 學習內容狀態")}setData(await r.json())}catch(e){setError(e instanceof Error?e.message:"載入失敗")}},[]);
 useEffect(()=>{void load()},[load]);
 const counts=useMemo(()=>{const videos=data?.videos||[];return{total:videos.length,current:videos.filter(v=>v.artifact_id&&!v.artifact_stale).length,stale:videos.filter(v=>v.artifact_stale).length,missing:videos.filter(v=>!v.artifact_id).length}},[data]);
 async function generate(video:VideoRow,force:boolean){const action=force?"重新產生":"產生";if(!window.confirm(`${action}「${video.title}」的 AI 學習整理？\n\n來源將固定在目前不可變字幕版本 v${video.latest_version_number??"?"}，這會呼叫付費 LLM，但不會修改 YouTube。`))return;setBusy(video.youtube_video_id);setMessage(null);setError(null);try{const r=await fetch(`/api/v1/review-admin/learning/videos/${encodeURIComponent(video.youtube_video_id)}/generate`,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirm:true,force})});const b=await r.json().catch(()=>({}));if(!r.ok)throw new Error(b.detail||"AI 學習整理失敗");setMessage(b.generated?`「${video.title}」的 AI 學習整理已完成。`:`「${video.title}」已有目前字幕版本的最新學習整理，未重複呼叫模型。`);await load()}catch(e){setError(e instanceof Error?e.message:"AI 學習整理失敗");await load()}finally{setBusy(null)}}
 return <main className={styles.page}><div className={styles.shell}>
  <header className={styles.header}><div><p className={styles.eyebrow}>AI Learning Content</p><h1>AI 學習內容管理</h1><p>每份正式筆記都鎖定一個不可變字幕版本與 SHA。字幕有新版時，舊筆記不會消失，而是標示「需要更新」；學員閱讀不會自動觸發付費模型。</p></div><a href="/review/learn" style={{color:"#315c4b",fontWeight:800,textDecoration:"none"}}>查看學習平台 ↗</a></header>
  <div className={styles.note}>安全規則：產生內容需要管理員明確確認；模型只能根據核准字幕整理，伺服器會重新驗證來源段落並建立影片時間引用。這個流程不會呼叫 YouTube 字幕更新。</div>
  {message?<div className={styles.message}>{message}</div>:null}{error?<div className={styles.error}>{error}</div>:null}
  {data?<><section className={styles.summary}><div><strong>{counts.total}</strong><span>課程</span></div><div><strong>{counts.current}</strong><span>AI 內容最新</span></div><div><strong>{counts.stale}</strong><span>字幕更新待重整</span></div><div><strong>{counts.missing}</strong><span>尚未產生</span></div></section>
    <section className={styles.panel}><div className={styles.panelHeader}><div><h2>課程 AI 學習整理</h2><p>目前產生一組完整 Study Pack：詳細筆記、10/3 分鐘複習、重點、問答、Flashcards、測驗與名詞表。</p></div></div>
      <div className={styles.grid}>{data.videos.map(video=><article className={styles.videoCard} key={video.youtube_video_id}><h3>{video.title}</h3><div className={styles.meta}>
        <span className={styles.badge}>{video.latest_version_number?`字幕 v${video.latest_version_number}`:"尚無字幕版本"}</span>
        {!video.artifact_id?<span className={`${styles.badge} ${styles.missing}`}>尚無 AI 學習整理</span>:video.artifact_stale?<span className={`${styles.badge} ${styles.warn}`}>需要更新</span>:<span className={`${styles.badge} ${styles.good}`}>內容最新</span>}
        {video.artifact_model?<span className={styles.badge}>{video.artifact_model}</span>:null}
      </div>{video.artifact_generated_at?<p style={{fontSize:12,color:"#758178"}}>最近產生：{dateTime(video.artifact_generated_at)}・{video.artifact_prompt_version}</p>:null}
      <div className={styles.actions}><button className={styles.primary} disabled={busy!==null||!video.latest_version_id} onClick={()=>void generate(video,Boolean(video.artifact_id))} type="button">{busy===video.youtube_video_id?"整理中…":video.artifact_id?"重新產生":"產生 AI 學習整理"}</button>{video.artifact_id?<a href={`/review/learn/${video.youtube_video_id}?tab=notes`}>預覽學員畫面</a>:null}</div></article>)}</div>
    </section>
    <section className={styles.panel}><div className={styles.panelHeader}><div><h2>最近產生紀錄</h2><p>失敗不會影響核准字幕，也不會被誤標成成功。</p></div></div><div className={styles.jobs}>{data.generation_jobs.length?data.generation_jobs.map(job=><div className={styles.job} key={job.id}><div><strong>{job.video_title}</strong><p>{job.model}・開始 {dateTime(job.started_at)}{job.error?`・${job.error}`:""}</p></div><span className={`${styles.status} ${styles[job.status]}`}>{job.status==="completed"?"完成":job.status==="running"?"處理中":"失敗"}</span></div>):<div className={styles.state}>還沒有 AI 學習內容產生紀錄。</div>}</div></section>
  </>:<div className={styles.state}>正在讀取 AI 學習內容狀態…</div>}
 </div></main>
}
