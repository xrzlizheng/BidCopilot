"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";

import { apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { NotificationListResponse } from "@/lib/types";

function formatTime(value: string): string {
  return value.slice(0, 16).replace("T", " ");
}

export function NotificationBell() {
  const user = useAuthStore((state) => state.user);
  const canRead = hasPermission(user?.permissions, "opportunity:read");
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const unreadQuery = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: () => apiFetch<{ unread: number }>("/notifications/unread-count"),
    enabled: canRead,
    // 全站唯一的轮询点：同步结果由调度器写入，页面自身没有刷新时机。
    refetchInterval: 60_000,
    retry: false,
  });

  const listQuery = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => apiFetch<NotificationListResponse>("/notifications?limit=10"),
    enabled: canRead && open,
    retry: false,
  });

  const markAllRead = useMutation({
    mutationFn: () =>
      apiFetch<{ updated: number }>("/notifications/read", {
        method: "POST",
        body: JSON.stringify({ all: true }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  if (!canRead) {
    return null;
  }

  const unreadFailed = unreadQuery.error !== null;
  const unread = unreadQuery.data?.unread ?? 0;
  const items = listQuery.data?.items ?? [];

  return (
    <div className="relative">
      <button
        type="button"
        aria-label={
          unreadFailed ? "消息中心，加载失败" : unread ? `消息中心，${unread} 条未读` : "消息中心"
        }
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
        className="relative rounded-lg p-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
      >
        <Bell size={16} />
        {unreadFailed ? (
          <span className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-amber-500 text-[10px] font-bold text-white">
            !
          </span>
        ) : null}
        {unread > 0 ? (
          <span className="tabular absolute -right-1 -top-1 grid min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <>
          <button
            type="button"
            aria-label="关闭消息中心"
            className="fixed inset-0 z-20 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 top-11 z-30 w-80 max-w-[calc(100vw-1rem)] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <span className="text-sm font-semibold">消息中心</span>
            <button
              type="button"
              className="text-xs font-medium text-electric-600 disabled:text-slate-400"
              disabled={!unread || markAllRead.isPending}
              onClick={() => markAllRead.mutate()}
            >
              全部已读
            </button>
          </div>
          {listQuery.error ? (
            <p className="px-4 py-6 text-center text-sm text-rose-600">
              加载失败：{listQuery.error instanceof Error ? listQuery.error.message : "未知错误"}
              <button
                type="button"
                className="ml-2 font-medium underline"
                onClick={() => listQuery.refetch()}
              >
                重试
              </button>
            </p>
          ) : listQuery.isLoading ? (
            <p className="px-4 py-6 text-center text-sm text-slate-500">加载中…</p>
          ) : items.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-500">暂无消息。</p>
          ) : (
            <ul className="max-h-80 divide-y divide-slate-100 overflow-y-auto">
              {items.map((item) => (
                <li key={item.id} className="px-4 py-3">
                  <Link href="/opportunities?new=1" onClick={() => setOpen(false)} className="block">
                    <p
                      className={`text-sm ${
                        item.type === "sync_failed"
                          ? "font-medium text-rose-700"
                          : item.read_at
                            ? "text-slate-500"
                            : "font-medium text-slate-800"
                      }`}
                    >
                      {item.title}
                    </p>
                    {item.body ? (
                      <p className="mt-1 text-xs leading-5 text-slate-500">{item.body}</p>
                    ) : null}
                    <p className="mono-label mt-1 text-slate-400">
                      {item.type === "sync_failed" ? "订阅同步失败" : "订阅同步"} ·{" "}
                      {formatTime(item.created_at)}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          </div>
        </>
      ) : null}
    </div>
  );
}
