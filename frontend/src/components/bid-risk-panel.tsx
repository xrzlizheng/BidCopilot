"use client";

import { Badge, Card, EmptyState } from "@/components/ui";
import type { BidRiskItem, BidRisks } from "@/lib/types";

const LEVEL_META: Record<
  string,
  { tone: "rose" | "amber" | "slate"; label: string; desc: string }
> = {
  blocker: { tone: "rose", label: "废标项", desc: "不满足即被否决，开标前必须逐项确认" },
  high: { tone: "amber", label: "高风险", desc: "极可能被否决或大幅扣分" },
  medium: { tone: "amber", label: "需评估", desc: "影响评分或履约，需评估能否满足" },
};

function formatAmount(value?: number | null): string | null {
  if (value == null || Number.isNaN(value)) return null;
  if (value >= 10000) return `${(value / 10000).toFixed(value % 10000 === 0 ? 0 : 1)} 万元`;
  return `${value.toLocaleString()} 元`;
}

function thresholdText(item: BidRiskItem): string | null {
  const parts: string[] = [];
  if (item.years) parts.push(`近 ${item.years} 年`);
  if (item.min_count) parts.push(`不少于 ${item.min_count} 个`);
  const amount = formatAmount(item.min_amount);
  if (amount) parts.push(`单个合同 ≥ ${amount}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function FlagRow({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-slate-100 py-2 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <Badge tone={good ? "green" : "rose"}>{value}</Badge>
    </div>
  );
}

export function BidRiskPanel({ risks }: { risks?: BidRisks }) {
  if (!risks || risks.items.length === 0) {
    return (
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-900">投标风险画像</h3>
        </div>
        <EmptyState text="未识别到业绩/联合体/信用查询截图/中小企业声明函等硬性条款。这不代表没有风险，请人工复核资格审查章节。" />
      </Card>
    );
  }

  const blockerItems = risks.items.filter((item) => item.level === "blocker");
  const otherItems = risks.items.filter((item) => item.level !== "blocker");
  const flags = risks.flags;
  const mustBring = risks.must_bring ?? [];

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-900">投标风险画像</h3>
          {risks.summary.blocker > 0 ? (
            <Badge tone="rose">{risks.summary.blocker} 项废标风险</Badge>
          ) : (
            <Badge tone="amber">{risks.summary.total} 项需确认</Badge>
          )}
        </div>
        <span className="text-xs text-slate-400">
          共 {risks.summary.total} 条
          {risks.summary.raw_hits && risks.summary.raw_hits > risks.summary.total
            ? `（原文命中 ${risks.summary.raw_hits} 处，已合并重复）`
            : ""}
        </span>
      </div>

      {blockerItems.length > 0 ? (
        <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50/60 p-3">
          <p className="mb-2 text-xs font-medium text-rose-700">
            以下 {blockerItems.length} 项不满足将直接导致废标，请在投标前逐项确认结论。
          </p>
          <ul className="space-y-2">
            {blockerItems.map((item) => {
              const threshold = thresholdText(item);
              return (
                <li key={item.code} className="rounded-md bg-white/80 p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="rose">废标项</Badge>
                    <span className="text-sm font-medium text-slate-900">{item.name}</span>
                    {threshold ? (
                      <span className="rounded bg-rose-100 px-1.5 py-0.5 text-xs font-medium text-rose-700">
                        {threshold}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-600">{item.hint}</p>
                  <p className="mt-1 border-l-2 border-rose-200 pl-2 text-xs italic text-slate-500">
                    {item.quote}
                  </p>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {otherItems.length > 0 ? (
        <ul className="mb-3 space-y-2">
          {otherItems.map((item) => {
            const meta = LEVEL_META[item.level] ?? LEVEL_META.medium;
            const threshold = thresholdText(item);
            return (
              <li key={item.code} className="rounded-md border border-slate-200 p-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                  <span className="text-sm font-medium text-slate-900">{item.name}</span>
                  {threshold ? (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                      {threshold}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-600">{item.hint}</p>
                <p className="mt-1 border-l-2 border-slate-200 pl-2 text-xs italic text-slate-500">
                  {item.quote}
                </p>
              </li>
            );
          })}
        </ul>
      ) : null}

      {mustBring.length > 0 ? (
        <div className="mb-3 rounded-lg border border-slate-200 bg-white p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-medium text-slate-800">开标必带材料（按已识别风险派生）</p>
            <span className="text-xs text-slate-400">
              {mustBring.filter((row) => row.critical).length} 项关键
            </span>
          </div>
          <ul className="space-y-1.5">
            {mustBring.map((row) => (
              <li key={row.code} className="flex gap-2 text-xs leading-5">
                <span
                  className={
                    row.critical
                      ? "mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500"
                      : "mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300"
                  }
                />
                <span>
                  <span className={row.critical ? "font-medium text-slate-900" : "text-slate-700"}>
                    {row.item}
                  </span>
                  <span className="block text-slate-500">{row.why}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-slate-400">
            仅覆盖系统已识别的风险项，完整材料清单以招标文件「投标文件组成」章节为准。
          </p>
        </div>
      ) : null}

      <div className="rounded-lg bg-slate-50 p-3">
        <p className="mb-1 text-xs font-medium text-slate-700">关键判定</p>
        <FlagRow
          label="是否接受联合体"
          value={flags.consortium_allowed ? "接受" : "不接受"}
          good={flags.consortium_allowed}
        />
        <FlagRow
          label="分公司可否使用总公司资质"
          value={flags.branch_allowed ? "文件明确允许" : "未明确 / 不允许"}
          good={flags.branch_allowed}
        />
        <FlagRow
          label="是否专门面向中小企业"
          value={flags.sme_only ? "是（需声明函）" : "否"}
          good={!flags.sme_only}
        />
        <FlagRow
          label="是否需现场核验原件"
          value={flags.original_required ? "需要" : "未识别"}
          good={!flags.original_required}
        />
        <FlagRow
          label="是否需信用查询截图"
          value={flags.credit_query_required ? "需要（核对查询项/时间/盖章）" : "未识别"}
          good={!flags.credit_query_required}
        />
      </div>

      <p className="mt-3 text-xs leading-5 text-slate-400">
        系统只抽取原文并标注风险等级，不做合规判断。是否满足、能否规避取决于企业实际资质与当地口径，
        须由投标负责人确认。
      </p>
    </Card>
  );
}
