"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Card, EmptyState, ErrorState, Loading } from "@/components/ui";
import { apiDownload, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { ComplianceReport, Finding } from "@/lib/types";

const SEVERITY_TONES: Record<string, "rose" | "amber" | "slate"> = {
  high: "rose",
  medium: "amber",
  low: "slate",
};
const STATUS_TONES: Record<string, "rose" | "green" | "slate" | "amber"> = {
  open: "rose",
  fixed: "green",
  accepted: "amber",
};

function locationText(finding: Finding): string {
  const title = finding.location?.section_title;
  const scope = finding.location?.scope;
  return String(title ?? scope ?? "—");
}

export function CompliancePanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canExport = hasPermission(user?.permissions, "compliance:export");
  const [message, setMessage] = useState<string | null>(null);

  const report = useQuery({
    queryKey: ["compliance", projectId],
    queryFn: () => apiFetch<ComplianceReport>(`/projects/${projectId}/compliance/latest`),
    retry: false,
  });

  const scan = useMutation({
    mutationFn: () =>
      apiFetch<ComplianceReport>(`/projects/${projectId}/compliance/scan`, { method: "POST" }),
    onSuccess: (result) => {
      setMessage(
        `预检完成：HARD ${result.summary_json.hard ?? 0} 条，SOFT ${result.summary_json.soft ?? 0} 条。`,
      );
      queryClient.setQueryData(["compliance", projectId], result);
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "预检失败"),
  });

  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      apiFetch<Finding>(`/findings/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["compliance", projectId] }),
    onError: (error) => setMessage(error instanceof Error ? error.message : "处置失败"),
  });

  const exportReport = useMutation({
    mutationFn: async (format: "xlsx" | "pdf") => {
      if (!report.data) throw new Error("尚未生成预检报告");
      return apiDownload(`/compliance/reports/${report.data.id}/export`, { format });
    },
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      setMessage(`已导出 ${filename}。`);
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "导出失败"),
  });

  const data = report.data;

  return (
    <Card className="mt-4 p-5" id="compliance-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">合规预检</h2>
          <p className="mt-1 text-xs text-slate-500">
            确定性规则扫描：暗标、格式、资格、证据、材料清单、时间、空章节；规则引擎不调用 LLM。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {data && canExport ? (
            <>
              <Button
                variant="secondary"
                onClick={() => exportReport.mutate("xlsx")}
                disabled={exportReport.isPending}
              >
                {exportReport.isPending ? "导出中…" : "导出 Excel"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => exportReport.mutate("pdf")}
                disabled={exportReport.isPending}
              >
                {exportReport.isPending ? "导出中…" : "导出 PDF"}
              </Button>
            </>
          ) : null}
          <Button onClick={() => scan.mutate()} disabled={scan.isPending}>
            {scan.isPending ? "扫描中…" : "运行合规预检"}
          </Button>
        </div>
      </div>

      {message ? <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">{message}</div> : null}

      {report.isLoading ? (
        <Loading />
      ) : report.isError ? (
        <ErrorState
          error={report.error}
          onRetry={() => report.refetch()}
          hint="预检报告没取到。若是 404，说明该项目还没跑过预检；先在上方点击「运行合规预检」。"
        />
      ) : !data ? (
        <div className="mt-3">
          <EmptyState text="尚未运行合规预检。生成并核查章节后点击上方按钮。" />
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-4">
            <Metric label="HARD" value={String(data.summary_json.hard ?? 0)} />
            <Metric label="SOFT" value={String(data.summary_json.soft ?? 0)} />
            <Metric label="规则版本" value={data.rule_version} />
            <Metric label="文件哈希" value={data.file_hash.slice(0, 10)} />
          </div>

          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-3 py-2">级别</th>
                  <th className="px-3 py-2">规则</th>
                  <th className="px-3 py-2">问题</th>
                  <th className="px-3 py-2">位置</th>
                  <th className="px-3 py-2">期望 / 实际</th>
                  <th className="px-3 py-2">建议</th>
                  <th className="px-3 py-2">处置</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.findings.map((finding) => (
                  <tr key={finding.id}>
                    <td className="px-3 py-2">
                      <Badge tone={SEVERITY_TONES[finding.severity] ?? "slate"}>
                        {finding.finding_type}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-600">{finding.rule_id}</td>
                    <td className="max-w-xs px-3 py-2">{finding.message}</td>
                    <td className="px-3 py-2 text-slate-600">{locationText(finding)}</td>
                    <td className="max-w-xs px-3 py-2 text-xs text-slate-600">
                      <div>期望：{finding.expected ?? "—"}</div>
                      <div>实际：{finding.actual ?? "—"}</div>
                    </td>
                    <td className="max-w-xs px-3 py-2 text-xs text-slate-600">{finding.suggestion ?? "—"}</td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1">
                        <Badge tone={STATUS_TONES[finding.status] ?? "slate"}>{finding.status}</Badge>
                        <select
                          className="rounded border border-slate-300 px-1 py-1 text-xs"
                          value={finding.status}
                          onChange={(event) =>
                            update.mutate({ id: finding.id, status: event.target.value })
                          }
                        >
                          <option value="open">open</option>
                          <option value="fixed">fixed</option>
                          <option value="accepted">accepted</option>
                        </select>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}
