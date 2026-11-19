"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileSignature, Plus, X } from "lucide-react";

import { Badge, Button, Card, EmptyState, Input, Loading, PageHeader, Select } from "@/components/ui";
import {
  ContractLifecyclePanel,
  type ContractRecordDetail,
  type ContractStatus,
} from "@/components/contract-lifecycle-panel";
import { ApiError, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";

type ContractRecordList = {
  items: ContractRecordDetail[];
  total: number;
  limit: number;
  offset: number;
};

type ProjectOption = {
  id: string;
  name: string;
  buyer: string | null;
  status: string;
};

const STATUS_OPTIONS: Array<{ value: "all" | ContractStatus; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "draft", label: "草稿" },
  { value: "pending_approval", label: "待审批" },
  { value: "active", label: "履约中" },
  { value: "expired", label: "已到期" },
  { value: "terminated", label: "已终止" },
  { value: "archived", label: "已归档" },
];

const STATUS_META: Record<ContractStatus, { label: string; tone: "slate" | "green" | "amber" | "rose" | "blue" }> = {
  draft: { label: "草稿", tone: "slate" },
  pending_approval: { label: "待审批", tone: "amber" },
  active: { label: "履约中", tone: "green" },
  expired: { label: "已到期", tone: "rose" },
  terminated: { label: "已终止", tone: "rose" },
  archived: { label: "已归档", tone: "slate" },
};

function newContractNo(): string {
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const rand = Math.random().toString(36).slice(2, 8).toUpperCase();
  return `HT-${stamp}-${rand}`;
}

type CreateForm = {
  project_id: string;
  contract_no: string;
  title: string;
  party_a: string;
  party_b: string;
  signed_date: string;
  effective_date: string;
  expiry_date: string;
  amount: string;
  currency: string;
};

const EMPTY_FORM: CreateForm = {
  project_id: "",
  contract_no: "",
  title: "",
  party_a: "",
  party_b: "",
  signed_date: "",
  effective_date: "",
  expiry_date: "",
  amount: "",
  currency: "CNY",
};

export default function ContractsPage() {
  const user = useAuthStore((state) => state.user);
  const queryClient = useQueryClient();
  const canRead = hasPermission(user?.permissions, "project:read");
  const canWrite = hasPermission(user?.permissions, "project:write");
  const [status, setStatus] = useState<"all" | ContractStatus>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [projectFilter, setProjectFilter] = useState("");
  const [createdNotice, setCreatedNotice] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["contract-records", status],
    queryFn: () =>
      apiFetch<ContractRecordList>(
        `/contract-records?limit=100&offset=0${status === "all" ? "" : `&status=${status}`}`,
      ),
    enabled: canRead,
    retry: false,
  });

  const projectsQuery = useQuery({
    queryKey: ["projects", "for-contract"],
    queryFn: () => apiFetch<ProjectOption[]>("/projects"),
    enabled: canRead,
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch<ContractRecordDetail>("/contract-records", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: (created) => {
      setShowCreate(false);
      setForm(EMPTY_FORM);
      setFormError(null);
      setStatus("all");
      setSelectedId(created.id);
      setCreatedNotice(`已创建合同「${created.title}」（${created.contract_no}），可继续起草与审批`);
      window.setTimeout(() => setCreatedNotice(null), 6000);
      void queryClient.invalidateQueries({ queryKey: ["contract-records"] });
      window.requestAnimationFrame(() => {
        document.getElementById("contract-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    },
    onError: (error: unknown) => {
      setFormError(
        error instanceof ApiError ? error.message : "创建失败，请检查表单后重试",
      );
    },
  });

  const rows = useMemo(() => query.data?.items ?? [], [query.data]);
  const activeId = selectedId ?? rows[0]?.id ?? null;

  useEffect(() => {
    if (!showCreate) return;
    const timer = window.setTimeout(
      () => document.getElementById("contract-project-search")?.focus(),
      60,
    );
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setShowCreate(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [showCreate]);

  function openCreate() {
    setForm({ ...EMPTY_FORM, contract_no: newContractNo() });
    setProjectFilter("");
    setFormError(null);
    setShowCreate(true);
  }

  function selectRecord(id: string) {
    setSelectedId(id);
    window.requestAnimationFrame(() => {
      document.getElementById("contract-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  const projectNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const project of projectsQuery.data ?? []) {
      map.set(project.id, project.name);
    }
    return map;
  }, [projectsQuery.data]);

  const filteredProjects = useMemo(() => {
    const list = projectsQuery.data ?? [];
    const keyword = projectFilter.trim().toLowerCase();
    if (!keyword) return list;
    return list.filter((item) =>
      `${item.name} ${item.buyer ?? ""}`.toLowerCase().includes(keyword),
    );
  }, [projectsQuery.data, projectFilter]);

  function pickProject(projectId: string) {
    const project = (projectsQuery.data ?? []).find((item) => item.id === projectId);
    setForm((prev) => ({
      ...prev,
      project_id: projectId,
      // 从项目自动带入名称与采购人，避免重复手输
      title: prev.title.trim() ? prev.title : project?.name ?? "",
      party_a: prev.party_a.trim() ? prev.party_a : project?.buyer ?? "",
    }));
  }

  function submitCreate() {
    if (!form.project_id) {
      setFormError("请选择关联投标项目");
      return;
    }
    if (!form.contract_no.trim() || !form.title.trim()) {
      setFormError("合同编号与合同名称为必填项");
      return;
    }
    setFormError(null);
    createMutation.mutate({
      project_id: form.project_id,
      contract_no: form.contract_no.trim(),
      title: form.title.trim(),
      party_a: form.party_a.trim(),
      party_b: form.party_b.trim(),
      signed_date: form.signed_date || null,
      effective_date: form.effective_date || null,
      expiry_date: form.expiry_date || null,
      amount: form.amount === "" ? null : form.amount,
      currency: form.currency || "CNY",
    });
  }

  if (!canRead) {
    return (
      <Card className="p-6 text-sm text-amber-800">
        当前角色缺少 `project:read` 权限，无法查看合同台账。
      </Card>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="CONTRACT / LIFECYCLE"
        title="合同台账"
        subtitle="合同主数据、机器草稿、审批流、履约金额核销与变更续签留痕。合同审查仍在项目工作流内独立运行。"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="slate">共 {query.data?.total ?? 0} 份</Badge>
            <Select
              aria-label="按合同状态筛选"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as "all" | ContractStatus);
                setSelectedId(null);
              }}
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
            {canWrite ? (
              <Button onClick={showCreate ? () => setShowCreate(false) : openCreate}>
                {showCreate ? (
                  <>
                    <X size={14} /> 取消
                  </>
                ) : (
                  <>
                    <Plus size={14} /> 新建合同
                  </>
                )}
              </Button>
            ) : null}
          </div>
        }
      />

      {createdNotice ? (
        <div className="mb-3 flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
          <span className="flex-1">{createdNotice}</span>
          <button
            type="button"
            aria-label="关闭提示"
            className="text-emerald-700 hover:text-emerald-900"
            onClick={() => setCreatedNotice(null)}
          >
            ×
          </button>
        </div>
      ) : null}

      {showCreate ? (
        <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label="新建合同">
          <button
            type="button"
            aria-label="关闭新建合同"
            className="absolute inset-0 cursor-default bg-slate-900/30 backdrop-blur-[1px]"
            onClick={() => setShowCreate(false)}
          />
          <div className="relative flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
            <div className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-200/70 px-5 py-4">
              <div>
                <h2 className="text-sm font-semibold text-slate-800">新建合同主数据</h2>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  先建台账，再在详情里起草条款、提交审批、登记履约台账。
                </p>
              </div>
              <button
                type="button"
                aria-label="关闭"
                className="rounded-lg p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                onClick={() => setShowCreate(false)}
              >
                <X size={16} />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mono-label text-slate-500">关联投标项目 *</span>
              <Input
                id="contract-project-search"
                className="mt-1 w-full"
                placeholder={`搜索项目名称或采购人（共 ${projectsQuery.data?.length ?? 0} 个）`}
                value={projectFilter}
                onChange={(event) => setProjectFilter(event.target.value)}
              />
              <Select
                className="mt-1 w-full"
                value={form.project_id}
                onChange={(event) => pickProject(event.target.value)}
              >
                <option value="">
                  {projectsQuery.isLoading
                    ? "加载项目中…"
                    : filteredProjects.length === 0
                      ? "无匹配项目，请调整搜索词"
                      : `请选择项目（${filteredProjects.length}）`}
                </option>
                {filteredProjects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </Select>
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">合同编号 *</span>
              <Input
                className="mt-1 w-full"
                value={form.contract_no}
                onChange={(event) => setForm((p) => ({ ...p, contract_no: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">合同名称 *</span>
              <Input
                className="mt-1 w-full"
                value={form.title}
                onChange={(event) => setForm((p) => ({ ...p, title: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">甲方</span>
              <Input
                className="mt-1 w-full"
                value={form.party_a}
                onChange={(event) => setForm((p) => ({ ...p, party_a: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">乙方</span>
              <Input
                className="mt-1 w-full"
                placeholder={user?.tenant_name ?? ""}
                value={form.party_b}
                onChange={(event) => setForm((p) => ({ ...p, party_b: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">合同金额（元）</span>
              <Input
                className="mt-1 w-full"
                type="number"
                min="0"
                step="0.01"
                placeholder="可稍后补录"
                value={form.amount}
                onChange={(event) => setForm((p) => ({ ...p, amount: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">币种</span>
              <Input
                className="mt-1 w-full"
                value={form.currency}
                onChange={(event) => setForm((p) => ({ ...p, currency: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">签约日期</span>
              <Input
                className="mt-1 w-full"
                type="date"
                value={form.signed_date}
                onChange={(event) => setForm((p) => ({ ...p, signed_date: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">生效日期</span>
              <Input
                className="mt-1 w-full"
                type="date"
                value={form.effective_date}
                onChange={(event) => setForm((p) => ({ ...p, effective_date: event.target.value }))}
              />
            </label>

            <label className="block">
              <span className="mono-label text-slate-500">到期日期</span>
              <Input
                className="mt-1 w-full"
                type="date"
                value={form.expiry_date}
                onChange={(event) => setForm((p) => ({ ...p, expiry_date: event.target.value }))}
              />
            </label>
          </div>

              {formError ? (
                <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{formError}</p>
              ) : null}
            </div>

            <div className="flex shrink-0 items-center gap-2 border-t border-slate-200/70 px-5 py-3">
              <Button onClick={submitCreate} disabled={createMutation.isPending}>
                {createMutation.isPending ? "创建中…" : "创建合同"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setShowCreate(false);
                  setFormError(null);
                }}
              >
                取消
              </Button>
              <span className="ml-auto text-[11px] text-slate-400">带 * 为必填</span>
            </div>
          </div>
        </div>
      ) : null}

      {query.isLoading ? (
        <Loading />
      ) : query.error ? (
        <Card className="p-6 text-sm text-rose-600">{query.error.message}</Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState text="暂无合同台账记录。点击右上角「新建合同」建立第一份合同主数据。" />
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(280px,380px)_1fr]">
          <Card className="overflow-hidden">
            <div className="border-b border-slate-200/70 px-4 py-3">
              <div className="flex items-center gap-2">
                <FileSignature size={15} className="text-aurora-600" />
                <h2 className="text-sm font-semibold text-slate-800">合同列表</h2>
              </div>
              <p className="mt-1 text-[11px] text-slate-400">筛选后 {rows.length} 条</p>
            </div>
            <ul className="max-h-[360px] divide-y divide-slate-100 overflow-y-auto xl:max-h-none xl:overflow-visible">
              {rows.map((row) => {
                const meta = STATUS_META[row.status] ?? { label: row.status, tone: "slate" as const };
                const active = row.id === activeId;
                return (
                  <li key={row.id}>
                    <button
                      type="button"
                      onClick={() => selectRecord(row.id)}
                      className={`w-full px-4 py-3 text-left transition ${
                        active ? "bg-aurora-500/[0.06]" : "hover:bg-slate-50"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-slate-800">
                          {row.title}
                        </span>
                        <Badge tone={meta.tone}>{meta.label}</Badge>
                      </div>
                      <p className="mt-1 font-mono text-[11px] tracking-tight text-slate-600">
                        {row.contract_no}
                      </p>
                      <p className="mt-0.5 truncate text-[11px] text-slate-400">
                        {projectNameById.get(row.project_id) &&
                        projectNameById.get(row.project_id) !== row.title
                          ? `${projectNameById.get(row.project_id)} · `
                          : ""}
                        {row.currency} {row.amount ?? "—"} · 到期 {row.expiry_date ?? "未设置"}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Card>

          {activeId ? (
            <div id="contract-detail" className="scroll-mt-20">
              <ContractLifecyclePanel key={activeId} recordId={activeId} />
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}