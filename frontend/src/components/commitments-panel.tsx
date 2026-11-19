"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card, ErrorState, Input } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type Commitment = {
  id: string;
  kind: string;
  label: string;
  value_text: string;
  value_amount: string | null;
  value_days: number | null;
  source: string;
  confirmed_at: string | null;
};

const KIND_LABELS: Record<string, string> = {
  price: "报价",
  schedule: "工期",
  warranty: "质保",
  personnel: "人员",
};

export function CommitmentsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [quote, setQuote] = useState("");
  const [duration, setDuration] = useState("");
  const [warranty, setWarranty] = useState("");
  const [personnel, setPersonnel] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const data = useQuery({
    queryKey: ["project-commitments", projectId],
    queryFn: () => apiFetch<Commitment[]>(`/projects/${projectId}/commitments`),
  });

  const snapshot = useMutation({
    mutationFn: () =>
      apiFetch<Commitment[]>(`/projects/${projectId}/commitments/snapshot`, {
        method: "POST",
        body: JSON.stringify({
          quote_amount: quote.trim() ? Number(quote) : null,
          duration_days: duration.trim() ? Number(duration) : null,
          warranty_days: warranty.trim() ? Number(warranty) : null,
          personnel: personnel
            .split(/[,，、]/)
            .map((item) => item.trim())
            .filter(Boolean),
        }),
      }),
    onSuccess: (rows) => {
      setNotice(`已沉淀 ${rows.length} 条投标承诺，合同审查时会自动对照。`);
      queryClient.invalidateQueries({ queryKey: ["project-commitments", projectId] });
    },
    onError: (error) =>
      setNotice(error instanceof Error ? error.message : "承诺快照失败"),
  });

  const rows = data.data ?? [];
  const loadFailed = data.isError;

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">合同履约衔接</h2>
          <p className="mt-1 text-xs text-slate-500">
            把报价、工期、质保、拟派人员沉淀为投标承诺；合同审查会对照偏离并提示。
          </p>
        </div>
        <Badge tone={rows.length ? "green" : "slate"}>{rows.length} 条承诺</Badge>
      </div>
      {notice ? <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">{notice}</p> : null}
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <Input placeholder="报价（元）" value={quote} onChange={(event) => setQuote(event.target.value)} />
        <Input placeholder="工期（天）" value={duration} onChange={(event) => setDuration(event.target.value)} />
        <Input placeholder="质保（天）" value={warranty} onChange={(event) => setWarranty(event.target.value)} />
        <Input
          placeholder="拟派人员（逗号分隔）"
          value={personnel}
          onChange={(event) => setPersonnel(event.target.value)}
        />
      </div>
      <div className="mt-2">
        <Button variant="secondary" onClick={() => snapshot.mutate()} disabled={snapshot.isPending}>
          {snapshot.isPending ? "保存中…" : "生成承诺快照"}
        </Button>
      </div>
      {loadFailed ? (
        <ErrorState
          error={data.error}
          onRetry={() => data.refetch()}
          hint="承诺列表没取到；这不代表没有承诺，请重试后再判断。"
        />
      ) : rows.length ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {rows.map((row) => (
            <div key={row.id} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{KIND_LABELS[row.kind] ?? row.kind}：{row.label}</span>
                <Badge tone="slate">{row.source}</Badge>
              </div>
              <p className="mt-1 text-slate-600">{row.value_text}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-sm text-slate-500">
          尚未沉淀投标承诺。点上方「从项目提取承诺」，系统会把报价、工期、质保、人员等承诺固化下来，合同审查时自动对照。
        </p>
      )}
    </Card>
  );
}
