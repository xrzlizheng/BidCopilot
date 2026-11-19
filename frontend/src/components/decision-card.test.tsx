import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import type { DecisionCard, DecisionEvidence } from "@/lib/types";
import { DecisionCardPanel } from "./decision-card";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

const CARD: DecisionCard = {
  id: "run-1",
  project_id: "p1",
  run_no: 2,
  engine_mode: "full",
  engine_version: "decision-v2",
  constraint_version: 1,
  score: 78.5,
  computed_status: "Conditional Go",
  status: "Conditional Go",
  human_status: null,
  dimensions: [
    {
      key: "competition",
      label: "竞争情报（历史中标/胜率）",
      score: 82.8,
      weight: 10,
      scored: true,
      reason: "同采购人历史中标供应商 2 家",
      source: "award-records",
    },
  ],
  vetoes: [],
  thresholds: {},
  source_labels: { competition: "award-records" },
  inputs: null,
  created_at: "2026-10-31T00:00:00Z",
  confirmed_at: null,
  override_reason: null,
};

function evidence(overrides: Partial<DecisionEvidence> = {}): DecisionEvidence {
  return {
    project_id: "p1",
    project_name: "某市政府数据中台运维",
    buyer: "某市大数据局",
    buyer_match_mode: "exact",
    window_days: 730,
    min_samples: { buyer_awards: 3 },
    generated_at: "2026-10-31T00:00:00Z",
    engine_version: "decision-evidence-v1",
    items: [
      {
        key: "buyer_history",
        label: "采购人历史中标",
        scored: true,
        reason: "采购人「某市大数据局」精确匹配：730 天窗口内 4 条中标记录",
        sources: ["award_records", "opportunities"],
        scope: {},
        metrics: { record_count: 4, median_win_amount: "975000.00" },
        hint: null,
        notes: [],
      },
      {
        key: "our_win_rate",
        label: "我方历史胜率",
        scored: false,
        reason: "同采购人已决标项目仅 1 个（需 ≥3 个），样本不足，不输出胜率",
        sources: ["bid_projects"],
        scope: {},
        metrics: { won: 1, lost: 0, decided: 1 },
        hint: null,
        notes: [],
      },
    ],
    missing: ["我方历史胜率：样本不足"],
    notes: ["取数窗口 730 天（award_at），单次采样上限 500 条。"],
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DecisionCardPanel projectId="p1" />
    </QueryClientProvider>,
  );
}

describe("DecisionCardPanel 决策证据链", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("展示证据链、数据来源与样本不足提示", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.endsWith("/decision-evidence")) return evidence();
      return CARD;
    });
    renderPanel();

    expect(await screen.findByText("决策证据链")).toBeTruthy();
    // 可计分项
    expect(screen.getByText("采购人历史中标")).toBeTruthy();
    expect(screen.getAllByText("已计分").length).toBeGreaterThan(0);
    // 来源表名透出，便于回溯
    expect(screen.getByText("award_records")).toBeTruthy();
    // 指标中文化与金额不被吞掉
    expect(screen.getByText(/中位数/)).toBeTruthy();
    expect(screen.getByText("975000.00")).toBeTruthy();
    // 样本不足必须显式标注，不能显示成 0
    expect(screen.getByText("样本不足")).toBeTruthy();
    expect(screen.getByText(/以下证据不足，决策卡对应维度不计分/)).toBeTruthy();
    // 竞争维度出现在决策卡表格里
    expect(screen.getByText("竞争情报（历史中标/胜率）")).toBeTruthy();
    expect(screen.getByText("82.8")).toBeTruthy();
  });

  it("模糊匹配时明确提示仅作提示、不计分", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.endsWith("/decision-evidence")) {
        return evidence({ buyer_match_mode: "fuzzy", items: [], missing: [] });
      }
      return CARD;
    });
    renderPanel();

    expect(await screen.findByText(/采购人名称只做到模糊匹配/)).toBeTruthy();
    expect(screen.getByText("采购人匹配 fuzzy")).toBeTruthy();
  });

  it("证据链接口失败时给出可解释的降级文案，不崩掉决策卡", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.endsWith("/decision-evidence")) throw new Error("404");
      return CARD;
    });
    renderPanel();

    expect(
      await screen.findByText(/证据链不可用（可能尚未关联供应商机或缺少历史中标记录）/),
    ).toBeTruthy();
    // 决策卡本身仍然渲染
    expect(screen.getByText("投标决策卡")).toBeTruthy();
  });
});
