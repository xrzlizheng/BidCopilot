import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import { BidderVerificationPanel } from "./bidder-verification-panel";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

const BATCH = {
  status: "ok",
  data: [
    {
      entName: "华为技术有限公司",
      creditCode: "914403001922038216",
      regStatus: "存续（在营、开业、在册）",
      legalPersonName: "赵明路",
      entScale: "大型",
      province: "广东",
      city: "深圳市",
    },
  ],
  source: "keeprisk-mcp",
  fetched_at: "2026-10-31T00:00:00Z",
  order_no: "order-batch-1",
  charged: true,
  api_calls: 0,
  billing_units: 0,
  warnings: [],
  error: null,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BidderVerificationPanel", () => {
  it("verifies the seeded bidders and lists unmatched names", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(BATCH);
    render(
      <BidderVerificationPanel ownCompanyName="华为技术有限公司" competitor="未匹配企业" />,
    );

    fireEvent.click(screen.getByRole("button", { name: "批量核验" }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    const [url, init] = vi.mocked(apiFetch).mock.calls[0];
    expect(url).toBe("/company-intel/batch");
    expect(JSON.parse(String(init?.body)).ent_names).toEqual([
      "华为技术有限公司",
      "未匹配企业",
    ]);
    expect(await screen.findByText("914403001922038216")).toBeTruthy();
    expect(screen.getByText(/未返回（名称需核对）：未匹配企业/)).toBeTruthy();

    const row = screen.getByText("914403001922038216").closest("tr");
    expect(row).toBeTruthy();
    fireEvent.click(row as HTMLTableRowElement);
    expect(await screen.findByText(/投标主体情报/)).toBeTruthy();
  });

  it("compares 2-5 bidders and surfaces unknown risk", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      ...BATCH,
      data: {
        ranking: [
          { entity: "华为技术有限公司", rank: 1, score: 93.7, risk_level: "unknown" },
        ],
        companies: [
          {
            entity: "华为技术有限公司",
            credit_score: { scores: { composite: { score: 93.7, level: "A" } } },
            risk_digest: {
              risk_summary: { overall_level: "green", red_count: 0, yellow_count: 0 },
            },
          },
        ],
        count: 1,
      },
    });
    render(
      <BidderVerificationPanel ownCompanyName="华为技术有限公司" competitor="中兴通讯股份有限公司" />,
    );

    fireEvent.click(screen.getByRole("button", { name: "企业对比（2–5 家）" }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    const [url, init] = vi.mocked(apiFetch).mock.calls[0];
    expect(url).toBe("/company-intel/compare");
    expect(JSON.parse(String(init?.body)).ent_names).toEqual([
      "华为技术有限公司",
      "中兴通讯股份有限公司",
    ]);
    expect(await screen.findByText("93.7")).toBeTruthy();
    expect(screen.getByText(/风险等级为 unknown/)).toBeTruthy();
  });
});
