"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Radar, Trash2 } from "lucide-react";

import { Badge, Button, Card, EmptyState, Input, Loading } from "@/components/ui";
import { useConfirm } from "@/components/confirm-dialog";
import { apiFetch } from "@/lib/api";
import type { ListResponse, OpportunitySubscription, SyncRun } from "@/lib/types";

function formatRun(value: string | null): string {
  return value ? value.slice(0, 16).replace("T", " ") : "从未运行";
}

function describeQuery(query: OpportunitySubscription["query_json"]): string {
  const parts: string[] = [];
  if (query.keywords?.length) parts.push(`关键词 ${query.keywords.join("、")}`);
  if (query.info_types?.length) parts.push(`类型 ${query.info_types.join("/")}`);
  if (query.industry_list?.length) parts.push(`行业 ${query.industry_list.join("、")}`);
  if (query.budget_min || query.budget_max) {
    parts.push(`预算 ${query.budget_min ?? "—"}~${query.budget_max ?? "—"}`);
  }
  if (query.publish_from || query.publish_to) {
    parts.push(`发布 ${query.publish_from ?? "—"}~${query.publish_to ?? "—"}`);
  }
  return parts.length ? parts.join(" · ") : "全量抓取";
}

export type FilterSnapshot = {
  keyword: string;
  infoTypes: string[];
  industry: string;
  budgetMin: string;
  budgetMax: string;
  publishFrom: string;
  publishTo: string;
};

function queryFromSnapshot(snapshot: FilterSnapshot): OpportunitySubscription["query_json"] {
  return {
    keywords: snapshot.keyword.trim() ? [snapshot.keyword.trim()] : [],
    info_types: snapshot.infoTypes,
    industry_list: snapshot.industry.trim() ? [snapshot.industry.trim()] : [],
    budget_min: snapshot.budgetMin.trim() || null,
    budget_max: snapshot.budgetMax.trim() || null,
    publish_from: snapshot.publishFrom || null,
    publish_to: snapshot.publishTo || null,
    page_size: 20,
  };
}

export function OpportunitySubscriptions({ defaults }: { defaults: FilterSnapshot }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [name, setName] = useState("");
  const [intervalMinutes, setIntervalMinutes] = useState("60");
  const [message, setMessage] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["opportunity-subscriptions"],
    queryFn: () =>
      apiFetch<ListResponse<OpportunitySubscription>>("/opportunity-subscriptions"),
  });

  const create = useMutation({
    mutationFn: () =>
      apiFetch<OpportunitySubscription>("/opportunity-subscriptions", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          interval_minutes: Number(intervalMinutes),
          query: queryFromSnapshot(defaults),
        }),
      }),
    onSuccess: () => {
      setName("");
      setMessage(null);
      queryClient.invalidateQueries({ queryKey: ["opportunity-subscriptions"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "订阅创建失败"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      apiFetch<OpportunitySubscription>(`/opportunity-subscriptions/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunity-subscriptions"] }),
    onError: (error) => setMessage(error instanceof Error ? error.message : "订阅更新失败"),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ deleted: boolean }>(`/opportunity-subscriptions/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunity-subscriptions"] }),
    onError: (error) =>
      setMessage(error instanceof Error ? error.message : "删除订阅失败，请重试"),
  });

  const runNow = useMutation({
    mutationFn: (id: string) =>
      apiFetch<SyncRun>(`/opportunity-subscriptions/${id}/run`, { method: "POST" }),
    onSuccess: (run) => {
      setMessage(
        run.status === "succeeded"
          ? `立即同步完成：抓取 ${run.rows_fetched} 条，新增 ${run.rows_new} 条。${
              run.warnings.length
                ? ` 告警 ${run.warnings.length} 条（${[...new Set(run.warnings.map((w) => w.code))].join("、")}）。`
                : ""
            }`
          : `立即同步失败：${run.error_code ?? run.status} ${run.error_message ?? ""}`,
      );
      queryClient.invalidateQueries({ queryKey: ["opportunity-subscriptions"] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "同步失败"),
  });

  const items = query.data?.items ?? [];

  return (
    <Card className="mb-4 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Radar size={15} className="text-electric-600" />
        <h2 className="text-sm font-semibold">商机订阅</h2>
        <span className="mono-label ml-auto text-slate-400">
          定时抓取按次计费 · 间隔下限 30 分钟
        </span>
      </div>

      <form
        className="mt-3 flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <label className="min-w-48 flex-1 text-xs font-medium text-slate-500">
          订阅名称
          <Input
            className="mt-1.5"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：政务云运维"
          />
        </label>
        <label className="text-xs font-medium text-slate-500">
          间隔（分钟）
          <Input
            className="mt-1.5 w-28"
            type="number"
            min={30}
            step={30}
            value={intervalMinutes}
            onChange={(event) => setIntervalMinutes(event.target.value)}
          />
        </label>
        <Button type="submit" disabled={create.isPending || !name.trim()}>
          {create.isPending ? "创建中…" : "新建订阅"}
        </Button>
        <span className="text-xs text-slate-500">
          将保存当前筛选：{describeQuery(queryFromSnapshot(defaults))}
        </span>
        <p className="w-full text-[11px] leading-5 text-slate-400">
          地区筛选不进入订阅快照：KeepRisk 的区域参数需要结构化省/市，当前免费文本地区只用于本页筛选与匹配度，避免伪造契约字段。
        </p>
      </form>

      {message ? (
        <p className="mt-3 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm text-slate-600">
          {message}
        </p>
      ) : null}

      {query.isLoading ? (
        <Loading />
      ) : query.error ? (
        <p className="mt-3 text-sm text-rose-600">{query.error.message}</p>
      ) : items.length === 0 ? (
        <EmptyState
                text="还没有订阅。"
                hint="在上方填名称与同步间隔后点击「新建订阅」；系统会按间隔自动拉取符合当前筛选条件的新商机。"
              />
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 px-3 py-2.5"
            >
              <div className="min-w-56 flex-1">
                <p className="text-sm font-medium text-slate-800">{item.name}</p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {describeQuery(item.query_json)} · 每 {item.interval_minutes} 分钟 ·{" "}
                  {formatRun(item.last_run_at)}
                </p>
              </div>
              <Badge tone={item.enabled ? "green" : "slate"}>
                {item.enabled ? "启用中" : "已停用"}
              </Badge>
              <Button
                variant="secondary"
                onClick={() => toggle.mutate({ id: item.id, enabled: !item.enabled })}
                disabled={toggle.isPending}
              >
                {item.enabled ? "停用" : "启用"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => runNow.mutate(item.id)}
                disabled={runNow.isPending}
              >
                <Play size={13} /> 立即同步
              </Button>
              <button
                type="button"
                aria-label={`删除订阅 ${item.name}`}
                className="rounded-lg p-2 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                onClick={async () => {
                  const ok = await confirm({
                    title: `删除订阅「${item.name}」？`,
                    description:
                      "删除后不再定时同步该订阅条件对应的新增商机；已同步入库的商机不受影响。",
                    confirmText: "删除订阅",
                    tone: "danger",
                  });
                  if (!ok) return;
                  remove.mutate(item.id);
                }}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
