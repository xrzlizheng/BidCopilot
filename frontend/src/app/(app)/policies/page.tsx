"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, SlidersHorizontal } from "lucide-react";

import { Badge, Button, Card, Loading, PageHeader, Select } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { CompliancePolicy } from "@/lib/types";

const CATEGORY_LABELS: Record<string, string> = {
  dark_bid: "暗标",
  format: "格式",
  qualification: "资格",
  consistency: "一致性",
  completeness: "完整性",
  time: "时间",
};

const SEVERITY_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

export default function PoliciesPage() {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canEdit = hasPermission(user?.permissions, "project:approve");
  const [message, setMessage] = useState<string | null>(null);
  const policies = useQuery({
    queryKey: ["compliance-policies"],
    queryFn: () => apiFetch<CompliancePolicy[]>("/policies"),
  });
  const update = useMutation({
    mutationFn: (payload: { ruleId: string; enabled: boolean; severity: string }) =>
      apiFetch<CompliancePolicy>(`/policies/${payload.ruleId}`, {
        method: "PATCH",
        body: JSON.stringify({
          enabled: payload.enabled,
          severity_override: payload.severity === "default" ? null : payload.severity,
        }),
      }),
    onSuccess: (policy) => {
      setMessage(`规则 ${policy.rule_id} 已更新。`);
      queryClient.invalidateQueries({ queryKey: ["compliance-policies"] });
    },
    onError: (error) =>
      setMessage(error instanceof ApiError ? `${error.message}（${error.code}）` : "更新失败"),
  });

  const rows = policies.data ?? [];
  const enabled = rows.filter((row) => row.enabled).length;
  const changed = rows.filter((row) => row.overridden).length;
  const groups = Object.entries(CATEGORY_LABELS).map(([key, label]) => ({
    key,
    label,
    rows: rows.filter((row) => row.category === key),
  }));

  return (
    <>
      <PageHeader
        eyebrow="COMPLIANCE / POLICY"
        title="合规策略"
        subtitle="按租户启用/停用规则与覆盖严重级别；规则引擎保持确定性，不因策略改变而调用 LLM。"
        actions={
          <div className="flex gap-2">
            <Badge tone="green">{enabled} 启用</Badge>
            <Badge tone="amber">{changed} 已定制</Badge>
          </div>
        }
      />
      {message ? (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white/80 px-4 py-3 text-sm text-slate-700">
          {message}
        </div>
      ) : null}
      {!canEdit ? (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          当前角色只读；需要 `project:approve` 才能修改策略。
        </div>
      ) : null}

      {policies.isLoading ? (
        <Loading />
      ) : policies.error ? (
        <div className="text-sm text-rose-600">{policies.error.message}</div>
      ) : (
        <div className="space-y-4">
          {groups
            .filter((group) => group.rows.length > 0)
            .map((group) => (
              <Card key={group.key} className="p-5">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-aurora-600" />
                  <h2 className="text-sm font-semibold">{group.label}规则</h2>
                  <span className="text-xs text-slate-400">{group.rows.length} 条</span>
                </div>
                <div className="mt-3 space-y-1">
                  {group.rows.map((row) => (
                    <div
                      key={row.rule_id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200/70 bg-white/60 px-3 py-2.5"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium text-slate-800">{row.name}</span>
                          <Badge tone={row.type === "HARD" ? "rose" : "amber"}>{row.type}</Badge>
                          <code className="text-[10px] text-slate-400">{row.rule_id}</code>
                        </div>
                        <p className="mt-0.5 text-[11px] text-slate-500">
                          默认严重级别：{SEVERITY_LABELS[row.default_severity] ?? row.default_severity}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="flex items-center gap-1.5 text-xs text-slate-500">
                          <input
                            type="checkbox"
                            checked={row.enabled}
                            disabled={!canEdit || update.isPending}
                            onChange={(event) =>
                              update.mutate({
                                ruleId: row.rule_id,
                                enabled: event.target.checked,
                                severity: row.overridden ? row.effective_severity : "default",
                              })
                            }
                          />
                          启用
                        </label>
                        <Select
                          className="h-8 text-xs"
                          value={
                            row.overridden && row.effective_severity !== row.default_severity
                              ? row.effective_severity
                              : "default"
                          }
                          disabled={!canEdit || update.isPending}
                          onChange={(event) =>
                            update.mutate({
                              ruleId: row.rule_id,
                              enabled: row.enabled,
                              severity: event.target.value,
                            })
                          }
                        >
                          <option value="default">默认级别</option>
                          <option value="high">高</option>
                          <option value="medium">中</option>
                          <option value="low">低</option>
                        </Select>
                        <Button
                          variant="ghost"
                          disabled={!canEdit || update.isPending}
                          onClick={() =>
                            update.mutate({
                              ruleId: row.rule_id,
                              enabled: true,
                              severity: "default",
                            })
                          }
                        >
                          <SlidersHorizontal size={14} /> 复位
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            ))}
        </div>
      )}
    </>
  );
}
