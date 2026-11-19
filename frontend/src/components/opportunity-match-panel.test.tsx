import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api";
import { OpportunityMatchBadges, OpportunityMatchPanel } from "./opportunity-match-panel";

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  }
  return {
    ApiError: MockApiError,
    apiFetch: vi.fn(),
    apiFetchBlob: vi.fn(),
    logout: vi.fn(),
    ensureSession: vi.fn(),
  };
});

const MATCH = {
  id: "m1",
  opportunity_id: "o1",
  match_score: 87,
  hit_groups: ["keyword", "company_industry", "qualification"],
  reasons: ["命中订阅：关键词 数据中心", "资格自检通过（1/1）"],
  risks: [],
  qualified: true,
  qualification_gaps: [],
  decision: "recommend",
  engine_version: "heuristic-rules-v1",
  computed_at: "2026-11-01T09:00:00+00:00",
  project_id: "p1",
};

const MATCH_WITH_GAP = {
  ...MATCH,
  match_score: 40,
  hit_groups: ["company_region"],
  reasons: [],
  risks: ["资格自检存在 1 项缺口"],
  qualified: false,
  decision: "review",
  qualification_gaps: [
    {
      requirement: "具有信息系统集成及服务资质二级及以上",
      category: "QUALIFICATION",
      status: "MISSING",
      reason: "企业资质库未找到匹配项",
      level: "二级",
      quantity: null,
    },
  ],
  project_id: null,
};

function renderPanel(canWrite = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OpportunityMatchPanel opportunityId="o1" canWrite={canWrite} />
    </QueryClientProvider>,
  );
}

describe("OpportunityMatchPanel", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });
  afterEach(() => cleanup());

  it("renders persisted score, hit groups and recommendation", async () => {
    vi.mocked(apiFetch).mockResolvedValue(MATCH);
    renderPanel();
    await screen.findByText("推荐立项");
    expect(screen.getByText(/87/)).toBeInTheDocument();
    expect(screen.getByText("命中 关键词")).toBeInTheDocument();
    expect(screen.getByText("命中 企业资质")).toBeInTheDocument();
    expect(screen.getByText(/启发式规则打分/)).toBeInTheDocument();
    expect(screen.getByText(/已自动立项/)).toBeInTheDocument();
  });

  it("recomputes through POST and replaces the displayed match", async () => {
    vi.mocked(apiFetch).mockImplementation(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") return { ...MATCH, match_score: 91 };
      return MATCH;
    });
    renderPanel();
    await screen.findByText(/87/);
    fireEvent.click(screen.getByRole("button", { name: /重算/ }));
    await waitFor(() => expect(screen.getByText(/91/)).toBeInTheDocument());
    expect(vi.mocked(apiFetch)).toHaveBeenCalledWith("/opportunities/o1/match", { method: "POST" });
  });

  it("shows qualification gaps and review marker", async () => {
    vi.mocked(apiFetch).mockResolvedValue(MATCH_WITH_GAP);
    renderPanel();
    await screen.findByText("人工复核");
    expect(screen.getByText("资格缺口")).toBeInTheDocument();
    expect(screen.getByText("具有信息系统集成及服务资质二级及以上")).toBeInTheDocument();
    expect(screen.getByText("企业资质库未找到匹配项")).toBeInTheDocument();
    expect(screen.getByText("缺失")).toBeInTheDocument();
  });

  it("offers a manual recompute when no persisted match exists", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(
      new ApiError(404, "not_found", "not found"),
    );
    vi.mocked(apiFetch).mockResolvedValueOnce({ ...MATCH, match_score: 88 });
    renderPanel();
    await screen.findByText("尚未计算");
    fireEvent.click(screen.getByRole("button", { name: /立即计算匹配/ }));
    await screen.findByText(/88/);
  });

  it("renders compact badges for opportunity cards", () => {
    render(<OpportunityMatchBadges match={MATCH} />);
    expect(screen.getByText("推荐立项")).toBeInTheDocument();
    expect(screen.getByText("匹配 87")).toBeInTheDocument();
  });
});
