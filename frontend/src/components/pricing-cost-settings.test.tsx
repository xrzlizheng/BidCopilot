import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { PricingCostSettings, type PricingSettings } from "./pricing-cost-settings";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
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

const SETTINGS: PricingSettings = {
  cost_model: {
    direct_rate: "0.55",
    indirect_rate: "0.15",
    tax_rate: "0.06",
    labor_rates: { 工程师: "1200" },
  },
  target_margins: { conservative: "0.12", balanced: "0.08", aggressive: "0.04" },
  source: { cost_model: "tenant-setting", target_margins: "tool-default" },
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PricingCostSettings />
    </QueryClientProvider>,
  );
}

async function expand() {
  fireEvent.click(screen.getByRole("button", { name: /报价成本参数/ }));
}

function setAdmin() {
  useAuthStore.getState().setSession("token", {
    id: "u1",
    tenant_id: "t1",
    email: "owner@example.com",
    display_name: "演示管理员",
    roles: ["tenant_admin"],
    permissions: ["tenant:settings:manage"],
    tenant_name: "演示投标企业",
  });
}

describe("PricingCostSettings", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    setAdmin();
  });

  afterEach(() => {
    cleanup();
    useAuthStore.getState().clear();
  });

  it("GET 渲染默认值：三率、人力单价行与三档目标净利率", async () => {
    vi.mocked(apiFetch).mockResolvedValue(SETTINGS);

    renderCard();
    await expand();

    expect(await screen.findByLabelText("直接成本率")).toHaveValue(0.55);
    expect(screen.getByLabelText("间接费率")).toHaveValue(0.15);
    expect(screen.getByLabelText("税率")).toHaveValue(0.06);
    expect(screen.getByLabelText("人力岗位第1行")).toHaveValue("工程师");
    expect(screen.getByLabelText("人力日单价第1行")).toHaveValue("1200");
    expect(screen.getByLabelText("保守档目标净利率")).toHaveValue(0.12);
    expect(screen.getByLabelText("均衡档目标净利率")).toHaveValue(0.08);
    expect(screen.getByLabelText("进取档目标净利率")).toHaveValue(0.04);
    expect(vi.mocked(apiFetch)).toHaveBeenCalledWith("/system/pricing-settings");
  });

  it("修改率并新增人力行后，PUT 全量提交两块且形状符合契约", async () => {
    const updated: PricingSettings = {
      ...SETTINGS,
      cost_model: {
        ...SETTINGS.cost_model,
        direct_rate: "0.6",
        labor_rates: { 工程师: "1200", 架构师: "1800" },
      },
    };
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/system/pricing-settings" && init?.method === "PUT") return updated;
      if (path === "/system/pricing-settings") return SETTINGS;
      throw new Error(`unexpected path: ${path}`);
    });

    renderCard();
    await expand();
    expect(await screen.findByLabelText("直接成本率")).toHaveValue(0.55);

    fireEvent.change(screen.getByLabelText("直接成本率"), { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: "添加人力单价行" }));
    fireEvent.change(screen.getByLabelText("人力岗位第2行"), { target: { value: "架构师" } });
    fireEvent.change(screen.getByLabelText("人力日单价第2行"), { target: { value: "1800" } });
    fireEvent.click(screen.getByRole("button", { name: "保存成本参数" }));

    await waitFor(() =>
      expect(screen.getByText("报价成本参数已保存，下一次报价策略测算生效。")).toBeTruthy(),
    );
    const call = vi
      .mocked(apiFetch)
      .mock.calls.find(([path, init]) => path === "/system/pricing-settings" && init?.method === "PUT");
    expect(call).toBeTruthy();
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body.cost_model).toEqual({
      direct_rate: "0.6",
      indirect_rate: "0.15",
      tax_rate: "0.06",
      labor_rates: { 工程师: "1200", 架构师: "1800" },
    });
    expect(body.target_margins).toEqual({
      conservative: "0.12",
      balanced: "0.08",
      aggressive: "0.04",
    });
  });

  it("PUT 返回 422 时原样显示后端 message", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/system/pricing-settings" && init?.method === "PUT") {
        throw new ApiError(422, "validation_error", "税率必须在 0–1 之间");
      }
      if (path === "/system/pricing-settings") return SETTINGS;
      throw new Error(`unexpected path: ${path}`);
    });

    renderCard();
    await expand();
    expect(await screen.findByLabelText("直接成本率")).toHaveValue(0.55);

    fireEvent.click(screen.getByRole("button", { name: "保存成本参数" }));

    expect(await screen.findByText("税率必须在 0–1 之间")).toBeTruthy();
  });

  it("无 tenant:settings:manage 权限时只读展示", async () => {
    useAuthStore.getState().setUser({
      id: "u2",
      tenant_id: "t1",
      email: "viewer@example.com",
      display_name: "只读成员",
      roles: ["bid_engineer"],
      permissions: ["project:read"],
      tenant_name: "演示投标企业",
    });
    vi.mocked(apiFetch).mockResolvedValue(SETTINGS);

    renderCard();
    await expand();

    expect(await screen.findByLabelText("直接成本率")).toBeDisabled();
    expect(screen.getByLabelText("人力岗位第1行")).toBeDisabled();
    expect(screen.getByLabelText("均衡档目标净利率")).toBeDisabled();
    expect(screen.getByText("只读")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存成本参数" })).toBeNull();
    expect(screen.queryByRole("button", { name: "添加人力单价行" })).toBeNull();
  });
});