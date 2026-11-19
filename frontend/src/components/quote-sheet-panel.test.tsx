import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import type { PricingStrategy } from "./pricing-strategy-panel";
import { QuoteSheetPanel, type PriceQuote } from "./quote-sheet-panel";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiDownload: vi.fn(),
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

const QUOTE: PriceQuote = {
  id: "q1",
  project_id: "p1",
  strategy_id: "s1",
  quote_number: "BJ-20261117-ABCD1234",
  project_name: "某市政信息化项目",
  buyer_name: "某市大数据局",
  supplier_name: "测试科技有限公司",
  total_amount: "50000.00",
  currency: "CNY",
  amount_in_words: "伍万元整",
  items: [
    {
      name: "软件开发",
      spec: "含部署",
      quantity: "1",
      unit: "项",
      unit_price: "30000.00",
      amount: "30000.00",
    },
    {
      name: "运维服务",
      spec: "",
      quantity: "2",
      unit: "年",
      unit_price: "10000.00",
      amount: "20000.00",
    },
  ],
  tier: null,
  price_source: "input",
  valid_until: "2027-02-15T00:00:00+00:00",
  notes: "含税总价",
  created_at: "2026-11-17T10:00:00+00:00",
};

const STRATEGY: PricingStrategy = {
  status: "ok",
  disclaimer: "启发式估算，非模型预测，非中标承诺。",
  missing: [],
  budget: { amount: "52000.00", source: "tender-constraint" },
  industry: { values: ["软件和信息技术服务业"], source: "opportunity" },
  cost: {
    status: "ok",
    quote_amount: "50000.00",
    labor_cost: "24000.00",
    other_direct_cost: "5000.00",
    direct_cost: "29000.00",
    indirect_cost: "6000.00",
    tax_cost: "3000.00",
    total_cost: "38000.00",
    net_profit: "12000.00",
    net_margin: "0.240000",
    direct_cost_ratio: "0.580000",
    direct_rate_variance: null,
    missing: [],
    source_labels: { rates: "tenant-setting:pricing_cost_model" },
    note: "",
  },
  price_band: {
    status: "insufficient_data",
    sample_count: 2,
    p25: null,
    p50: null,
    p75: null,
    p25_amount: null,
    p50_amount: null,
    p75_amount: null,
    decided_count: 2,
    won_count: 1,
    lost_count: 1,
    price_loss_count: 1,
    base_win_rate: null,
    notes: [],
    missing: [],
    disclaimer: "启发式估算，非模型预测，非中标承诺。",
  },
  candidate: null,
  tiers: [
    {
      tier: "conservative",
      label: "保守",
      status: "ok",
      price: "41428.57",
      target_margin: "0.120000",
      reason: "按目标净利率 12.00% 反解。",
      risks: [],
    },
    {
      tier: "balanced",
      label: "均衡",
      status: "ok",
      price: "39189.19",
      target_margin: "0.080000",
      reason: "按目标净利率 8.00% 反解。",
      risks: [],
    },
    {
      tier: "aggressive",
      label: "进取",
      status: "ok",
      price: "37179.49",
      target_margin: "0.040000",
      reason: "按目标净利率 4.00% 反解。",
      risks: [],
    },
  ],
  sensitivity: null,
  assumptions: {
    target_margins: { conservative: "0.120000", balanced: "0.080000", aggressive: "0.040000" },
    target_margins_source: "tool-default",
    note: "",
  },
  narrative: { text: "策略叙述。", source: "template", disclaimer: "启发式估算。" },
};

function mockApi() {
  vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/pricing/quote") && init?.method === "POST") return QUOTE;
    if (path.endsWith("/pricing/quote/latest")) return QUOTE;
    if (path.endsWith("/pricing/strategy/latest")) return STRATEGY;
    throw new Error(`unexpected path: ${path}`);
  });
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QuoteSheetPanel projectId="p1" />
    </QueryClientProvider>,
  );
}

function lastQuotePostBody() {
  const call = vi
    .mocked(apiFetch)
    .mock.calls.find(([path, init]) => path.endsWith("/pricing/quote") && init?.method === "POST");
  expect(call).toBeTruthy();
  return JSON.parse(String(call?.[1]?.body));
}

describe("QuoteSheetPanel", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiDownload).mockReset();
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:mock"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => cleanup());

  it("404 时提示先生成策略，不渲染报价数据", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(404, "not_found", "尚未生成报价单"));

    renderPanel();

    expect(await screen.findByText(/尚未生成报价单/)).toBeTruthy();
    expect(screen.queryByText("伍万元整")).toBeNull();
  });

  it("生成报价单时提交档位与自定义总价，并渲染大写金额与明细", async () => {
    mockApi();

    renderPanel();
    expect(await screen.findByText("BJ-20261117-ABCD1234")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("可选；填写后覆盖档位价"), {
      target: { value: "50000" },
    });
    fireEvent.change(screen.getByPlaceholderText("如：含税总价"), {
      target: { value: "含税总价" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成报价单" }));

    await waitFor(() =>
      expect(screen.getByText("报价单 BJ-20261117-ABCD1234 已生成。")).toBeTruthy(),
    );
    const body = lastQuotePostBody();
    expect(body.quote_amount).toBe("50000");
    expect(body.tier).toBe("balanced");
    expect(body.notes).toBe("含税总价");
    expect(body.items).toEqual([]);
    expect(body.supplier_name).toBeNull();
    expect(screen.getByText("伍万元整")).toBeTruthy();
    expect(screen.getByText("软件开发")).toBeTruthy();
    expect(screen.getByText("运维服务")).toBeTruthy();
    expect(screen.getByText("显式报价")).toBeTruthy();
  });

  it("导出走 POST 下载通道", async () => {
    mockApi();
    vi.mocked(apiDownload).mockResolvedValue({
      blob: new Blob(["docx"]),
      filename: "quote_BJ-20261117-ABCD1234.docx",
    });

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "导出报价单" }));

    await waitFor(() => {
      expect(vi.mocked(apiDownload)).toHaveBeenCalledWith(
        "/projects/p1/pricing/quote/export/docx",
        {},
      );
    });
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
  });

  it("明细合计与预期总额不一致时禁用生成并给出琥珀提示，修正后恢复", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.endsWith("/pricing/quote/latest")) {
        throw new ApiError(404, "not_found", "尚未生成报价单");
      }
      if (path.endsWith("/pricing/strategy/latest")) return STRATEGY;
      throw new Error(`unexpected path: ${path}`);
    });

    renderPanel();
    expect(await screen.findByText(/尚未生成报价单/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "添加明细行" }));
    fireEvent.change(screen.getByLabelText("明细第1行名称"), { target: { value: "软件开发" } });
    fireEvent.change(screen.getByLabelText("明细第1行数量"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("明细第1行单位"), { target: { value: "项" } });
    fireEvent.change(screen.getByLabelText("明细第1行单价"), { target: { value: "30000" } });

    // 预期总额取均衡档策略建议价 39189.19，明细合计 30000 不一致
    expect(await screen.findByText(/明细合计 .* 与预期总额 .* 不一致/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成报价单" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("明细第1行单价"), { target: { value: "39189.19" } });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "生成报价单" })).not.toBeDisabled(),
    );
    expect(screen.queryByText(/与预期总额 .* 不一致/)).toBeNull();
  });

  it("填写供应商后 POST body 含 supplier_name", async () => {
    mockApi();

    renderPanel();
    expect(await screen.findByText("BJ-20261117-ABCD1234")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("供应商名称"), {
      target: { value: "测试科技有限公司" },
    });
    fireEvent.change(screen.getByPlaceholderText("可选；填写后覆盖档位价"), {
      target: { value: "50000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成报价单" }));

    await waitFor(() =>
      expect(screen.getByText("报价单 BJ-20261117-ABCD1234 已生成。")).toBeTruthy(),
    );
    const body = lastQuotePostBody();
    expect(body.supplier_name).toBe("测试科技有限公司");
    expect(body.items).toEqual([]);
  });

  it("编辑明细行合计一致时随 body 提交字符串数量与单价", async () => {
    mockApi();

    renderPanel();
    expect(await screen.findByText("BJ-20261117-ABCD1234")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("可选；填写后覆盖档位价"), {
      target: { value: "50000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加明细行" }));
    fireEvent.change(screen.getByLabelText("明细第1行名称"), { target: { value: "软件开发" } });
    fireEvent.change(screen.getByLabelText("明细第1行规格"), { target: { value: "含部署" } });
    fireEvent.change(screen.getByLabelText("明细第1行数量"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("明细第1行单位"), { target: { value: "项" } });
    fireEvent.change(screen.getByLabelText("明细第1行单价"), { target: { value: "25000" } });

    expect(screen.queryByText(/与预期总额 .* 不一致/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "生成报价单" }));

    await waitFor(() =>
      expect(screen.getByText("报价单 BJ-20261117-ABCD1234 已生成。")).toBeTruthy(),
    );
    const body = lastQuotePostBody();
    expect(body.items).toEqual([
      { name: "软件开发", spec: "含部署", quantity: "2", unit: "项", unit_price: "25000" },
    ]);
  });
});