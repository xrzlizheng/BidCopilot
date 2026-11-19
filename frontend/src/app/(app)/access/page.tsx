"use client";

import Link from "next/link";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, KeyRound, ShieldCheck, UserCog, Users } from "lucide-react";

import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Loading,
  PageHeader,
  Select,
} from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type {
  AccessRequest,
  FieldPolicy,
  OrgUnitNode,
  PersonalResource,
  RoleOption,
  TenantUser,
} from "@/lib/types";

type FlatOrg = { node: OrgUnitNode; depth: number };
type AccessTab = "org" | "fields" | "handover";

function flattenOrg(nodes: OrgUnitNode[], depth = 0): FlatOrg[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...flattenOrg(node.children ?? [], depth + 1),
  ]);
}

function splitList(value: string): string[] {
  return value
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? `${error.message}（${error.code}）` : fallback;
}

export default function AccessGovernancePage() {
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((state) => state.user);
  const canReadMetadata = hasPermission(currentUser?.permissions, "audit:read");
  const canTransfer = hasPermission(currentUser?.permissions, "user:manage");
  const [message, setMessage] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<AccessTab>("org");
  const [transferTargets, setTransferTargets] = useState<Record<string, string>>({});

  const [orgName, setOrgName] = useState("");
  const [memberOrgId, setMemberOrgId] = useState("");
  const [memberUserId, setMemberUserId] = useState("");
  const [memberRole, setMemberRole] = useState("member");

  const [roleName, setRoleName] = useState("");
  const [rolePermissions, setRolePermissions] = useState("");
  const [roleScopes, setRoleScopes] = useState<string[]>(["tenant"]);

  const [policyKind, setPolicyKind] = useState("qualifications");
  const [policyField, setPolicyField] = useState("cert_no");
  const [policyClearance, setPolicyClearance] = useState("confidential");
  const [policyStrategy, setPolicyStrategy] = useState("mask");

  const orgTree = useQuery({
    queryKey: ["org-units"],
    queryFn: () => apiFetch<OrgUnitNode[]>("/org-units/tree"),
  });
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => apiFetch<TenantUser[]>("/users"),
  });
  const roles = useQuery({
    queryKey: ["roles"],
    queryFn: () => apiFetch<RoleOption[]>("/roles"),
  });
  const fieldPolicies = useQuery({
    queryKey: ["asset-field-policies"],
    queryFn: () => apiFetch<FieldPolicy[]>("/asset-field-policies"),
  });
  const accessRequests = useQuery({
    queryKey: ["access-requests"],
    queryFn: () => apiFetch<AccessRequest[]>("/compliance/access-requests"),
  });
  const personalResources = useQuery({
    queryKey: ["personal-resources"],
    queryFn: () => apiFetch<PersonalResource[]>("/compliance/personal-resources"),
    enabled: canReadMetadata,
  });

  const flatOrgs = useMemo(() => flattenOrg(orgTree.data ?? []), [orgTree.data]);
  const pendingRequests = (accessRequests.data ?? []).filter(
    (request) => request.status === "pending",
  ).length;
  const personalCount = personalResources.data?.length ?? 0;
  const fieldCount = fieldPolicies.data?.length ?? 0;

  const createOrg = useMutation({
    mutationFn: () =>
      apiFetch("/org-units", {
        method: "POST",
        body: JSON.stringify({ name: orgName.trim(), kind: "department" }),
      }),
    onSuccess: () => {
      setOrgName("");
      setMessage("部门已创建。");
      queryClient.invalidateQueries({ queryKey: ["org-units"] });
    },
    onError: (error) => setMessage(errorMessage(error, "创建部门失败")),
  });

  const addMember = useMutation({
    mutationFn: () =>
      apiFetch(`/org-units/${memberOrgId}/members`, {
        method: "POST",
        body: JSON.stringify({
          user_id: memberUserId,
          member_role: memberRole,
          is_primary: false,
        }),
      }),
    onSuccess: () => {
      setMessage("部门成员已更新。");
      queryClient.invalidateQueries({ queryKey: ["org-units"] });
    },
    onError: (error) => setMessage(errorMessage(error, "添加成员失败")),
  });

  const createRole = useMutation({
    mutationFn: () =>
      apiFetch("/roles", {
        method: "POST",
        body: JSON.stringify({
          name: roleName.trim(),
          permissions: splitList(rolePermissions),
          scope_types: roleScopes,
        }),
      }),
    onSuccess: () => {
      setRoleName("");
      setRolePermissions("");
      setMessage("自定义角色已创建。");
      queryClient.invalidateQueries({ queryKey: ["roles"] });
    },
    onError: (error) => setMessage(errorMessage(error, "创建角色失败")),
  });

  const upsertPolicy = useMutation({
    mutationFn: () =>
      apiFetch("/asset-field-policies", {
        method: "PUT",
        body: JSON.stringify({
          resource_type: policyKind,
          field_name: policyField.trim(),
          min_clearance: policyClearance,
          mask_strategy: policyStrategy,
        }),
      }),
    onSuccess: () => {
      setMessage("字段策略已保存。");
      queryClient.invalidateQueries({ queryKey: ["asset-field-policies"] });
    },
    onError: (error) => setMessage(errorMessage(error, "保存字段策略失败")),
  });

  const decideRequest = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "deny" }) =>
      apiFetch(`/compliance/access-requests/${id}/${action}`, { method: "POST" }),
    onSuccess: () => {
      setMessage("调阅申请已处理。");
      queryClient.invalidateQueries({ queryKey: ["access-requests"] });
    },
    onError: (error) => setMessage(errorMessage(error, "处理调阅申请失败")),
  });

  const transferResource = useMutation({
    mutationFn: (resource: PersonalResource) => {
      const key = `${resource.resource_type}:${resource.id}`;
      const targetUserId = transferTargets[key];
      if (!targetUserId) throw new Error("请选择接收人");
      return apiFetch(
        `/compliance/personal-resources/${resource.resource_type}/${resource.id}/transfer`,
        {
          method: "POST",
          body: JSON.stringify({ target_user_id: targetUserId }),
        },
      );
    },
    onSuccess: () => {
      setTransferTargets({});
      setMessage("个人资源所有权已转移。");
      queryClient.invalidateQueries({ queryKey: ["personal-resources"] });
    },
    onError: (error) => setMessage(errorMessage(error, "转移失败")),
  });

  function toggleScope(scope: string) {
    setRoleScopes((current) =>
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope],
    );
  }

  return (
    <>
      <PageHeader
        title="成员与权限"
        subtitle="分区二「部门与权限策略」：部门、自定义角色、资产字段脱敏、个人资源调阅与离职交接；所有变更由后端重新鉴权并写审计。"
      />
      <div className="mb-4 flex gap-1 rounded-lg bg-slate-100 p-1">
        <Link
          href="/users"
          className="rounded-md px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800"
        >
          成员与角色
        </Link>
        <Link
          href="/access"
          className="rounded-md bg-white px-3 py-1.5 text-sm font-medium shadow-sm"
        >
          部门与权限策略
        </Link>
      </div>
      {message ? (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
          {message}
        </div>
      ) : null}

      <Card className="z-20 mb-4 p-4 shadow-card sm:p-5 lg:sticky lg:top-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <ShieldCheck size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">权限治理</h2>
            <Badge tone="slate">部门 {flatOrgs.length}</Badge>
            <Badge tone="slate">角色 {roles.data?.length ?? 0}</Badge>
            {pendingRequests ? <Badge tone="amber">待审 {pendingRequests}</Badge> : null}
          </div>
          <p className="text-xs text-slate-500">所有变更由后端重新鉴权并写审计。</p>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {[
            { key: "org" as const, label: "组织与角色", detail: "部门、成员与自定义角色", icon: <Building2 size={14} /> },
            { key: "fields" as const, label: "字段策略", detail: "密级与脱敏", icon: <ShieldCheck size={14} /> },
            { key: "handover" as const, label: "调阅与交接", detail: "break-glass 与资源转移", icon: <KeyRound size={14} /> },
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

      <div id="access-content" className="scroll-mt-24 lg:scroll-mt-[220px]">
      {activeTab === "org" ? (
      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <Building2 size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">部门与成员</h2>
            <Badge tone="slate">{flatOrgs.length}</Badge>
          </div>
          {orgTree.isLoading ? (
            <Loading />
          ) : flatOrgs.length === 0 ? (
            <EmptyState
                text="还没有部门。"
                hint="部门用于把成员与资源按组织单元隔离；没有部门时所有成员都在租户根下，权限按角色生效。"
              />
          ) : (
            <div className="mt-3 space-y-1">
              {flatOrgs.map(({ node, depth }) => (
                <div
                  key={node.id}
                  className="rounded-lg border border-slate-200/70 bg-white/60 px-3 py-2 text-xs"
                  style={{ marginLeft: depth * 16 }}
                >
                  <span className="font-medium text-slate-700">{node.name}</span>
                  <span className="ml-2 text-slate-400">{node.kind}</span>
                </div>
              ))}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <Input
              className="min-w-48 flex-1"
              placeholder="新部门名称"
              value={orgName}
              onChange={(event) => setOrgName(event.target.value)}
            />
            <Button
              variant="secondary"
              disabled={!orgName.trim() || createOrg.isPending}
              onClick={() => createOrg.mutate()}
            >
              新建部门
            </Button>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            <Select value={memberOrgId} onChange={(event) => setMemberOrgId(event.target.value)}>
              <option value="">选择部门</option>
              {flatOrgs.map(({ node }) => (
                <option key={node.id} value={node.id}>
                  {node.name}
                </option>
              ))}
            </Select>
            <Select value={memberUserId} onChange={(event) => setMemberUserId(event.target.value)}>
              <option value="">选择用户</option>
              {(users.data ?? []).map((user) => (
                <option key={user.id} value={user.id}>
                  {user.display_name} · {user.email}
                </option>
              ))}
            </Select>
            <div className="flex gap-2">
              <Select value={memberRole} onChange={(event) => setMemberRole(event.target.value)}>
                <option value="member">成员</option>
                <option value="manager">经理</option>
              </Select>
              <Button
                variant="secondary"
                disabled={!memberOrgId || !memberUserId || addMember.isPending}
                onClick={() => addMember.mutate()}
              >
                添加
              </Button>
            </div>
          </div>
        </Card>

        <Card className="p-5">
          <div className="flex items-center gap-2">
            <Users size={16} className="text-aurora-600" />
            <h2 className="text-sm font-semibold">角色</h2>
            <Badge tone="slate">{roles.data?.length ?? 0}</Badge>
          </div>
          {roles.isLoading ? (
            <Loading />
          ) : (
            <div className="mt-3 space-y-2">
              {(roles.data ?? []).map((role) => (
                <div key={role.code} className="rounded-lg border border-slate-200/70 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-700">{role.name}</span>
                    <div className="flex gap-1">
                      <Badge tone={role.is_system ? "slate" : "blue"}>
                        {role.is_system ? "系统" : "自定义"}
                      </Badge>
                      {(role.scope_types ?? ["tenant"]).map((scope) => (
                        <Badge key={scope} tone="cyan">{scope}</Badge>
                      ))}
                    </div>
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400">
                    {role.permissions.slice(0, 8).join(" · ")}
                    {role.permissions.length > 8 ? " …" : ""}
                  </p>
                </div>
              ))}
            </div>
          )}
          <div className="mt-3 space-y-2">
            <Input
              placeholder="角色名称"
              value={roleName}
              onChange={(event) => setRoleName(event.target.value)}
            />
            <Input
              placeholder="权限码，逗号分隔，例如 knowledge:read, asset:read"
              value={rolePermissions}
              onChange={(event) => setRolePermissions(event.target.value)}
            />
            <div className="flex flex-wrap gap-3 text-xs text-slate-600">
              {["tenant", "org_unit"].map((scope) => (
                <label key={scope} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={roleScopes.includes(scope)}
                    onChange={() => toggleScope(scope)}
                  />
                  {scope}
                </label>
              ))}
            </div>
            <Button
              variant="secondary"
              disabled={
                !roleName.trim() || !splitList(rolePermissions).length || createRole.isPending
              }
              onClick={() => createRole.mutate()}
            >
              新建自定义角色
            </Button>
          </div>
        </Card>
      </div>
      ) : null}

      {activeTab === "fields" ? (
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck size={16} className="text-amber-600" />
            <h2 className="text-sm font-semibold">资产字段策略</h2>
          </div>
          {fieldPolicies.isLoading ? (
            <Loading />
          ) : (fieldPolicies.data ?? []).length === 0 ? (
            <EmptyState
                text="还没有字段脱敏策略。"
                hint="字段策略决定敏感字段（如报价、联系方式）对哪些角色可见；不配置时按角色默认权限展示。"
              />
          ) : (
            <div className="mt-3 space-y-2">
              {(fieldPolicies.data ?? []).map((policy) => (
                <div key={policy.id} className="rounded-lg border border-slate-200/70 p-3 text-xs">
                  <span className="font-medium text-slate-700">
                    {policy.resource_type}.{policy.field_name}
                  </span>
                  <span className="ml-2 text-slate-400">
                    ≥ {policy.min_clearance} · {policy.mask_strategy}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <Select value={policyKind} onChange={(event) => setPolicyKind(event.target.value)}>
              <option value="qualifications">资质</option>
              <option value="personnel">人员</option>
              <option value="performances">业绩</option>
              <option value="credits">信用</option>
            </Select>
            <Input
              placeholder="字段名，例如 cert_no"
              value={policyField}
              onChange={(event) => setPolicyField(event.target.value)}
            />
            <Select
              value={policyClearance}
              onChange={(event) => setPolicyClearance(event.target.value)}
            >
              <option value="internal">internal</option>
              <option value="confidential">confidential</option>
              <option value="restricted">restricted</option>
            </Select>
            <Select
              value={policyStrategy}
              onChange={(event) => setPolicyStrategy(event.target.value)}
            >
              <option value="mask">mask</option>
              <option value="hide">hide</option>
              <option value="partial">partial</option>
            </Select>
          </div>
          <Button
            className="mt-3"
            variant="secondary"
            disabled={!policyField.trim() || upsertPolicy.isPending}
            onClick={() => upsertPolicy.mutate()}
          >
            保存字段策略
          </Button>
        </Card>
      ) : null}

      {activeTab === "handover" ? (
      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <KeyRound size={16} className="text-rose-500" />
            <h2 className="text-sm font-semibold">个人资源调阅</h2>
          </div>
          {accessRequests.isLoading ? (
            <Loading />
          ) : (accessRequests.data ?? []).length === 0 ? (
            <EmptyState text="暂无调阅申请。" />
          ) : (
            <div className="mt-3 space-y-2">
              {(accessRequests.data ?? []).map((request) => (
                <div key={request.id} className="rounded-lg border border-slate-200/70 p-3 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-slate-700">
                      {request.resource_type} · {request.status}
                    </span>
                    <Badge tone={request.status === "pending" ? "amber" : "slate"}>
                      {request.requested_minutes} 分钟
                    </Badge>
                  </div>
                  <p className="mt-1 text-slate-500">{request.reason}</p>
                  {request.status === "pending" && request.requester_id ? (
                    <div className="mt-2 flex gap-2">
                      <Button
                        variant="secondary"
                        disabled={decideRequest.isPending}
                        onClick={() => decideRequest.mutate({ id: request.id, action: "approve" })}
                      >
                        批准
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={decideRequest.isPending}
                        onClick={() => decideRequest.mutate({ id: request.id, action: "deny" })}
                      >
                        拒绝
                      </Button>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </Card>

        {canReadMetadata ? (
          <Card className="p-5 xl:col-span-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <UserCog size={16} className="text-electric-600" />
                <h2 className="text-sm font-semibold">个人资源与离职交接</h2>
                <Badge tone="slate">{personalResources.data?.length ?? 0}</Badge>
              </div>
              <p className="text-xs text-slate-500">
                仅展示元数据，不读取正文；所有权转移由后端校验并写审计。
              </p>
            </div>
            {personalResources.isLoading ? <Loading /> : null}
            {personalResources.error ? (
              <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-600">
                {personalResources.error.message}
              </div>
            ) : null}
            {!personalResources.isLoading &&
            !personalResources.error &&
            (personalResources.data ?? []).length === 0 ? (
              <EmptyState
                text="该成员名下没有个人知识库或模板。"
                hint="这是正常状态：只有被授予 personal 作用域的资产才需要交接。若该成员离职前确有个人资产，请先确认他是否使用了个人空间。"
              />
            ) : null}
            <div className="mt-3 space-y-2">
              {(personalResources.data ?? []).map((resource) => {
                const key = `${resource.resource_type}:${resource.id}`;
                return (
                  <div
                    key={key}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200/70 px-3 py-2 text-xs"
                  >
                    <div className="min-w-48">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="blue">
                          {resource.resource_type === "knowledge_base" ? "知识库" : "模板"}
                        </Badge>
                        <span className="font-medium text-slate-700">{resource.name}</span>
                        <Badge tone={resource.owner_status === "active" ? "green" : "amber"}>
                          {resource.owner_status}
                        </Badge>
                        <Badge tone={resource.classification === "internal" ? "slate" : "cyan"}>
                          {resource.classification}
                        </Badge>
                      </div>
                      <p className="mt-1 text-slate-400">
                        所有者 {resource.owner_email ?? "未知"} ·{" "}
                        {resource.resource_type === "knowledge_base"
                          ? `${resource.document_count} 篇 · ${Math.max(
                              1,
                              Math.round(resource.size_bytes / 1024),
                            )} KB`
                          : `版本 ${resource.version ?? "-"}`}
                      </p>
                    </div>
                    {canTransfer ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <Select
                          className="min-w-44"
                          value={transferTargets[key] ?? ""}
                          onChange={(event) =>
                            setTransferTargets((current) => ({
                              ...current,
                              [key]: event.target.value,
                            }))
                          }
                        >
                          <option value="">选择接收人</option>
                          {(users.data ?? [])
                            .filter(
                              (item) =>
                                item.status === "active" && item.id !== resource.owner_id,
                            )
                            .map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.display_name}（{item.email}）
                              </option>
                            ))}
                        </Select>
                        <Button
                          variant="secondary"
                          disabled={!transferTargets[key] || transferResource.isPending}
                          onClick={() => transferResource.mutate(resource)}
                        >
                          转移所有权
                        </Button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </Card>
        ) : null}
      </div>
      ) : null}
      </div>
    </>
  );
}
