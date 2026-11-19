"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card, ErrorState, Input } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import type { DecisionCard, DecisionEvidence } from "@/lib/types";

const STATUS_TONE: Record<string, "green" | "amber" | "rose" | "slate"> = {
  Go: "green",
  "Conditional Go": "amber",
  "No-Go": "rose",
  insufficient_data: "slate",
};

function displayStatus(value: string): string {
  if (value === "insufficient_data") return "数据不足";
  return value;
}

function numberOrNull(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 证据链里 map 型指标的中文标签；未知键直接显示原键名，不猜含义。 */
const METRIC_LABELS: Record<string, string> = {
  record_count: "中标记录数",
  amount_samples: "含金额样本",
  distinct_winners: "不同中标供应商",
  median_win_amount: "中标价中位数",
  p25_win_amount: "中标价 P25",
  p75_win_amount: "中标价 P75",
  mean_win_amount: "中标价均值",
  latest_award_at: "最近中标时间",
  project_budget: "本项目预算",
  budget_to_median_ratio: "预算/中位比",
  won: "中标",
  lost: "未中标",
  decided: "已决标",
  win_rate: "历史胜率",
  position: "价格位置",
};

function formatMetric(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (key === "win_rate" && typeof value === "number") return `${(value * 100).toFixed(1)}%`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function EvidencePanel({ evidence }: { evidence: DecisionEvidence }) {
  const scoredCount = evidence.items.filter((item) => item.scored).length;
  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">决策证据链</h3>
          <Badge tone={scoredCount === evidence.items.length ? "green" : "amber"}>
            {scoredCount}/{evidence.items.length} 项可计分
          </Badge>
          <Badge tone="slate">时间窗 {evidence.window_days} 天</Badge>
          <Badge tone={evidence.buyer_match_mode === "exact" ? "cyan" : "amber"}>
            采购人匹配 {evidence.buyer_match_mode}
          </Badge>
        </div>
        <span className="text-xs text-slate-500">只读聚合，不写库、不消耗计量</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        每项都标数据来源与取数口径；样本不足时显式标注「样本不足」并且不计分，不用 0 冒充统计。
      </p>

      {evidence.buyer_match_mode === "fuzzy" ? (
        <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
          采购人名称只做到模糊匹配，结果仅作提示，未计入竞争情报评分。
        </p>
      ) : null}

      <div className="mt-3 space-y-2">
        {evidence.items.map((item) => {
          const metrics = Object.entries(item.metrics || {}).filter(
            ([key]) => key !== "top_winners" && key !== "buyer_intel",
          );
          return (
            <div
              key={item.key}
              className="rounded border border-slate-200 bg-white px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">{item.label}</span>
                {item.scored ? (
                  <Badge tone="green">已计分</Badge>
                ) : (
                  <Badge tone="amber">样本不足</Badge>
                )}
                {item.sources.map((source) => (
                  <span
                    key={source}
                    className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500"
                  >
                    {source}
                  </span>
                ))}
              </div>
              <p className="mt-1 text-xs leading-5 text-slate-600">{item.reason}</p>
              {metrics.length > 0 ? (
                <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                  {metrics.map(([key, value]) => (
                    <div key={key} className="flex gap-1">
                      <dt>{METRIC_LABELS[key] ?? key}</dt>
                      <dd className="font-medium text-slate-700">{formatMetric(key, value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {item.notes.length > 0 ? (
                <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-500">
                  {item.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>

      {evidence.missing.length > 0 ? (
        <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <p className="font-medium">以下证据不足，决策卡对应维度不计分：</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {evidence.missing.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <ul className="mt-2 list-disc pl-4 text-[11px] text-slate-500">
        {evidence.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </div>
  );
}

export function DecisionCardPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [quote, setQuote] = useState("");
  const [cost, setCost] = useState("");
  const [taxRate, setTaxRate] = useState("0.09");
  const [humanStatus, setHumanStatus] = useState("Conditional Go");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const data = useQuery({
    queryKey: ["project-decision-card", projectId],
    queryFn: () => apiFetch<DecisionCard>(`/projects/${projectId}/decision-card`),
    retry: false,
  });

  // 证据链是独立只读接口：决策卡不存在时也要能看（"先看证据再决定投不投"）。
  const evidence = useQuery({
    queryKey: ["project-decision-evidence", projectId],
    queryFn: () => apiFetch<DecisionEvidence>(`/projects/${projectId}/decision-evidence`),
    retry: false,
  });

  const compute = useMutation({
    mutationFn: () =>
      apiFetch<DecisionCard>(`/projects/${projectId}/decision-card`, {
        method: "POST",
        body: JSON.stringify({
          inputs: {
            quote_amount: numberOrNull(quote),
            estimated_cost: numberOrNull(cost),
            tax_rate: numberOrNull(taxRate),
          },
        }),
      }),
    onSuccess: (card) => {
      setNotice({ tone: "ok", text: `决策卡已生成（第 ${card.run_no} 次）。` });
      setHumanStatus(card.status);
      queryClient.invalidateQueries({ queryKey: ["project-decision-card", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "生成失败" }),
  });

  const confirm = useMutation({
    mutationFn: () =>
      apiFetch<DecisionCard>(`/projects/${projectId}/decision-card`, {
        method: "PATCH",
        body: JSON.stringify({
          human_status: humanStatus,
          override_reason: reason.trim() || null,
        }),
      }),
    onSuccess: (card) => {
      setNotice({ tone: "ok", text: `决策已确认为 ${card.status}。` });
      queryClient.invalidateQueries({ queryKey: ["project-decision-card", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "确认失败" }),
  });

  const card = data.data;

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">投标决策卡</h2>
            {card ? <Badge tone={STATUS_TONE[card.status] ?? "slate"}>{displayStatus(card.status)}</Badge> : null}
            {card ? <Badge tone="slate">第 {card.run_no} 次</Badge> : null}
            {card?.computed_status === "insufficient_data" ? <Badge tone="amber">数据不足</Badge> : null}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            六维加权（含竞争情报），硬否决优先；缺数据维度不计分，不拿默认值凑分。成本/报价输入仅审批人可见。
          </p>
        </div>
        {card?.score !== null && card?.score !== undefined ? (
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-right">
            <p className="text-xs text-slate-500">综合得分</p>
            <p className="text-xl font-semibold">{card.score.toFixed(1)}</p>
          </div>
        ) : null}
      </div>

      {notice ? (
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${notice.tone === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
          {notice.text}
        </p>
      ) : null}

      <div className="mt-4 grid gap-3 lg:grid-cols-5">
        <label className="text-xs text-slate-500">
          投标报价（元）
          <Input className="mt-1" value={quote} onChange={(event) => setQuote(event.target.value)} placeholder="可选" />
        </label>
        <label className="text-xs text-slate-500">
          估算成本（元）
          <Input className="mt-1" value={cost} onChange={(event) => setCost(event.target.value)} placeholder="可选" />
        </label>
        <label className="text-xs text-slate-500">
          税率
          <Input className="mt-1" value={taxRate} onChange={(event) => setTaxRate(event.target.value)} placeholder="0.09" />
        </label>
        <div className="flex items-end">
          <Button onClick={() => compute.mutate()} disabled={compute.isPending}>
            {compute.isPending ? "测算中…" : "生成决策卡"}
          </Button>
        </div>
      </div>

      {data.isError && !(data.error instanceof ApiError && data.error.status === 404) ? (
        <ErrorState error={data.error} onRetry={() => data.refetch()} />
      ) : null}
      {data.isError && data.error instanceof ApiError && data.error.status === 404 ? (
        <p className="mt-3 text-sm text-slate-500">尚未生成决策卡；填写报价/成本后点击测算。</p>
      ) : null}

      {/* 证据链先于决策卡展示：先看历史依据，再看综合结论。 */}
      {evidence.data ? <EvidencePanel evidence={evidence.data} /> : null}
      {evidence.error && !evidence.data ? (
        <p className="mt-3 text-xs text-slate-500">
          证据链不可用（可能尚未关联供应商机或缺少历史中标记录）；该维度不计分。
        </p>
      ) : null}

      {card ? (
        <>
          {card.vetoes.length > 0 ? (
            <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              <p className="font-medium">硬否决（满足任意一项即不建议投标）</p>
              <ul className="mt-1 space-y-1">
                {card.vetoes.map((veto) => (
                  <li key={veto.code} className="text-xs leading-5">
                    <span className="font-medium">{veto.label}</span>
                    {veto.reason ? <span className="text-rose-600">：{veto.reason}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">维度</th>
                  <th className="py-2 pr-3">得分</th>
                  <th className="py-2 pr-3">权重</th>
                  <th className="py-2">说明</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {card.dimensions.map((dimension) => (
                  <tr key={dimension.key}>
                    <td className="py-2 pr-3 font-medium">{dimension.label}</td>
                    <td className="py-2 pr-3">
                      {dimension.scored && dimension.score !== null ? dimension.score.toFixed(1) : "未计入"}
                    </td>
                    <td className="py-2 pr-3">{dimension.weight}</td>
                    <td className="py-2 text-slate-600">{dimension.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {card.inputs ? (
            <p className="mt-3 text-xs text-slate-500">
              输入：报价 {card.inputs.quote_amount ?? "—"} / 成本 {card.inputs.estimated_cost ?? "—"} / 税率{" "}
              {card.inputs.tax_rate ?? "—"}
            </p>
          ) : (
            <p className="mt-3 text-xs text-slate-500">成本/报价输入需审批权限查看。</p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <select
              className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm"
              value={humanStatus}
              onChange={(event) => setHumanStatus(event.target.value)}
            >
              <option value="Go">Go</option>
              <option value="Conditional Go">Conditional Go</option>
              <option value="No-Go">No-Go</option>
            </select>
            <Input
              className="min-w-[220px] flex-1"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="人工覆盖理由（与计算状态不同时必填）"
            />
            <Button variant="secondary" onClick={() => confirm.mutate()} disabled={confirm.isPending}>
              确认/覆盖
            </Button>
          </div>
        </>
      ) : null}
    </Card>
  );
}
