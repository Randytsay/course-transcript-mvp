"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  ChevronDown,
  CircleHelp,
  FileAudio,
  Gauge,
  Library,
  Menu,
  Plus,
  Search,
  Settings,
  Sparkles,
  X
} from "lucide-react";
import { useState } from "react";

const navigation = [
  { href: "/", label: "儀表板", icon: Gauge, match: (path: string) => path === "/" },
  { href: "/jobs/new", label: "新增任務", icon: Plus, match: (path: string) => path === "/jobs/new" },
  { href: "/#jobs", label: "任務記錄", icon: FileAudio, match: (path: string) => path.startsWith("/jobs/") && path !== "/jobs/new" },
  { href: "/#glossary", label: "術語庫", icon: Library, match: () => false },
  { href: "/review-admin/ai-accounts", label: "帳號設定", icon: Settings, match: (path: string) => path.startsWith("/review-admin/ai-accounts") }
];

export default function AppShell({
  children,
  title,
  description,
  actions,
  onSearch
}: {
  children: React.ReactNode;
  title: string;
  description?: string;
  actions?: React.ReactNode;
  onSearch?: (query: string) => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  function handleSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    const query = searchQuery.trim();
    if (!query) return;
    if (onSearch) {
      onSearch(query);
      return;
    }
    router.push(`/?q=${encodeURIComponent(query)}#jobs`);
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
        <nav className="secondary-nav" aria-label="次要導覽">
          <Link href="/review-admin/help" className="nav-item" onClick={() => setMenuOpen(false)}><CircleHelp size={18} /><span>使用說明</span></Link>
          <Link href="/review-admin/ai-accounts" className="nav-item" onClick={() => setMenuOpen(false)}><Settings size={18} /><span>系統設定</span></Link>
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
          <div className="search-box"><Search size={20} /><input aria-label="搜尋任務" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={handleSearchKeyDown} placeholder="搜尋檔名、課程或任務編號" /><kbd>⌘ K</kbd></div>
          <Link href="/review-admin/help" className="topbar-help"><CircleHelp size={17} />使用說明</Link>
        </header>

        <div className="content-wrap">
          <div className="page-heading">
            <div>
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
