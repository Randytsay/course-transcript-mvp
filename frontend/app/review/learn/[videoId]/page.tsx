"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "../learning.module.css";

type Segment = { id:number; segment_index:number; start_ms:number; end_ms:number; working_text:string };
type Citation = { segment_index:number; start_ms:number; end_ms:number; text:string };
type StudyItem = Record<string, unknown> & { source_segment_indexes?: number[] };
type Artifact = {
  id:string; title:string; source_sha256:string; subtitle_version_id:string; prompt_version:string;
  model:string; generated_at:string; is_stale:boolean; latest_subtitle_version:number | null;
  citations:Citation[];
  content:{
    overview?: StudyItem;
    detailed_notes?: StudyItem[];
    quick_review_10m?: StudyItem[];
    quick_review_3m?: StudyItem[];
    key_points?: StudyItem[];
    qa?: StudyItem[];
    flashcards?: StudyItem[];
    quiz?: StudyItem[];
    glossary?: StudyItem[];
  };
};
type Lesson = {
  video:{youtube_video_id:string;title:string;duration_ms:number|null};
  progress:{last_playback_ms:number;reviewed_until_ms:number}|null;
  learning_state:{learning_status:"not_started"|"in_progress"|"completed";saved:number};
  segments:Segment[]; artifact:Artifact|null;
  notes:Array<{id:string;title:string|null;body:string;start_ms:number|null;updated_at:string}>;
  bookmarks:Array<{id:string;label:string|null;note:string|null;start_ms:number}>;
  review_schedule:{next_due_at:string|null;stage:number}|null;
  quiz_attempts:Array<{id:string;score:number;total:number;created_at:string}>;
};
type Me = {csrf_token:string; user:{display_name:string}};
type Player = { getCurrentTime:()=>number; seekTo:(seconds:number,allow:boolean)=>void; destroy:()=>void };
type YTNamespace = { Player:new(element:HTMLElement,config:{videoId:string;playerVars?:Record<string,number>;events?:{onReady?:(event:{target:Player})=>void}})=>Player };
declare global { interface Window { YT?:YTNamespace; onYouTubeIframeAPIReady?:()=>void } }
let ytPromise:Promise<void>|null=null;
function loadYT(){if(typeof window==="undefined")return Promise.resolve();if(window.YT?.Player)return Promise.resolve();if(ytPromise)return ytPromise;ytPromise=new Promise<void>((resolve)=>{const prior=window.onYouTubeIframeAPIReady;window.onYouTubeIframeAPIReady=()=>{prior?.();resolve()};if(!document.getElementById("learning-youtube-api")){const s=document.createElement("script");s.id="learning-youtube-api";s.src="https://www.youtube.com/iframe_api";s.async=true;document.head.appendChild(s)}});return ytPromise}
function stamp(ms:number){const total=Math.max(0,Math.floor(ms/1000));const h=Math.floor(total/3600);const m=Math.floor((total%3600)/60);const s=total%60;return h?`${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`:`${m}:${String(s).padStart(2,"0")}`}
function asText(value:unknown){return typeof value==="string"?value:""}
function asStrings(value:unknown){return Array.isArray(value)?value.map(String).filter(Boolean):[]}

export default function LearningLessonPage(){
  const params=useParams<{videoId:string}>();const router=useRouter();const videoId=params.videoId;
  const hostRef=useRef<HTMLDivElement|null>(null);const playerRef=useRef<Player|null>(null);
  const [me,setMe]=useState<Me|null>(null);const [lesson,setLesson]=useState<Lesson|null>(null);const [currentMs,setCurrentMs]=useState(0);
  const [tab,setTab]=useState("transcript");const [message,setMessage]=useState<string|null>(null);const [noteBody,setNoteBody]=useState("");const [noteTitle,setNoteTitle]=useState("");
  const [revealedCard,setRevealedCard]=useState<string|null>(null);const [answers,setAnswers]=useState<Record<string,number>>({});const [quizResult,setQuizResult]=useState<string|null>(null);
  const headers=useCallback(()=>({"Content-Type":"application/json",...(me?.csrf_token?{"X-Review-CSRF":me.csrf_token}:{})}),[me?.csrf_token]);
  const reload=useCallback(async()=>{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}`,{cache:"no-store",credentials:"same-origin"});if(r.status===401){router.replace("/review");return null}if(!r.ok)throw new Error("目前無法讀取這堂課");const body:Lesson=await r.json();setLesson(body);return body},[router,videoId]);

  useEffect(()=>{let cancel=false;async function boot(){try{const mr=await fetch("/api/v1/review/auth/me",{cache:"no-store",credentials:"same-origin"});if(mr.status===401){router.replace("/review");return}if(!mr.ok)throw new Error("目前無法確認登入狀態");const m:Me=await mr.json();if(cancel)return;setMe(m);const body=await reload();if(!body||cancel)return;let start=body.progress?.last_playback_ms||0;const query=new URLSearchParams(window.location.search);const explicit=Number(query.get("t")||"");if(Number.isFinite(explicit)&&explicit>=0&&query.has("t"))start=explicit*1000;setCurrentMs(start);const requested=query.get("tab");if(requested)setTab(requested)}catch(e){if(!cancel)setMessage(e instanceof Error?e.message:"載入失敗")}}void boot();return()=>{cancel=true}},[reload,router]);

  useEffect(()=>{if(!lesson||!hostRef.current)return;let disposed=false;void loadYT().then(()=>{if(disposed||!hostRef.current||!window.YT?.Player)return;playerRef.current?.destroy();const node=document.createElement("div");hostRef.current.replaceChildren(node);playerRef.current=new window.YT.Player(node,{videoId,playerVars:{rel:0,modestbranding:1,playsinline:1},events:{onReady:({target})=>{if(currentMs>0)target.seekTo(currentMs/1000,true)}}})});return()=>{disposed=true;playerRef.current?.destroy();playerRef.current=null}},[lesson?.video.youtube_video_id,videoId]);

  useEffect(()=>{if(!lesson)return;const timer=window.setInterval(()=>{const p=playerRef.current;if(!p)return;const value=Math.max(0,Math.floor(p.getCurrentTime()*1000));setCurrentMs(value)},1000);return()=>window.clearInterval(timer)},[lesson]);
  useEffect(()=>{if(!lesson||!me)return;const timer=window.setInterval(()=>{const p=playerRef.current;if(!p)return;const value=Math.max(0,Math.floor(p.getCurrentTime()*1000));void fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/watch`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({last_playback_ms:value})})},10000);return()=>window.clearInterval(timer)},[headers,lesson,me,videoId]);

  const citations=useMemo(()=>new Map((lesson?.artifact?.citations||[]).map(c=>[c.segment_index,c])),[lesson?.artifact?.citations]);
  const seek=(ms:number)=>{playerRef.current?.seekTo(ms/1000,true);setCurrentMs(ms);window.scrollTo({top:0,behavior:"smooth"})};
  const sourceButtons=(item:StudyItem)=>{const indexes=Array.isArray(item.source_segment_indexes)?item.source_segment_indexes:[];return <span>{indexes.slice(0,6).map(index=>{const c=citations.get(Number(index));return c?<button className={styles.citation} key={Number(index)} onClick={()=>seek(c.start_ms)} type="button">▶ {stamp(c.start_ms)}</button>:null})}</span>};
  async function statePatch(payload:Record<string,unknown>){const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/state`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify(payload)});if(!r.ok){const b=await r.json().catch(()=>({}));throw new Error(b.detail||"更新失敗")}await reload()}
  async function addBookmark(){try{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/bookmarks`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({start_ms:currentMs,label:`${stamp(currentMs)} 重點`})});if(!r.ok)throw new Error("書籤新增失敗");setMessage("已把這個時間點加入書籤");await reload()}catch(e){setMessage(e instanceof Error?e.message:"書籤新增失敗")}}
  async function addNote(){if(!noteBody.trim())return;try{const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/notes`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({title:noteTitle||null,body:noteBody,start_ms:currentMs})});if(!r.ok){const b=await r.json().catch(()=>({}));throw new Error(b.detail||"筆記儲存失敗")}setNoteBody("");setNoteTitle("");setMessage("筆記已儲存");await reload()}catch(e){setMessage(e instanceof Error?e.message:"筆記儲存失敗")}}
  async function removeNote(id:string){await fetch(`/api/v1/review/learning/notes/${id}`,{method:"DELETE",credentials:"same-origin",headers:headers()});await reload()}
  async function removeBookmark(id:string){await fetch(`/api/v1/review/learning/bookmarks/${id}`,{method:"DELETE",credentials:"same-origin",headers:headers()});await reload()}
  async function rateCard(card:StudyItem,rating:"again"|"hard"|"good"|"easy"){if(!lesson?.artifact)return;await fetch("/api/v1/review/learning/flashcards/review",{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({artifact_id:lesson.artifact.id,card_key:asText(card.id)||asText(card.front),rating})});setRevealedCard(null);setMessage("已安排下一次複習")}
  async function gradeQuiz(){const quiz=lesson?.artifact?.content.quiz||[];if(!quiz.length||!lesson?.artifact)return;let score=0;for(const q of quiz){const id=asText(q.id)||asText(q.question);if(answers[id]===Number(q.answer_index))score++}setQuizResult(`${score} / ${quiz.length}`);await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/quiz-attempts`,{method:"POST",credentials:"same-origin",headers:headers(),body:JSON.stringify({score,total:quiz.length,artifact_id:lesson.artifact.id,answers})})}
  async function finishReview(){const r=await fetch(`/api/v1/review/learning/videos/${encodeURIComponent(videoId)}/review-complete`,{method:"POST",credentials:"same-origin",headers:headers()});if(r.ok){setMessage("本次複習完成，已安排下一次複習時間");await reload()}}

  if(message&&!lesson)return <main className={styles.page}><div className={styles.shell}><p className={styles.error}>{message}</p></div></main>;
  if(!lesson)return <main className={styles.page}><div className={styles.shell}><p className={styles.status}>正在準備這堂課…</p></div></main>;
  const artifact=lesson.artifact;const content=artifact?.content||{};const active=lesson.segments.find(s=>currentMs>=s.start_ms&&currentMs<s.end_ms);
  return <main className={styles.page}><div className={styles.shell}>
    <header className={styles.topbar}><a className={styles.brand} href="/review/learn"><span className={styles.brandMark}>學</span><span className={styles.brandText}><strong>佛學共學平台</strong><span>我的學習中心</span></span></a><nav className={styles.nav}><a href="/review/learn">學習中心</a><a href="/review/videos">字幕共修</a><a href="/review/learn/review">複習中心</a><a href="/review/learn/search">知識搜尋</a></nav></header>
    <div className={styles.workspace}>
      <section className={styles.videoPanel}>
        <div className={styles.videoFrame} ref={hostRef}/>
        <div className={styles.videoContent}><div className={styles.lessonHeading}><div><p className={styles.eyebrow}>正在學習</p><h1>{lesson.video.title}</h1><p className={styles.muted}>目前 {stamp(currentMs)} / {stamp(lesson.video.duration_ms||0)} {active?`・字幕第 ${active.segment_index} 段`:""}</p></div><div className={styles.actionRow}>
          <button type="button" onClick={()=>void addBookmark()}>🔖 收藏此刻</button>
          <button type="button" onClick={()=>void statePatch({saved:!Boolean(lesson.learning_state.saved)})}>{lesson.learning_state.saved?"★ 已收藏":"☆ 收藏課程"}</button>
          {lesson.learning_state.learning_status==="completed"?<button type="button" onClick={()=>void statePatch({learning_status:"in_progress"})}>改為學習中</button>:<button className={styles.accent} type="button" onClick={()=>{if(window.confirm("確定已完成這堂課的學習？之後仍可繼續觀看與做筆記。"))void statePatch({learning_status:"completed"})}}>✓ 我已學完</button>}
          <a href={`/review/videos/${videoId}`}>協助校字幕</a>
        </div></div>{message?<p className={styles.muted}>{message}</p>:null}</div>
        <div className={styles.tabs} role="tablist">
          {[['transcript','影片＋字幕'],['notes','AI 筆記'],['review','快速複習'],['qa','問答'],['cards','Flashcards'],['quiz','自我測驗'],['personal','我的筆記']].map(([key,label])=><button className={tab===key?styles.active:""} key={key} onClick={()=>setTab(key)} type="button">{label}</button>)}
        </div>
        <div className={styles.tabBody}>
          {tab==="transcript"?<div className={styles.list}>{lesson.segments.map(segment=><button key={segment.id} onClick={()=>seek(segment.start_ms)} type="button" className={styles.listItem} style={{textAlign:"left",cursor:"pointer",background:active?.id===segment.id?"#edf5f1":undefined}}><strong>{stamp(segment.start_ms)}</strong><p>{segment.working_text}</p></button>)}</div>:null}
          {tab==="notes"?<>{!artifact?<div className={styles.empty}>這堂課的 AI 學習整理尚未產生。你仍可看影片、做自己的筆記與書籤。</div>:<>{artifact.is_stale?<p className={styles.error}>字幕已有較新的核准版本；這份 AI 筆記仍可閱讀，但已標記為舊版，等待管理員重新整理。</p>:null}<div className={styles.noteSection}><h3>{asText(content.overview?.title)||"本堂總覽"}</h3><p>{asText(content.overview?.summary)}</p>{content.overview?sourceButtons(content.overview):null}</div>{(content.detailed_notes||[]).map((item,i)=><div className={styles.noteSection} key={i}><h3>{asText(item.heading)||`重點 ${i+1}`}</h3><ul>{asStrings(item.points).map((p,j)=><li key={j}>{p}</li>)}</ul>{sourceButtons(item)}</div>)}{(content.glossary||[]).length?<div className={styles.noteSection}><h3>名詞整理</h3>{(content.glossary||[]).map((item,i)=><p key={i}><strong>{asText(item.term)}</strong>：{asText(item.explanation)} {sourceButtons(item)}</p>)}</div>:null}<p className={styles.muted}>依字幕版本 v{artifact.latest_subtitle_version} 整理・{artifact.model}・每個時間標記都可回到影片核對。</p></>}</> :null}
          {tab==="review"?<>{!artifact?<div className={styles.empty}>AI 快速複習尚未產生，可以先回看影片與自己的筆記。</div>:<><div className={styles.noteSection}><h3>3 分鐘快速複習</h3>{(content.quick_review_3m||[]).map((item,i)=><p key={i}>{i+1}. {asText(item.text)} {sourceButtons(item)}</p>)}</div><div className={styles.noteSection}><h3>10 分鐘複習</h3>{(content.quick_review_10m||[]).map((item,i)=><div key={i}><strong>{asText(item.heading)}</strong><p>{asText(item.summary)}</p>{sourceButtons(item)}</div>)}</div><div className={styles.noteSection}><h3>本堂一定要記住</h3>{(content.key_points||[]).map((item,i)=><p key={i}>✓ {asText(item.text)} {sourceButtons(item)}</p>)}</div><button className={styles.primaryButton} type="button" onClick={()=>void finishReview()}>完成本次複習</button></>}</>:null}
          {tab==="qa"?<>{(content.qa||[]).length?(content.qa||[]).map((item,i)=><div className={styles.noteSection} key={i}><h3>Q：{asText(item.question)}</h3><p>A：{asText(item.answer)}</p>{sourceButtons(item)}</div>):<div className={styles.empty}>尚無本堂問答整理。</div>}</>:null}
          {tab==="cards"?<>{(content.flashcards||[]).length?(content.flashcards||[]).map((card,i)=>{const key=asText(card.id)||String(i);const open=revealedCard===key;return <div className={styles.flashcard} key={key}><strong>{asText(card.front)}</strong>{open?<><div className={styles.flashAnswer}>{asText(card.back)}<div>{sourceButtons(card)}</div></div><div className={styles.ratingRow}><button onClick={()=>void rateCard(card,"again")} type="button">再來一次</button><button onClick={()=>void rateCard(card,"hard")} type="button">有點難</button><button onClick={()=>void rateCard(card,"good")} type="button">記得</button><button onClick={()=>void rateCard(card,"easy")} type="button">很熟</button></div></>:<button className={styles.primaryButton} style={{marginTop:14}} onClick={()=>setRevealedCard(key)} type="button">看答案</button>}</div>}):<div className={styles.empty}>尚無 Flashcards。</div>}</>:null}
          {tab==="quiz"?<>{(content.quiz||[]).length?(content.quiz||[]).map((q,i)=>{const id=asText(q.id)||asText(q.question)||String(i);return <div className={styles.quizQuestion} key={id}><strong>{i+1}. {asText(q.question)}</strong><div className={styles.choices}>{asStrings(q.choices).map((choice,j)=><button className={answers[id]===j?styles.selected:""} key={j} onClick={()=>setAnswers(a=>({...a,[id]:j}))} type="button">{choice}</button>)}</div>{quizResult?<p className={styles.muted}>正解：{asStrings(q.choices)[Number(q.answer_index)]}。{asText(q.explanation)} {sourceButtons(q)}</p>:null}</div>}):<div className={styles.empty}>尚無自我測驗。</div>}{(content.quiz||[]).length?<><button className={styles.primaryButton} onClick={()=>void gradeQuiz()} type="button">送出答案</button>{quizResult?<p><strong>本次成績：{quizResult}</strong></p>:null}</>:null}</>:null}
          {tab==="personal"?<div><h3>新增自己的筆記</h3><input className={styles.input} placeholder="標題（可留空）" value={noteTitle} onChange={e=>setNoteTitle(e.target.value)}/><textarea className={styles.textarea} placeholder={`把此刻 ${stamp(currentMs)} 的理解、疑問或提醒寫下來…`} value={noteBody} onChange={e=>setNoteBody(e.target.value)}/><button className={styles.saveButton} onClick={()=>void addNote()} type="button">儲存筆記（連結目前時間）</button><div className={styles.miniList}>{lesson.notes.map(n=><div className={styles.miniItem} key={n.id}><button onClick={()=>void removeNote(n.id)} type="button">刪除</button><strong>{n.title||stamp(n.start_ms||0)}</strong><p>{n.body}</p>{n.start_ms!=null?<button className={styles.citation} onClick={()=>seek(n.start_ms||0)} type="button">▶ {stamp(n.start_ms||0)}</button>:null}</div>)}</div></div>:null}
        </div>
      </section>
      <aside className={styles.sidePanel}><h2>這堂課</h2><div className={styles.metaRow}><span className={styles.badge}>{lesson.learning_state.learning_status==="completed"?"已學完":lesson.learning_state.learning_status==="in_progress"?"學習中":"尚未開始"}</span>{artifact?<span className={styles.badge}>AI 學習整理</span>:null}{lesson.review_schedule?.next_due_at?<span className={styles.badge}>已排複習</span>:null}</div><p className={styles.muted}>觀看進度、學習完成、複習與字幕共修分開記錄，不會因影片播完就自動算學習完成。</p><h2 style={{marginTop:22}}>我的書籤</h2><div className={styles.miniList}>{lesson.bookmarks.length?lesson.bookmarks.map(mark=><div className={styles.miniItem} key={mark.id}><button onClick={()=>void removeBookmark(mark.id)} type="button">刪除</button><a href="#" onClick={e=>{e.preventDefault();seek(mark.start_ms)}}>{mark.label||stamp(mark.start_ms)}</a>{mark.note?<p>{mark.note}</p>:null}</div>):<div className={styles.empty}>看到重要段落時按「收藏此刻」。</div>}</div><h2 style={{marginTop:22}}>最近測驗</h2><div className={styles.miniList}>{lesson.quiz_attempts.length?lesson.quiz_attempts.slice(0,5).map(a=><div className={styles.miniItem} key={a.id}><strong>{a.score} / {a.total}</strong><p>{new Date(a.created_at).toLocaleString("zh-TW")}</p></div>):<p className={styles.muted}>還沒有測驗紀錄。</p>}</div></aside>
    </div>
  </div></main>
}
