import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import {
  ArrowRight,
  BookOpenText,
  Building2,
  CircleHelp,
  Compass,
  FileSignature,
  FolderKanban,
  Gauge,
  Info,
  Layers3,
  Lightbulb,
  Radar,
  Settings,
  ShieldAlert,
  TriangleAlert,
} from "lucide-react";

import { Badge, Card, PageHeader } from "@/components/ui";
import {
  HELP_GROUPS,
  QUICK_STEPS,
  type HelpBlock,
  type HelpSection,
  type HelpTone,
} from "@/lib/help-content";

export const metadata: Metadata = {
  title: "使用帮助 · BidCopilot",
};

const QUICK_LINKS: {
  href: string;
  label: string;
  desc: string;
  icon: ReactNode;
}[] = [
  { href: "/dashboard", label: "指挥台", desc: "KPI、流水线与数据健康", icon: <Gauge size={16} /> },
  { href: "/opportunities", label: "机会中心", desc: "商机、订阅与联网情报", icon: <Radar size={16} /> },
  { href: "/projects", label: "投标项目", desc: "解析、生成、合规与复盘", icon: <FolderKanban size={16} /> },
  { href: "/contracts", label: "合同台账", desc: "审批、履约与变更", icon: <FileSignature size={16} /> },
  { href: "/assets", label: "企业资产", desc: "资质、人员、业绩与划型", icon: <Building2 size={16} /> },
  { href: "/knowledge", label: "企业知识库", desc: "资料、图片与召回测试", icon: <BookOpenText size={16} /> },
  { href: "/templates", label: "模板与技能", desc: "结构、格式与技能目录", icon: <Layers3 size={16} /> },
  { href: "/settings", label: "账号设置", desc: "密码、模型与决策参数", icon: <Settings size={16} /> },
];

const NOTE_STYLES: Record<
  HelpTone,
  { box: string; title: string; icon: ReactNode }
> = {
  info: {
    box: "border-blue-200/70 bg-blue-50/60",
    title: "text-blue-900",
    icon: <Info size={16} className="text-blue-600" />,
  },
  tip: {
    box: "border-emerald-200/70 bg-emerald-50/60",
    title: "text-emerald-900",
    icon: <Lightbulb size={16} className="text-emerald-600" />,
  },
  warn: {
    box: "border-amber-200/80 bg-amber-50/60",
    title: "text-amber-900",
    icon: <TriangleAlert size={16} className="text-amber-600" />,
  },
  danger: {
    box: "border-rose-200/80 bg-rose-50/60",
    title: "text-rose-900",
    icon: <ShieldAlert size={16} className="text-rose-600" />,
  },
};

const INLINE_PATTERN = /(\*\*[^*]+\*\*|`[^`]+`)/g;

function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(INLINE_PATTERN).map((piece, index) => {
        if (piece.startsWith("**") && piece.endsWith("**") && piece.length > 4) {
          return (
            <strong key={index} className="font-semibold text-slate-800">
              {piece.slice(2, -2)}
            </strong>
          );
        }
        if (piece.startsWith("`") && piece.endsWith("`") && piece.length > 2) {
          return (
            <code
              key={index}
              className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] text-slate-700"
            >
              {piece.slice(1, -1)}
            </code>
          );
        }
        return <span key={index}>{piece}</span>;
      })}
    </>
  );
}

function BlockView({ block }: { block: HelpBlock }) {
  switch (block.type) {
    case "paragraph":
      return (
        <p className="text-sm leading-7 text-slate-600">
          <Inline text={block.text} />
        </p>
      );
    case "subheading":
      return <h4 className="pt-1 text-sm font-semibold text-slate-900">{block.text}</h4>;
    case "steps":
      return (
        <ol className="space-y-2">
          {block.items.map((item, index) => (
            <li key={item} className="flex gap-3 text-sm leading-6 text-slate-600">
              <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-900 text-[11px] font-semibold text-white">
                {index + 1}
              </span>
              <span>
                <Inline text={item} />
              </span>
            </li>
          ))}
        </ol>
      );
    case "bullets":
      return (
        <ul className="space-y-1.5">
          {block.items.map((item) => (
            <li key={item} className="flex gap-2 text-sm leading-6 text-slate-600">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-aurora-500" />
              <span>
                <Inline text={item} />
              </span>
            </li>
          ))}
        </ul>
      );
    case "note": {
      const style = NOTE_STYLES[block.tone];
      return (
        <div className={`flex gap-3 rounded-2xl border px-4 py-3 ${style.box}`}>
          <span className="mt-0.5 shrink-0">{style.icon}</span>
          <div>
            <p className={`text-sm font-semibold ${style.title}`}>{block.title}</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              <Inline text={block.text} />
            </p>
          </div>
        </div>
      );
    }
    case "table":
      return (
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80">
          <table className="min-w-[640px] text-left text-sm">
            <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                {block.head.map((cell) => (
                  <th key={cell} className="whitespace-nowrap px-3 py-2.5 font-medium">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {block.rows.map((row, rowIndex) => (
                <tr key={`${row[0]}-${rowIndex}`} className="align-top">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={`${cell}-${cellIndex}`}
                      className={`px-3 py-2.5 leading-6 ${
                        cellIndex === 0 ? "font-medium text-slate-800" : "text-slate-600"
                      }`}
                    >
                      <Inline text={cell} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "cards":
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          {block.items.map((item) => (
            <div
              key={item.title}
              className="rounded-2xl border border-slate-200/70 bg-white/70 p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-slate-800">{item.title}</p>
                {item.badge ? <Badge tone="cyan">{item.badge}</Badge> : null}
              </div>
              <p className="mt-1 text-xs leading-6 text-slate-500">
                <Inline text={item.text} />
              </p>
            </div>
          ))}
        </div>
      );
    case "faq":
      return (
        <div className="space-y-2">
          {block.items.map((item) => (
            <details
              key={item.q}
              className="rounded-2xl border border-slate-200/70 bg-white/70 px-4 py-3"
            >
              <summary className="cursor-pointer text-sm font-medium text-slate-800">
                {item.q}
              </summary>
              <p className="mt-2 text-sm leading-7 text-slate-600">
                <Inline text={item.a} />
              </p>
            </details>
          ))}
        </div>
      );
    default:
      return null;
  }
}

function SectionCard({ section }: { section: HelpSection }) {
  return (
    <section
      id={section.id}
      className="scroll-mt-24 rounded-3xl border border-slate-200/70 bg-white/85 p-5 shadow-card backdrop-blur-sm sm:p-6"
    >
      <p className="mono-label text-aurora-600">{section.eyebrow}</p>
      <h3 className="mt-1 text-xl font-semibold tracking-tight text-slate-950">
        {section.title}
      </h3>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">{section.intro}</p>
      <div className="mt-5 space-y-4">
        {section.blocks.map((block, index) => (
          <BlockView key={`${section.id}-${block.type}-${index}`} block={block} />
        ))}
      </div>
    </section>
  );
}

function TableOfContents({ compact = false }: { compact?: boolean }) {
  return (
    <nav className={compact ? "mt-3 space-y-3" : "mt-3 space-y-4"}>
      {HELP_GROUPS.map((group) => (
        <div key={group.id}>
          <p className="mono-label px-2 text-slate-400">{group.label}</p>
          <div className={compact ? "mt-1 grid grid-cols-2 gap-x-3" : "mt-1.5 space-y-0.5"}>
            {group.sections.map((section) => (
              <a
                key={section.id}
                href={`#${section.id}`}
                className="block rounded-lg px-2 py-1.5 text-xs text-slate-500 transition hover:bg-slate-50 hover:text-slate-900"
              >
                {section.nav}
              </a>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}

export default function HelpPage() {
  return (
    <>
      <PageHeader
        eyebrow="GUIDE / HANDBOOK"
        title="使用帮助"
        subtitle="按当前产品页面与后端校验整理的使用说明：从商机、解析、决策到生成、合规、导出与复盘。"
        actions={
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm text-slate-700 shadow-sm"
          >
            <Gauge size={15} /> 返回指挥台
          </Link>
        }
      />

      <Card className="overflow-hidden">
        <div className="aurora-panel relative px-5 py-6 text-white sm:px-7">
          <div className="aurora-grid pointer-events-none absolute inset-0 opacity-30" />
          <div className="relative">
            <div className="flex flex-wrap items-center gap-2">
              <span className="grid h-8 w-8 place-items-center rounded-xl bg-gradient-to-br from-aurora-400 to-aurora-500 text-ink-950">
                <Compass size={17} />
              </span>
              <p className="mono-label text-aurora-400">QUICK START / 9 STEPS</p>
              <Badge tone="cyan">菜单按权限显示</Badge>
            </div>
            <h2 className="mt-3 text-xl font-semibold tracking-tight sm:text-2xl">
              一条主线，从商机到交付
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              优先沿着下面这条链路走；需要单独查某个页面时，直接用右侧目录或下方快捷入口跳转。
            </p>
            <ol className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-9">
              {QUICK_STEPS.map((step, index) => (
                <li
                  key={step}
                  className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-2.5 py-2"
                >
                  <span className="mono-label text-aurora-400">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <p className="mt-1 text-[11px] leading-4 text-slate-300">{step}</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
        <div className="grid gap-2 p-4 sm:grid-cols-2 xl:grid-cols-4">
          {QUICK_LINKS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="group flex items-center gap-3 rounded-xl border border-slate-200/70 bg-white/70 px-3 py-2.5 transition hover:border-aurora-500/40 hover:shadow-glow"
            >
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-aurora-500/10 text-aurora-600">
                {item.icon}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-semibold text-slate-800">{item.label}</span>
                <span className="block truncate text-[11px] text-slate-400">{item.desc}</span>
              </span>
              <ArrowRight
                size={13}
                className="shrink-0 text-slate-300 transition group-hover:text-electric-600"
              />
            </Link>
          ))}
        </div>
      </Card>

      <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-slate-400">
        <Info className="mt-0.5 shrink-0" size={14} />
        <span>
          本说明按当前仓库的页面与后端校验整理。实际可见菜单、按钮与选项会随角色权限、租户策略和部署配置变化；
          遇到与文档不一致时，以页面展示、接口返回和审计记录为准。
        </span>
      </p>

      <div className="mt-6 grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <div className="sticky top-6 rounded-2xl border border-slate-200/70 bg-white/80 p-4 shadow-sm backdrop-blur-sm">
            <div className="flex items-center gap-2">
              <CircleHelp size={15} className="text-electric-600" />
              <p className="text-sm font-semibold text-slate-800">本页目录</p>
            </div>
            <TableOfContents />
            <div className="mt-4 border-t border-slate-100 pt-3">
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-1.5 text-xs font-medium text-electric-600"
              >
                返回指挥台 <ArrowRight size={12} />
              </Link>
            </div>
          </div>
        </aside>

        <div className="min-w-0 space-y-8">
          <details className="rounded-2xl border border-slate-200/70 bg-white/80 p-4 lg:hidden">
            <summary className="cursor-pointer text-sm font-semibold text-slate-800">
              本页目录
            </summary>
            <TableOfContents compact />
          </details>

          {HELP_GROUPS.map((group) => (
            <section key={group.id} aria-labelledby={`${group.id}-heading`}>
              <div className="mb-3 flex items-center gap-3">
                <h2
                  id={`${group.id}-heading`}
                  className="text-sm font-semibold tracking-tight text-slate-800"
                >
                  {group.label}
                </h2>
                <span className="h-px flex-1 bg-slate-200/70" />
                <span className="mono-label text-slate-400">
                  {String(group.sections.length).padStart(2, "0")} SECTIONS
                </span>
              </div>
              <div className="space-y-4">
                {group.sections.map((section) => (
                  <SectionCard key={section.id} section={section} />
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </>
  );
}
