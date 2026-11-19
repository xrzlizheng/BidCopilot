"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, CircleDollarSign, ClipboardCheck, Gauge, Layers3 } from "lucide-react";

import { Badge, Card, EmptyState, Loading, PageHeader, StatCard } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import type { BidAnalytics, Project } from "@/lib/types";

const DECISION_TONES: Record<string, "green" | "amber" | "rose" | "slate"> = {
  Go: "green",
  "Conditional Go": "amber",
  "No-Go": "rose",
};

const STATUS_TONES: Record<string, "green" | "amber" | "rose" | "blue" | "slate"> = {
  won: "green",
  lost: "rose",
  active: "blue",
  cancelled: "slate",
};

export default function ProjectsPage() {
  const query = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/projects"),
  });
  const analytics = useQuery({
    queryKey: ["bid-analytics"],
    queryFn: () => apiFetch<BidAnalytics>("/analytics/bid-results"),
  });

  const summary = analytics.data?.summary;
  const decided = summary ? summary.won + summary.lost : 0;
  const topLoss = analytics.data?.by_loss_reason[0];

  return (
    <>
      <PageHeader
        eyebrow="PORTFOLIO / DELIVERY"
        title="投标项目"
        subtitle="从机会转项目，沿模板、生成、核查、合规与导出推进；结果复盘反哺赢率。"
        actions={
          <Link
            href="/templates"
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm text-slate-700 shadow-sm"
          >
            <Layers3 size={15} /> 模板与技能
          </Link>
        }
      />
      {summary ? (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="中标率"
            value={decided > 0 ? `${summary.win_rate}%` : "—"}
            hint={`已决 ${decided} 个`}
            icon={<Gauge size={16} />}
          />
          <StatCard
            label="已复盘"
            value={`${summary.total} 个`}
            hint={summary.cancelled ? `流标 ${summary.cancelled}` : "中标/未中标/流标"}
            icon={<ClipboardCheck size={16} />}
            tone="electric"
          />
          <StatCard
            label="中标金额"
            value={money(summary.win_amount)}
            hint="复盘累计"
            icon={<CircleDollarSign size={16} />}
            tone="amber"
          />
          <StatCard
            label="主要失标原因"
            value={topLoss ? `${topLoss.label} × ${topLoss.count}` : "—"}
            hint="用于下一轮改进"
            icon={<AlertTriangle size={16} />}
            tone="slate"
          />
        </div>
      ) : null}
      <Card>
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <div className="p-6 text-sm text-rose-600">{query.error.message}</div>
        ) : !query.data || query.data.length === 0 ? (
          <EmptyState text="暂无投标项目。可在机会详情点击“转为投标项目”。" />
        ) : (
          <>
            <div className="divide-y divide-slate-100 md:hidden">
              {query.data.map((project) => (
                <Link
                  key={project.id}
                  href={`/projects/${project.id}`}
                  className="block px-4 py-4 active:bg-slate-50"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-medium leading-6 text-slate-900">{project.name}</p>
                      <p className="mt-0.5 truncate text-xs text-slate-500">{project.code ?? "—"}</p>
                    </div>
                    <ArrowUpRight className="mt-0.5 shrink-0 text-slate-400" size={16} />
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Badge tone={STATUS_TONES[project.status] ?? "slate"}>{project.status}</Badge>
                    {project.decision_status ? (
                      <Badge tone={DECISION_TONES[project.decision_status] ?? "slate"}>
                        {project.decision_status}
                      </Badge>
                    ) : (
                      <span className="text-xs text-slate-400">未决策</span>
                    )}
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <p className="text-slate-400">采购人</p>
                      <p className="mt-0.5 truncate text-slate-700">{project.buyer ?? "—"}</p>
                    </div>
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <p className="text-slate-400">截止时间</p>
                      <p className="mt-0.5 truncate text-slate-700">
                        {project.bid_deadline
                          ? project.bid_deadline.slice(0, 16).replace("T", " ")
                          : "—"}
                      </p>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
            <div className="hidden overflow-x-auto md:block">
              <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">项目</th>
                  <th className="px-4 py-3">采购人</th>
                  <th className="px-4 py-3">截止时间</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">决策</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {query.data.map((project) => (
                  <tr key={project.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Link className="font-medium hover:underline" href={`/projects/${project.id}`}>
                        {project.name}
                      </Link>
                      <div className="mt-1 text-xs text-slate-500">{project.code ?? "—"}</div>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{project.buyer ?? "—"}</td>
                    <td className="px-4 py-3 text-slate-600">
                      {project.bid_deadline ? project.bid_deadline.slice(0, 16).replace("T", " ") : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={STATUS_TONES[project.status] ?? "slate"}>{project.status}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      {project.decision_status ? (
                        <Badge tone={DECISION_TONES[project.decision_status] ?? "slate"}>
                          {project.decision_status}
                        </Badge>
                      ) : (
                        <span className="text-slate-400">未决策</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </Card>
    </>
  );
}

function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isNaN(amount)
    ? String(value)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(amount);
}
