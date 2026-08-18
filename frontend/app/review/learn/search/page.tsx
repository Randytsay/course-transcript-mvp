"use client";

import { FormEvent, useState } from "react";
import styles from "../learning.module.css";

type SubtitleResult={segment_id:number;youtube_video_id:string;segment_index:number;start_ms:number;end_ms:number;text:string;video_title:string};
type ArtifactResult={id:string;youtube_video_id:string;video_title:string;title:string;artifact_type:string;snippet:string;start_ms:number;generated_at:string};
type SearchResponse={query:string;subtitle_results:SubtitleResult[];artifact_results:ArtifactResult[]};
function stamp(ms:number){const total=Math.max(0,Math.floor(ms/1000));const h=Math.floor(total/3600);const m=Math.floor((total%3600)/60);const s=total%60;return h?`${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${m}:${String(s).padStart(2,"0")}`}
export default function LearningSearchPage(){
 const [query,setQuery]=useState("");const [data,setData]=useState<SearchResponse|null>(null);const [loading,setLoading]=useState(false);const [error,setError]=useState<string|null>(null);
 async function submit(event:FormEvent){event.preventDefault();if(query.trim().length<2){setError("請至少輸入兩個字");return}setLoading(true);setError(null);try{const r=await fetch(`/api/v1/review/learning/search?q=${encodeURIComponent(query.trim())}`,{cache:"no-store",credentials:"same-origin"});if(r.status===401){window.location.assign("/review");return}if(!r.ok){const b=await r.json().catch(()=>({}));throw new Error(b.detail||"搜尋失敗")}setData(await r.json())}catch(e){setError(e instanceof Error?e.message:"搜尋失敗")}finally{setLoading(false)}}
 const total=(data?.subtitle_results.length||0)+(data?.artifact_results.length||0);
 return <main className={styles.page}><div className={styles.shell}>
  <header className={styles.topbar}><a className={styles.brand} href="/review/learn"><span className={styles.brandMark}>學</span><span className={styles.brandText}><strong>佛學共學平台</strong><span>知識搜尋</span></span></a><nav className={styles.nav}><a href="/review/learn">學習中心</a><a href="/review/learn/review">複習中心</a><a href="/review/videos">字幕共修</a></nav></header>
  <section className={styles.heroMain}><p className={styles.eyebrow}>知識搜尋</p><h1>忘了在哪一堂聽過？直接從核准字幕與 AI 學習整理找回來。</h1><p>搜尋結果優先呈現可追溯來源；點時間就回到影片原段落。這裡不讓 AI 憑記憶猜答案。</p><form className={styles.searchBar} onSubmit={submit}><input aria-label="搜尋課程內容" placeholder="例如：龍華三會、兜率天、發菩提心…" value={query} onChange={e=>setQuery(e.target.value)}/><button disabled={loading} type="submit">{loading?"搜尋中…":"搜尋"}</button></form>{error?<p className={styles.error}>{error}</p>:null}</section>
  {data?<><div className={styles.sectionHeader}><div><h2>「{data.query}」的結果</h2><p>找到 {total} 筆可追溯內容。</p></div></div>
   <div className={styles.twoColumn}>
    <section className={styles.panel}><h2>影片字幕</h2>{data.subtitle_results.length?data.subtitle_results.map(item=><article className={styles.result} key={`${item.youtube_video_id}-${item.segment_id}`}><h3>{item.video_title}</h3><p>{item.text}</p><a href={`/review/learn/${item.youtube_video_id}?t=${Math.floor(item.start_ms/1000)}`}>▶ {stamp(item.start_ms)} 回到影片</a></article>):<div className={styles.empty}>核准字幕裡沒有找到相符內容。</div>}</section>
    <section className={styles.panel}><h2>AI 學習整理</h2><p className={styles.muted}>AI 內容仍以核准字幕版本為來源，點時間可回影片核對。</p>{data.artifact_results.length?data.artifact_results.map(item=><article className={styles.result} key={item.id}><h3>{item.video_title}</h3><p>{item.snippet}</p><a href={`/review/learn/${item.youtube_video_id}?t=${Math.floor(item.start_ms/1000)}&tab=notes`}>▶ {stamp(item.start_ms)} 查看來源</a></article>):<div className={styles.empty}>AI 學習整理裡沒有找到相符內容。</div>}</section>
   </div>
  </>:<div className={styles.sectionHeader}><div><h2>可以怎麼找</h2><p>輸入經名、人物、名詞、修行概念或你記得的一小段話。</p></div></div>}
 </div></main>
}
