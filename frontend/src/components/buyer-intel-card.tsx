"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";

import { Badge, Button, Card } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { BuyerWinner, OpportunityBuyerIntel } from "@/lib/types";

function formatDate(value: string | null): string {
  return value ? value.slice(0, 10) : "—";
}

function formatMoney(value: string | null): string {
  if (!value) return "—";
  const amount = Number(value);
  return Number.isNaN(amount)
    ? value
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(amount);
}

function WinnerRow({ row, incumbent }: { row: BuyerWinner; incumbent: boolean }) {
  return (
    <tr className="border-t border-slate-100">
      <td className="py-2 pr-3 font-medium text-slate-800">{row.winner}</td>
      <td className="tabular py-2 pr-3">{row.wins}</td>
      <td className="py-2 pr-3 text-slate-500">{formatDate(row.latest_at)}</td>
      <td className="tabular py-2 pr-3">{formatMoney(row.total_amount)}</td>
      <td className="py-2">
        <div className="flex flex-wrap gap-1.5">
          {incumbent ? <Badge tone="amber">既有供应商</Badge> : null}
          {row.tracked ? <Badge tone="blue">已建档</Badge> : null}
          {row.competitor_noted ? <Badge tone="green">复盘记录过</Badge> : null}
        </div>
      </td>
    </tr>
  );
}

export function BuyerIntelCard({
  opportunityId,
  buyer,
}: {
  opportunityId: string;
  buyer: string | null;
}) {
  const user = useAuthStore((state) => state.user);
  const canSync = hasPermission(user?.permissions, "opportunity:sync");
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["buyer-intel", opportunityId],
    queryFn: () =>
      apiFetch<OpportunityBuyerIntel>(`/opportunities/${opportunityId}/buyer-intel`),
    retry: false,
  });

  const sync = useMutation({
    mutationFn: () =>
      apiFetch<OpportunityBuyerIntel>(
        `/opportunities/${opportunityId}/buyer-intel/sync`,
        { method: "POST" },
      ),
    onSuccess: (row) => {
      queryClient.setQueryData(["buyer-intel", opportunityId], row);
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  if (!buyer) {
    return null;
  }

  const notSynced = query.error instanceof ApiError && query.error.status === 404;
  const loadError = query.error !== null && !notSynced;
  const data = query.data;
  const isSuccess = data ? data.status === "ok" || data.status === "empty" : false;
  const incumbentNames = new Set(
    (data?.existing_suppliers ?? []).map((row) => row.winner),
  );

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldAlert size={16} className="text-electric-600" />
        <h2 className="text-sm font-semibold">竞争情报</h2>
        <span className="mono-label text-slate-400">采购人：{buyer}</span>
        {data ? (
          <Badge tone={isSuccess ? "green" : "rose"}>
            {isSuccess ? `已同步 ${data.rows_fetched} 条` : `上游状态 ${data.status}`}
          </Badge>
        ) : null}
        {canSync ? (
          <Button
            className="ml-auto"
            variant="secondary"
            onClick={() => sync.mutate()}
            disabled={sync.isPending}
          >
            {sync.isPending ? "查询中…" : data ? "重新查询" : "查询采购人历史中标"}
          </Button>
        ) : null}
      </div>

      <p className="mt-2 text-xs leading-5 text-slate-500">
        按采购人名称检索 KeepRisk 中标类公告后聚合，属名称匹配的启发式结果，可能不完整或包含同名主体；
        近 12 个月同一厂商中标 ≥ 2 次标记为「既有供应商」。投标决策前请人工核实。
      </p>

      {sync.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {sync.error instanceof Error ? sync.error.message : "查询失败"}
        </p>
      ) : null}

      {notSynced ? (
        <p className="mt-3 text-sm text-slate-500">
          尚未查询过该采购人的历史中标{canSync ? "，点击右上角开始查询（按次计费）。" : "。"}
        </p>
      ) : null}

      {loadError ? (
        <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          加载失败：{query.error instanceof Error ? query.error.message : "未知错误"}
          <button type="button" className="ml-2 font-medium underline" onClick={() => query.refetch()}>
            重试
          </button>
        </p>
      ) : null}

      {data && !isSuccess ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          上游返回 {data.error_code ?? data.status}：{data.error_message ?? data.status}
        </p>
      ) : null}

      {data && !isSuccess && data.top_winners.length > 0 ? (
        <p className="mt-3 text-xs text-slate-500">以下为上次成功同步的结果，未被本次失败覆盖。</p>
      ) : null}

      {data && data.top_winners.length > 0 ? (
        <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs text-slate-500">
                <tr>
                  <th className="pb-2 pr-3">中标厂商</th>
                  <th className="pb-2 pr-3">中标次数</th>
                  <th className="pb-2 pr-3">最近中标</th>
                  <th className="pb-2 pr-3">累计中标额</th>
                  <th className="pb-2">标记</th>
                </tr>
              </thead>
              <tbody>
                {data.top_winners.map((row) => (
                  <WinnerRow
                    key={row.winner}
                    row={row}
                    incumbent={incumbentNames.has(row.winner)}
                  />
                ))}
              </tbody>
            </table>
          <p className="mt-2 text-[11px] text-slate-400">
            近 12 个月中标 {data.recent_wins_12m} 次 · 数据时间 {formatDate(data.synced_at)}
            {data.gateway_order_no ? ` · orderNo ${data.gateway_order_no}` : ""}
          </p>
        </div>
      ) : data && isSuccess ? (
        <p className="mt-3 text-sm text-slate-500">
          未检索到该采购人名下的中标类公告，无法判断既有供应商。
        </p>
      ) : null}
    </Card>
  );
}
