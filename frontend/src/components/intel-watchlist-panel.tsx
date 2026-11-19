"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellPlus, Play, Trash2 } from "lucide-react";

import { Badge, Button, Card, EmptyState, Loading } from "@/components/ui";
import { useConfirm } from "@/components/confirm-dialog";
import { apiFetch } from "@/lib/api";

type WatchItem = {
  id: string;
  ent_name: string;
  label: string | null;
  sections: string;
  active: boolean;
  last_checked_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_digest: Record<string, unknown> | null;
  created_at: string;
};

export function IntelWatchlistPanel({ companyName }: { companyName?: string | null }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [message, setMessage] = useState<string | null>(null);

  const items = useQuery({
    queryKey: ["intel-watchlist"],
    queryFn: () => apiFetch<WatchItem[]>("/company-intel/watchlist"),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["intel-watchlist"] });

  const add = useMutation({
    mutationFn: () =>
      apiFetch<WatchItem>("/company-intel/watchlist", {
        method: "POST",
        body: JSON.stringify({
          ent_name: companyName,
          label: companyName,
          sections: "enforced,dishonest,admin_penalty,abnormal_operation",
        }),
      }),
    onSuccess: () => {
      setMessage("已加入监控；调度器按周期检查风险变化并通知。");
      refresh();
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "加入监控失败"),
  });

  const run = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ status: string }>(`/company-intel/watchlist/${id}/run`, { method: "POST" }),
    onSuccess: (result) => {
      setMessage(`检查完成：${result.status}`);
      refresh();
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "检查失败"),
  });

  const toggle = useMutation({
    mutationFn: (item: WatchItem) =>
      apiFetch(`/company-intel/watchlist/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: !item.active }),
      }),
    onSuccess: refresh,
    onError: (error) => setMessage(error instanceof Error ? error.message : "更新失败"),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/company-intel/watchlist/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setMessage("已移除监控");
      refresh();
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "移除失败"),
  });

  const watched = (items.data ?? []).some((item) => item.ent_name === companyName);

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">情报监控订阅</h2>
          <p className="mt-1 text-xs text-slate-500">
            定时检查被执行、失信、行政处罚、经营异常；只有风险变化才发站内通知，每次检查按尽调节数计费。
          </p>
        </div>
        <Button
          variant="secondary"
          disabled={!companyName || watched || add.isPending}
          onClick={() => add.mutate()}
        >
          <BellPlus size={14} />
          {watched ? "已在监控" : add.isPending ? "加入中…" : "监控当前主体"}
        </Button>
      </div>

      {message ? <p className="mt-3 text-xs text-slate-600">{message}</p> : null}
      {items.isLoading ? <Loading /> : null}
      {items.error ? <p className="mt-3 text-xs text-rose-600">{items.error.message}</p> : null}
      {items.data && items.data.length === 0 ? (
        <EmptyState text="还没有监控对象；先在上方选择主体并加入监控。" />
      ) : null}

      <ul className="mt-3 space-y-2">
        {(items.data ?? []).map((item) => {
          const risk = (item.last_digest?._risk as string | undefined) ?? null;
          return (
            <li
              key={item.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-100 px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-800">
                  {item.label || item.ent_name}
                </p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {item.ent_name} · 检查 {item.last_checked_at?.slice(0, 16).replace("T", " ") || "未执行"}
                  {risk ? ` · 风险 ${risk}` : ""}
                </p>
                {item.last_error ? (
                  <p className="mt-0.5 text-xs text-rose-600">{item.last_error}</p>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={item.active ? "green" : "slate"}>
                  {item.active ? "启用" : "停用"}
                </Badge>
                <Button
                  variant="secondary"
                  disabled={run.isPending}
                  onClick={() => run.mutate(item.id)}
                >
                  <Play size={13} /> 立即检查
                </Button>
                <Button variant="secondary" onClick={() => toggle.mutate(item)}>
                  {item.active ? "停用" : "启用"}
                </Button>
                <button
                  type="button"
                  aria-label={`移除 ${item.ent_name}`}
                  className="text-slate-400 hover:text-rose-600"
                  onClick={async () => {
                    const ok = await confirm({
                      title: `不再监控「${item.ent_name}」？`,
                      description:
                        "移除后该主体不再参与周期性风险检查（被执行/失信/处罚/经营异常），已有检查记录保留。",
                      confirmText: "停止监控",
                      tone: "danger",
                    });
                    if (!ok) return;
                    remove.mutate(item.id);
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
