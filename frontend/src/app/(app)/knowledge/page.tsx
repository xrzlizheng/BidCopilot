"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  FileText,
  ImageIcon,
  Pencil,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";

import { Badge, Button, Card, EmptyState, Input, Loading, PageHeader, Select } from "@/components/ui";
import { AuthedImage } from "@/components/authed-image";
import { ApiError, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type {
  KnowledgeAcl,
  KnowledgeAclSubjects,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentDetail,
  KnowledgeImageAsset,
  KnowledgePack,
  KnowledgeSearchResult,
} from "@/lib/types";

const KINDS = [
  { value: "product", label: "产品资料" },
  { value: "solution", label: "方案" },
  { value: "case", label: "案例" },
  { value: "certificate", label: "证书" },
  { value: "policy", label: "制度" },
  { value: "other", label: "其他" },
];

type KnowledgeTab = "documents" | "images" | "recall" | "permissions";

function sizeLabel(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function kindLabel(kind: string): string {
  return KINDS.find((item) => item.value === kind)?.label ?? kind;
}

function splitTags(value: string): string[] {
  return value
    .replace(/，/g, ",")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 20);
}

export default function KnowledgePage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const user = useAuthStore((state) => state.user);
  const canManageKnowledge = hasPermission(user?.permissions, "knowledge:manage");
  const [selectedKbId, setSelectedKbId] = useState("");
  const [newKbName, setNewKbName] = useState("");
  const [docTitle, setDocTitle] = useState("");
  const [docKind, setDocKind] = useState("product");
  const [docTags, setDocTags] = useState("");
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [aclSubjectType, setAclSubjectType] = useState<"user" | "tenant" | "org_unit">("user");
  const [aclSubjectId, setAclSubjectId] = useState("");
  const [aclRole, setAclRole] = useState("viewer");
  const [aclIncludeDescendants, setAclIncludeDescendants] = useState(false);
  const [imageTitle, setImageTitle] = useState("");
  const [imageCaption, setImageCaption] = useState("");
  const [imageTags, setImageTags] = useState("");
  const [imageSensitive, setImageSensitive] = useState(false);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editKind, setEditKind] = useState("other");
  const [editTags, setEditTags] = useState("");
  const [editingImageId, setEditingImageId] = useState<string | null>(null);
  const [imageEditTitle, setImageEditTitle] = useState("");
  const [imageEditCaption, setImageEditCaption] = useState("");
  const [imageEditTags, setImageEditTags] = useState("");
  const [pendingDelete, setPendingDelete] = useState<{
    kind: "document" | "image";
    id: string;
  } | null>(null);
  const [activeTab, setActiveTab] = useState<KnowledgeTab>("documents");
  const [message, setMessage] = useState<string | null>(null);
  const [generatedDraft, setGeneratedDraft] = useState<KnowledgePack | null>(null);

  const bases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => apiFetch<KnowledgeBase[]>("/knowledge/bases"),
  });
  const selectedBase = bases.data?.find((base) => base.id === selectedKbId);
  const canShareSelectedBase = Boolean(
    canManageKnowledge && selectedBase && selectedBase.scope !== "personal",
  );
  const kbAcl = useQuery({
    queryKey: ["knowledge-base-acl", selectedKbId],
    queryFn: () => apiFetch<KnowledgeAcl[]>(`/knowledge/bases/${selectedKbId}/acl`),
    enabled: canShareSelectedBase,
  });
  const aclSubjects = useQuery({
    queryKey: ["knowledge-acl-subjects"],
    queryFn: () => apiFetch<KnowledgeAclSubjects>("/knowledge/acl-subjects"),
    enabled: canShareSelectedBase,
  });
  const aclEntries = kbAcl.data ?? [];
  const orgOptions = aclSubjects.data?.org_units ?? [];
  const aclSubjectLabel = (entry: KnowledgeAcl): string => {
    if (entry.subject_type === "tenant") return "整个企业";
    if (entry.subject_type === "org_unit") {
      const org = orgOptions.find((item) => item.id === entry.subject_id);
      return org ? `部门 · ${org.name}` : `部门 · ${entry.subject_id.slice(0, 8)}`;
    }
    const target = aclSubjects.data?.users.find((item) => item.id === entry.subject_id);
    return target ? `${target.display_name}（${target.email}）` : `用户 · ${entry.subject_id.slice(0, 8)}`;
  };
  const kbQuery = selectedKbId ? `?kb_id=${encodeURIComponent(selectedKbId)}` : "";
  const documents = useQuery({
    queryKey: ["knowledge-documents", selectedKbId],
    queryFn: () => apiFetch<KnowledgeDocument[]>(`/knowledge/documents${kbQuery}`),
  });
  const images = useQuery({
    queryKey: ["knowledge-images", selectedKbId],
    queryFn: () => apiFetch<KnowledgeImageAsset[]>(`/knowledge/images${kbQuery}`),
  });
  const search = useQuery({
    queryKey: ["knowledge-search", submitted, selectedKbId],
    queryFn: () => {
      const params = new URLSearchParams({ q: submitted });
      if (selectedKbId) params.set("kb_id", selectedKbId);
      return apiFetch<KnowledgeSearchResult>(`/knowledge/search?${params.toString()}`);
    },
    enabled: submitted.length > 0,
  });
  const detail = useQuery({
    queryKey: ["knowledge-document", selectedDocumentId],
    queryFn: () =>
      apiFetch<KnowledgeDocumentDetail>(`/knowledge/documents/${selectedDocumentId}`),
    enabled: Boolean(selectedDocumentId),
  });

  useEffect(() => {
    if (!detail.data) return;
    setEditTitle(detail.data.title);
    setEditKind(detail.data.kind);
    setEditTags(detail.data.tags.join(", "));
  }, [detail.data]);

  useEffect(() => {
    if (activeTab === "permissions" && !canShareSelectedBase) {
      setActiveTab("documents");
    }
  }, [activeTab, canShareSelectedBase]);

  const createBase = useMutation({
    mutationFn: () =>
      apiFetch<KnowledgeBase>("/knowledge/bases", {
        method: "POST",
        body: JSON.stringify({
          name: newKbName,
          scope: "personal",
        }),
      }),
    onSuccess: (base) => {
      setSelectedKbId(base.id);
      setNewKbName("");
      setMessage(`已创建个人知识库「${base.name}」，后续上传会进入该库。`);
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-documents"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-images"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "创建知识库失败"),
  });

  const grantKbAcl = useMutation({
    mutationFn: () => {
      const subjectId = aclSubjectType === "tenant" ? user?.tenant_id : aclSubjectId;
      if (!selectedKbId || !subjectId) throw new Error("请选择授权对象");
      return apiFetch<KnowledgeAcl>(`/knowledge/bases/${selectedKbId}/acl`, {
        method: "POST",
        body: JSON.stringify({
          subject_type: aclSubjectType,
          subject_id: subjectId,
          role: aclRole,
          include_descendants:
            aclSubjectType === "org_unit" ? aclIncludeDescendants : false,
        }),
      });
    },
    onSuccess: () => {
      setAclSubjectId("");
      setMessage("已更新知识库授权。");
      queryClient.invalidateQueries({ queryKey: ["knowledge-base-acl", selectedKbId] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "授权失败"),
  });

  const revokeKbAcl = useMutation({
    mutationFn: (aclId: string) =>
      apiFetch(`/knowledge/bases/${selectedKbId}/acl/${aclId}`, { method: "DELETE" }),
    onSuccess: () => {
      setMessage("已撤销知识库授权。");
      queryClient.invalidateQueries({ queryKey: ["knowledge-base-acl", selectedKbId] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "撤销失败"),
  });

  const updateDocument = useMutation({
    mutationFn: (payload: { id: string; title: string; kind: string; tags: string[] }) =>
      apiFetch<KnowledgeDocument>(`/knowledge/documents/${payload.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: payload.title,
          kind: payload.kind,
          tags: payload.tags,
        }),
      }),
    onSuccess: (document) => {
      setMessage(`已更新元数据：${document.title}`);
      queryClient.invalidateQueries({ queryKey: ["knowledge-documents"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-document", document.id] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-search"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "更新失败"),
  });

  const uploadDocument = useMutation({
    mutationFn: async (overrides?: { kind?: string; tags?: string }) => {
      if (!docFiles.length) throw new Error("请选择文档");
      const kind = overrides?.kind ?? docKind;
      const tags = overrides?.tags ?? docTags;
      const succeeded: KnowledgeDocument[] = [];
      const failures: string[] = [];
      for (const file of docFiles) {
        const data = new FormData();
        data.append("file", file);
        data.append("kind", kind);
        if (selectedKbId) data.append("kb_id", selectedKbId);
        if (docFiles.length === 1 && docTitle.trim()) data.append("title", docTitle.trim());
        if (tags.trim()) data.append("tags", tags.trim());
        try {
          succeeded.push(
            await apiFetch<KnowledgeDocument>("/knowledge/documents", {
              method: "POST",
              body: data,
            }),
          );
        } catch (error) {
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "上传失败"}`);
        }
      }
      return { succeeded, failures };
    },
    onSuccess: ({ succeeded, failures }) => {
      if (succeeded.length) {
        const titles = succeeded.slice(0, 3).map((document) => document.title).join("、");
        const suffix = succeeded.length > 3 ? " 等" : "";
        setMessage(
          failures.length
            ? `已入库 ${succeeded.length} 篇（${titles}${suffix}），失败 ${failures.length} 篇：${failures.join("；")}`
            : `已入库 ${succeeded.length} 篇：${titles}${suffix}`,
        );
      } else {
        setMessage(`上传失败：${failures.join("；")}`);
      }
      setDocFiles([]);
      setDocTitle("");
      setDocTags("");
      queryClient.invalidateQueries({ queryKey: ["knowledge-documents"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "上传失败"),
  });

  const uploadImage = useMutation({
    mutationFn: async () => {
      if (!imageFiles.length) throw new Error("请选择图片");
      const succeeded: KnowledgeImageAsset[] = [];
      const failures: string[] = [];
      for (const file of imageFiles) {
        const data = new FormData();
        data.append("file", file);
        if (imageFiles.length === 1 && imageTitle.trim()) data.append("title", imageTitle.trim());
        if (imageFiles.length === 1 && imageCaption.trim()) {
          data.append("caption", imageCaption.trim());
        }
        if (imageTags.trim()) data.append("tags", imageTags.trim());
        data.append("sensitive", String(imageSensitive));
        if (selectedKbId) data.append("kb_id", selectedKbId);
        try {
          succeeded.push(
            await apiFetch<KnowledgeImageAsset>("/knowledge/images", {
              method: "POST",
              body: data,
            }),
          );
        } catch (error) {
          failures.push(`${file.name}: ${error instanceof Error ? error.message : "上传失败"}`);
        }
      }
      return { succeeded, failures };
    },
    onSuccess: ({ succeeded, failures }) => {
      if (succeeded.length) {
        const titles = succeeded.slice(0, 3).map((image) => image.title).join("、");
        const suffix = succeeded.length > 3 ? " 等" : "";
        setMessage(
          failures.length
            ? `已入库 ${succeeded.length} 张（${titles}${suffix}），失败 ${failures.length} 张：${failures.join("；")}`
            : `已入库 ${succeeded.length} 张：${titles}${suffix}`,
        );
      } else {
        setMessage(`上传失败：${failures.join("；")}`);
      }
      setImageFiles([]);
      setImageTitle("");
      setImageCaption("");
      setImageTags("");
      setImageSensitive(false);
      queryClient.invalidateQueries({ queryKey: ["knowledge-images"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "上传失败"),
  });

  const removeDocument = useMutation({
    mutationFn: (id: string) => apiFetch(`/knowledge/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setPendingDelete(null);
      queryClient.invalidateQueries({ queryKey: ["knowledge-documents"] });
    },
  });
  const removeImage = useMutation({
    mutationFn: (id: string) => apiFetch(`/knowledge/images/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setPendingDelete(null);
      queryClient.invalidateQueries({ queryKey: ["knowledge-images"] });
    },
  });
  const toggleSensitive = useMutation({
    mutationFn: ({ id, sensitive }: { id: string; sensitive: boolean }) =>
      apiFetch(`/knowledge/images/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ sensitive }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-images"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-search"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "更新失败"),
  });
  const updateImageMeta = useMutation({
    mutationFn: (payload: {
      id: string;
      title: string;
      caption: string;
      tags: string[];
    }) =>
      apiFetch<KnowledgeImageAsset>(`/knowledge/images/${payload.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: payload.title,
          caption: payload.caption,
          tags: payload.tags,
        }),
      }),
    onSuccess: (image) => {
      setMessage(`已更新图片元数据：${image.title}`);
      setEditingImageId(null);
      queryClient.invalidateQueries({ queryKey: ["knowledge-images"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-search"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "更新失败"),
  });

  const generateTemplateDraft = useMutation({
    mutationFn: () =>
      apiFetch<KnowledgePack>(`/knowledge/bases/${selectedKbId}/template-draft`, {
        method: "POST",
        // 显式空对象：apiFetch 默认按 JSON 发送，空 body 会被严格解析器拒绝。
        body: JSON.stringify({}),
      }),
    onSuccess: (pack) => {
      const rawSections = (pack.content_json as { sections?: unknown }).sections;
      const sectionCount = Array.isArray(rawSections) ? rawSections.length : 0;
      setMessage(
        `已生成结构模板草稿「${pack.name}」（${sectionCount} 个章节）。草稿不会自动生效，需到「模板与技能」页人工确认并发布后才能应用到项目。`,
      );
      setGeneratedDraft(pack);
      queryClient.invalidateQueries({ queryKey: ["knowledge-packs"] });
    },
    onError: (error) => {
      // 展示后端真实错误：输入不足（历史标书少于 2 份 / 可识别章节少于 3 个）
      // 必须让用户看到，不能静默失败或伪造成功。
      setGeneratedDraft(null);
      setMessage(
        error instanceof ApiError
          ? `生成结构模板草稿失败：${error.message}（${error.code}）`
          : `生成结构模板草稿失败：${error instanceof Error ? error.message : "未知错误"}`,
      );
    },
  });

  function startImageEdit(image: KnowledgeImageAsset) {
    setEditingImageId(image.id);
    setImageEditTitle(image.title);
    setImageEditCaption(image.caption ?? "");
    setImageEditTags(image.tags.join(", "));
  }

  return (
    <>
      <PageHeader
        title="企业知识库"
        subtitle="上传产品资料、方案、案例与图片；生成标书时按章节标题、评分项与项目信息加权召回，只使用知识库中的真实内容与图片。"
      />

      {message ? (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
          {message}
        </div>
      ) : null}

      <Card className="z-20 mb-4 p-4 shadow-card sm:p-5 lg:sticky lg:top-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <BookOpen size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">知识空间</h2>
            <Badge tone="slate">{bases.data?.length ?? 0}</Badge>
            {selectedBase ? <Badge tone="cyan">{selectedBase.name}</Badge> : null}
          </div>
          <p className="text-xs text-slate-500">个人库仅自己可读/用；企业库按角色与 ACL 授权。</p>
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="min-w-56 flex-1 text-sm">
            当前知识库
            <Select
              className="mt-1"
              value={selectedKbId}
              onChange={(event) => setSelectedKbId(event.target.value)}
            >
              <option value="">全部可访问知识库</option>
              {(bases.data ?? []).map((base) => (
                <option key={base.id} value={base.id}>
                  {base.scope === "personal"
                    ? "个人 · "
                    : base.scope === "org_unit"
                      ? "部门 · "
                      : "企业 · "}
                  {base.name}
                </option>
              ))}
            </Select>
          </label>
          <Button
            variant="secondary"
            onClick={() => {
              setActiveTab("documents");
              window.requestAnimationFrame(() => {
                document.getElementById("kb-content")?.scrollIntoView({
                  behavior: "smooth",
                  block: "start",
                });
              });
            }}
          >
            <Upload size={14} /> 上传资料
          </Button>
        </div>
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium text-slate-500">新建个人知识库</summary>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <Input
              className="min-w-52 flex-1"
              value={newKbName}
              onChange={(event) => setNewKbName(event.target.value)}
              placeholder="例如：我的投标素材"
            />
            <Button
              variant="secondary"
              disabled={!newKbName.trim() || createBase.isPending}
              onClick={() => createBase.mutate()}
            >
              {createBase.isPending ? "创建中…" : "新建个人库"}
            </Button>
          </div>
        </details>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            { key: "documents" as const, label: "资料", icon: <FileText size={14} />, count: documents.data?.length ?? 0 },
            { key: "images" as const, label: "图片", icon: <ImageIcon size={14} />, count: images.data?.length ?? 0 },
            { key: "recall" as const, label: "召回测试", icon: <Search size={14} />, count: null },
            ...(canShareSelectedBase
              ? [{ key: "permissions" as const, label: "授权", icon: <ShieldCheck size={14} />, count: aclEntries.length }]
              : []),
          ].map((tab) => (
            <button
              key={tab.key}
              type="button"
              aria-pressed={activeTab === tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center justify-between gap-2 rounded-xl border px-3 py-2 text-left transition ${
                activeTab === tab.key
                  ? "border-aurora-500/60 bg-aurora-500/[0.06] text-slate-900 shadow-glow"
                  : "border-slate-200/80 bg-white/60 text-slate-600 hover:border-slate-300"
              }`}
            >
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
                <span className={activeTab === tab.key ? "text-aurora-600" : "text-slate-400"}>
                  {tab.icon}
                </span>
                {tab.label}
              </span>
              {tab.count !== null ? <Badge tone="slate">{tab.count}</Badge> : null}
            </button>
          ))}
        </div>
      </Card>

      <div id="kb-content" className="scroll-mt-24 lg:scroll-mt-[300px]">
      {activeTab === "permissions" && canShareSelectedBase && selectedBase ? (
        <Card className="mb-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ShieldCheck size={16} className="text-electric-600" />
              <h2 className="text-sm font-semibold">共享与访问控制</h2>
              <Badge tone="slate">{selectedBase.name}</Badge>
            </div>
            <p className="text-xs text-slate-500">仅企业/部门库可授权；个人库保持私有。</p>
          </div>
          <div className="mt-3 space-y-2">
            {kbAcl.isLoading ? <Loading /> : null}
            {kbAcl.error ? (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-600">
                {kbAcl.error.message}
              </div>
            ) : null}
            {aclSubjects.error ? (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-600">
                授权对象加载失败：{aclSubjects.error.message}
              </div>
            ) : null}
            {!kbAcl.isLoading && !kbAcl.error && aclEntries.length === 0 ? (
              <EmptyState
                text="该知识库没有单独授权。"
                hint="这不影响使用：未单独授权时按角色与密级决定可见范围。需要精确到人或部门时，在上方添加授权。"
              />
            ) : null}
            {aclEntries.map((entry) => (
              <div
                key={entry.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white/70 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge tone={entry.subject_type === "tenant" ? "blue" : "green"}>
                    {entry.subject_type === "tenant"
                      ? "企业"
                      : entry.subject_type === "org_unit"
                        ? "部门"
                        : "用户"}
                  </Badge>
                  <span className="text-slate-700">
                    {aclSubjectLabel(entry)}
                    {entry.subject_type === "org_unit" && entry.include_descendants
                      ? "（含子部门）"
                      : ""}
                  </span>
                  <Badge tone="slate">
                    {entry.role === "viewer"
                      ? "只读"
                      : entry.role === "user"
                        ? "使用"
                        : entry.role === "editor"
                          ? "编辑"
                          : "管理"}
                  </Badge>
                  {entry.expires_at ? (
                    <span className="text-xs text-slate-400">
                      到期 {new Date(entry.expires_at).toLocaleString()}
                    </span>
                  ) : null}
                </div>
                <button
                  className="rounded-lg p-1.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                  aria-label="撤销授权"
                  disabled={revokeKbAcl.isPending}
                  onClick={async () => {
                    const ok = await confirm({
                      title: "撤销这条知识库授权？",
                      description:
                        "撤销后该成员/部门立即失去对该知识库的访问权限，已生成的章节与引用不受影响。需要时可重新授权。",
                      confirmText: "撤销授权",
                      tone: "danger",
                    });
                    if (!ok) return;
                    revokeKbAcl.mutate(entry.id);
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
          <form
            className="mt-4 flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              grantKbAcl.mutate();
            }}
          >
            <label className="text-sm">
              对象类型
              <Select
                className="mt-1"
                value={aclSubjectType}
                onChange={(event) => {
                  setAclSubjectType(event.target.value as "user" | "tenant" | "org_unit");
                  setAclSubjectId("");
                  setAclIncludeDescendants(false);
                }}
              >
                <option value="user">指定用户</option>
                <option value="org_unit">指定部门</option>
                <option value="tenant">整个企业</option>
              </Select>
            </label>
            {aclSubjectType === "tenant" ? (
              <div className="min-w-56 flex-1 text-sm">
                授权范围
                <div className="mt-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-slate-600">
                  企业内全部成员（受密级与角色能力约束）
                </div>
              </div>
            ) : aclSubjectType === "org_unit" ? (
              <label className="min-w-56 flex-1 text-sm">
                部门
                <Select
                  className="mt-1"
                  value={aclSubjectId}
                  onChange={(event) => setAclSubjectId(event.target.value)}
                >
                  <option value="">请选择部门</option>
                  {orgOptions.map((org) => (
                    <option key={org.id} value={org.id}>
                      {"　".repeat(org.depth)}
                      {org.name}
                    </option>
                  ))}
                </Select>
              </label>
            ) : (
              <label className="min-w-56 flex-1 text-sm">
                用户
                <Select
                  className="mt-1"
                  value={aclSubjectId}
                  onChange={(event) => setAclSubjectId(event.target.value)}
                >
                  <option value="">请选择用户</option>
                  {(aclSubjects.data?.users ?? [])
                    .filter((item) => item.id !== user?.id)
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.display_name}（{item.email}）
                      </option>
                    ))}
                </Select>
              </label>
            )}
            <label className="text-sm">
              权限
              <Select
                className="mt-1"
                value={aclRole}
                onChange={(event) => setAclRole(event.target.value)}
              >
                <option value="viewer">只读</option>
                <option value="user">使用</option>
                <option value="editor">编辑</option>
                <option value="manager">管理</option>
              </Select>
            </label>
            {aclSubjectType === "org_unit" ? (
              <label className="flex items-center gap-2 pb-2 text-sm text-slate-600">
                <input
                  type="checkbox"
                  checked={aclIncludeDescendants}
                  onChange={(event) => setAclIncludeDescendants(event.target.checked)}
                />
                含子部门
              </label>
            ) : null}
            <Button
              type="submit"
              variant="secondary"
              disabled={
                grantKbAcl.isPending ||
                (aclSubjectType !== "tenant" && !aclSubjectId)
              }
            >
              {grantKbAcl.isPending ? "授权中…" : "添加授权"}
            </Button>
          </form>
        </Card>
      ) : null}

      {activeTab === "documents" ? (
      <div className="grid gap-4">
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <Upload size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">历史标书冷启动</h2>
            <Badge tone="blue">批量</Badge>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            一次选择多份历史标书，按「案例 + 历史标书」入库并自动切片；入库后可基于这些标书确定性生成结构模板草稿（不调用 LLM，章节与材料均取自原文）。草稿需人工确认后启用。
          </p>
          <label className="mt-3 block text-sm">
            历史标书文件（可多选）
            <input
              className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
              type="file"
              multiple
              accept=".docx,.pdf,.txt,.md,.xlsx,.xlsm,.pptx"
              onChange={(event) => setDocFiles(Array.from(event.target.files ?? []))}
            />
          </label>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              disabled={uploadDocument.isPending || docFiles.length === 0}
              onClick={() => uploadDocument.mutate({ kind: "case", tags: "历史标书" })}
            >
              <Upload size={14} />{" "}
              {uploadDocument.isPending
                ? `冷启动入库中…（${docFiles.length}）`
                : `冷启动入库${docFiles.length ? `（${docFiles.length}）` : ""}`}
            </Button>
            <Button
              variant="secondary"
              disabled={!selectedKbId || generateTemplateDraft.isPending}
              title={
                selectedKbId ? undefined : "请先在上方选择一个知识库，再生成结构模板草稿"
              }
              onClick={() => generateTemplateDraft.mutate()}
            >
              <Sparkles size={14} />{" "}
              {generateTemplateDraft.isPending ? "生成中…" : "生成结构模板草稿"}
            </Button>
            <span className="text-xs text-slate-500">
              {selectedKbId
                ? "基于所选知识库内已入库的历史标书生成"
                : "需先选择一个知识库"}
            </span>
          </div>
          {generatedDraft ? (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="amber">草稿 · 需人工确认后启用</Badge>
                <span className="text-sm font-medium text-amber-900">{generatedDraft.name}</span>
              </div>
              <p className="mt-1 text-xs leading-5 text-amber-800">
                已保存为 draft（未发布）。项目只能应用已发布模板，请到「模板与技能 → 模板库」确认章节无误后发布。
              </p>
              <div className="mt-2 flex flex-wrap gap-1">
                {(
                  ((generatedDraft.content_json as { sections?: { title?: string }[] })
                    .sections ?? []) as { title?: string }[]
                ).map((section, index) => (
                  <span
                    key={`${section.title ?? "section"}-${index}`}
                    className="rounded-md bg-white/70 px-1.5 py-0.5 text-[10px] text-amber-800"
                  >
                    {section.title}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </Card>
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <FileText size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">上传文档</h2>
            <span className="mono-label text-slate-400">DOCX / PDF / TXT / MD / XLSX / PPTX</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-sm">
              标题（可选，仅单文件生效）
              <Input className="mt-1" value={docTitle} onChange={(event) => setDocTitle(event.target.value)} />
            </label>
            <label className="text-sm">
              类型
              <Select className="mt-1" value={docKind} onChange={(event) => setDocKind(event.target.value)}>
                {KINDS.map((kind) => (
                  <option key={kind.value} value={kind.value}>{kind.label}</option>
                ))}
              </Select>
            </label>
            <label className="text-sm sm:col-span-2">
              标签（逗号分隔）
              <Input className="mt-1" value={docTags} onChange={(event) => setDocTags(event.target.value)} placeholder="运维, 政务云, 产品" />
            </label>
            <label className="text-sm sm:col-span-2">
              文件（可多选）
              <input
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                type="file"
                multiple
                accept=".docx,.pdf,.txt,.md,.xlsx,.xlsm,.pptx"
                onChange={(event) => setDocFiles(Array.from(event.target.files ?? []))}
              />
            </label>
          </div>
          {docFiles.length > 1 ? (
            <p className="mt-2 text-xs text-slate-500">
              已选择 {docFiles.length} 个文件；将按顺序解析入库，标题默认取文件名。
            </p>
          ) : null}
          <Button
            className="mt-4"
            onClick={() => uploadDocument.mutate()}
            disabled={uploadDocument.isPending || docFiles.length === 0}
          >
            <Upload size={14} />{" "}
            {uploadDocument.isPending
              ? `解析入库中…${docFiles.length > 1 ? `（${docFiles.length}）` : ""}`
              : `上传并解析${docFiles.length > 1 ? `（${docFiles.length}）` : ""}`}
          </Button>
        </Card>
      </div>
      ) : null}

      {activeTab === "images" ? (
      <div className="grid gap-4">
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <ImageIcon size={16} className="text-aurora-600" />
            <h2 className="text-sm font-semibold">上传图片</h2>
            <span className="mono-label text-slate-400">PNG / JPG</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-sm">
              标题（可选，仅单文件生效）
              <Input className="mt-1" value={imageTitle} onChange={(event) => setImageTitle(event.target.value)} placeholder="双活架构图" />
            </label>
            <label className="text-sm">
              标签（逗号分隔）
              <Input className="mt-1" value={imageTags} onChange={(event) => setImageTags(event.target.value)} placeholder="架构图, 产品" />
            </label>
            <label className="text-sm sm:col-span-2">
              题注（可选，仅单文件生效）
              <Input className="mt-1" value={imageCaption} onChange={(event) => setImageCaption(event.target.value)} placeholder="图 1-1 双活架构" />
            </label>
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <input type="checkbox" checked={imageSensitive} onChange={(event) => setImageSensitive(event.target.checked)} />
              敏感图片（含证书/铭牌/身份信息；暗标生成时不引用）
            </label>
            <label className="text-sm sm:col-span-2">
              文件（可多选）
              <input
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                type="file"
                multiple
                accept=".png,.jpg,.jpeg,.webp"
                onChange={(event) => setImageFiles(Array.from(event.target.files ?? []))}
              />
            </label>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            WebP 图片在入库时统一转为 PNG，以便嵌入 docx 并去除源文件元数据。
          </p>
          {imageFiles.length > 1 ? (
            <p className="mt-2 text-xs text-slate-500">
              已选择 {imageFiles.length} 张图片；共用标签与敏感标记，标题默认取文件名。
            </p>
          ) : null}
          <Button
            className="mt-4"
            onClick={() => uploadImage.mutate()}
            disabled={uploadImage.isPending || imageFiles.length === 0}
          >
            <Upload size={14} />{" "}
            {uploadImage.isPending
              ? `上传中…${imageFiles.length > 1 ? `（${imageFiles.length}）` : ""}`
              : `上传图片${imageFiles.length > 1 ? `（${imageFiles.length}）` : ""}`}
          </Button>
        </Card>
      </div>
      ) : null}

      {activeTab === "recall" ? (
      <Card className="mt-4 p-5">
        <div className="flex items-center gap-2">
          <Search size={16} className="text-electric-600" />
          <h2 className="text-sm font-semibold">召回测试</h2>
          <span className="mono-label text-slate-400">按章节关键词与元数据查看命中</span>
        </div>
        <form
          className="mt-3 flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setSubmitted(query.trim());
          }}
        >
          <Input className="w-full sm:w-96" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：运维平台 双活架构" />
          <Button type="submit" variant="secondary" disabled={!query.trim()}>检索知识</Button>
        </form>
        {search.isLoading ? <Loading /> : null}
        {search.data ? (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mono-label mb-2 text-slate-400">文档片段</p>
              {search.data.chunks.length === 0 ? (
                <EmptyState
                text="没有命中文档片段。"
                hint="换一个更短、更通用的关键词再试；也可以确认该知识库是否已上传并解析完成（文档状态需为 parsed）。"
              />
              ) : (
                <div className="space-y-2">
                  {search.data.chunks.map((hit, index) => (
                    <div key={`${hit.document_id}-${index}`} className="rounded-xl border border-slate-200 bg-white/70 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-slate-700">{hit.title}</span>
                        <Badge tone="blue">score {hit.score}</Badge>
                      </div>
                      <p className="mt-1 line-clamp-3 text-xs leading-5 text-slate-500">{hit.chunk}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <p className="mono-label mb-2 text-slate-400">图片命中</p>
              {search.data.images.length === 0 ? (
                <EmptyState
                text="没有命中图片。"
                hint="图片按文件名与标签匹配。换关键词，或先到「企业知识库 → 图片」给图片补上标签。"
              />
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  {search.data.images.map((image) => (
                    <div key={image.id} className="rounded-xl border border-slate-200 bg-white/70 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-slate-700">{image.title}</span>
                        {image.sensitive ? <Badge tone="amber">敏感</Badge> : <Badge tone="green">可用</Badge>}
                      </div>
                      <p className="mt-1 text-xs text-slate-500">{image.caption ?? image.tags.join("、")}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}
      </Card>
      ) : null}

      {activeTab === "documents" ? (
      <Card className="mt-4">
        <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
          <BookOpen size={16} className="text-electric-600" />
          <h2 className="text-sm font-semibold">已入库文档</h2>
          <Badge tone="slate">{documents.data?.length ?? 0}</Badge>
        </div>
        {documents.isLoading ? (
          <Loading />
        ) : !documents.data || documents.data.length === 0 ? (
          <EmptyState text="暂无文档。上传产品资料后，生成章节时会自动召回。" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-5 py-3">标题</th>
                  <th className="px-5 py-3">类型</th>
                  <th className="px-5 py-3">状态</th>
                  <th className="px-5 py-3">标签</th>
                  <th className="px-5 py-3">字数</th>
                  <th className="px-5 py-3">大小</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {documents.data.map((document) => (
                  <tr key={document.id} className="hover:bg-slate-50">
                    <td className="px-5 py-3">
                      <button
                        type="button"
                        className="text-left font-medium text-slate-800 hover:text-electric-600 hover:underline"
                        onClick={() =>
                          setSelectedDocumentId((current) =>
                            current === document.id ? null : document.id,
                          )
                        }
                      >
                        {document.title}
                      </button>
                      <div className="mt-0.5 text-xs text-slate-400">{document.filename}</div>
                    </td>
                    <td className="px-5 py-3 text-slate-600">{kindLabel(document.kind)}</td>
                    <td className="px-5 py-3">
                      <Badge tone={document.status === "parsed" ? "green" : "rose"}>{document.status}</Badge>
                    </td>
                    <td className="px-5 py-3 text-xs text-slate-500">{document.tags.join("、") || "—"}</td>
                    <td className="px-5 py-3 text-slate-600">{document.text_chars}</td>
                    <td className="px-5 py-3 text-slate-600">{sizeLabel(document.size_bytes)}</td>
                    <td className="px-5 py-3 text-right">
                      {pendingDelete?.kind === "document" && pendingDelete.id === document.id ? (
                        <div className="flex items-center justify-end gap-1">
                          <button
                            className="rounded-lg bg-rose-50 px-2 py-1 text-xs text-rose-600"
                            onClick={() => removeDocument.mutate(document.id)}
                          >
                            确认删除
                          </button>
                          <button
                            className="rounded-lg bg-slate-100 px-2 py-1 text-xs text-slate-600"
                            onClick={() => setPendingDelete(null)}
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <button
                          className="rounded-lg p-2 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                          aria-label="删除文档"
                          onClick={() => setPendingDelete({ kind: "document", id: document.id })}
                        >
                          <Trash2 size={15} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      ) : null}

      {activeTab === "documents" && selectedDocumentId ? (
        <Card className="mt-4 p-5">
          <div className="flex items-center justify-between gap-2">
            <div>
              <p className="mono-label text-slate-400">DOCUMENT DETAIL</p>
              <h2 className="mt-1 text-sm font-semibold">{detail.data?.title ?? "文档详情"}</h2>
            </div>
            <Button variant="secondary" onClick={() => setSelectedDocumentId(null)}>
              关闭
            </Button>
          </div>
          {detail.isLoading ? <Loading /> : null}
          {detail.error ? (
            <div className="mt-3 text-sm text-rose-600">{detail.error.message}</div>
          ) : null}
          {detail.data ? (
            <div className="mt-4 space-y-4">
              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                <p className="mono-label mb-2 text-slate-400">元数据（参与召回加权）</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="text-sm">
                    标题
                    <Input className="mt-1" value={editTitle} onChange={(event) => setEditTitle(event.target.value)} />
                  </label>
                  <label className="text-sm">
                    类型
                    <Select className="mt-1" value={editKind} onChange={(event) => setEditKind(event.target.value)}>
                      {KINDS.map((kind) => (
                        <option key={kind.value} value={kind.value}>{kind.label}</option>
                      ))}
                    </Select>
                  </label>
                  <label className="text-sm sm:col-span-2">
                    标签（逗号分隔）
                    <Input className="mt-1" value={editTags} onChange={(event) => setEditTags(event.target.value)} />
                  </label>
                </div>
                <Button
                  className="mt-3"
                  variant="secondary"
                  disabled={updateDocument.isPending || !editTitle.trim()}
                  onClick={() =>
                    updateDocument.mutate({
                      id: detail.data!.id,
                      title: editTitle.trim(),
                      kind: editKind,
                      tags: splitTags(editTags),
                    })
                  }
                >
                  <Save size={14} /> {updateDocument.isPending ? "保存中…" : "保存元数据"}
                </Button>
              </div>
              <div>
                <p className="mono-label mb-2 text-slate-400">解析文本预览</p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-600">
                  {detail.data.text_preview || "（无文本）"}
                </pre>
              </div>
              <div>
                <p className="mono-label mb-2 text-slate-400">
                  分块（{detail.data.chunks.length}）
                </p>
                <div className="space-y-2">
                  {detail.data.chunks.map((chunk) => (
                    <div
                      key={chunk.chunk_index}
                      className="rounded-xl border border-slate-200 bg-white/70 p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <Badge tone="blue">#{chunk.chunk_index + 1}</Badge>
                        {chunk.heading ? <span>{chunk.heading}</span> : null}
                        <span className="text-slate-400">
                          {chunk.keywords.slice(0, 8).join(" · ")}
                        </span>
                      </div>
                      <p className="mt-2 line-clamp-4 text-xs leading-5 text-slate-600">
                        {chunk.text}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : null}
        </Card>
      ) : null}

      {activeTab === "images" ? (
      <Card className="mt-4">
        <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
          <ImageIcon size={16} className="text-aurora-600" />
          <h2 className="text-sm font-semibold">图片资产</h2>
          <Badge tone="slate">{images.data?.length ?? 0}</Badge>
        </div>
        {images.isLoading ? (
          <Loading />
        ) : !images.data || images.data.length === 0 ? (
          <EmptyState text="暂无图片。上传产品图/架构图后，生成时可引用。" />
        ) : (
          <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-3">
            {images.data.map((image) => (
              <div key={image.id} className="rounded-xl border border-slate-200 bg-white/70 p-3">
                <AuthedImage
                  imageId={image.id}
                  alt={image.title}
                  className="mb-2 h-32 w-full rounded-lg border border-slate-100 bg-slate-50 object-cover"
                />
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-slate-800">{image.title}</span>
                  <button
                    type="button"
                    title="切换敏感标记"
                    onClick={() =>
                      toggleSensitive.mutate({ id: image.id, sensitive: !image.sensitive })
                    }
                  >
                    {image.sensitive ? <Badge tone="amber">敏感</Badge> : <Badge tone="green">可用</Badge>}
                  </button>
                </div>
                {editingImageId === image.id ? (
                  <div className="mt-2 space-y-2">
                    <Input
                      value={imageEditTitle}
                      onChange={(event) => setImageEditTitle(event.target.value)}
                      placeholder="标题"
                    />
                    <Input
                      value={imageEditCaption}
                      onChange={(event) => setImageEditCaption(event.target.value)}
                      placeholder="题注（可选）"
                    />
                    <Input
                      value={imageEditTags}
                      onChange={(event) => setImageEditTags(event.target.value)}
                      placeholder="标签（逗号分隔）"
                    />
                    <div className="flex gap-2">
                      <Button
                        disabled={updateImageMeta.isPending || !imageEditTitle.trim()}
                        onClick={() =>
                          updateImageMeta.mutate({
                            id: image.id,
                            title: imageEditTitle.trim(),
                            caption: imageEditCaption.trim(),
                            tags: splitTags(imageEditTags),
                          })
                        }
                      >
                        <Save size={13} /> {updateImageMeta.isPending ? "保存中…" : "保存"}
                      </Button>
                      <Button variant="secondary" onClick={() => setEditingImageId(null)}>
                        取消
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <p className="mt-1 text-xs text-slate-500">{image.caption ?? "—"}</p>
                    <p className="mt-1 text-[11px] text-slate-400">
                      {image.tags.length ? image.tags.join("、") : "无标签"}
                    </p>
                  </>
                )}
                <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
                  <span>
                    {image.width && image.height ? `${image.width}×${image.height} · ` : ""}
                    {sizeLabel(image.size_bytes)}
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                      aria-label="编辑图片元数据"
                      onClick={() => startImageEdit(image)}
                    >
                      <Pencil size={14} />
                    </button>
                    {pendingDelete?.kind === "image" && pendingDelete.id === image.id ? (
                      <>
                        <button
                          className="rounded-lg bg-rose-50 px-2 py-1 text-[11px] text-rose-600"
                          onClick={() => removeImage.mutate(image.id)}
                        >
                          确认删除
                        </button>
                        <button
                          className="rounded-lg bg-slate-100 px-2 py-1 text-[11px] text-slate-600"
                          onClick={() => setPendingDelete(null)}
                        >
                          取消
                        </button>
                      </>
                    ) : (
                      <button
                        className="rounded-lg p-1.5 text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                        aria-label="删除图片"
                        onClick={() => setPendingDelete({ kind: "image", id: image.id })}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
      ) : null}
      </div>
    </>
  );
}import { useConfirm } from "@/components/confirm-dialog";

