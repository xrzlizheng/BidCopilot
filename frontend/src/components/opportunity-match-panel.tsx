"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, ShieldAlert, Sparkles } from "lucide-react";

import { Badge, Button, Card, Loading } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";

export type QualificationGap = {
  requirement: string;
  category: string;
  status: string;
  reason: string;
  level?: string | null;
  quantity?: number | null;
};

export type OpportunityMatch = {
  id: string;
  opportunity_id: string;
  match_score: number;
  hit_groups: string[];
  reasons: string[];
  risks: string[];
  qualified: boolean | null;
  qualification_gaps: QualificationGap[];
  decision: string;
  engine_version: string;
  computed_at: string;
  project_id?: string | null;
};

export type OpportunityMatchSummary = Pick<
  OpportunityMatch,
  "match_score" | "hit_groups" | "qualified" | "decision" | "computed_at"
>;

export const HIT_GROUP_LABELS: Record<string, string> = {
  keyword: "关键词",
  region: "地区",
  industry: "行业",
  budget: "预算",
  company_region: "企业地区",
  company_industry: "企业行业",
  qualification: "企业资质",
};

const DECISION_META: Record<
  string,
  { label: string; tone: "green" | "amber" | "slate" | "blue" }
> = {
  recommend: { label: "推荐立项", tone: "green" },
  review: { label: "人工复核", tone: "amber" },
  skip: { label: "跳过", tone: "slate" },
};

const GAP_META: Record<string, { label: string; tone: "rose" | "amber" | "slate" }> = {
  MISSING: { label: "缺失", tone: "rose" },
  FAIL: { label: "不满足", tone: "rose" },
  UNKNOWN: { label: "待确认", tone: "amber" },
};

export function matchDecisionLabel(decision: string): string {
  return DECISION_META[decision]?.label ?? "待复核";
}

export function hitGroupLabels(groups: string[]): string[] {
  return groups.map((group) => HIT_GROUP_LABELS[group] ?? group);
}


export function OpportunityMatchBadges({
  match,
}: {
  match?: OpportunityMatchSummary | null;
}) {
  if (!match) return null;
  const decision = DECISION_META[match.decision] ?? DECISION_META.review;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="opportunity-match-badges">
      <Badge tone={decision.tone}>{decision.label}</Badge>
      <Badge tone={match.qualified === true ? "green" : "slate"}>
        匹配 {match.match_score}
      </Badge>
      {match.hit_groups.length ? (
        <span className="text-[11px] text-slate-400">
          {hitGroupLabels(match.hit_groups).join(" · ")}
        </span>
      ) : null}
    </div>
  );
}

export function OpportunityMatchPanel({
  opportunityId,
  canWrite = true,
}: {
  opportunityId: string;
  canWrite?: boolean;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["opportunity-match", opportunityId];
  const query = useQuery({
    queryKey,
    retry: false,
    queryFn: () => apiFetch<OpportunityMatch>(`/opportunities/${opportunityId}/match`),
  });
  const recompute = useMutation({
    mutationFn: () =>
      apiFetch<OpportunityMatch>(`/opportunities/${opportunityId}/match`, { method: "POST" }),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  });

  if (query.isLoading) return <Loading />;

  const notFound = query.error instanceof ApiError && query.error.status === 404;
  if (query.error && !notFound) {
    return (
      <Card className="mt-4 p-5">
        <p className="text-sm text-rose-600">{query.error.message}</p>
      </Card>
    );
  }

  if (notFound || !query.data) {
    return (
      <Card className="mt-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Sparkles size={15} className="text-electric-600" />
          <h2 className="text-sm font-semibold text-slate-900">自动匹配</h2>
          <Badge tone="slate">尚未计算</Badge>
        </div>
        <p className="mt-2 text-sm text-slate-500">
          这条机会尚未生成持久化匹配结果；可手动重算，订阅同步完成后也会自动触发。
        </p>
        {canWrite ? (
          <Button className="mt-3" onClick={() => recompute.mutate()} disabled={recompute.isPending}>
            <RefreshCw size={14} /> {recompute.isPending ? "计算中…" : "立即计算匹配"}
          </Button>
        ) : null}
        {recompute.error ? (
          <p className="mt-2 text-sm text-rose-600">{recompute.error.message}</p>
        ) : null}
      </Card>
    );
  }

  const match = query.data;
  const decision = DECISION_META[match.decision] ?? DECISION_META.review;
  return (
    <Card className="mt-4 p-5" data-testid="opportunity-match-panel">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles size={15} className="text-electric-600" />
        <h2 className="text-sm font-semibold text-slate-900">自动匹配</h2>
        <Badge tone={decision.tone}>{decision.label}</Badge>
        <span className="tabular text-lg font-semibold text-slate-900">
          {match.match_score}
          <span className="ml-0.5 text-xs font-normal text-slate-400">/100</span>
        </span>
        <span className="mono-label ml-auto text-slate-400">启发式规则打分 · 非模型评分</span>
        {canWrite ? (
          <Button variant="secondary" onClick={() => recompute.mutate()} disabled={recompute.isPending}>
            <RefreshCw size={14} /> {recompute.isPending ? "重算中…" : "重算"}
          </Button>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {match.hit_groups.length ? (
          hitGroupLabels(match.hit_groups).map((label) => (
            <Badge key={label} tone="blue">
              命中 {label}
            </Badge>
          ))
        ) : (
          <Badge tone="slate">未命中订阅条件</Badge>
        )}
        <Badge tone={match.qualified === true ? "green" : match.qualified === false ? "rose" : "amber"}>
          {match.qualified === true
            ? "资格自检通过"
            : match.qualified === false
              ? "资格存在缺口"
              : "资格待确认"}
        </Badge>
      </div>

      {match.reasons.length ? (
        <div className="mt-4">
          <p className="text-xs font-medium text-slate-500">匹配理由</p>
          <ul className="mt-1 space-y-1 text-sm text-emerald-700">
            {match.reasons.map((reason) => (
              <li key={reason}>· {reason}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {match.qualification_gaps.length ? (
        <div className="mt-4">
          <p className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
            <ShieldAlert size={13} /> 资格缺口
          </p>
          <ul className="mt-1 space-y-2">
            {match.qualification_gaps.map((gap, index) => {
              const meta = GAP_META[gap.status] ?? GAP_META.UNKNOWN;
              return (
                <li
                  key={`${gap.requirement}-${index}`}
                  className="rounded-xl border border-slate-200/80 bg-white/70 px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                    <span className="text-sm text-slate-700">{gap.requirement}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{gap.reason}</p>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {match.risks.length ? (
        <div className="mt-4">
          <p className="text-xs font-medium text-slate-500">风险提示</p>
          <ul className="mt-1 space-y-1 text-sm text-amber-700">
            {match.risks.map((risk) => (
              <li key={risk}>· {risk}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="mt-4 border-t border-slate-100 pt-3 text-[11px] text-slate-400">
        引擎 {match.engine_version} · 计算于 {match.computed_at.slice(0, 16).replace("T", " ")}
        {match.project_id ? " · 已自动立项" : ""}
      </p>
    </Card>
  );
}
