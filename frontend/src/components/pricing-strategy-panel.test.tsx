import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import { PricingStrategyPanel, type PriceBandResponse, type PricingStrategy } from "./pricing-strategy-panel";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

const STRATEGY: PricingStrategy = {
  id: "strategy-1",
  project_id: "p1",
  engine_version: "pricing-v1",
  created_at: "2026-11-01T10:00:00+00:00",
  status: "insufficient_data",
  disclaimer: "启发式估算，非模型预测，非中标承诺；仅供报价决策参考。",
  missing: ["同类项目中标价/预算比样本不足（需至少 5 条，当前 2 条）"],
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
    direct_rate_variance: "0.030000",
    missing: [],
    source_labels: { rates: "tenant-setting:pricing_cost_model" },
    note: "直接成本率仅作为租户校准基准。",
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
    notes: ["同类项目已决标样本不足，不输出赢率估计"],
    missing: ["同类项目中标价/预算比样本不足"],
    disclaimer: "启发式估算，非模型预测，非中标承诺；仅供报价决策参考。",
  },
  candidate: {
    quote_amount: "50000.00",
    quote_ratio: "0.961538",
    discount_rate: "0.038462",
    net_margin: "0.240000",
    total_cost: "38000.00",
    band_position: "历史价格带不足，无法判断价格位置",
    win_rate: {
      status: "insufficient_data",
      rate: null,
      basis: "历史价格带样本不足，无法估计赢率",
      heuristic: true,
      disclaimer: "启发式估算，非模型预测，非中标承诺；仅供报价决策参考。",
    },
  },
  tiers: [
    {
      tier: "balanced",
      label: "均衡",
      status: "ok",
      price: "39189.19",
      target_margin: "0.080000",
      net_margin: "0.080000",
      discount_rate: "0.246362",
      win_rate: null,
      reason: "按目标净利率 8.00% 反解。",
      risks: ["历史样本不足，无法给出赢率估计"],
    },
  ],
  sensitivity: {
    status: "ok",
    base_quote: "50000.00",
    base_source: "input",
    rows: [
      {
        change_rate: "-0.080000",
        quote_amount: "46000.00",
        net_margin: "0.189565",
        discount_rate: "0.115385",
        win_rate: null,
        win_rate_status: "insufficient_data",
        below_cost: false,
        band_position: "历史价格带不足，无法判断价格位置",
      },
    ],
    missing: [],
    disclaimer: "启发式估算，非模型预测，非中标承诺；仅供报价决策参考。",
  },
  assumptions: {
    target_margins: { conservative: "0.120000", balanced: "0.080000", aggressive: "0.040000" },
    target_margins_source: "tool-default",
    note: "目标净利率档位是工具默认假设。",
  },
  narrative: {
    text: "当前数据不足，不能给出可复核的报价结论。未使用默认值补齐。本说明为启发式提示，不是模型预测，也不是中标承诺。",
    source: "template",
    disclaimer: "启发式估算，非模型预测，非中标承诺；仅供报价决策参考。",
  },
};

const BAND: PriceBandResponse = {
  project_id: "p1",
  budget_amount: "52000.00",
  budget_source: "tender-constraint",
  industry: ["软件和信息技术服务业"],
  industry_source: "opportunity",
  price_band: STRATEGY.price_band,
  hypothesis: "启发式估算，非模型预测，非中标承诺。",
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PricingStrategyPanel projectId="p1" />
    </QueryClientProvider>,
  );
}

describe("PricingStrategyPanel", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  afterEach(() => cleanup());

  it("shows insufficient_data honestly and never claims a win guarantee", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.endsWith("/pricing/strategy/latest")) return STRATEGY;
      if (path.endsWith("/pricing/price-band")) return BAND;
      throw new Error(`unexpected path: ${path}`);
    });

    renderPanel();

    expect((await screen.findAllByText("数据不足")).length).toBeGreaterThan(0);
    expect(screen.getByText("同类项目中标价/预算比样本不足（需至少 5 条，当前 2 条）")).toBeTruthy();
    expect(screen.getByText(/未使用默认值补齐/)).toBeTruthy();
    expect(screen.getByText(/非中标承诺/)).toBeTruthy();
    expect(screen.getByText(/P25/)).toBeTruthy();
  });

  it("posts candidate inputs and renders the generated strategy", async () => {
    const okStrategy: PricingStrategy = {
      ...STRATEGY,
      status: "ok",
      missing: [],
      narrative: {
        text: "均衡建议价 39189.19 元；该赢率为启发式估计，不是模型预测，也不构成中标承诺。",
        source: "template",
        disclaimer: STRATEGY.disclaimer,
      },
      tiers: [
        {
          tier: "conservative",
          label: "保守",
          status: "ok",
          price: "41428.57",
          target_margin: "0.120000",
          net_margin: "0.120000",
          discount_rate: "0.203297",
          win_rate: "0.780000",
          reason: "按目标净利率 12.00% 反解。",
          risks: ["价格偏高可能降低竞争力"],
        },
        {
          tier: "balanced",
          label: "均衡",
          status: "ok",
          price: "39189.19",
          target_margin: "0.080000",
          net_margin: "0.080000",
          discount_rate: "0.246362",
          win_rate: "0.800000",
          reason: "按目标净利率 8.00% 反解。",
          risks: ["仍取决于竞争对手报价"],
        },
        {
          tier: "aggressive",
          label: "进取",
          status: "ok",
          price: "37179.49",
          target_margin: "0.040000",
          net_margin: "0.040000",
          discount_rate: "0.285010",
          win_rate: "0.820000",
          reason: "按目标净利率 4.00% 反解。",
          risks: ["毛利空间最薄"],
        },
      ],
    };
    vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path.endsWith("/pricing/strategy") && init?.method === "POST") return okStrategy;
      if (path.endsWith("/pricing/strategy/latest")) return STRATEGY;
      if (path.endsWith("/pricing/price-band")) return BAND;
      throw new Error(`unexpected path: ${path}`);
    });

    renderPanel();
    expect((await screen.findAllByText("数据不足")).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByPlaceholderText("可选；不填则用均衡档测算敏感性"), {
      target: { value: "50000" },
    });
    fireEvent.change(screen.getByPlaceholderText("无则填 0"), {
      target: { value: "0" },
    });
    fireEvent.change(screen.getByPlaceholderText("须与租户人力单价配置一致"), {
      target: { value: "工程师" },
    });
    fireEvent.change(screen.getByPlaceholderText("如 20"), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成报价策略" }));

    await waitFor(() => expect(screen.getByText("报价策略已生成。")).toBeTruthy());
    expect(vi.mocked(apiFetch)).toHaveBeenCalledWith(
      "/projects/p1/pricing/strategy",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.getByText("保守")).toBeTruthy();
    expect(screen.getByText("均衡")).toBeTruthy();
    expect(screen.getByText("进取")).toBeTruthy();
    expect(screen.getByText(/不是模型预测，也不构成中标承诺/)).toBeTruthy();
  });
});
