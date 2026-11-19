import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import { ConfirmProvider } from "@/components/confirm-dialog";
import { useAuthStore } from "@/lib/auth-store";
import SettingsPage from "@/app/(app)/settings/page";

const push = vi.fn();
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace, prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => "/settings",
}));

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
  apiDownload: vi.fn(),
  logout: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  },
}));

// 决策参数卡有独立的数据依赖，与 D7 无关，这里隔离掉
vi.mock("@/components/decision-settings-card", () => ({
  DecisionSettingsCard: () => <div data-testid="decision-settings" />,
}));

const MODELS = [
  {
    purpose: "main",
    scope: "tenant",
    provider: "openai",
    base_url: "https://api.example.com/v1",
    model: "deepseek-flash",
    enabled: true,
    effective_base_url: "https://api.example.com/v1",
    effective_model: "deepseek-flash",
    configured: true,
    overridden: true,
    key_last4: "abcd",
    credential_scope: "tenant",
  },
  {
    purpose: "judge",
    scope: "tenant",
    provider: "openai",
    base_url: null,
    model: null,
    enabled: false,
    effective_base_url: "http://judge:3000/v1",
    effective_model: "judge",
    configured: true,
    overridden: false,
    key_last4: null,
    credential_scope: "tenant",
  },
];

const PERSONAL = MODELS.map((row) => ({ ...row, scope: "personal" }));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ConfirmProvider>
      <QueryClientProvider client={client}>
      <SettingsPage />
      </QueryClientProvider>
    </ConfirmProvider>,
  );
}

function cardOf(title: string): HTMLElement {
  const heading = screen.getByRole("heading", { name: title });
  // Card 组件根节点是 <section>，以此确定卡片边界
  const card = heading.closest("section");
  if (!card) throw new Error(`找不到卡片：${title}`);
  return card as HTMLElement;
}

describe("SettingsPage 模型配置错误消息归属", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    push.mockReset();
    replace.mockReset();
    useAuthStore.getState().setSession("token", {
      id: "u1",
      tenant_id: "t1",
      email: "owner@example.com",
      display_name: "演示管理员",
      roles: ["tenant_admin"],
      permissions: [
        "model:manage",
        "model:use",
        "model:policy:manage",
        "tenant:settings:manage",
      ],
      tenant_name: "演示投标企业",
    });
    vi.mocked(apiFetch).mockImplementation(async (path: string, init: RequestInit = {}) => {
      if (path === "/system/model-configs") return MODELS;
      if (path === "/system/model-configs/personal") return PERSONAL;
      if (path === "/system/model-policy")
        return { byok_enabled: false, allow_personal_key_for_enterprise_data: false };
      if (init.method === "PATCH") {
        const { ApiError } = await import("@/lib/api");
        throw new ApiError(
          422,
          "validation_error",
          "模型网关地址不能指向内网、环回或链路本地地址",
        );
      }
      return {};
    });
  });

  afterEach(() => {
    cleanup();
    useAuthStore.getState().clear();
  });

  it("租户模型保存失败时，错误消息出现在模型卡片（不串到账号安全页签）", async () => {
    renderPage();

    // 切到「AI 模型」页签并等表单渲染（而不是「加载中…」）
    fireEvent.click(await screen.findByRole("button", { name: /AI 模型/ }));
    await screen.findAllByPlaceholderText("模型名，例如 deepseek-flash");
    const modelCard = cardOf("AI 模型配置");
    const gateway = within(modelCard).getAllByPlaceholderText(
      "网关地址，例如 http://host:3000/v1",
    )[0];
    fireEvent.change(gateway, { target: { value: "http://10.0.0.5:8080/v1" } });
    fireEvent.click(within(modelCard).getByRole("button", { name: "保存主模型" }));

    // 回归 D7：错误提示必须落在触发它的卡片里
    await waitFor(() => {
      expect(within(modelCard).getByText(/不能指向内网/)).toBeInTheDocument();
    });
    // 页签化后「修改密码」只在账号安全页签渲染；此处不应存在，
    // 因此模型报错在结构上就不可能串到改密卡片。
    expect(screen.queryByRole("heading", { name: "修改密码" })).toBeNull();
  });

  it("修改密码校验错误只出现在修改密码卡片（不串到模型页签）", async () => {
    renderPage();

    // 默认页签是「账号安全」，修改密码卡片在此页签内
    await screen.findByRole("heading", { name: "修改密码" });
    const passwordCard = cardOf("修改密码");

    const passwordInputs = [
      within(passwordCard).getByPlaceholderText("当前密码 *"),
      within(passwordCard).getByPlaceholderText("新密码（≥8 位）*"),
      within(passwordCard).getByPlaceholderText("确认新密码 *"),
    ];
    fireEvent.change(passwordInputs[0], { target: { value: "old" } });
    fireEvent.change(passwordInputs[1], { target: { value: "NewPassw0rd!" } });
    fireEvent.change(passwordInputs[2], { target: { value: "MismatchPass!" } });
    fireEvent.click(within(passwordCard).getByRole("button", { name: "修改密码" }));

    await waitFor(() => {
      expect(within(passwordCard).getByText("两次输入的新密码不一致。")).toBeInTheDocument();
    });
    // 模型卡片不在本页签，改密提示不会串过去
    expect(screen.queryByRole("heading", { name: "AI 模型配置" })).toBeNull();
  });
});
