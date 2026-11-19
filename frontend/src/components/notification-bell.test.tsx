import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { NotificationBell } from "./notification-bell";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
  logout: vi.fn(),
  ensureSession: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

const NOTIFICATIONS = {
  items: [
    {
      id: "n1",
      type: "sync_new_rows",
      title: "订阅「政务云」新增 3 条商机",
      body: "抓取 3 条，入库 3 条，其中新增 3 条。",
      resource_type: "opportunity_subscription",
      resource_id: "s1",
      created_at: "2026-10-23T10:00:00+00:00",
      read_at: null,
    },
    {
      id: "n2",
      type: "sync_failed",
      title: "订阅「信创」同步失败",
      body: "107: upstream unavailable",
      resource_type: "opportunity_subscription",
      resource_id: "s2",
      created_at: "2026-10-22T09:00:00+00:00",
      read_at: "2026-10-22T09:30:00+00:00",
    },
  ],
  unread: 1,
};

function renderBell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NotificationBell />
    </QueryClientProvider>,
  );
}

describe("NotificationBell", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    useAuthStore.getState().setSession("token", {
      id: "u1",
      tenant_id: "t1",
      email: "owner@example.com",
      display_name: "演示管理员",
      roles: ["tenant_admin"],
      permissions: ["opportunity:*"],
      tenant_name: "演示投标企业",
    });
  });

  afterEach(() => {
    cleanup();
    useAuthStore.getState().clear();
  });

  it("shows the unread badge and opens the message list", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/notifications/unread-count") return { unread: 1 };
      return NOTIFICATIONS;
    });
    renderBell();

    const bell = await screen.findByRole("button", { name: "消息中心，1 条未读" });
    expect(screen.getByText("1")).toBeInTheDocument();

    fireEvent.click(bell);
    await screen.findByText("订阅「政务云」新增 3 条商机");
    expect(screen.getByText("107: upstream unavailable")).toBeInTheDocument();
    expect(screen.getByText(/订阅同步失败/)).toBeInTheDocument();
  });

  it("marks everything read through the API", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/notifications/unread-count") return { unread: 1 };
      if (path.startsWith("/notifications?")) return NOTIFICATIONS;
      return { updated: 1 };
    });
    renderBell();

    fireEvent.click(await screen.findByRole("button", { name: "消息中心，1 条未读" }));
    fireEvent.click(await screen.findByText("全部已读"));

    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(([path, init]) => path === "/notifications/read" && init?.method === "POST");
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ all: true });
    });
  });

  it("stays hidden without opportunity read permission", async () => {
    useAuthStore.getState().setUser({
      id: "u2",
      tenant_id: "t1",
      email: "guest@example.com",
      display_name: "访客",
      roles: [],
      permissions: ["project:read"],
      tenant_name: "演示投标企业",
    });
    const { container } = renderBell();
    expect(container).toBeEmptyDOMElement();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
