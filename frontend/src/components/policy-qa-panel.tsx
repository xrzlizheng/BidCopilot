"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { MessageCircleQuestion } from "lucide-react";

import { Badge, Button, Card, Input } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";

type QaResponse = {
  answer: string;
  sources: { index: number; title: string; excerpt: string; document_id: string }[];
};

export function PolicyQaPanel({ projectId }: { projectId: string }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QaResponse | null>(null);

  const qa = useMutation({
    mutationFn: () =>
      apiFetch<QaResponse>(`/projects/${projectId}/policy-qa`, {
        method: "POST",
        body: JSON.stringify({ question }),
      }),
    onSuccess: (data) => setResult(data),
  });

  return (
    <Card className="mt-4 p-5">
      <div className="flex items-center gap-2">
        <MessageCircleQuestion size={16} className="text-electric-600" />
        <h2 className="text-sm font-semibold">制度口径问答</h2>
        <Badge tone="slate">仅依据知识库</Badge>
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (question.trim()) qa.mutate();
        }}
      >
        <Input
          className="flex-1"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="例如：差旅报销多久内提交？"
        />
        <Button type="submit" variant="secondary" disabled={qa.isPending || !question.trim()}>
          {qa.isPending ? "检索中…" : "提问"}
        </Button>
      </form>
      {qa.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {qa.error instanceof ApiError || qa.error instanceof Error ? qa.error.message : "问答失败"}
        </p>
      ) : null}
      {result ? (
        <div className="mt-3 space-y-2">
          <p className="text-sm leading-6 text-slate-800">{result.answer}</p>
          {result.sources.map((source) => (
            <p key={source.index} className="text-xs text-slate-500">
              [{source.index}] {source.title}：{source.excerpt}
            </p>
          ))}
        </div>
      ) : null}
    </Card>
  );
}
