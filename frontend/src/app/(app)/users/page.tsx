"use client";

import Link from "next/link";

import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Card, EmptyState, Input, Loading, PageHeader } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import type { RoleOption, TenantUser } from "@/lib/types";

export default function UsersPage() {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [form, setForm] = useState({
    email: "",
    display_name: "",
    password: "",
    role_codes: ["bid_engineer"] as string[],
  });
  const [drafts, setDrafts] = useState<Record<string, string[]>>({});

  const users = useQuery({
    queryKey: ["tenant-users"],
    queryFn: () => apiFetch<TenantUser[]>("/users"),
  });
  const roles = useQuery({
    queryKey: ["tenant-roles"],
    queryFn: () => apiFetch<RoleOption[]>("/roles"),
  });

  useEffect(() => {
    if (users.data) {
      setDrafts(
        Object.fromEntries(users.data.map((user) => [user.id, [...user.roles]])),
      );
    }
  }, [users.data]);

  const createUser = useMutation({
    mutationFn: () =>
      apiFetch<TenantUser>("/users", {
        method: "POST",
        body: JSON.stringify(form),
      }),
    onSuccess: () => {
      setMessage("用户已创建。");
      setForm({ email: "", display_name: "", password: "", role_codes: ["bid_engineer"] });
      queryClient.invalidateQueries({ queryKey: ["tenant-users"] });
    },
    onError: (error) =>
      setMessage(error instanceof ApiError ? `${error.message}（${error.code}）` : "创建失败"),
  });

  const updateUser = useMutation({
    mutationFn: (payload: { id: string; body: Record<string, unknown> }) =>
      apiFetch<TenantUser>(`/users/${payload.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload.body),
      }),
    onSuccess: () => {
      setMessage("用户已更新。");
      queryClient.invalidateQueries({ queryKey: ["tenant-users"] });
    },
    onError: (error) =>
      setMessage(error instanceof ApiError ? `${error.message}（${error.code}）` : "更新失败"),
  });

  function toggleFormRole(code: string) {
    setForm((current) => ({
      ...current,
      role_codes: current.role_codes.includes(code)
        ? current.role_codes.filter((item) => item !== code)
        : [...current.role_codes, code],
    }));
  }

  function toggleDraftRole(userId: string, code: string) {
    setDrafts((current) => {
      const existing = current[userId] ?? [];
      return {
        ...current,
        [userId]: existing.includes(code)
          ? existing.filter((item) => item !== code)
          : [...existing, code],
      };
    });
  }

  function submitCreate(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    createUser.mutate();
  }

  const roleOptions = roles.data ?? [];

  return (
    <>
      <PageHeader
        title="成员与权限"
        subtitle="分区一「成员与角色」：租户内账号、角色与状态，最后一个管理员不可被停用或降权。"
      />
      <div className="mb-4 flex gap-1 rounded-lg bg-slate-100 p-1">
        <Link
          href="/users"
          className="rounded-md bg-white px-3 py-1.5 text-sm font-medium shadow-sm"
        >
          成员与角色
        </Link>
        <Link
          href="/access"
          className="rounded-md px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800"
        >
          部门与权限策略
        </Link>
      </div>
      {message ? (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
          {message}
        </div>
      ) : null}

      <Card className="p-5">
        <h2 className="text-sm font-semibold">新建用户</h2>
        <form className="mt-3 grid gap-2 sm:grid-cols-2" onSubmit={submitCreate}>
          <Input
            type="email"
            placeholder="邮箱 *"
            value={form.email}
            onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
            required
          />
          <Input
            placeholder="显示名称 *"
            value={form.display_name}
            onChange={(event) =>
              setForm((current) => ({ ...current, display_name: event.target.value }))
            }
            required
          />
          <Input
            type="password"
            placeholder="初始密码（≥8 位）*"
            value={form.password}
            onChange={(event) =>
              setForm((current) => ({ ...current, password: event.target.value }))
            }
            required
            minLength={8}
          />
          <div className="flex flex-wrap items-center gap-3 text-sm">
            {roleOptions.map((role) => (
              <label key={role.code} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={form.role_codes.includes(role.code)}
                  onChange={() => toggleFormRole(role.code)}
                />
                {role.name}
              </label>
            ))}
          </div>
          <div className="sm:col-span-2">
            <Button type="submit" disabled={createUser.isPending || form.role_codes.length === 0}>
              {createUser.isPending ? "创建中…" : "创建用户"}
            </Button>
          </div>
        </form>
      </Card>

      <Card className="mt-4">
        {users.isLoading ? (
          <Loading />
        ) : users.error ? (
          <div className="p-6 text-sm text-rose-600">{users.error.message}</div>
        ) : !users.data || users.data.length === 0 ? (
          <EmptyState
                text="租户里还没有成员。"
                hint="用上方表单创建第一个成员并分配角色；至少保留一个企业管理员，否则无人能改权限。"
              />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">用户</th>
                  <th className="px-4 py-3">角色</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">最近登录</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {users.data.map((user) => (
                  <tr key={user.id} className="align-top hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <div className="font-medium">{user.display_name}</div>
                      <div className="text-xs text-slate-500">{user.email}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        {roleOptions.map((role) => (
                          <label key={role.code} className="flex items-center gap-1 text-xs">
                            <input
                              type="checkbox"
                              checked={(drafts[user.id] ?? []).includes(role.code)}
                              onChange={() => toggleDraftRole(user.id, role.code)}
                            />
                            {role.name}
                          </label>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={user.status === "active" ? "green" : "rose"}>
                        {user.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-slate-500">
                      {user.last_login_at
                        ? user.last_login_at.slice(0, 16).replace("T", " ")
                        : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="secondary"
                          onClick={() =>
                            updateUser.mutate({
                              id: user.id,
                              body: { role_codes: drafts[user.id] ?? user.roles },
                            })
                          }
                          disabled={updateUser.isPending}
                        >
                          保存角色
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() =>
                            updateUser.mutate({
                              id: user.id,
                              body: { status: user.status === "active" ? "disabled" : "active" },
                            })
                          }
                          disabled={updateUser.isPending}
                        >
                          {user.status === "active" ? "停用" : "启用"}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
