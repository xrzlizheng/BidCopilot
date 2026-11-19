"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { apiFetch, apiFetchBlob } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { Badge, Button, Card, ErrorState } from "@/components/ui";

type ResponseStatus = "pending" | "responded" | "deviated" | "not_responded";

type ComplianceItem = {
  id: string;
  requirement_type: "qualification" | "substantive" | "scoring" | string;
  requirement_text: string;
  category: string;
  response_status: ResponseStatus;
  response_note: string | null;
  evidence_refs: string[];
  section_ref: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
};

type ComplianceList = {
  items: ComplianceItem[];
  summary: Record<ResponseStatus, number>;
  total: number;
  unresolved: number;
};

const TYPE_LABEL: Record<string, string> = {
  qualification: "资格要求",
  substantive: "实质性要求",
  scoring: "评分项",
  clarification: "澄清事项",
};

const STATUS_META: Record<ResponseStatus, { label: string; tone: "green" | "rose" | "amber" | "slate" }> = {
  responded: { label: "已响应", tone: "green" },
  deviated: { label: "偏离", tone: "amber" },
  not_responded: { label: "不响应", tone: "rose" },
  pending: { label: "待确认", tone: "slate" },
};

const GROUP_ORDER = ["clarification", "qualification", "substantive", "scoring"];
const BATCH_STATUSES: ResponseStatus[] = ["responded", "deviated", "not_responded"];

export function ComplianceResponses({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "project:write");
  const canExport = hasPermission(user?.permissions, "project:export");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [evidenceDrafts, setEvidenceDrafts] = useState<Record<string, string>>({});
  const [statusFilter, setStatusFilter] = useState<"all" | "unresolved" | ResponseStatus>("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const data = useQuery({
    queryKey: ["project-responses", projectId],
    queryFn: () => apiFetch<ComplianceList>(`/projects/${projectId}/responses`),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["project-responses", projectId] });
  };

  const build = useMutation({
    mutationFn: () =>
      apiFetch<ComplianceList>(`/projects/${projectId}/responses/build`, { method: "POST" }),
    onSuccess: () => {
      setSelected([]);
      setNotice({ tone: "ok", text: "响应表已生成。" });
      refresh();
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "响应表生成失败" }),
  });

  const update = useMutation({
    mutationFn: (input: { id: string; status: ResponseStatus; note?: string }) =>
      apiFetch<ComplianceItem>(`/projects/${projectId}/responses/${input.id}`, {
        method: "PATCH",
        body: JSON.stringify({ response_status: input.status, response_note: input.note ?? null }),
      }),
    onSuccess: refresh,
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "标记失败" }),
  });

  const sections = useQuery({
    queryKey: ["project-sections", projectId],
    queryFn: () => apiFetch<Array<{ id: string; title: string }>>(`/projects/${projectId}/sections`),
    staleTime: 30_000,
  });

  const link = useMutation({
    mutationFn: (input: {
      id: string;
      note: string | null;
      section_ref?: string | null;
      evidence_refs?: string[];
    }) =>
      apiFetch<ComplianceItem>(`/projects/${projectId}/responses/${input.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          response_note: input.note,
          section_ref: input.section_ref,
          evidence_refs: input.evidence_refs,
        }),
      }),
    onSuccess: refresh,
  });

  const batch = useMutation({
    mutationFn: (input: { ids: string[]; status: ResponseStatus; note?: string }) =>
      apiFetch<{ updated: number; unresolved: number }>(`/projects/${projectId}/responses/batch`, {
        method: "POST",
        body: JSON.stringify({
          response_ids: input.ids,
          response_status: input.status,
          response_note: input.note ?? null,
        }),
      }),
    onSuccess: () => {
      setSelected([]);
      setNotice(null);
      refresh();
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "批量标记失败" }),
  });

  const exportExcel = useMutation({
    mutationFn: () => apiFetchBlob(`/projects/${projectId}/responses/export`),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "合规响应表.xlsx";
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "导出失败" }),
  });

  const items = data.data?.items ?? [];
  const summary = data.data?.summary ?? { pending: 0, responded: 0, deviated: 0, not_responded: 0 };
  const unresolvedKeys: ResponseStatus[] = ["pending", "deviated", "not_responded"];
  const filtered = items.filter((item) => {
    if (typeFilter !== "all" && item.requirement_type !== typeFilter) return false;
    if (statusFilter === "all") return true;
    if (statusFilter === "unresolved") return unresolvedKeys.includes(item.response_status);
    return item.response_status === statusFilter;
  });
  const grouped = GROUP_ORDER.filter((type) => filtered.some((item) => item.requirement_type === type));
  const selectedInFilter = filtered.filter((item) => selected.includes(item.id)).length;

  const toggle = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id]));

  return (
    <Card className="mt-4 p-5" id="compliance-responses-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">逐条响应 / 偏离表</h2>
          <p className="mt-1 text-xs text-slate-500">
            对招标文件每一项要求逐条给出响应状态；未响应与偏离合计
            <span className="font-medium text-slate-700"> {data.data?.unresolved ?? 0} </span>
            项仍待处理。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <Badge tone="slate">待确认 {summary.pending}</Badge>
          <Badge tone="green">已响应 {summary.responded}</Badge>
          <Badge tone="amber">偏离 {summary.deviated}</Badge>
          <Badge tone="rose">不响应 {summary.not_responded}</Badge>
          {canWrite ? (
            <Button variant="secondary" onClick={() => build.mutate()} disabled={build.isPending}>
              {build.isPending ? "生成中…" : items.length ? "重新生成" : "生成响应表"}
            </Button>
          ) : null}
          {canExport ? (
            <Button
              variant="ghost"
              disabled={!items.length || exportExcel.isPending}
              onClick={() => exportExcel.mutate()}
            >
              {exportExcel.isPending ? "导出中…" : "导出 Excel"}
            </Button>
          ) : null}
        </div>
      </div>

      {notice ? (
        <div
          className={`mt-3 rounded-lg px-3 py-2 text-sm ${
            notice.tone === "error"
              ? "border border-rose-200 bg-rose-50 text-rose-700"
              : "bg-slate-50 text-slate-600"
          }`}
        >
          {notice.text}
        </div>
      ) : null}

      {data.isLoading ? <p className="mt-3 text-sm text-slate-500">加载中…</p> : null}
      {!data.isLoading && data.isError ? (
        <ErrorState error={data.error} onRetry={() => data.refetch()} />
      ) : null}
      {!data.isLoading && !data.isError && items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          尚未生成。先上传并解析招标文件，再点击“生成响应表”。
        </p>
      ) : null}

      {items.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-3 py-2">
          <select
            aria-label="按响应状态筛选"
            className="h-7 rounded border border-slate-300 bg-white px-2 text-xs"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}
          >
            <option value="all">全部状态</option>
            <option value="unresolved">只看未处理</option>
            <option value="pending">待确认</option>
            <option value="responded">已响应</option>
            <option value="deviated">偏离</option>
            <option value="not_responded">不响应</option>
          </select>
          <select
            aria-label="按条款类型筛选"
            className="h-7 rounded border border-slate-300 bg-white px-2 text-xs"
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
          >
            <option value="all">全部类型</option>
            {GROUP_ORDER.map((type) => (
              <option key={type} value={type}>
                {TYPE_LABEL[type] ?? type}
              </option>
            ))}
          </select>
          <span className="text-xs text-slate-500">
            筛选 {filtered.length} / {items.length} 条
          </span>
          <Button
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={() => setSelected(filtered.map((item) => item.id))}
            disabled={!filtered.length}
          >
            选中筛选结果
          </Button>
          <Button
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={() => setSelected([])}
            disabled={!selected.length}
          >
            清空选择
          </Button>
          <span className="ml-auto text-xs text-slate-500">已选 {selected.length} 条</span>
          {BATCH_STATUSES.map((status) => (
            <Button
              key={status}
              variant="secondary"
              className="h-7 px-2 text-xs"
              disabled={!selected.length || batch.isPending}
              onClick={() =>
                batch.mutate({
                  ids: selected,
                  status,
                  note: selected
                    .map((id) => drafts[id])
                    .find((value) => value && value.trim())
                    ?.trim(),
                })
              }
            >
              批量标记为{STATUS_META[status].label}
            </Button>
          ))}
          {batch.isError ? (
            <span className="text-xs text-rose-600">
              批量标记失败：{(batch.error as Error).message}
            </span>
          ) : null}
          {selected.length && selectedInFilter < selected.length ? (
            <span className="text-xs text-amber-700">
              注意：已选条目中有 {selected.length - selectedInFilter} 条不在当前筛选内，批量标记仍会生效。
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 space-y-4">
        {grouped.map((type) => (
          <div key={type}>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
              {TYPE_LABEL[type] ?? type}
            </p>
            <div className="space-y-2">
              {filtered
                .filter((item) => item.requirement_type === type)
                .map((item) => {
                  const meta = STATUS_META[item.response_status];
                  return (
                    <div key={item.id} className="rounded-lg border border-slate-200 px-3 py-2">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex min-w-0 gap-2">
                          <input
                            type="checkbox"
                            className="mt-1 h-4 w-4 accent-indigo-600"
                            checked={selected.includes(item.id)}
                            aria-label={`选择 ${item.requirement_text.slice(0, 20)}`}
                            onChange={() => toggle(item.id)}
                          />
                          <div className="min-w-0">
                            <p className="text-sm text-slate-800">{item.requirement_text}</p>
                            <p className="mt-1 text-xs text-slate-500">
                              {item.category}
                              {item.section_ref ? ` · 对应章节 ${item.section_ref}` : ""}
                              {item.confirmed_at ? " · 已人工确认" : ""}
                            </p>
                            {item.response_note ? (
                              <p className="mt-1 text-xs text-slate-600">说明：{item.response_note}</p>
                            ) : null}
                          </div>
                        </div>
                        <Badge tone={meta.tone}>{meta.label}</Badge>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-1">
                        {(["responded", "deviated", "not_responded"] as ResponseStatus[]).map(
                          (status) => (
                            <Button
                              key={status}
                              variant="ghost"
                              className="h-7 px-2 text-xs"
                              disabled={update.isPending}
                              onClick={() =>
                                update.mutate({
                                  id: item.id,
                                  status,
                                  note: drafts[item.id] ?? item.response_note ?? undefined,
                                })
                              }
                            >
                              标记为{STATUS_META[status].label}
                            </Button>
                          ),
                        )}
                        <input
                          className="ml-auto h-7 w-56 rounded border border-slate-300 px-2 text-xs"
                          placeholder="响应说明（可选）"
                          value={drafts[item.id] ?? item.response_note ?? ""}
                          onChange={(event) =>
                            setDrafts((prev) => ({ ...prev, [item.id]: event.target.value }))
                          }
                        />
                      </div>

                      <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
                        <select
                          className="h-7 max-w-56 rounded border border-slate-300 bg-white px-2 text-xs"
                          aria-label={`关联章节 ${item.requirement_text.slice(0, 16)}`}
                          value={item.section_ref ?? ""}
                          disabled={link.isPending}
                          onChange={(event) =>
                            link.mutate({
                              id: item.id,
                              note: drafts[item.id] ?? item.response_note ?? null,
                              section_ref: event.target.value,
                            })
                          }
                        >
                          <option value="">未关联章节</option>
                          {(sections.data ?? []).map((section) => (
                            <option key={section.id} value={section.title}>
                              {section.title}
                            </option>
                          ))}
                        </select>
                        <input
                          className="h-7 w-64 rounded border border-slate-300 px-2 text-xs"
                          placeholder="证据引用（逗号分隔）"
                          aria-label={`证据引用 ${item.requirement_text.slice(0, 16)}`}
                          value={
                            evidenceDrafts[item.id] ?? (item.evidence_refs ?? []).join("，")
                          }
                          onChange={(event) =>
                            setEvidenceDrafts((prev) => ({ ...prev, [item.id]: event.target.value }))
                          }
                          onKeyDown={(event) => {
                            if (event.key !== "Enter") return;
                            event.preventDefault();
                            (event.target as HTMLInputElement).blur();
                          }}
                          onBlur={() => {
                            const draft = evidenceDrafts[item.id];
                            if (draft === undefined || draft === (item.evidence_refs ?? []).join("，")) {
                              return;
                            }
                            link.mutate({
                              id: item.id,
                              note: drafts[item.id] ?? item.response_note ?? null,
                              evidence_refs: draft
                                .split(/[，,、;；]/)
                                .map((value) => value.trim())
                                .filter(Boolean),
                            });
                            setEvidenceDrafts((prev) => {
                              const next = { ...prev };
                              delete next[item.id];
                              return next;
                            });
                          }}
                        />
                        {item.evidence_refs?.length ? (
                          <span className="text-xs text-slate-500">
                            证据 {item.evidence_refs.length} 条
                          </span>
                        ) : null}
                        {link.isPending ? <span className="text-xs text-slate-400">保存中…</span> : null}
                        {link.isError ? (
                          <span className="text-xs text-rose-600">
                            关联保存失败：{(link.error as Error).message}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
            </div>
          </div>
        ))}
        {items.length && !filtered.length ? (
          <p className="text-sm text-slate-500">当前筛选没有条目。</p>
        ) : null}
      </div>
    </Card>
  );
}
