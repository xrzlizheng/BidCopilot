"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Badge, Button, Card, EmptyState, Input, Loading, PageHeader } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import type { AuditEvent, AuditVerify } from "@/lib/types";

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [verifyResult, setVerifyResult] = useState<AuditVerify | null>(null);

  const events = useQuery({
    queryKey: ["audit-events"],
    queryFn: () =>
      apiFetch<{ items: AuditEvent[]; total: number }>("/audit/events?page_size=100"),
  });
  const verify = useMutation({
    mutationFn: () => apiFetch<AuditVerify>("/audit/verify"),
    onSuccess: setVerifyResult,
  });

  const filtered = (events.data?.items ?? []).filter(
    (event) =>
      (!action || event.action.includes(action)) &&
      (!resourceType || event.resource_type.includes(resourceType)),
  );

  return (
    <>
      <PageHeader
        title="审计日志"
        subtitle="租户内追加式审计事件与 SHA-256 哈希链校验；不展示客户正文"
        actions={
          <Button variant="secondary" onClick={() => verify.mutate()} disabled={verify.isPending}>
            {verify.isPending ? "校验中…" : "验证哈希链"}
          </Button>
        }
      />
      {verifyResult ? (
        <div
          className={`mb-4 rounded-xl border px-4 py-3 text-sm ${
            verifyResult.valid
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-rose-200 bg-rose-50 text-rose-700"
          }`}
        >
          {verifyResult.valid
            ? `哈希链完整（${verifyResult.events} 条事件）。`
            : `哈希链校验失败（${verifyResult.events} 条事件），请立即排查。`}
        </div>
      ) : null}
      {verify.isError ? (
        <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {verify.error instanceof Error ? verify.error.message : "哈希链校验失败"}
        </div>
      ) : null}

      <Card className="p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="w-52"
            placeholder="按 action 过滤"
            value={action}
            onChange={(event) => setAction(event.target.value)}
          />
          <Input
            className="w-52"
            placeholder="按 resource_type 过滤"
            value={resourceType}
            onChange={(event) => setResourceType(event.target.value)}
          />
          <span className="text-sm text-slate-500">
            共 {events.data?.total ?? 0} 条，当前显示 {filtered.length} 条
          </span>
        </div>

        {events.isLoading ? (
          <Loading />
        ) : events.error ? (
          <div className="mt-4 text-sm text-rose-600">{events.error.message}</div>
        ) : filtered.length === 0 ? (
          <EmptyState
                text="当前筛选条件下没有审计事件。"
                hint="审计在登录、建项目、审批、导出、改权限等动作后产生。若刚部署完还没有任何操作，这里是空的属正常；也可放宽上方筛选条件。"
              />
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">Seq</th>
                  <th className="py-2 pr-3">时间</th>
                  <th className="py-2 pr-3">动作</th>
                  <th className="py-2 pr-3">资源</th>
                  <th className="py-2 pr-3">操作人</th>
                  <th className="py-2">数据</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.map((event) => (
                  <tr key={event.id} className="align-top">
                    <td className="py-2 pr-3 text-slate-500">{event.seq}</td>
                    <td className="py-2 pr-3 text-slate-500">
                      {event.created_at.slice(0, 19).replace("T", " ")}
                    </td>
                    <td className="py-2 pr-3">
                      <Badge tone="blue">{event.action}</Badge>
                    </td>
                    <td className="py-2 pr-3">
                      <div>{event.resource_type}</div>
                      <div className="text-xs text-slate-500">{event.resource_id ?? "—"}</div>
                    </td>
                    <td className="py-2 pr-3 text-xs text-slate-500">
                      {event.actor_id ? event.actor_id.slice(0, 8) : "system"}
                    </td>
                    <td className="max-w-md py-2 text-xs text-slate-600">
                      {JSON.stringify(event.data).slice(0, 200)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
