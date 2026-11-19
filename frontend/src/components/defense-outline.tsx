"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { apiFetch, apiFetchBlob } from "@/lib/api";

type DefenseSlide = { title: string; minutes: number; points: string[]; source: string };
type DefenseOutline = {
  project: { name: string; buyer: string | null };
  minutes: number;
  allocated_minutes: number;
  slides: DefenseSlide[];
  sources: Record<string, unknown>;
  notes: string[];
};

export function DefenseOutlinePanel({ projectId }: { projectId: string }) {
  const [minutes, setMinutes] = useState("20");
  const [outline, setOutline] = useState<DefenseOutline | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () =>
      apiFetch<DefenseOutline>(`/projects/${projectId}/defense-outline?minutes=${minutes}`),
    onSuccess: (data) => {
      setOutline(data);
      setNotice(`已生成 ${data.slides.length} 页提纲，共分配 ${data.allocated_minutes} 分钟。`);
    },
    onError: (error) =>
      setNotice(error instanceof Error ? error.message : "生成答辩提纲失败"),
  });

  const exportDocx = useMutation({
    mutationFn: () =>
      apiFetchBlob(`/projects/${projectId}/defense-outline/export?minutes=${minutes}`),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "答辩提纲.docx";
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) =>
      setNotice(error instanceof Error ? error.message : "导出失败"),
  });

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">答辩 / PPT 提纲</h2>
          <p className="mt-1 text-xs text-slate-500">
            按评分项、章节、承诺和报价生成结构化提纲；内容需人工核对后使用。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm"
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
          >
            <option value="10">10 分钟</option>
            <option value="15">15 分钟</option>
            <option value="20">20 分钟</option>
            <option value="30">30 分钟</option>
          </select>
          <Button variant="secondary" onClick={() => generate.mutate()} disabled={generate.isPending}>
            {generate.isPending ? "生成中…" : "生成提纲"}
          </Button>
          {outline ? (
            <Button variant="secondary" onClick={() => exportDocx.mutate()} disabled={exportDocx.isPending}>
              导出 docx
            </Button>
          ) : null}
        </div>
      </div>
      {notice ? <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">{notice}</p> : null}
      {outline ? (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-2">
            <Badge tone="slate">{outline.project.name}</Badge>
            <Badge tone="slate">评分项 {String(outline.sources.scoring_items ?? 0)}</Badge>
            <Badge tone="slate">章节 {String(outline.sources.sections ?? 0)}</Badge>
            <Badge tone="slate">承诺 {String(outline.sources.commitments ?? 0)}</Badge>
          </div>
          {outline.slides.map((slide, index) => (
            <details key={`${slide.title}-${index}`} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <summary className="flex cursor-pointer items-center justify-between gap-2">
                <span className="font-medium">
                  {index + 1}. {slide.title}
                </span>
                <span className="text-xs text-slate-500">{slide.minutes} 分钟</span>
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-600">
                {slide.points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      ) : null}
    </Card>
  );
}
