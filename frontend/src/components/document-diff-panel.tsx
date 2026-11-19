"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card, ErrorState } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type DiffList = {
  added: string[];
  removed: string[];
  modified: { key: string; changed_fields: string[] }[];
};

type DocumentDiff = {
  has_diff: boolean;
  has_changes?: boolean;
  reason?: string;
  from_version?: number;
  to_version?: number;
  from_document_kind?: string | null;
  to_document_kind?: string | null;
  budget?: { from: string | null; to: string | null; changed: boolean };
  bid_validity_days?: { from: number | null; to: number | null; changed: boolean };
  dark_bid?: { changed: boolean };
  packages?: { changed: boolean };
  [key: string]: unknown;
};

const DIMENSIONS = [
  ["substantive_requirements", "实质性要求"],
  ["scoring_items", "评分项"],
  ["qualification_requirements", "资格要求"],
  ["timeline", "时间节点"],
  ["submission_checklist", "材料清单"],
  ["front_appendix", "投标人须知前附表"],
  ["star_clauses", "★/▲ 条款"],
  ["technical_parameters", "技术参数"],
  ["format_rules", "格式要求"],
  ["reject_clauses", "否决条款"],
  ["clarifications", "澄清事项"],
] as const;

function asDiffList(value: unknown): DiffList {
  if (!value || typeof value !== "object") return { added: [], removed: [], modified: [] };
  const row = value as Partial<DiffList>;
  return {
    added: Array.isArray(row.added) ? row.added : [],
    removed: Array.isArray(row.removed) ? row.removed : [],
    modified: Array.isArray(row.modified) ? row.modified : [],
  };
}

export function DocumentDiffPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const data = useQuery({
    queryKey: ["project-document-diff", projectId],
    queryFn: () => apiFetch<DocumentDiff>(`/projects/${projectId}/document-diff`),
  });

  const apply = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/document-diff/apply`, {
        method: "POST",
        body: JSON.stringify({ confirm: confirmed, note: note || null }),
      }),
    onSuccess: () => {
      setNotice({ tone: "ok", text: "补遗已应用到逐条响应与交标清单。" });
      queryClient.invalidateQueries({ queryKey: ["project-document-diff", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project-responses", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project-readiness", projectId] });
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "应用失败" }),
  });

  if (data.error) {
    return (
      <Card className="p-5">
        <ErrorState error={data.error} onRetry={() => data.refetch()} />
      </Card>
    );
  }
  if (!data.data?.has_diff) {
    return null;
  }

  const diff = data.data;
  const canApply = diff.to_document_kind === "amendment" && Boolean(diff.has_changes);

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">版本差异 / 补遗应用</h2>
            <Badge tone="slate">
              v{diff.from_version ?? "?"} → v{diff.to_version ?? "?"}
            </Badge>
            <Badge tone={diff.to_document_kind === "amendment" ? "amber" : "slate"}>
              {diff.to_document_kind === "amendment" ? "补遗/更正" : diff.to_document_kind ?? "招标文件"}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            上传补遗不会自动改响应；确认差异后显式应用，已确认的同文本条目会保留。
          </p>
        </div>
        {diff.budget?.changed ? (
          <Badge tone="amber">
            预算 {diff.budget.from ?? "—"} → {diff.budget.to ?? "—"}
          </Badge>
        ) : null}
      </div>

      {notice ? (
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${notice.tone === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
          {notice.text}
        </p>
      ) : null}

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {DIMENSIONS.map(([key, label]) => {
          const list = asDiffList(diff[key]);
          const total = list.added.length + list.removed.length + list.modified.length;
          if (total === 0) return null;
          return (
            <details key={key} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                <span className="font-medium">{label}</span>
                {list.added.length ? <Badge tone="green">+{list.added.length}</Badge> : null}
                {list.removed.length ? <Badge tone="rose">-{list.removed.length}</Badge> : null}
                {list.modified.length ? <Badge tone="amber">改 {list.modified.length}</Badge> : null}
              </summary>
              <div className="mt-2 space-y-1 text-xs text-slate-600">
                {list.added.slice(0, 5).map((item) => (
                  <p key={`add-${item}`}>新增：{item}</p>
                ))}
                {list.removed.slice(0, 5).map((item) => (
                  <p key={`remove-${item}`}>删除：{item}</p>
                ))}
                {list.modified.slice(0, 5).map((item) => (
                  <p key={`modify-${item.key}`}>
                    修改：{item.key}
                    {item.changed_fields.length ? `（${item.changed_fields.join("、")}）` : ""}
                  </p>
                ))}
              </div>
            </details>
          );
        })}
      </div>

      {canApply ? (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50/50 px-3 py-3">
          <label className="flex items-start gap-2 text-sm text-amber-900">
            <input
              type="checkbox"
              className="mt-1"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            我已核对上述差异，确认应用到最新版本的逐条响应与交标清单。
          </label>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              className="min-w-[240px] flex-1 rounded-lg border border-amber-200 bg-white px-2 py-1.5 text-sm"
              placeholder="应用备注（可选）"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
            <Button variant="secondary" onClick={() => apply.mutate()} disabled={!confirmed || apply.isPending}>
              {apply.isPending ? "应用中…" : "应用补遗"}
            </Button>
          </div>
        </div>
      ) : diff.to_document_kind === "amendment" ? (
        <p className="mt-3 text-sm text-slate-500">最新补遗没有可应用的变更。</p>
      ) : null}
    </Card>
  );
}
