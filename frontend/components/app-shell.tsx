"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpenText,
  ChevronDown,
  CircleHelp,
  FileAudio,
  Gauge,
  Library,
  Menu,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  X
} from "lucide-react";
import { useEffect, useState } from "react";

const navigation = [
  { href: "/", label: "儀表板", icon: Gauge, match: (path: string | null) => path === "/" },
  { href: "/jobs/new", label: "新增任務", icon: Plus, match: (path: string | null) => path === "/jobs/new" },
  { href: "/#jobs", label: "任務記錄", icon: FileAudio, match: (path: string | null) => Boolean(path && path.startsWith("/jobs/") && path !== "/jobs/new") },
  { href: "/#glossary", label: "術語庫", icon: Library, match: () => false }
];

type FontSize = "standard" | "large" | "xlarge";
const fontSizes: FontSize[] = ["standard", "large", "xlarge"];

function nextFontSize(current: FontSize): FontSize {
  return fontSizes[(fontSizes.indexOf(current) + 1) % fontSizes.length];
}

export default function AppShell({
  children,
  title,
  description,
  actions
}: {
  children: React.ReactNode;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [fontSize, setFontSize] = useState<FontSize>("large");

  useEffect(() => {
    const saved = localStorage.getItem("course-transcript-font-size");
    const initialSize: FontSize = saved === "standard" || saved === "large" || saved === "xlarge" ? saved : "large";
    setFontSize(initialSize);
    document.documentElement.setAttribute("data-font-size", initialSize);
  }, []);

  function handleFontSizeChange(size: FontSize) {
    setFontSize(size);
    document.documentElement.setAttribute("data-font-size", size);
    localStorage.setItem("course-transcript-font-size", size);
  }

  return (
    <div className="app-frame">
      <aside className={`sidebar ${menuOpen ? "sidebar--open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true"><Sparkles size={20} strokeWidth={2.4} /></div>
          <div>
            <div className="brand-name">Course Transcript</div>
            <div className="brand-caption">AI 課程轉錄工作台</div>
          </div>
          <button className="icon-button sidebar-close" onClick={() => setMenuOpen(false)} aria-label="關閉選單"><X size={20} /></button>
        </div>

        <nav className="primary-nav" aria-label="主要導覽">
          <div className="nav-label">工作區</div>
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = item.match(pathname);
            return (
              <Link key={item.label} href={item.href} className={`nav-item ${active ? "nav-item--active" : ""}`} onClick={() => setMenuOpen(false)}>
                <Icon size={18} /><span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-spacer" />
        <div className="pipeline-card">
          <div className="pipeline-card__icon"><ShieldCheck size={18} /></div>
          <div><strong>私人工作區已啟用</strong><span>實際服務狀態請查看儀表板與任務紀錄</span></div>
        </div>
        <nav className="secondary-nav" aria-label="次要導覽">
          <a href="#help" className="nav-item"><CircleHelp size={18} /><span>使用說明</span></a>
          <a href="#settings" className="nav-item"><Settings size={18} /><span>系統設定</span></a>
        </nav>
        <div className="account-row">
          <div className="avatar">R</div>
          <div className="account-copy"><strong>Randy</strong><span>系統管理員</span></div>
          <ChevronDown size={16} />
        </div>
      </aside>

      {menuOpen && <button className="sidebar-scrim" onClick={() => setMenuOpen(false)} aria-label="關閉導覽" />}

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMenuOpen(true)} aria-label="開啟選單"><Menu size={26} /></button>
          <div className="search-box"><Search size={20} /><input aria-label="搜尋任務" placeholder="搜尋檔名、課程或任務編號" /><kbd>⌘ K</kbd></div>

          <div className="font-size-switcher" role="group" aria-label="字體大小">
            <span className="font-size-label">字體切換：</span>
            <button type="button" aria-pressed={fontSize === "standard"} className={`font-size-btn ${fontSize === "standard" ? "active" : ""}`} onClick={() => handleFontSizeChange("standard")}>A 標準</button>
            <button type="button" aria-pressed={fontSize === "large"} className={`font-size-btn ${fontSize === "large" ? "active" : ""}`} onClick={() => handleFontSizeChange("large")}>A+ 大字</button>
            <button type="button" aria-pressed={fontSize === "xlarge"} className={`font-size-btn ${fontSize === "xlarge" ? "active" : ""}`} onClick={() => handleFontSizeChange("xlarge")}>A++ 特大</button>
          </div>
          <button
            type="button"
            className="icon-button mobile-font-size-button"
            aria-label={`目前為${fontSize === "standard" ? "標準" : fontSize === "large" ? "大字" : "特大字"}，點擊切換下一級字體`}
            onClick={() => handleFontSizeChange(nextFontSize(fontSize))}
          >
            <span aria-hidden="true">Aa</span>
          </button>

          <div className="topbar-status"><span className="status-dot status-dot--success" /><span>私人連線</span></div>
        </header>

        <div className="content-wrap">
          <div className="page-heading">
            <div>
              <div className="eyebrow"><BookOpenText size={15} /> AI TRANSCRIPTION WORKSPACE</div>
              <h1>{title}</h1>
              {description && <p>{description}</p>}
            </div>
            {actions && <div className="page-actions">{actions}</div>}
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
