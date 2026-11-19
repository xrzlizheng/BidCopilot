"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Badge, Button, Card, Loading, PageHeader } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type DeliverySummary = {
  project: {
    id: string;
    name: string;
    code: string | null;
    buyer: string | null;
    status: string;
    bid_deadline: string | null;
  };
  brand: { name: string; logo_url: string | null };
  client: { id: string; name: string } | null;
  readiness: {
    total: number;
    done: number;
    pending: number;
    blocked: number;
    blocking_open: number;
    completion_rate: number;
  };
  template: { name: string; version: number };
  latest_receipt: {
    submitted_at: string;
    platform: string;
    receipt_no: string | null;
    notes: string | null;
  } | null;
  generated_at: string;
};

export default function DeliverySummaryPage({ params }: { params: { id: string } }) {
  const summary = useQuery({
    queryKey: ["delivery-summary", params.id],
    queryFn: () => apiFetch<DeliverySummary>(`/projects/${params.id}/delivery-summary`),
  });

  if (summary.isLoading) return <Loading />;
  if (summary.error) {
    return <Card className="p-6 text-sm text-rose-600">{summary.error.message}</Card>;
  }
  if (!summary.data) return null;
  const data = summary.data;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-3 flex items-center justify-between gap-2 print:hidden">
        <Link
          href={`/projects/${params.id}`}
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          ← 返回项目
        </Link>
        <Button variant="secondary" onClick={() => window.print()}>
          打印 / 另存 PDF
        </Button>
      </div>

      <Card className="p-8">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 pb-5">
          <div className="flex items-center gap-3">
            {data.brand.logo_url ? (
              <div
                role="img"
                aria-label={data.brand.name}
                className="h-12 w-12 rounded-lg bg-contain bg-center bg-no-repeat"
                style={{ backgroundImage: `url(${data.brand.logo_url})` }}
              />
            ) : null}
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">交付摘要</p>
              <h1 className="text-xl font-semibold text-slate-900">{data.brand.name}</h1>
            </div>
          </div>
          <p className="text-xs text-slate-500">
            生成于 {data.generated_at.slice(0, 16).replace("T", " ")}
          </p>
        </div>

        <div className="mt-5 space-y-1">
          <h2 className="text-lg font-semibold text-slate-900">{data.project.name}</h2>
          <p className="text-xs text-slate-500">
            项目编号 {data.project.code || "—"} · 采购人 {data.project.buyer || "—"} · 状态{" "}
            {data.project.status}
          </p>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-4">
          {[
            ["检查项", data.readiness.total],
            ["已完成", data.readiness.done],
            ["阻断未闭环", data.readiness.blocking_open],
            ["完成率", `${Math.round((data.readiness.completion_rate || 0) * 100)}%`],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-xl border border-slate-200 p-3">
              <p className="text-[11px] text-slate-500">{label}</p>
              <p className="mt-1 text-lg font-semibold text-slate-900">{value}</p>
            </div>
          ))}
        </div>

        <div className="mt-6">
          <h3 className="text-sm font-semibold text-slate-800">交标自查</h3>
          <p className="mt-1 text-xs text-slate-500">
            模板 {data.template.name} v{data.template.version}；待确认 {data.readiness.pending}、阻断{" "}
            {data.readiness.blocked}。
          </p>
        </div>

        <div className="mt-6">
          <h3 className="text-sm font-semibold text-slate-800">交标回执</h3>
          {data.latest_receipt ? (
            <div className="mt-2 rounded-xl border border-slate-200 p-3 text-xs text-slate-600">
              <p>
                {data.latest_receipt.platform}
                {data.latest_receipt.receipt_no ? ` · ${data.latest_receipt.receipt_no}` : ""}
              </p>
              <p className="mt-1">
                递交时间 {data.latest_receipt.submitted_at.slice(0, 16).replace("T", " ")}
              </p>
              {data.latest_receipt.notes ? <p className="mt-1">{data.latest_receipt.notes}</p> : null}
            </div>
          ) : (
            <p className="mt-2 text-xs text-slate-500">尚未记录交标回执。</p>
          )}
        </div>

        <div className="mt-8 border-t border-slate-200 pt-4 text-[11px] text-slate-500">
          <p>
            本摘要由 BidCopilot 生成，用于交付沟通；交标自查与回执为人工确认记录，不代表交易平台已受理。
          </p>
          <Badge tone="slate">非法律意见</Badge>
        </div>
      </Card>
    </div>
  );
}
