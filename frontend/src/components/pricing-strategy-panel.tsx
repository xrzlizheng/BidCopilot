"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card, ErrorState, Input } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";

export type PricingWinRate = {
  status: string;
  rate: string | null;
  basis: string;
  heuristic: boolean;
  disclaimer: string;
};

export type PricingTier = {
  tier: "conservative" | "balanced" | "aggressive";
  label: string;
  status: string;
  price: string | null;
  target_margin: string;
  net_margin?: string | null;
  discount_rate?: string | null;
  quote_ratio?: string | null;
  win_rate?: string | null;
  win_rate_status?: string;
  win_rate_basis?: string;
  band_position?: string;
  price_source?: string;
  market_anchor_ratio?: string | null;
  market_anchor_price?: string | null;
  cost_plus_price?: string | null;
  cost_floor_price?: string | null;
  clamp_notes?: string[];
  reason: string;
  risks: string[];
};

export type PricingSensitivityRow = {
  change_rate: string;
  quote_amount: string;
  net_margin: string | null;
  discount_rate: string | null;
  win_rate: string | null;
  win_rate_status: string;
  below_cost: boolean;
  band_position: string;
};

export type PricingStrategy = {
  id?: string;
  project_id?: string;
  engine_version?: string;
  created_at?: string | null;
  status: string;
  disclaimer: string;
  missing: string[];
  budget: { amount: string | null; source: string };
  industry: { values: string[]; source: string };
  cost: {
    status: string;
    quote_amount: string | null;
    labor_cost: string | null;
    other_direct_cost: string | null;
    direct_cost: string | null;
    indirect_cost: string | null;
    tax_cost: string | null;
    total_cost: string | null;
    net_profit: string | null;
    net_margin: string | null;
    direct_cost_ratio: string | null;
    direct_rate_variance: string | null;
    missing: string[];
    source_labels: Record<string, string>;
    note: string;
  };
  price_band: {
    status: string;
    sample_count: number;
    p25: string | null;
    p50: string | null;
    p75: string | null;
    p25_amount: string | null;
    p50_amount: string | null;
    p75_amount: string | null;
    decided_count: number;
    won_count: number;
    lost_count: number;
    price_loss_count: number;
    base_win_rate: string | null;
    notes: string[];
    missing: string[];
    disclaimer: string;
  };
  candidate: {
    quote_amount: string;
    quote_ratio: string | null;
    discount_rate: string | null;
    net_margin: string | null;
    total_cost: string | null;
    band_position: string;
    win_rate: PricingWinRate;
  } | null;
  tiers: PricingTier[];
  sensitivity: {
    status: string;
    base_quote: string | null;
    base_source: string;
    rows: PricingSensitivityRow[];
    missing: string[];
    disclaimer: string;
  } | null;
  assumptions: {
    target_margins: Record<string, string>;
    target_margins_source: string;
    note: string;
  };
  narrative: { text: string; source: string; disclaimer: string };
};

export type PriceBandResponse = {
  project_id: string;
  budget_amount: string | null;
  budget_source: string;
  industry: string[];
  industry_source: string;
  price_band: PricingStrategy["price_band"];
  hypothesis: string;
};

const STATUS_TONE: Record<string, "green" | "amber" | "rose" | "slate"> = {
  ok: "green",
  insufficient_data: "amber",
};

function statusLabel(value: string | undefined): string {
  if (!value) return "未知";
  if (value === "insufficient_data") return "数据不足";
  if (value === "ok") return "可复核";
  return value;
}

function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isNaN(parsed)
    ? String(value)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(parsed);
}

function percent(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  if (Number.isNaN(parsed)) return String(value);
  return `${(parsed * 100).toFixed(2)}%`;
}

function signedPercent(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  if (Number.isNaN(parsed)) return String(value);
  return `${parsed > 0 ? "+" : ""}${(parsed * 100).toFixed(2)}%`;
}

export function PricingStrategyPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [quote, setQuote] = useState("");
  const [otherDirectCost, setOtherDirectCost] = useState("");
  const [laborRole, setLaborRole] = useState("");
  const [laborDays, setLaborDays] = useState("");
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const latest = useQuery({
    queryKey: ["project-pricing-strategy", projectId],
    queryFn: () => apiFetch<PricingStrategy>(`/projects/${projectId}/pricing/strategy/latest`),
    retry: false,
  });
  const priceBand = useQuery({
    queryKey: ["project-pricing-price-band", projectId],
    queryFn: () => apiFetch<PriceBandResponse>(`/projects/${projectId}/pricing/price-band`),
    retry: false,
  });

  const generate = useMutation({
    mutationFn: () =>
      apiFetch<PricingStrategy>(`/projects/${projectId}/pricing/strategy`, {
        method: "POST",
        body: JSON.stringify({
          inputs: {
            quote_amount: quote.trim() || null,
            other_direct_cost: otherDirectCost.trim() || null,
            labor_items:
              laborRole.trim() && laborDays.trim()
                ? [{ role: laborRole.trim(), days: laborDays.trim() }]
                : [],
          },
        }),
      }),
    onSuccess: (strategy) => {
      setNotice({
        tone: strategy.status === "ok" ? "ok" : "error",
        text:
          strategy.status === "ok"
            ? "报价策略已生成。"
            : "数据不足，已如实返回缺项，未用默认值凑数。",
      });
      queryClient.setQueryData(["project-pricing-strategy", projectId], strategy);
      queryClient.invalidateQueries({ queryKey: ["project-pricing-price-band", projectId] });
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "生成失败" }),
  });

  const strategy = latest.data;
  const band = priceBand.data?.price_band;

  return (
    <Card className="mt-4 p-5" aria-label="报价策略面板">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">智能报价策略</h2>
            {strategy ? (
              <Badge tone={STATUS_TONE[strategy.status] ?? "slate"}>{statusLabel(strategy.status)}</Badge>
            ) : null}
            {strategy?.engine_version ? <Badge tone="slate">{strategy.engine_version}</Badge> : null}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            基于租户成本参数与同类历史中标样本的价格带；样本不足时显式返回「数据不足」。所有赢率为启发式估计，不是模型预测，也不是中标承诺。
          </p>
        </div>
      </div>

      {notice ? (
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${notice.tone === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>
          {notice.text}
        </p>
      ) : null}

      <div className="mt-4 grid gap-3 lg:grid-cols-5">
        <label className="text-xs text-slate-500">
          候选报价（元）
          <Input className="mt-1" value={quote} onChange={(event) => setQuote(event.target.value)} placeholder="可选；不填则用均衡档测算敏感性" />
        </label>
        <label className="text-xs text-slate-500">
          其他直接成本（元）
          <Input className="mt-1" value={otherDirectCost} onChange={(event) => setOtherDirectCost(event.target.value)} placeholder="无则填 0" />
        </label>
        <label className="text-xs text-slate-500">
          人力岗位
          <Input className="mt-1" value={laborRole} onChange={(event) => setLaborRole(event.target.value)} placeholder="须与租户人力单价配置一致" />
        </label>
        <label className="text-xs text-slate-500">
          人日
          <Input className="mt-1" value={laborDays} onChange={(event) => setLaborDays(event.target.value)} placeholder="如 20" />
        </label>
        <div className="flex items-end">
          <Button onClick={() => generate.mutate()} disabled={generate.isPending} aria-label="生成报价策略">
            {generate.isPending ? "测算中…" : "生成/重算策略"}
          </Button>
        </div>
      </div>

      {latest.isError && !(latest.error instanceof ApiError && latest.error.status === 404) ? (
        <ErrorState error={latest.error} onRetry={() => latest.refetch()} />
      ) : null}
      {latest.isError && latest.error instanceof ApiError && latest.error.status === 404 ? (
        <p className="mt-3 text-sm text-slate-500">
          尚未生成报价策略；请填写人力投入/其他直接成本后点击生成。历史价格带可单独刷新查看。
        </p>
      ) : null}

      {strategy ? (
        <>
          {strategy.missing.length > 0 ? (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <p className="font-medium">数据不足，未给出确定性报价</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-xs">
                {strategy.missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <div className="rounded-lg bg-slate-50 p-3">
              <p className="text-xs text-slate-500">直接成本</p>
              <p className="mt-1 text-lg font-semibold">{money(strategy.cost.direct_cost)}</p>
              <p className="mt-1 text-xs text-slate-500">人力 {money(strategy.cost.labor_cost)} / 其他 {money(strategy.cost.other_direct_cost)}</p>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <p className="text-xs text-slate-500">总成本（含间接费/税）</p>
              <p className="mt-1 text-lg font-semibold">{money(strategy.cost.total_cost)}</p>
              <p className="mt-1 text-xs text-slate-500">间接 {money(strategy.cost.indirect_cost)} / 税 {money(strategy.cost.tax_cost)}</p>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <p className="text-xs text-slate-500">候选报价净利率</p>
              <p className="mt-1 text-lg font-semibold">{percent(strategy.candidate?.net_margin)}</p>
              <p className="mt-1 text-xs text-slate-500">相对预算折扣 {percent(strategy.candidate?.discount_rate)}</p>
            </div>
          </div>

          <div className="mt-4 rounded-lg border border-slate-200 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium">同类历史价格带（中标价 / 预算）</h3>
              <Badge tone={STATUS_TONE[strategy.price_band.status] ?? "slate"}>{statusLabel(strategy.price_band.status)}</Badge>
              <span className="text-xs text-slate-500">
                样本 {strategy.price_band.sample_count} 条 / 已决标 {strategy.price_band.decided_count} 条 / 报价失标 {strategy.price_band.price_loss_count} 条
              </span>
            </div>
            <div className="mt-2 grid gap-2 text-sm sm:grid-cols-3">
              <p>P25：{percent(strategy.price_band.p25)}（{money(strategy.price_band.p25_amount)} 元）</p>
              <p>P50：{percent(strategy.price_band.p50)}（{money(strategy.price_band.p50_amount)} 元）</p>
              <p>P75：{percent(strategy.price_band.p75)}（{money(strategy.price_band.p75_amount)} 元）</p>
            </div>
            {strategy.price_band.notes.length > 0 ? (
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-500">
                {strategy.price_band.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
          </div>

          {strategy.tiers.length > 0 ? (
            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="py-2 pr-3">方案</th>
                    <th className="py-2 pr-3">建议价（元）</th>
                    <th className="py-2 pr-3">定价依据</th>
                    <th className="py-2 pr-3">净利率</th>
                    <th className="py-2 pr-3">相对预算折扣</th>
                    <th className="py-2 pr-3">粗略赢率</th>
                    <th className="py-2">理由与风险</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {strategy.tiers.map((tier) => (
                    <tr key={tier.tier}>
                      <td className="py-2 pr-3 font-medium">{tier.label}</td>
                      <td className="py-2 pr-3">{money(tier.price)}</td>
                      <td className="py-2 pr-3 text-xs">
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">
                          {priceSourceLabel(tier.price_source)}
                        </span>
                        {tier.cost_plus_price ? (
                          <p className="mt-1 text-slate-500">成本加成 {money(tier.cost_plus_price)}</p>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3">{percent(tier.net_margin ?? tier.target_margin)}</td>
                      <td className="py-2 pr-3">{percent(tier.discount_rate)}</td>
                      <td className="py-2 pr-3">{percent(tier.win_rate)}</td>
                      <td className="py-2 text-xs text-slate-600">
                        <p>{tier.reason}</p>
                        {tier.clamp_notes && tier.clamp_notes.length > 0 ? (
                          <p className="mt-1 text-sky-700">{tier.clamp_notes.join("；")}</p>
                        ) : null}
                        <p className="mt-1 text-amber-700">{tier.risks.join("；")}</p>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {strategy.sensitivity?.rows.length ? (
            <div className="mt-4 overflow-x-auto">
              <h3 className="text-sm font-medium">报价敏感性（±3% / ±5% / ±8%）</h3>
              <table className="mt-2 min-w-full text-sm">
                <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="py-2 pr-3">报价变动</th>
                    <th className="py-2 pr-3">报价（元）</th>
                    <th className="py-2 pr-3">净利率</th>
                    <th className="py-2 pr-3">赢率估计</th>
                    <th className="py-2">价格位置</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {strategy.sensitivity.rows.map((row) => (
                    <tr key={row.change_rate}>
                      <td className="py-2 pr-3">{signedPercent(row.change_rate)}</td>
                      <td className="py-2 pr-3">{money(row.quote_amount)}</td>
                      <td className={`py-2 pr-3 ${row.below_cost ? "font-medium text-rose-700" : ""}`}>{percent(row.net_margin)}</td>
                      <td className="py-2 pr-3">{percent(row.win_rate)}</td>
                      <td className="py-2 text-xs text-slate-600">{row.band_position}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium">策略叙述</h3>
              <Badge tone="slate">{strategy.narrative.source === "llm" ? "LLM 生成" : "模板降级"}</Badge>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{strategy.narrative.text}</p>
            <p className="mt-2 text-xs text-slate-500">{strategy.disclaimer}</p>
          </div>

          <p className="mt-3 text-xs text-slate-500">
            成本参数来源：{strategy.cost.source_labels.rates ?? "未配置"}；目标利润率档位来源：{strategy.assumptions.target_margins_source}
          </p>
        </>
      ) : band ? (
        <div className="mt-4 rounded-lg border border-slate-200 p-3 text-sm text-slate-600">
          <p>历史价格带状态：{statusLabel(band.status)}；样本 {band.sample_count} 条。生成策略后可查看三档建议与敏感性。</p>
          {band.missing.length > 0 ? <p className="mt-1 text-xs text-amber-700">{band.missing.join("；")}</p> : null}
        </div>
      ) : null}
    </Card>
  );
}

const PRICE_SOURCE_LABELS: Record<string, string> = {
  market_band: "市场锚定",
  market_band_cost_floor: "市场锚定·成本底线",
  market_band_budget_cap: "市场锚定·预算封顶",
  cost_plus: "成本加成",
  none: "不可用",
};

function priceSourceLabel(source?: string): string {
  if (!source) {
    return "—";
  }
  return PRICE_SOURCE_LABELS[source] ?? source;
}
