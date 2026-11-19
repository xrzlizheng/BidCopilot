"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  FileStack,
  FolderKanban,
  Layers3,
  ListChecks,
  Plus,
  ScanText,
  Sparkles,
  Wand2,
  X,
} from "lucide-react";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  Loading,
  PageHeader,
  Select,
} from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { KnowledgePack, Project, SkillItem } from "@/lib/types";

type PackSection = {
  key?: string;
  title?: string;
  purpose?: string;
  required_materials?: string[];
  guidance?: string;
};

type FormatProfile = {
  page?: { size?: string; margins_cm?: Record<string, number> };
  body?: { font?: string; size_pt?: number; line_spacing?: number };
};

type TemplatesTab = "apply" | "library" | "skills";

export default function TemplatesPage() {  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canPublish = hasPermission(user?.permissions, "template:publish");
  const [projectId, setProjectId] = useState("");
  const [templateCode, setTemplateCode] = useState("");
  const [formatCode, setFormatCode] = useState("");
  const [activeTab, setActiveTab] = useState<TemplatesTab>("apply");
  const [message, setMessage] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customDescription, setCustomDescription] = useState("");
  const [customScope, setCustomScope] = useState<"personal" | "tenant">("personal");
  const [customSections, setCustomSections] = useState([
    { title: "", purpose: "", guidance: "", materials: "" },
  ]);

  const packs = useQuery({
    queryKey: ["knowledge-packs"],
    queryFn: () => apiFetch<KnowledgePack[]>("/knowledge-packs"),
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/projects"),
  });
  const skills = useQuery({
    queryKey: ["skills"],
    queryFn: () => apiFetch<SkillItem[]>("/skills"),
  });

  const structures = useMemo(
    () => (packs.data ?? []).filter((pack) => pack.category === "structure" && pack.enabled),
    [packs.data],
  );
  const formats = useMemo(
    () => (packs.data ?? []).filter((pack) => pack.category === "format" && pack.enabled),
    [packs.data],
  );
  const selectedProject = projects.data?.find((project) => project.id === projectId);
  const selectedTemplate = structures.find((pack) => pack.code === templateCode);
  const selectedFormat = formats.find((pack) => pack.code === formatCode);
  const sections = ((selectedTemplate?.content_json.sections as PackSection[] | undefined) ?? []);

  useEffect(() => {
    if (!projectId && projects.data?.length) {
      setProjectId(projects.data[0].id);
    }
  }, [projectId, projects.data]);

  useEffect(() => {
    if (!templateCode && structures.length) {
      setTemplateCode(structures[0].code);
    }
    if (!formatCode && formats.length) {
      setFormatCode(formats[0].code);
    }
  }, [templateCode, formatCode, structures, formats]);

  useEffect(() => {
    if (selectedProject?.template_code) setTemplateCode(selectedProject.template_code);
    if (selectedProject?.format_code) setFormatCode(selectedProject.format_code);
  }, [selectedProject?.id, selectedProject?.template_code, selectedProject?.format_code]);

  const apply = useMutation({    mutationFn: () =>
      apiFetch<{ sections: unknown[] }>(`/projects/${projectId}/template`, {
        method: "PUT",
        body: JSON.stringify({ template_code: templateCode, format_code: formatCode || null }),
      }),
    onSuccess: (result) => {
      setMessage(`模板已应用，生成 ${result.sections.length} 个章节。`);
      setConfirming(false);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project-workspace", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project-sections", projectId] });
    },
    onError: (error) => {
      setConfirming(false);
      setMessage(error instanceof ApiError ? `${error.message}（${error.code}）` : "模板应用失败");
    },
  });

  const createPack = useMutation({
    mutationFn: () =>
      apiFetch<KnowledgePack>("/knowledge-packs", {
        method: "POST",
        body: JSON.stringify({
          name: customName.trim(),
          category: "structure",
          scope: customScope,
          description: customDescription.trim() || null,
          content_json: {
            sections: customSections
              .filter((section) => section.title.trim())
              .map((section) => ({
                title: section.title.trim(),
                purpose: section.purpose.trim(),
                guidance: section.guidance.trim(),
                required_materials: section.materials
                  .split(/[,，、]/)
                  .map((item) => item.trim())
                  .filter(Boolean),
              })),
          },
        }),
      }),
    onSuccess: (pack) => {
      setMessage(`自定义模板「${pack.name}」已创建。`);
      setTemplateCode(pack.code);
      setCustomOpen(false);
      setCustomName("");
      setCustomDescription("");
      setCustomScope("personal");
      setCustomSections([{ title: "", purpose: "", guidance: "", materials: "" }]);
      queryClient.invalidateQueries({ queryKey: ["knowledge-packs"] });
    },
    onError: (error) =>
      setMessage(error instanceof ApiError ? `${error.message}（${error.code}）` : "创建模板失败"),
  });

  function updateCustomSection(index: number, field: "title" | "purpose" | "guidance" | "materials", value: string) {
    setCustomSections((current) =>
      current.map((section, sectionIndex) =>
        sectionIndex === index ? { ...section, [field]: value } : section,
      ),
    );
  }

  function handleApply() {
    if (!projectId || !templateCode) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setMessage(null);
    apply.mutate();
  }

  return (
    <>
      <PageHeader
        eyebrow="COMPOSE / KNOWLEDGE"
        title="模板与技能"
        subtitle="结构模板决定投标文件的章节骨架；格式模板决定 docx 的页面与字体。内置模板独立编写，可被项目复用。"
      />
      {message ? (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white/80 px-4 py-3 text-sm text-slate-700">
          {message}
        </div>
      ) : null}

      <Card className="z-20 mb-4 p-4 shadow-card sm:p-5 lg:sticky lg:top-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Layers3 size={16} className="text-aurora-600" />
            <h2 className="text-sm font-semibold">模板与技能</h2>
            <Badge tone="cyan">{structures.length} 结构 · {formats.length} 格式</Badge>
            {selectedProject ? <Badge tone="slate">{selectedProject.name}</Badge> : null}
          </div>
          <p className="text-xs text-slate-500">应用模板会重建未审批章节；已有审批章节时会被拒绝。</p>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {[
            { key: "apply" as const, label: "应用到项目", detail: "选择项目并生成大纲", icon: <Wand2 size={14} /> },
            { key: "library" as const, label: "模板库", detail: "结构、格式与自定义模板", icon: <Layers3 size={14} /> },
            { key: "skills" as const, label: "技能目录", detail: "当前可用能力", icon: <Sparkles size={14} /> },
          ].map((tab) => (
            <button
              key={tab.key}
              type="button"
              aria-pressed={activeTab === tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`rounded-xl border px-3 py-2 text-left transition ${
                activeTab === tab.key
                  ? "border-aurora-500/60 bg-aurora-500/[0.06] text-slate-900 shadow-glow"
                  : "border-slate-200/80 bg-white/60 text-slate-600 hover:border-slate-300"
              }`}
            >
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
                <span className={activeTab === tab.key ? "text-aurora-600" : "text-slate-400"}>{tab.icon}</span>
                {tab.label}
              </span>
              <span className="mt-1 block truncate text-[11px] text-slate-500">{tab.detail}</span>
            </button>
          ))}
        </div>
      </Card>

      {activeTab === "apply" ? (
      <Card className="p-5">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <p className="mono-label mb-2 text-slate-400">TARGET PROJECT</p>
            <Select
              className="w-full"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            >
              <option value="">选择投标项目</option>
              {(projects.data ?? []).map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </Select>
          </div>
          <div className="text-xs text-slate-500">
            {selectedProject
              ? `项目已保存：${selectedProject.template_code ?? "未选模板"} / ${selectedProject.format_code ?? "未选格式"}`
              : "先创建项目并解析招标文件"}
          </div>
          <Button onClick={handleApply} disabled={!projectId || !templateCode || apply.isPending}>
            <Wand2 size={15} />
            {apply.isPending ? "应用中…" : confirming ? "确认重建大纲" : "应用模板并生成大纲"}
          </Button>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
            结构模板
            <div className="mt-0.5 text-sm font-medium text-slate-800">
              {selectedTemplate?.name ?? "未选择"}
            </div>
          </div>
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
            格式模板
            <div className="mt-0.5 text-sm font-medium text-slate-800">
              {selectedFormat?.name ?? "未选择"}
            </div>
          </div>
        </div>
        {confirming ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs text-amber-800">
            <span>应用模板会重建该项目未审批章节；已有审批章节时会被拒绝。</span>
            <button
              type="button"
              className="font-medium underline"
              onClick={() => setConfirming(false)}
            >
              取消
            </button>
          </div>
        ) : null}
      </Card>
      ) : null}

      {activeTab === "library" ? (
      <>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <Card className="p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Layers3 size={16} className="text-aurora-600" />
              <h2 className="text-sm font-semibold">结构模板</h2>
            </div>
            <Button variant="ghost" onClick={() => setCustomOpen((value) => !value)}>
              <Plus size={14} /> 新建模板
            </Button>
          </div>
          {customOpen ? (
            <div className="mt-3 rounded-xl border border-aurora-500/25 bg-aurora-500/[0.04] p-3">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-slate-600">自定义结构模板</p>
                <button type="button" aria-label="关闭" onClick={() => setCustomOpen(false)}>
                  <X size={15} className="text-slate-400" />
                </button>
              </div>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <Input
                  placeholder="模板名称 *"
                  value={customName}
                  onChange={(event) => setCustomName(event.target.value)}
                />
                <Input
                  placeholder="适用说明"
                  value={customDescription}
                  onChange={(event) => setCustomDescription(event.target.value)}
                />
                <label className="text-xs text-slate-500">
                  可见范围
                  <Select
                    className="mt-1"
                    value={customScope}
                    onChange={(event) => {
                      const nextScope = event.target.value as "personal" | "tenant";
                      setCustomScope(nextScope);
                    }}
                  >
                    <option value="personal">个人（仅自己）</option>
                    {canPublish ? <option value="tenant">企业（全员可用）</option> : null}
                  </Select>
                </label>
                <label className="text-xs text-slate-500">
                  归属客户
                </label>
              </div>
              <div className="mt-2 space-y-2">
                {customSections.map((section, index) => (
                  <div key={index} className="rounded-lg border border-slate-200/70 bg-white/70 p-2">
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Input
                        placeholder="章节标题 *"
                        value={section.title}
                        onChange={(event) => updateCustomSection(index, "title", event.target.value)}
                      />
                      <Input
                        placeholder="材料要求（逗号分隔）"
                        value={section.materials}
                        onChange={(event) => updateCustomSection(index, "materials", event.target.value)}
                      />
                      <Input
                        placeholder="章节目标"
                        value={section.purpose}
                        onChange={(event) => updateCustomSection(index, "purpose", event.target.value)}
                      />
                      <Input
                        placeholder="写作要求"
                        value={section.guidance}
                        onChange={(event) => updateCustomSection(index, "guidance", event.target.value)}
                      />
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  onClick={() =>
                    setCustomSections((current) => [
                      ...current,
                      { title: "", purpose: "", guidance: "", materials: "" },
                    ])
                  }
                >
                  <Plus size={14} /> 增加章节
                </Button>
                <Button
                  onClick={() => createPack.mutate()}
                  disabled={!customName.trim() || createPack.isPending}
                >
                  {createPack.isPending ? "创建中…" : "保存模板"}
                </Button>
              </div>
            </div>
          ) : null}
          {packs.isLoading ? (
            <Loading />
          ) : packs.isError ? (
            <ErrorState
              error={packs.error}
              onRetry={() => packs.refetch()}
              hint="模板列表没取到。若是权限不足，请联系企业管理员确认你是否有 template:read。"
            />
          ) : structures.length === 0 ? (
            <EmptyState
              text="还没有结构模板。"
              hint="结构模板决定标书的章节骨架。可以从历史标书自动生成，也可以在上方手工新建。"
              action={
                <Link
                  href="/knowledge"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white/80 px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm"
                >
                  去企业知识库生成草稿
                </Link>
              }
            />
          ) : (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {structures.map((pack) => {
                const active = pack.code === templateCode;
                const packSections = (pack.content_json.sections as PackSection[] | undefined) ?? [];
                return (
                  <button
                    key={pack.code}
                    type="button"
                    onClick={() => setTemplateCode(pack.code)}
                    className={`rounded-2xl border p-4 text-left transition ${
                      active
                        ? "border-aurora-500/60 bg-aurora-500/[0.06] shadow-glow"
                        : "border-slate-200/80 bg-white/60 hover:border-slate-300"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-slate-800">{pack.name}</span>
                      <span className="flex items-center gap-1">
                        <Badge
                          tone={
                            pack.scope === "personal"
                              ? "amber"
                              : pack.scope === "system"
                                ? "slate"
                                : "blue"
                          }
                        >
                          {pack.scope === "personal"
                            ? "个人"
                            : pack.scope === "system"
                              ? "系统"
                              : "企业"}
                        </Badge>
                        {pack.status === "draft" ? <Badge tone="amber">草稿</Badge> : null}
                        {active ? <CheckCircle2 size={16} className="text-aurora-600" /> : null}
                      </span>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{pack.description}</p>
                    <div className="mt-3 flex flex-wrap gap-1">
                      {packSections.slice(0, 5).map((section) => (
                        <span
                          key={section.key ?? section.title}
                          className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500"
                        >
                          {section.title}
                        </span>
                      ))}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </Card>

        <Card className="p-5">
          <div className="flex items-center gap-2">
            <ScanText size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">格式模板</h2>
          </div>
          {formats.length === 0 ? (
            <EmptyState
                text="还没有格式模板。"
                hint="格式模板决定导出 docx 的版式（字体、页边距、页眉页脚）。不配置时会用系统默认版式导出，不影响内容正确性。"
              />
          ) : (
            <div className="mt-4 space-y-2">
              {formats.map((pack) => {
                const profile = pack.content_json as FormatProfile;
                const active = pack.code === formatCode;
                return (
                  <button
                    key={pack.code}
                    type="button"
                    onClick={() => setFormatCode(pack.code)}
                    className={`w-full rounded-xl border p-3 text-left transition ${
                      active
                        ? "border-electric-500/50 bg-electric-500/[0.05]"
                        : "border-slate-200/80 bg-white/60 hover:border-slate-300"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{pack.name}</span>
                      <span className="flex items-center gap-1">
                        {pack.scope === "system" ? <Badge tone="slate">系统</Badge> : null}
                        {active ? <Badge tone="blue">已选</Badge> : null}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      {profile.page?.size ?? "A4"} · {profile.body?.font ?? "宋体"}{" "}
                      {profile.body?.size_pt ?? 12}pt · {profile.body?.line_spacing ?? 1.5} 倍行距
                    </p>
                  </button>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      {selectedTemplate ? (
        <Card className="mt-4 p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileStack size={16} className="text-aurora-600" />
              <h2 className="text-sm font-semibold">{selectedTemplate.name} · 章节预览</h2>
            </div>
            <Badge>{sections.length} 个模板章节 + 评分项响应</Badge>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {sections.map((section, index) => (
              <div key={section.key ?? index} className="rounded-xl border border-slate-200/70 bg-white/60 p-4">
                <div className="flex items-center justify-between">
                  <span className="mono-label text-slate-400">{String(index + 1).padStart(2, "0")}</span>
                  <Badge tone="slate">{section.required_materials?.length ?? 0} 项材料</Badge>
                </div>
                <p className="mt-2 text-sm font-semibold text-slate-800">{section.title}</p>
                <p className="mt-1 text-xs leading-5 text-slate-500">{section.purpose}</p>
                {section.required_materials?.length ? (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {section.required_materials.map((material) => (
                      <span
                        key={material}
                        className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500"
                      >
                        {material}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </Card>
      ) : null}
      </>
      ) : null}

      {activeTab === "skills" ? (
      <Card className="mt-4 p-5">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-electric-600" />
          <h2 className="text-sm font-semibold">技能目录</h2>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          能力以受控模块注册，不在运行时执行任意脚本；planned 项不伪装为可用。
        </p>
        {skills.isLoading ? (
          <Loading />
        ) : (
          <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {(skills.data ?? []).map((skill) => (
              <div
                key={skill.code}
                className="flex items-start gap-3 rounded-xl border border-slate-200/70 bg-white/60 p-3"
              >
                <span className="mt-0.5 text-aurora-600">
                  {skill.status === "ready" ? <ListChecks size={15} /> : <FolderKanban size={15} />}
                </span>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-800">{skill.name}</span>
                    <Badge tone={skill.status === "ready" ? "green" : "slate"}>{skill.status}</Badge>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-500">{skill.description}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
      ) : null}
      <p className="mt-3 text-right text-[11px] text-slate-400">
        材料状态：available 已匹配 / unknown 有待确认 / missing 缺失 / to_write 需编写
      </p>
    </>
  );
}
