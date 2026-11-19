"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BarChart3,
  Building2,
  CheckCircle2,
  CircleDollarSign,
  FileText,
  Gauge,
  Radar,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react";

import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  Loading,
  ProgressBar,
  StatCard,
} from "@/components/ui";
import { PlatformHintCard } from "@/components/platform-hint-card";
import { apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { BidAnalytics, DataOps, Project } from "@/lib/types";

type AiStatus = {
  llm: { configured: boolean; model: string | null; host: string | null };
  judge: { configured: boolean; model: string | null; host: string | null };
  judge_fallback_to_main: boolean;
};

const STAGES = [
  { key: "01", title: "商机与决策", text: "真实标讯·资格预审·Bid-No-Bid", icon: <Radar size={18} /> },
  { key: "02", title: "模板与大纲", text: "结构模板·素材清单·评分矩阵", icon: <Workflow size={18} /> },
  { key: "03", title: "生成与核查", text: "章节生成·事实核查·人工审批", icon: <Sparkles size={18} /> },
  { key: "04", title: "合规与交付", text: "预检·暗标·docx 导出·复盘", icon: <ShieldCheck size={18} /> },
];

function statusTone(status: string): "green" | "amber" | "rose" | "blue" | "slate" {
  if (status === "won") return "green";
  if (status === "lost") return "rose";
  if (status === "active") return "blue";
  return "slate";
}

function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isNaN(amount)
    ? String(value)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(amount);
}

export default function DashboardPage() {
  const user = useAuthStore((state) => state.user);
  const canProjects = hasPermission(user?.permissions, "project:read");
  const canAudit = hasPermission(user?.permissions, "audit:read");

  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/projects"),
    enabled: canProjects,
  });
  const analytics = useQuery({
    queryKey: ["bid-analytics"],
    queryFn: () => apiFetch<BidAnalytics>("/analytics/bid-results"),
    enabled: canProjects,
  });
  const dataOps = useQuery({
    queryKey: ["data-ops"],
    queryFn: () => apiFetch<DataOps>("/analytics/data-ops?days=30"),
    enabled: canAudit,
  });
  const ai = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => apiFetch<AiStatus>("/system/ai-status"),
    retry: false,
  });

  const rows = projects.data ?? [];
  const active = rows.filter((project) => project.status === "active").length;
  const summary = analytics.data?.summary;
  const winRate = summary?.win_rate ?? 0;
  const recent = rows.slice(0, 5);
  const ops = dataOps.data?.costs;

  return (
    <div className="space-y-6">
      <section className="aurora-panel relative overflow-hidden rounded-3xl px-6 py-7 text-white shadow-card sm:px-8">
        <div className="aurora-grid pointer-events-none absolute inset-0 opacity-30" />
        <div className="relative flex flex-wrap items-start justify-between gap-6">
          <div>
            <p className="mono-label text-aurora-400">COMMAND DECK / {user?.tenant_name ?? "WORKSPACE"}</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight">指挥台</h1>
            <p className="mt-2 max-w-xl text-sm leading-6 text-slate-400">
              从真实商机到可提交标书，一条可追踪、可复核、可复盘的生产链路。
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link
                href="/projects"
                className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-electric-500 to-electric-600 px-4 py-2 text-sm font-medium text-white shadow-glow"
              >
                进入投标项目 <ArrowRight size={15} />
              </Link>
              <Link
                href="/opportunities"
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-sm text-slate-300 hover:bg-white/[0.08]"
              >
                浏览机会 <Radar size={15} />
              </Link>
            </div>
          </div>
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4 text-xs">
            <p className="mono-label text-slate-500">AI RUNTIME</p>
            <div className="mt-3 space-y-2">
              <div className="flex items-center gap-2">
                <span className={`h-1.5 w-1.5 rounded-full ${ai.data?.llm.configured ? "bg-aurora-400" : "bg-slate-600"}`} />
                <span className="text-slate-300">
                  {ai.isError ? "AI 状态未知" : ai.data?.llm.model ?? "主模型未配置"}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className={`h-1.5 w-1.5 rounded-full ${ai.data?.judge.configured ? "bg-electric-400" : "bg-slate-600"}`} />
                <span className="text-slate-400">{ai.data?.judge.model ?? "判别模型未配置"}</span>
              </div>
              <p className="pt-1 text-[10px] text-slate-600">
                {ai.data?.llm.host ?? "gateway offline"}
              </p>
            </div>
          </div>
        </div>
      </section>

      {!canProjects ? (
        <PlatformHintCard />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {projects.isError || analytics.isError || (canAudit && dataOps.isError) ? (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 sm:col-span-2 xl:col-span-4">
                部分看板数据加载失败（项目{projects.isError ? "失败" : "正常"}、复盘
                {analytics.isError ? "失败" : "正常"}、成本
                {canAudit ? (dataOps.isError ? "失败" : "正常") : "无权限"}）。
                对应数值显示为“—”，请刷新重试。
              </div>
            ) : null}
            <StatCard
              label="在投项目"
              value={projects.isError ? "—" : active}
              hint={projects.isError ? "数据加载失败" : `全部项目 ${rows.length}`}
              icon={<FileText size={16} />}
            />
            <StatCard
              label="中标率"
              value={analytics.isError || !summary ? "—" : `${winRate}%`}
              hint={
                analytics.isError
                  ? "数据加载失败"
                  : summary
                    ? `已复盘 ${summary.total} 个`
                    : "暂无复盘"
              }
              icon={<Gauge size={16} />}
              tone="electric"
            />
            <StatCard
              label="中标金额"
              value={analytics.isError ? "—" : money(summary?.win_amount)}
              hint={analytics.isError ? "数据加载失败" : "已复盘项目累计"}
              icon={<CircleDollarSign size={16} />}
              tone="amber"
            />
            <StatCard
              label="数据调用 / 30天"
              value={!canAudit || dataOps.isError ? "—" : ops?.total_calls ?? "—"}
              hint={
                !canAudit
                  ? "无审计权限"
                  : dataOps.isError
                    ? "数据加载失败"
                    : `计费 ${ops?.charged_calls ?? 0} · 失败 ${ops?.failed_calls ?? 0}`
              }
              icon={<BarChart3 size={16} />}
              tone="slate"
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
            <div className="space-y-4">
              <Card className="p-5">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="mono-label text-slate-400">PRODUCTION PIPELINE</p>
                    <h2 className="mt-1 text-sm font-semibold">投标流水线</h2>
                  </div>
                  <Link href="/projects" className="text-xs font-medium text-electric-600">
                    查看全部
                  </Link>
                </div>
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  {STAGES.map((stage) => (
                    <div
                      key={stage.key}
                      className="group rounded-xl border border-slate-200/70 bg-white/60 p-4 transition hover:border-aurora-500/40 hover:shadow-glow"
                    >
                      <div className="flex items-center justify-between">
                        <span className="mono-label text-slate-400">{stage.key}</span>
                        <span className="text-aurora-600">{stage.icon}</span>
                      </div>
                      <p className="mt-3 text-sm font-semibold text-slate-800">{stage.title}</p>
                      <p className="mt-1 text-xs leading-5 text-slate-500">{stage.text}</p>
                    </div>
                  ))}
                </div>
              </Card>

              <Card className="p-5">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold">最近项目</h2>
                  <Link href="/projects" className="text-xs font-medium text-electric-600">
                    全部项目
                  </Link>
                </div>
                {projects.isLoading ? (
                  <Loading />
                ) : projects.isError ? (
                  <ErrorState
                    error={projects.error}
                    onRetry={() => projects.refetch()}
                    hint="项目列表没取到，可能是服务未就绪或会话过期。重试无效时请重新登录。"
                  />
                ) : recent.length === 0 ? (
                  <EmptyState
                    text="还没有投标项目。"
                    hint="投标项目是从商机转来的：先去机会中心挑一条值得投的商机，在详情里点「转为投标项目」。"
                    action={
                      <Link
                        href="/opportunities"
                        className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-electric-500 to-electric-600 px-3.5 py-2 text-sm font-medium text-white shadow-[0_12px_30px_-16px_rgba(99,102,241,0.9)]"
                      >
                        去机会中心 <ArrowRight size={14} />
                      </Link>
                    }
                  />
                ) : (
                  <div className="mt-3 overflow-x-auto">
                    <table className="min-w-full text-sm">
                      <thead className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-400">
                        <tr>
                          <th className="py-2 pr-3">项目</th>
                          <th className="py-2 pr-3">状态</th>
                          <th className="py-2 pr-3">决策</th>
                          <th className="py-2">截止</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {recent.map((project) => (
                          <tr key={project.id} className="hover:bg-slate-50/60">
                            <td className="py-3 pr-3">
                              <Link
                                href={`/projects/${project.id}`}
                                className="font-medium text-slate-800 hover:text-electric-600"
                              >
                                {project.name}
                              </Link>
                              <p className="text-[11px] text-slate-400">{project.code ?? "—"}</p>
                            </td>
                            <td className="py-3 pr-3">
                              <Badge tone={statusTone(project.status)}>{project.status}</Badge>
                            </td>
                            <td className="py-3 pr-3 text-slate-500">
                              {project.decision_status ?? "未决策"}
                            </td>
                            <td className="py-3 text-slate-500">
                              {project.bid_deadline
                                ? project.bid_deadline.slice(0, 10)
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>

            <div className="space-y-4">
              <Card className="p-5">
                <div className="flex items-center gap-2">
                  <CheckCircle2 size={16} className="text-aurora-600" />
                  <h2 className="text-sm font-semibold">赢率与复盘</h2>
                </div>
                <div className="mt-4 flex items-end justify-between">
                  <p className="tabular text-3xl font-semibold text-slate-950">
                    {summary ? `${winRate}%` : "—"}
                  </p>
                  <span className="text-xs text-slate-500">
                    {summary ? `${summary.won} 中标 / ${summary.lost} 未中标` : "暂无数据"}
                  </span>
                </div>
                <div className="mt-3">
                  <ProgressBar value={winRate} />
                </div>
                {analytics.data?.by_loss_reason?.[0] ? (
                  <p className="mt-3 text-xs text-slate-500">
                    主要失标原因：{analytics.data.by_loss_reason[0].label} ×{" "}
                    {analytics.data.by_loss_reason[0].count}
                  </p>
                ) : null}
              </Card>

              <Card className="p-5">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-electric-600" />
                  <h2 className="text-sm font-semibold">数据健康</h2>
                </div>
                {!canAudit ? (
                  <p className="mt-3 text-sm text-slate-500">当前角色无审计权限。</p>
                ) : dataOps.isLoading ? (
                  <Loading />
                ) : dataOps.isError ? (
                  <ErrorState
                    error={dataOps.error}
                    onRetry={() => dataOps.refetch()}
                    hint="数据运营统计没取到；不影响其他功能。"
                  />
                ) : (
                  <>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                      <div className="rounded-xl bg-slate-50 px-3 py-2">
                        <p className="text-[11px] text-slate-500">调用</p>
                        <p className="tabular font-semibold">{ops?.total_calls ?? 0}</p>
                      </div>
                      <div className="rounded-xl bg-slate-50 px-3 py-2">
                        <p className="text-[11px] text-slate-500">失败</p>
                        <p className="tabular font-semibold">{ops?.failed_calls ?? 0}</p>
                      </div>
                    </div>
                    <div className="mt-3 space-y-2">
                      {(dataOps.data?.sources ?? []).slice(0, 3).map((source) => (
                        <div key={`${source.source}-${source.pcode}`} className="flex items-center justify-between text-xs">
                          <span className="text-slate-500">{source.pcode}</span>
                          <span className={source.failed > 0 ? "text-rose-600" : "text-emerald-600"}>
                            成功率 {source.success_rate}%
                          </span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
                <Link href="/data-ops" className="mt-3 inline-flex text-xs font-medium text-electric-600">
                  打开数据运营
                </Link>
              </Card>

              <Card className="p-5">
                <h2 className="text-sm font-semibold">快捷入口</h2>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {[
                    { href: "/opportunities", label: "找商机", icon: <Radar size={15} /> },
                    { href: "/assets", label: "企业资产", icon: <Building2 size={15} /> },
                    { href: "/projects", label: "投标项目", icon: <FileText size={15} /> },
                    { href: "/audit", label: "审计日志", icon: <ShieldCheck size={15} /> },
                  ].map((item) => (
                    <Link
                      key={item.href}
                      href={item.href}
                      className="flex items-center gap-2 rounded-xl border border-slate-200/70 bg-white/60 px-3 py-2.5 text-xs font-medium text-slate-600 transition hover:border-aurora-500/40 hover:text-slate-900"
                    >
                      <span className="text-aurora-600">{item.icon}</span>
                      {item.label}
                    </Link>
                  ))}
                </div>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
