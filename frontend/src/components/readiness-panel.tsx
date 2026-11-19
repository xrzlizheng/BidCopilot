"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { apiFetch, apiFetchBlob } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { ReadinessItem, ReadinessResponse, ReadinessStatus } from "@/lib/types";

const STATUS_META: Record<
  ReadinessStatus,
  { label: string; tone: "green" | "rose" | "amber" | "slate" | "blue" }
> = {
  done: { label: "已完成", tone: "green" },
  blocked: { label: "阻断", tone: "rose" },
  pending: { label: "待处理", tone: "amber" },
  not_applicable: { label: "不适用", tone: "slate" },
  waived: { label: "已豁免", tone: "blue" },
};

const STATUS_ORDER: ReadinessStatus[] = ["blocked", "pending", "done", "not_applicable", "waived"];

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function ReadinessPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "project:write");
  const canExport = hasPermission(user?.permissions, "project:export");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [category, setCategory] = useState("all");
  const [evidenceDrafts, setEvidenceDrafts] = useState<Record<string, string>>({});
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const data = useQuery({
    queryKey: ["project-readiness", projectId],
    queryFn: () => apiFetch<ReadinessResponse>(`/projects/${projectId}/readiness`),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["project-readiness", projectId] });
  };

  const refreshAuto = useMutation({
    mutationFn: () =>
      apiFetch<ReadinessResponse>(`/projects/${projectId}/readiness/refresh`, {
        method: "POST",
      }),
    onSuccess: () => {
      setNotice({ tone: "ok", text: "自动检查项已刷新。" });
      refresh();
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "刷新失败" }),
  });

  const update = useMutation({
    mutationFn: (input: {
      id: string;
      status: ReadinessStatus;
      evidence_refs?: string[];
      note?: string;
      waived_reason?: string;
    }) =>
      apiFetch(`/projects/${projectId}/readiness/${input.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status: input.status,
          evidence_refs: input.evidence_refs,
          note: input.note,
          waived_reason: input.waived_reason,
        }),
      }),
    onSuccess: () => {
      setNotice({ tone: "ok", text: "检查项已更新。" });
      refresh();
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "更新失败" }),
  });

  const exportFile = useMutation({
    mutationFn: () => apiFetchBlob(`/projects/${projectId}/readiness/export`),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "交标就绪清单.xlsx";
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "导出失败" }),
  });

  const exportEvidencePack = useMutation({
    mutationFn: () => apiFetchBlob(`/projects/${projectId}/evidence-pack`),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "项目证据包.zip";
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "证据包导出失败" }),
  });

  const items = useMemo(() => data.data?.items ?? [], [data.data?.items]);
  const summary = data.data?.summary;
  const categories = useMemo(
    () => Array.from(new Set(items.map((item) => item.category))),
    [items],
  );
  const filtered = items.filter((item) => {
    if (category !== "all" && item.category !== category) return false;
    if (!onlyOpen) return true;
    return item.status === "blocked" || item.status === "pending";
  });
  const groups = categories
    .map((name) => ({ name, items: filtered.filter((item) => item.category === name) }))
    .filter((group) => group.items.length > 0);

  const act = (item: ReadinessItem, status: ReadinessStatus) => {
    const evidence = (evidenceDrafts[item.id] ?? "").trim();
    const note = (noteDrafts[item.id] ?? "").trim();
    if (status === "done" && (item.is_blocking || item.evidence_required) && !evidence) {
      setNotice({ tone: "error", text: "阻断项/需证据项完成前必须填写证据。" });
      return;
    }
    if ((status === "not_applicable" || status === "waived") && !note) {
      setNotice({ tone: "error", text: "不适用/豁免必须填写理由。" });
      return;
    }
    update.mutate({
      id: item.id,
      status,
      evidence_refs: evidence ? [evidence] : undefined,
      note: note || undefined,
      waived_reason: status === "not_applicable" || status === "waived" ? note : undefined,
    });
  };

  return (
    <Card className="mt-4 p-5" id="readiness-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">交标自查</h2>
            {data.data ? (
              <Badge tone="slate">
                {data.data.template.name} v{data.data.template.version}
              </Badge>
            ) : null}
            {data.isLoading ? (
              <Badge tone="slate">自查状态加载中</Badge>
            ) : data.isError ? (
              <Badge tone="amber">自查状态加载失败</Badge>
            ) : summary?.blocking_open ? (
              <Badge tone="rose">{summary.blocking_open} 个阻断项未关闭</Badge>
            ) : (
              <Badge tone="green">阻断项已清零</Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            自动项由合规检查/响应表驱动；人工项需证据或理由，阻断项不能静默通过。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => refreshAuto.mutate()} disabled={!canWrite || refreshAuto.isPending}>
            {refreshAuto.isPending ? "刷新中…" : "刷新自动项"}
          </Button>
          {canExport ? (
            <>
              <Button variant="secondary" onClick={() => exportFile.mutate()} disabled={exportFile.isPending}>
                导出 Excel
              </Button>
              <Button
                variant="secondary"
                onClick={() => exportEvidencePack.mutate()}
                disabled={exportEvidencePack.isPending}
              >
                {exportEvidencePack.isPending ? "打包中…" : "导出证据包"}
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {notice ? (
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${notice.tone === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
          {notice.text}
        </p>
      ) : null}
      {data.error ? (
        <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {data.error instanceof Error ? data.error.message : "交标清单加载失败"}
        </p>
      ) : null}

      {summary ? (
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <p className="text-xs text-slate-500">完成率</p>
            <p className="mt-1 text-lg font-semibold">{Math.round(summary.completion_rate * 100)}%</p>
          </div>
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <p className="text-xs text-slate-500">已完成</p>
            <p className="mt-1 text-lg font-semibold">{summary.done} / {summary.total}</p>
          </div>
          <div className="rounded-lg bg-rose-50 px-3 py-2">
            <p className="text-xs text-rose-600">阻断</p>
            <p className="mt-1 text-lg font-semibold text-rose-700">{summary.blocked}</p>
          </div>
          <div className="rounded-lg bg-amber-50 px-3 py-2">
            <p className="text-xs text-amber-700">待处理</p>
            <p className="mt-1 text-lg font-semibold text-amber-800">{summary.pending}</p>
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-2 text-slate-600">
          <input type="checkbox" checked={onlyOpen} onChange={(event) => setOnlyOpen(event.target.checked)} />
          只看未关闭
        </label>
        <select
          className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          <option value="all">全部类别</option>
          {categories.map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
        <span className="text-xs text-slate-500">
          截止：{formatTime(data.data?.internal_deadline ?? data.data?.bid_deadline ?? null)}
        </span>
      </div>

      <div className="mt-4 space-y-5">
        {groups.map((group) => (
          <div key={group.name}>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{group.name}</h3>
            <div className="mt-2 space-y-2">
              {group.items
                .slice()
                .sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || a.sort_order - b.sort_order)
                .map((item) => {
                  const meta = STATUS_META[item.status];
                  return (
                    <div
                      key={item.id}
                      className={`rounded-xl border px-3 py-3 ${item.is_blocking && item.status !== "done" ? "border-rose-200 bg-rose-50/30" : "border-slate-200 bg-white"}`}
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-slate-800">{item.label}</span>
                            <Badge tone={meta.tone}>{meta.label}</Badge>
                            {item.is_blocking ? <Badge tone="rose">阻断</Badge> : null}
                            <Badge tone="slate">{item.source_type === "auto" ? "自动" : "人工"}</Badge>
                            {item.stage ? <span className="text-xs text-slate-500">{item.stage}</span> : null}
                          </div>
                          <p className="mt-1 text-xs text-slate-500">
                            {item.rule_id ? `规则 ${item.rule_id}` : "人工确认"}
                            {" · "}
                            截止 {formatTime(item.due_at)}
                            {item.source_state === "stale" ? " · 自动项待刷新" : ""}
                            {item.waived_reason ? ` · ${item.waived_reason}` : ""}
                          </p>
                        </div>
                        {item.source_type === "auto" ? (
                          <span className="text-xs text-slate-500">由检查结果驱动</span>
                        ) : canWrite ? (
                          <div className="flex flex-wrap items-center gap-2">
                            <Button variant="secondary" onClick={() => act(item, "done")} disabled={update.isPending}>
                              完成
                            </Button>
                            <Button variant="ghost" onClick={() => act(item, "not_applicable")} disabled={update.isPending}>
                              不适用
                            </Button>
                            <Button variant="ghost" onClick={() => act(item, "waived")} disabled={update.isPending}>
                              豁免
                            </Button>
                            {item.status !== "pending" ? (
                              <Button variant="ghost" onClick={() => act(item, "pending")} disabled={update.isPending}>
                                重置
                              </Button>
                            ) : null}
                          </div>
                        ) : null}
                      </div>

                      {item.source_type === "manual" && canWrite ? (
                        <div className="mt-2 grid gap-2 sm:grid-cols-2">
                          <input
                            className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
                            placeholder="证据：文件名 / 链接 / 资产编号"
                            value={evidenceDrafts[item.id] ?? item.evidence_refs.join("、")}
                            onChange={(event) =>
                              setEvidenceDrafts((prev) => ({ ...prev, [item.id]: event.target.value }))
                            }
                          />
                          <input
                            className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
                            placeholder="备注 / 不适用或豁免理由"
                            value={noteDrafts[item.id] ?? item.note ?? ""}
                            onChange={(event) =>
                              setNoteDrafts((prev) => ({ ...prev, [item.id]: event.target.value }))
                            }
                          />
                        </div>
                      ) : item.evidence_refs.length > 0 ? (
                        <p className="mt-2 text-xs text-slate-500">证据：{item.evidence_refs.join("、")}</p>
                      ) : null}
                    </div>
                  );
                })}
            </div>
          </div>
        ))}
        {groups.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">
            当前筛选下没有检查项。
          </p>
        ) : null}
      </div>
    </Card>
  );
}
