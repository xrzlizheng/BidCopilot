"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  Building2,
  CircleHelp,
  Command,
  FileSignature,
  FolderKanban,
  LayoutDashboard,
  Layers3,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  ScrollText,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";

import { apiFetch, logout } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { PLATFORM_NAV } from "@/lib/platform-nav";
import { NotificationBell } from "@/components/notification-bell";

type NavigationLink = {
  href: string;
  label: string;
  permission?: string;
  icon: ReactNode;
};

type AiStatus = {
  llm: { configured: boolean; model: string | null; host: string | null };
  judge: { configured: boolean; model: string | null; host: string | null };
};

const SIDEBAR_STORAGE_KEY = "bidcopilot.sidebar.collapsed";

const groups: { label: string; links: NavigationLink[] }[] = [
  {
    label: "投标工作流",
    links: [
      { href: "/dashboard", label: "指挥台", icon: <LayoutDashboard size={16} /> },
      { href: "/opportunities", label: "机会中心", permission: "opportunity:read", icon: <Radar size={16} /> },
      { href: "/projects", label: "投标项目", permission: "project:read", icon: <FolderKanban size={16} /> },
      { href: "/contracts", label: "合同台账", permission: "project:read", icon: <FileSignature size={16} /> },
      { href: "/assets", label: "企业资产", permission: "asset:read", icon: <Building2 size={16} /> },
      { href: "/knowledge", label: "企业知识库", permission: "knowledge:read", icon: <BookOpen size={16} /> },
      { href: "/templates", label: "模板与技能", permission: "template:read", icon: <Layers3 size={16} /> },
    ],
  },
  {
    label: "运营与治理",
    links: [
      { href: "/data-ops", label: "数据运营", permission: "audit:read", icon: <Activity size={16} /> },
      { href: "/audit", label: "审计日志", permission: "audit:read", icon: <ScrollText size={16} /> },
      { href: "/policies", label: "合规策略", permission: "project:approve", icon: <ShieldCheck size={16} /> },
      { href: "/users", label: "成员与权限", permission: "user:manage", icon: <Users size={16} /> },
    ],
  },
  ...(PLATFORM_NAV.length > 0
    ? [{ label: "平台", links: PLATFORM_NAV.map((item) => ({ ...item })) }]
    : []),
  {
    label: "帮助",
    links: [{ href: "/help", label: "使用帮助", icon: <CircleHelp size={16} /> }],
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const user = useAuthStore((state) => state.user);
  const [collapsed, setCollapsed] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const ai = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => apiFetch<AiStatus>("/system/ai-status"),
    retry: false,
    staleTime: 60_000,
  });

  // 折叠状态持久化：首帧按展开渲染，挂载后再同步本地偏好，避免 SSR 水合不一致。
  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1");
    } catch {
      // 隐私模式下 localStorage 可能不可用，保持默认展开
    }
  }, []);

  function toggleSidebar() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // 忽略写入失败
      }
      return next;
    });
  }

  const visibleGroups = groups
    .map((group) => ({
      ...group,
      links: group.links.filter(
        (link) => !link.permission || hasPermission(user?.permissions, link.permission),
      ),
    }))
    .filter((group) => group.links.length > 0);
  const allLinks = visibleGroups.flatMap((group) => group.links);
  const current = allLinks.find(
    (link) => pathname === link.href || pathname.startsWith(`${link.href}/`),
  );

  async function handleLogout() {
    setLogoutError(null);
    try {
      await logout();
      router.replace("/login");
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : "退出失败，请重试");
    }
  }

  return (
    <div className="md:flex md:h-screen md:overflow-hidden">
      {logoutError ? (
        <div
          role="alert"
          className="fixed right-4 top-4 z-50 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 shadow"
        >
          {logoutError}
        </div>
      ) : null}
      <aside
        className={`aurora-panel relative hidden shrink-0 flex-col overflow-hidden border-r border-white/5 text-slate-200 transition-[width] duration-200 ease-out md:flex md:h-screen ${
          collapsed ? "md:w-[76px] px-2 py-5" : "md:w-64 px-4 py-6"
        }`}
      >
        <div className="aurora-grid pointer-events-none absolute inset-0 opacity-30" />

        <div className={`relative flex items-center ${collapsed ? "justify-center" : "justify-between gap-2"}`}>
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-aurora-400 to-aurora-500 text-ink-950 shadow-glow">
              <Command size={20} strokeWidth={2.4} />
            </span>
            {collapsed ? null : (
              <div>
                <p className="text-[15px] font-semibold tracking-tight text-white">BidCopilot</p>
                <p className="mono-label mt-0.5 text-slate-400">AI BIDDING OS</p>
              </div>
            )}
          </div>
          {collapsed ? null : (
            <button
              aria-label="收起左侧菜单"
              title="收起左侧菜单"
              className="rounded-lg p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-200"
              onClick={toggleSidebar}
            >
              <PanelLeftClose size={16} />
            </button>
          )}
        </div>

        {collapsed ? (
          <button
            aria-label="展开左侧菜单"
            title="展开左侧菜单"
            className="relative mt-4 grid h-9 w-full place-items-center rounded-xl border border-white/[0.06] text-slate-400 transition hover:bg-white/5 hover:text-white"
            onClick={toggleSidebar}
          >
            <PanelLeftOpen size={16} />
          </button>
        ) : null}

        <nav className="relative mt-6 flex-1 space-y-6 overflow-y-auto overflow-x-hidden pr-1">
          {visibleGroups.map((group) => (
            <div key={group.label}>
              {collapsed ? null : <p className="mono-label mb-2 px-3 text-slate-500">{group.label}</p>}
              <div className="space-y-1">
                {group.links.map((link) => {
                  const active =
                    pathname === link.href || pathname.startsWith(`${link.href}/`);
                  return (
                    <Link
                      key={link.href}
                      href={link.href}
                      title={collapsed ? link.label : undefined}
                      aria-label={link.label}
                      className={`group relative flex items-center rounded-xl text-sm transition ${
                        collapsed ? "justify-center px-0 py-2.5" : "gap-3 px-3 py-2.5"
                      } ${
                        active
                          ? "bg-white/[0.08] text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.08)]"
                          : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100"
                      }`}
                    >
                      {active ? (
                        <span className="absolute left-0 top-2.5 h-5 w-0.5 rounded-full bg-aurora-400" />
                      ) : null}
                      <span
                        className={`shrink-0 ${
                          active ? "text-aurora-400" : "text-slate-500 group-hover:text-slate-300"
                        }`}
                      >
                        {link.icon}
                      </span>
                      {collapsed ? null : <span className="truncate">{link.label}</span>}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div
          className={`relative mt-4 rounded-2xl border border-white/[0.06] bg-white/[0.03] ${
            collapsed ? "p-2" : "p-3"
          }`}
        >
          {collapsed ? (
            <div className="flex flex-col items-center gap-2">
              <Link
                href="/settings"
                aria-label="账号设置"
                title="账号设置"
                className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-slate-600 to-slate-800 text-xs font-semibold text-white transition hover:ring-2 hover:ring-aurora-500/40"
              >
                {(user?.display_name ?? "U").slice(0, 1)}
              </Link>
              <button
                aria-label="退出登录"
                title="退出登录"
                className="rounded-lg p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-200"
                onClick={handleLogout}
              >
                <LogOut size={15} />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Link
                href="/settings"
                aria-label="账号设置"
                title="账号设置"
                className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-slate-600 to-slate-800 text-xs font-semibold text-white transition hover:ring-2 hover:ring-aurora-500/40"
              >
                {(user?.display_name ?? "U").slice(0, 1)}
              </Link>
              <div className="min-w-0 flex-1">
                <Link
                  href="/settings"
                  className="block truncate text-xs font-medium text-slate-200 hover:text-aurora-400"
                >
                  {user?.display_name}
                </Link>
                <p className="truncate text-[11px] text-slate-500">{user?.tenant_name ?? "—"}</p>
              </div>
              <Link
                href="/settings"
                aria-label="账号设置"
                title="账号设置"
                className="rounded-lg p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-200"
              >
                <Settings size={15} />
              </Link>
              <button
                aria-label="退出登录"
                title="退出登录"
                className="rounded-lg p-1.5 text-slate-500 transition hover:bg-white/5 hover:text-slate-200"
                onClick={handleLogout}
              >
                <LogOut size={15} />
              </button>
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col md:h-screen md:overflow-hidden">
        <header className="sticky top-0 z-20 shrink-0 border-b border-slate-200/70 bg-white/70 backdrop-blur-xl">
          <div className="mx-auto flex h-16 max-w-[1500px] items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
            <div className="flex min-w-0 items-center gap-3">
              <span className="mono-label hidden text-slate-400 lg:inline">BIDDING /</span>
              <span className="truncate text-sm font-semibold text-slate-800">
                {current?.label ?? "BidCopilot"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={`hidden items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] sm:inline-flex ${
                  ai.data?.llm.configured
                    ? "border-aurora-500/25 bg-aurora-500/10 text-aurora-600"
                    : "border-slate-200 bg-slate-50 text-slate-500"
                }`}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-current" />
                {ai.isError
                  ? "AI 状态未知"
                  : ai.data?.llm.configured
                    ? ai.data.llm.model ?? "AI ONLINE"
                    : "AI 未配置"}
              </span>
              {ai.data?.judge.configured ? (
                <span className="mono-label hidden items-center gap-1 text-slate-400 lg:inline-flex">
                  <Sparkles size={12} /> {ai.data.judge.model}
                </span>
              ) : null}
              <NotificationBell />
              <span className="grid h-9 w-9 place-items-center rounded-full border border-slate-200 bg-white text-xs font-semibold text-slate-700 shadow-sm">
                {(user?.display_name ?? "U").slice(0, 1)}
              </span>
            </div>
          </div>
          <div className="flex gap-1 overflow-x-auto border-t border-slate-100 px-3 py-2 md:hidden">
            {allLinks.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-xs ${
                    active ? "bg-slate-900 text-white" : "text-slate-500"
                  }`}
                >
                  {link.label}
                </Link>
              );
            })}
          </div>
        </header>
        <main className="min-w-0 flex-1 md:overflow-y-auto">
          <div className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8">{children}</div>
        </main>
      </div>
    </div>
  );
}