"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type SelfCheckFinding = {
  code: string;
  severity: string;
  message: string;
  evidence: Record<string, unknown>;
};

type SelfCheckResult = {
  findings: SelfCheckFinding[];
  checked: Record<string, unknown>;
  notes: string[];
};

export function SelfCheckPanel({ projectId }: { projectId: string }) {
  const [result, setResult] = useState<SelfCheckResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useMutation({
    mutationFn: () => apiFetch<SelfCheckResult>(`/projects/${projectId}/self-check`),
    onSuccess: (data) => {
      setResult(data);
      setNotice(
        data.findings.length
          ? `发现 ${data.findings.length} 个需确认事项（只提示，不提供清理/规避建议）。`
          : "未发现跨项目人员重复或最终文件元数据异常。",
      );
    },
    onError: (error) =>
      setNotice(error instanceof Error ? error.message : "自我检查失败"),
  });

  return (
    <Card className="mt-4 p-5" id="selfcheck-panel">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">有限自我检查</h2>
          <p className="mt-1 text-xs text-slate-500">
            检查人员跨项目/联合体重复与最终文件元数据异常；只做合规提示和冲突预防。
          </p>
        </div>
        <Button variant="secondary" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "检查中…" : "运行自我检查"}
        </Button>
      </div>
      {notice ? <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">{notice}</p> : null}
      {result ? (
        <div className="mt-3 space-y-2">
          {result.findings.map((finding) => (
            <div key={`${finding.code}-${finding.message}`} className="rounded-lg border border-amber-200 bg-amber-50/40 px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={finding.severity === "high" ? "rose" : "amber"}>{finding.severity}</Badge>
                <span className="font-medium">{finding.code}</span>
              </div>
              <p className="mt-1 text-slate-700">{finding.message}</p>
            </div>
          ))}
          {result.findings.length === 0 ? (
            <p className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600">
              未发现需要确认的跨项目人员或元数据问题。
            </p>
          ) : null}
          <p className="text-xs text-slate-500">{result.notes.join(" ")}</p>
        </div>
      ) : null}
    </Card>
  );
}
