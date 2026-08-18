import styles from "../learn/learning.module.css";

const steps = [
  ["1", "選一堂課開始", "從「我的學習中心」進入課程。影片每隔一段時間會保存觀看位置，下次可以從上次的位置繼續。"],
  ["2", "學習時留下自己的線索", "看到重要段落可以按「收藏此刻」，也可以在「我的筆記」記下理解、疑問與提醒；筆記會連回當時的影片時間。"],
  ["3", "看 AI 整理，但隨時回原影片核對", "AI 筆記、重點、問答、Flashcards 與測驗只會使用管理員核定的字幕版本。看到時間標記可直接跳回影片來源。"],
  ["4", "確定學完後再自己標記", "影片播完不會自動算「已學完」。完成理解後按「我已學完」，系統才會把這堂課放進後續複習節奏。"],
  ["5", "到期時回來快速複習", "複習中心會依 1、3、7、14、30 天節奏安排課程複習；Flashcards 會依你的熟悉程度另外安排。"],
  ["6", "發現字幕錯字就一起共修", "從課程頁按「協助校字幕」。不用調時間碼，只要修正文句並送出建議；正式字幕由管理員審核。"],
];

export default function LearnerHelpPage() {
  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.topbar}>
          <a className={styles.brand} href="/review/learn">
            <span className={styles.brandMark}>學</span>
            <span className={styles.brandText}><strong>佛學共學平台</strong><span>使用說明</span></span>
          </a>
          <nav className={styles.nav} aria-label="使用說明導覽">
            <a href="/review/learn">學習中心</a>
            <a href="/review/videos">字幕共修</a>
            <a href="/review/learn/review">複習中心</a>
            <a href="/review/learn/search">知識搜尋</a>
          </nav>
        </header>

        <section className={styles.heroMain}>
          <p className={styles.eyebrow}>第一次使用，從這裡開始</p>
          <h1>看課、做筆記、複習、校字幕，其實只是一條學習路徑。</h1>
          <p>不用一次學會所有功能。第一次只要「選課 → 看影片 → 留下進度」；需要時再使用 AI 筆記、複習與字幕共修。</p>
        </section>

        <div className={styles.sectionHeader}><div><h2>第一次使用：6 個步驟</h2><p>照順序做一次，就會知道整個平台怎麼運作。</p></div></div>
        <section className={styles.lessonGrid}>
          {steps.map(([number, title, description]) => (
            <article className={styles.lessonCard} key={number}>
              <div className={styles.lessonBody}>
                <div className={styles.metaRow}><span className={styles.badge}>步驟 {number}</span></div>
                <h3 className={styles.lessonTitle}>{title}</h3>
                <p className={styles.muted} style={{fontSize:14,lineHeight:1.7}}>{description}</p>
              </div>
            </article>
          ))}
        </section>

        <div className={styles.sectionHeader}><div><h2>四種進度，不要混在一起</h2><p>平台刻意分開記錄，避免「看完」被誤認為「學會」或「校完字幕」。</p></div></div>
        <section className={styles.panel}>
          <div className={styles.stats}>
            <div className={styles.statCard}><strong>▶</strong><span>觀看進度：影片看到哪裡</span></div>
            <div className={styles.statCard}><strong>✓</strong><span>學習完成：你主動標記已學完</span></div>
            <div className={styles.statCard}><strong>↻</strong><span>複習進度：何時該再複習</span></div>
            <div className={styles.statCard}><strong>校</strong><span>字幕共修：字幕校訂到哪裡</span></div>
          </div>
          <p className={styles.muted}>影片播放到最後，不會自動標記為「已學完」；學習完成也不代表你已經完成字幕校訂。</p>
        </section>

        <div className={styles.sectionHeader}><div><h2>課程頁每個分頁在做什麼</h2><p>需要什麼就用什麼，不必每一項都做。</p></div></div>
        <section className={styles.twoColumn}>
          <div className={styles.panel}><h2>影片＋字幕</h2><p>一邊看影片、一邊讀字幕。點字幕時間可跳到該段；看到重要地方可以收藏當下時間。</p></div>
          <div className={styles.panel}><h2>AI 筆記</h2><p>依已核准字幕整理詳細筆記與名詞。時間標記可直接回影片核對，不把 AI 當成不可驗證的答案。</p></div>
          <div className={styles.panel}><h2>快速複習</h2><p>提供 3 分鐘、10 分鐘與「一定要記住」重點。完成複習後，系統會安排下一次複習時間。</p></div>
          <div className={styles.panel}><h2>問答／Flashcards／測驗</h2><p>用不同方式重新叫回記憶。Flashcards 可回報「再來一次／有點難／記得／很熟」，測驗成績會留下紀錄。</p></div>
          <div className={styles.panel}><h2>我的筆記</h2><p>保存你自己的理解、疑問與提醒，和 AI 筆記分開。筆記可以連到當時的影片時間。</p></div>
          <div className={styles.panel}><h2>協助校字幕</h2><p>發現錯字時才需要進入字幕共修。只改文字、不改時間碼；送出的是建議，不會直接覆蓋正式字幕或 YouTube。</p></div>
        </section>

        <div className={styles.sectionHeader}><div><h2>常見問題</h2><p>第一次使用最容易疑惑的地方。</p></div></div>
        <section className={styles.panel}>
          <div className={styles.list}>
            <div className={styles.listItem}><strong>影片看完會自動變成「已學完」嗎？</strong><p>不會。學習完成需要你自己按「我已學完」，避免單純播放完畢被誤判成真正完成學習。</p></div>
            <div className={styles.listItem}><strong>為什麼有些課沒有 AI 筆記？</strong><p>AI 正式內容只會在管理員核定字幕版本並完成整理後出現。沒有 AI 筆記時仍可正常看影片、字幕、做書籤與個人筆記。</p></div>
            <div className={styles.listItem}><strong>AI 筆記可信嗎？</strong><p>正式 AI 筆記只使用管理員核定的不可變字幕版本，並保存來源版本與時間碼。重要內容仍建議點時間標記回原影片核對。</p></div>
            <div className={styles.listItem}><strong>字幕修正會立刻改到 YouTube 嗎？</strong><p>不會。你送出的是校訂建議，管理員審核後才會成為本地正式字幕；YouTube 發布又是另一個獨立的管理員步驟。</p></div>
            <div className={styles.listItem}><strong>我換手機或電腦，可以接著學嗎？</strong><p>可以。使用同一個已連結的登入身分後，觀看位置、學習狀態、個人筆記、書籤與共修紀錄都會跟著帳號。</p></div>
            <div className={styles.listItem}><strong>Google 和 LINE 都能登入，會變成兩個帳號嗎？</strong><p>不會自動把不同身分硬合併；登入後可明確加綁另一種登入方式，之後用任一已綁定方式回到同一個學習身分。</p></div>
          </div>
        </section>

        <div className={styles.sectionHeader}><div><h2>現在就開始</h2><p>第一次先完成一件事就好：選一堂課開始看。</p></div></div>
        <section className={styles.panel}>
          <div className={styles.cardActions} style={{maxWidth:520}}>
            <a className={styles.openButton} href="/review/learn">前往我的學習中心</a>
            <a className={styles.reviewButton} href="/review/videos">前往字幕共修</a>
          </div>
        </section>
      </div>
    </main>
  );
}
