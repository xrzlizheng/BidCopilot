"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Input } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import type { Project } from "@/lib/types";

const STATUS_META: Record<
  string,
  { label: string; tone: "green" | "rose" | "amber" | "slate"; hint: string }
> = {
  active: { label: "在投", tone: "amber", hint: "正在准备或已投标，尚未出结果" },
  won: { label: "已中标", tone: "green", hint: "已中标，进入履约阶段" },
  lost: { label: "未中标", tone: "rose", hint: "未中标或已废标" },
};

// 与后端 PROJECT_TRANSITIONS 保持一致
const TRANSITIONS: Record<string, { status: string; label: string; needsReason?: boolean }[]> = {
  active: [
    { status: "won", label: "标记已中标" },
    { status: "lost", label: "标记未中标", needsReason: true },
  ],
  won: [],
  lost: [{ status: "active", label: "重新激活" }],
};

export function ProjectStatusActions({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const meta = STATUS_META[project.status] ?? {
    label: project.status,
    tone: "slate" as const,
    hint: "",
  };
  const options = TRANSITIONS[project.status] ?? [];

  const change = useMutation({
    mutationFn: async ({ status, reasonText }: { status: string; reasonText?: string }) =>
      apiFetch<Project>(`/projects/${project.id}/status`, {
        method: "POST",
        body: JSON.stringify({ status, reason: reasonText ?? null }),
      }),
    onSuccess: async () => {
      setPending(null);
      setReason("");
      setMessage("状态已更新");
      await queryClient.invalidateQueries({ queryKey: ["project", project.id] });
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error: Error) => setMessage(error.message),
  });

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <Badge tone={meta.tone}>{meta.label}</Badge>
      {meta.hint ? <span className="text-xs text-slate-400">{meta.hint}</span> : null}

      {options.length === 0 ? (
        <span className="text-xs text-slate-400">
          {project.status === "won" ? "已中标为终态，如需重开请新建项目" : ""}
        </span>
      ) : null}

      {options.map((option) =>
        pending === option.status ? (
          <span key={option.status} className="flex items-center gap-1">
            <Input
              className="h-8 w-52"
              placeholder="填写原因（未中标必填）"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
            <Button
              className="h-8"
              onClick={() => change.mutate({ status: option.status, reasonText: reason })}
              disabled={change.isPending}
            >
              确认
            </Button>
            <Button className="h-8" variant="ghost" onClick={() => setPending(null)}>
              取消
            </Button>
          </span>
        ) : (
          <Button
            key={option.status}
            className="h-8"
            variant="secondary"
            onClick={() => {
              if (option.needsReason) {
                setPending(option.status);
                setReason("");
              } else {
                change.mutate({ status: option.status });
              }
            }}
          >
            {option.label}
          </Button>
        ),
      )}

      {message ? <span className="text-xs text-slate-500">{message}</span> : null}
    </div>
  );
}
