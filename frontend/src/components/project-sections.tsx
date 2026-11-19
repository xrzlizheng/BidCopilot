"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Card, EmptyState, ErrorState, Input, Loading } from "@/components/ui";
import { SectionBlockEditor } from "@/components/section-block-editor";
import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type {
  BidSection,
  EvidenceLink,
  GenerationRun,
  KnowledgeImageAsset,
  KnowledgeSearchResult,
} from "@/lib/types";

const SECTION_TONES: Record<string, "green" | "amber" | "slate"> = {
  approved: "green",
  draft: "amber",
};
const FACT_TONES: Record<string, "green" | "amber" | "rose" | "slate"> = {
  passed: "green",
  needs_review: "rose",
  pending: "slate",
};

export function ProjectSections({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "project:write");
  const canApprove = hasPermission(user?.permissions, "project:approve");
  const canExport = hasPermission(user?.permissions, "project:export");
  const canUseKnowledge = hasPermission(user?.permissions, "knowledge:use");
  const [selectedId, setSelectedId] = useState<string>("");
  const [title, setTitle] = useState("");
  const [blocks, setBlocks] = useState<Array<Record<string, any>>>([]);
  const [acknowledge, setAcknowledge] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceLink[]>([]);
  const [showKnowledge, setShowKnowledge] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [blockedExport, setBlockedExport] = useState<{ dark: boolean; reason: string } | null>(null);

  const sections = useQuery({
    queryKey: ["project-sections", projectId],
    queryFn: () => apiFetch<BidSection[]>(`/projects/${projectId}/sections`),
  });
  const knowledgeImages = useQuery({
    queryKey: ["knowledge-images"],
    queryFn: () => apiFetch<KnowledgeImageAsset[]>("/knowledge/images"),
    staleTime: 60_000,
  });
  const sectionKnowledge = useQuery({
    queryKey: ["section-knowledge", selectedId],
    queryFn: () =>
      apiFetch<KnowledgeSearchResult>(
        `/projects/${projectId}/sections/${selectedId}/knowledge-context`,
      ),
    enabled: Boolean(selectedId) && showKnowledge && canUseKnowledge,
  });

  useEffect(() => {
    if (!selectedId && sections.data && sections.data.length > 0) {
      setSelectedId(sections.data[0].id);
    }
  }, [selectedId, sections.data]);

  const selected = sections.data?.find((section) => section.id === selectedId) ?? null;

  useEffect(() => {
    if (selected) {
      setTitle(selected.title);
      setBlocks(
        selected.content_json.length > 0
          ? selected.content_json
          : [{ type: "paragraph", text: selected.content_text }],
      );
      setAcknowledge(false);
      setEvidence([]);
    }
  }, [selected]);

  const outline = useMutation({
    mutationFn: () => apiFetch<BidSection[]>(`/projects/${projectId}/outline`, { method: "POST" }),
    onSuccess: () => {
      setMessage("章节大纲已生成。");
      queryClient.invalidateQueries({ queryKey: ["project-sections", projectId] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "大纲生成失败"),
  });

  const generate = useMutation({
    mutationFn: (mode: "rules" | "llm") =>
      apiFetch<GenerationRun>(`/projects/${projectId}/generate`, {
        method: "POST",
        body: JSON.stringify({ mode }),
      }),
    onSuccess: (run) => {
      setMessage(
        run.status === "succeeded"
          ? `${run.sections_generated} 个章节已生成。`
          : `生成失败：${run.error_code ?? run.status} ${run.error_message ?? ""}`,
      );
      queryClient.invalidateQueries({ queryKey: ["project-sections", projectId] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "生成失败"),
  });

  const save = useMutation({
    mutationFn: () =>
      apiFetch<BidSection>(`/sections/${selectedId}`, {
        method: "PATCH",
        body: JSON.stringify({ title, content_json: blocks }),
      }),
    onSuccess: () => {
      setMessage("章节已保存，核查状态已重置。");
      queryClient.invalidateQueries({ queryKey: ["project-sections", projectId] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "保存失败"),
  });

  const factcheck = useMutation({
    mutationFn: () =>
      apiFetch<{ section: BidSection; evidence: EvidenceLink[] }>(
        `/sections/${selectedId}/factcheck`,
        { method: "POST" },
      ),
    onSuccess: (result) => {
      setEvidence(result.evidence);
      setMessage(
        result.section.unsourced_count > 0
          ? `发现 ${result.section.unsourced_count} 条无源主张，请人工处理。`
          : "事实核查通过，所有主张均可溯源。",
      );
      queryClient.invalidateQueries({ queryKey: ["project-sections", projectId] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "核查失败"),
  });

  const approve = useMutation({
    mutationFn: () =>
      apiFetch<BidSection>(`/sections/${selectedId}/approve`, {
        method: "POST",
        body: JSON.stringify({ acknowledge_unsourced: acknowledge }),
      }),
    onSuccess: () => {
      setMessage("章节已审批。");
      queryClient.invalidateQueries({ queryKey: ["project-sections", projectId] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "审批失败"),
  });

  const exportDocx = useMutation({
    mutationFn: ({ dark, force }: { dark: boolean; force: boolean }) =>
      apiDownload(`/projects/${projectId}/export/docx`, {
        dark_bid: dark,
        acknowledge_unresolved: force,
      }),
    onSuccess: ({ blob, filename }) => {
      setBlockedExport(null);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      setMessage(`已导出 ${filename}。`);
    },
    onError: (error, { dark, force }) => {
      if (error instanceof ApiError && error.status === 409 && !force) {
        setBlockedExport({ dark, reason: error.message });
        setMessage(null);
        return;
      }
      setBlockedExport(null);
      setMessage(error instanceof Error ? error.message : "导出失败");
    },
  });

  // 后端 /anonymize 只注册了 POST（见 generation/router.py），
  // 用 GET 必然 405；这里复用「POST + Bearer」的下载通道。
  const anonymize = useMutation({
    mutationFn: () => apiDownload(`/projects/${projectId}/anonymize`, {}),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename || `bid_${projectId}_dark.docx`;
      link.click();
      URL.revokeObjectURL(url);
      setMessage("已生成匿名化文件，请人工确认后再交付。");
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "匿名化失败"),
  });

  return (
    <Card className="mt-4 p-5" id="sections-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">章节生成与编辑</h2>
          <p className="mt-1 text-xs text-slate-500">
            规则草稿只使用真实资产与招标条款；LLM 未配置时会明确失败，不静默降级。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canWrite ? (
            <>
              <Button variant="secondary" onClick={() => outline.mutate()} disabled={outline.isPending}>
                {outline.isPending ? "生成中…" : "生成大纲"}
              </Button>
              <Button variant="secondary" onClick={() => generate.mutate("rules")} disabled={generate.isPending}>
                {generate.isPending ? "草稿生成中…" : "规则草稿"}
              </Button>
              <Button variant="secondary" onClick={() => generate.mutate("llm")} disabled={generate.isPending}>
                {generate.isPending ? "生成中…" : "LLM 生成"}
              </Button>
            </>
          ) : null}
          {canExport ? (
            <>
              <Button
                variant="secondary"
                onClick={() => anonymize.mutate()}
                disabled={anonymize.isPending || exportDocx.isPending}
                title="清空 docx 元数据并把正文/表格/页眉页脚中的企业与人员名替换为中性称呼；绕过响应表闸门，仅做清洗"
              >
                {anonymize.isPending ? "处理中…" : "一键匿名化"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => exportDocx.mutate({ dark: false, force: false })}
                disabled={exportDocx.isPending}
              >
                {exportDocx.isPending ? "导出中…" : "导出 docx"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => exportDocx.mutate({ dark: true, force: false })}
                disabled={exportDocx.isPending}
              >
                {exportDocx.isPending ? "导出中…" : "导出暗标"}
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {message ? <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">{message}</div> : null}

      {blockedExport ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3">
          <p className="text-sm font-medium text-amber-900">导出已被合规闸门拦下</p>
          <p className="mt-1 text-sm text-amber-800">{blockedExport.reason}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              onClick={() => exportDocx.mutate({ dark: blockedExport.dark, force: true })}
              disabled={exportDocx.isPending}
            >
              已确认风险，强制导出
            </Button>
            <Button variant="secondary" onClick={() => setBlockedExport(null)} disabled={exportDocx.isPending}>
              返回处理
            </Button>
          </div>
        </div>
      ) : null}

      {sections.isLoading ? (
        <Loading />
      ) : sections.isError ? (
        <div className="mt-4">
          <ErrorState error={sections.error} onRetry={() => sections.refetch()} />
        </div>
      ) : !sections.data || sections.data.length === 0 ? (
        <div className="mt-4">
          <EmptyState text="尚未生成章节大纲。点击“生成大纲”开始。" />
        </div>
      ) : (
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <div className="space-y-2">
            {sections.data.map((section) => (
              <button
                key={section.id}
                className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${
                  section.id === selectedId ? "border-slate-900 bg-slate-50" : "border-slate-200 bg-white"
                }`}
                onClick={() => setSelectedId(section.id)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{section.title}</span>
                  <Badge tone={SECTION_TONES[section.status] ?? "slate"}>{section.status}</Badge>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-slate-500">
                  <Badge tone={FACT_TONES[section.factcheck_status] ?? "slate"}>
                    {section.factcheck_status}
                  </Badge>
                  <span>{section.generation_mode ?? "—"}</span>
                  {section.traceability_rate !== null ? (
                    <span>可溯源 {Math.round(Number(section.traceability_rate) * 100)}%</span>
                  ) : null}
                  {section.unsourced_count > 0 ? <span>无源 {section.unsourced_count}</span> : null}
                </div>
              </button>
            ))}
          </div>

          <div className="lg:col-span-2">
            {selected ? (
              <div className="space-y-3">
                <Input value={title} onChange={(event) => setTitle(event.target.value)} />
                <SectionBlockEditor
                  blocks={blocks}
                  onChange={setBlocks}
                  images={knowledgeImages.data ?? []}
                />
                <div className="flex flex-wrap items-center gap-2">
                  {canWrite ? (
                    <>
                      <Button onClick={() => save.mutate()} disabled={save.isPending}>
                        {save.isPending ? "保存中…" : "保存"}
                      </Button>
                      <Button variant="secondary" onClick={() => factcheck.mutate()} disabled={factcheck.isPending}>
                        {factcheck.isPending ? "核查中…" : "事实核查"}
                      </Button>
                    </>
                  ) : null}
                  {canUseKnowledge ? (
                    <Button variant="secondary" onClick={() => setShowKnowledge((value) => !value)}>
                      知识召回预览
                    </Button>
                  ) : null}
                  {canApprove ? (
                    <>
                      <label className="flex items-center gap-2 text-sm text-slate-600">
                        <input
                          type="checkbox"
                          checked={acknowledge}
                          onChange={(event) => setAcknowledge(event.target.checked)}
                        />
                      已人工确认无源主张
                      </label>
                      <Button
                        variant="secondary"
                        onClick={() => approve.mutate()}
                        disabled={approve.isPending || selected.factcheck_status === "pending"}
                      >
                        {approve.isPending ? "审批中…" : "审批章节"}
                      </Button>
                    </>
                  ) : null}
                </div>
                {showKnowledge ? (
                  <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="text-sm font-semibold">本节知识召回</h3>
                      {sectionKnowledge.data ? (
                        <span className="text-xs text-slate-400">
                          {sectionKnowledge.data.chunks.length} 片段 ·{" "}
                          {sectionKnowledge.data.images.length} 图片
                        </span>
                      ) : null}
                    </div>
                    {sectionKnowledge.isLoading ? <Loading /> : null}
                    {sectionKnowledge.error ? (
                      <p className="mt-2 text-xs text-rose-600">{sectionKnowledge.error.message}</p>
                    ) : null}
                    {sectionKnowledge.data ? (
                      <p className="mt-1 break-all text-[11px] text-slate-400">
                        召回查询：{sectionKnowledge.data.query}
                      </p>
                    ) : null}
                    {sectionKnowledge.data ? (
                      <div className="mt-2 space-y-2">
                        {sectionKnowledge.data.chunks.length === 0 &&
                        sectionKnowledge.data.images.length === 0 ? (
                          <p className="text-xs text-slate-400">
                            知识库暂无匹配。可在“企业知识库”上传产品资料或图片。
                          </p>
                        ) : null}
                        {sectionKnowledge.data.chunks.map((hit, index) => (
                          <div
                            key={`${hit.document_id}-${index}`}
                            className="rounded-lg border border-slate-200 bg-white px-3 py-2"
                          >
                            <div className="flex items-center justify-between gap-2 text-xs">
                              <span className="font-medium text-slate-700">{hit.title}</span>
                              <Badge tone="blue">{hit.score}</Badge>
                            </div>
                            <p className="mt-1 line-clamp-2 text-xs text-slate-500">{hit.chunk}</p>
                          </div>
                        ))}
                        {sectionKnowledge.data.images.length > 0 ? (
                          <div className="flex flex-wrap gap-2">
                            {sectionKnowledge.data.images.map((image) => (
                              <span
                                key={image.id}
                                className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600"
                              >
                                {image.title}
                                {image.sensitive ? <Badge tone="amber">敏感</Badge> : null}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                    <p className="mt-2 text-[11px] text-slate-400">
                      生成时只会使用上述召回内容；敏感图片不会进入暗标生成。
                    </p>
                  </div>
                ) : null}
                {evidence.length > 0 ? (
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold">核查明细</h3>
                    {evidence.map((link, index) => (
                      <div key={`${link.claim}-${index}`} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium">{link.claim}</span>
                          <Badge
                            tone={
                              link.status === "sourced"
                                ? "green"
                                : link.status === "policy_unverified"
                                  ? "amber"
                                  : "rose"
                            }
                          >
                            {link.status === "policy_unverified" ? "语料未收录" : link.status}
                          </Badge>
                        </div>
                        {link.source_quote ? (
                          <p className="mt-1 text-xs text-slate-500">
                            来源 {link.source_type} / {link.source_ref}：{link.source_quote}
                          </p>
                        ) : (
                          <p className="mt-1 text-xs text-rose-600">未找到引用源，需补充证据或改写。</p>
                        )}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : (
              <EmptyState text="选择一个章节开始编辑。" />
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
