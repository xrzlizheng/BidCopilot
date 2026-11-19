import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, apiFetchBlob } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { ContractReviewCard } from "./contract-review-card";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
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

// 列表端点按 API 参考只返回审查概要（不含 findings/disclaimer）。
const LIST = [
  {
    id: "rev-1",
    project_id: "p1",
    filename: "contract.txt",
    size_bytes: 120,
    file_hash: "abc",
    status: "completed",
    summary_json: { clauses: 1, findings: { high: 0, medium: 2, low: 1 } },
    created_by: "u1",
    created_at: "2026-10-28T10:00:00+00:00",
  },
];

const DETAIL = {
  ...LIST[0],
  clauses: [{ id: "c1", seq: 1, title: "合同价款", text: "合同总价：95 万元。", category: "price" }],
  disclaimer: "规则与启发式审查结果，非法律意见。",
  template_matches: {
    matched: [{ name: "买卖合同审查模板", basis: "keyword", score: 3 }],
    selected: ["sale"],
  },
  findings: [
    {
      id: "f1",
      clause_id: "c1",
      kind: "risk",
      rule_id: null,
      severity: "medium",
      message: "付款节点缺失验收标准",
      legal_basis: "《中华人民共和国民法典》第五百八十五条",
      evidence_span: { excerpt: "合同总价", quote_verified: true, char_start: 0, char_end: 4, legal_basis_verified: true },
      source: "llm",
      status: "pending",
      confirmed_by: null,
      confirmed_at: null,
      confidence: "0.60",
      detection_method: "llm",
      redline: "合同总价：人民币玖拾伍万元整（¥950,000.00）。",
    },
    {
      id: "f2",
      clause_id: "c1",
      kind: "missing",
      rule_id: "CONTRACT-MISS-002",
      severity: "low",
      message: "未约定知识产权归属",
      legal_basis: null,
      evidence_span: {},
      source: "rule",
      status: "confirmed",
      confirmed_by: "u1",
      confirmed_at: "2026-10-28T11:00:00+00:00",
      confidence: "1.00",
      detection_method: "rule",
    },
  ],
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContractReviewCard projectId="p1" />
    </QueryClientProvider>,
  );
}

describe("ContractReviewCard", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetchBlob).mockReset();
    useAuthStore.getState().setSession("token", {
      id: "u1",
      tenant_id: "t1",
      email: "owner@example.com",
      display_name: "演示管理员",
      roles: ["tenant_admin"],
      permissions: ["project:write", "compliance:export"],
      tenant_name: "演示投标企业",
    });
    vi.mocked(apiFetch).mockImplementation(async (path: string, init: RequestInit = {}) => {
      if (init.method === "PATCH") return DETAIL;
      if (path === "/projects/p1/contract-reviews") return LIST;
      if (path.endsWith("/review-templates")) return [];
      if (path === "/projects/p1/contract-reviews/rev-1") return DETAIL;
      return DETAIL;
    });
    vi.mocked(apiFetchBlob).mockResolvedValue(new Blob(["xlsx-bytes"]));
    // jsdom 不实现对象 URL 与锚点导航（下载），这里只关心是否发起了带鉴权的 blob 请求
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:mock"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    useAuthStore.getState().clear();
  });

  it("加载已有审查时按详情接口取 findings，并渲染确认/驳回入口", async () => {
    renderCard();

    // 回归 D1：刷新页面后（列表已有记录、没有刚上传的响应）必须仍能看到 findings
    await screen.findByText("付款节点缺失验收标准");
    expect(screen.getByText("未约定知识产权归属")).toBeInTheDocument();
    // pending 的那条有处置按钮，confirmed 的那条没有
    expect(screen.getByRole("button", { name: "确认" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "驳回" })).toBeInTheDocument();
    expect(screen.getByText("中 2")).toBeInTheDocument();
    expect(screen.getByText("低 1")).toBeInTheDocument();
  });

  it("确认 finding 走 PATCH 且只发状态字段", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "确认" }));

    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(
          ([path, init]) => path.endsWith("/findings/f1") && init?.method === "PATCH",
        );
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ status: "confirmed" });
    });
  });

  it("导出审查清单走带鉴权的 blob 请求，而不是裸链接", async () => {
    renderCard();
    await screen.findByText("付款节点缺失验收标准");

    const exportLink = screen.getByRole("button", { name: "导出审查清单（xlsx）" });
    fireEvent.click(exportLink);

    // 回归 D3：裸 <a href> 不会带 Bearer，必然 401；必须走 apiFetchBlob
    await waitFor(() => {
      expect(vi.mocked(apiFetchBlob)).toHaveBeenCalledWith("/projects/p1/contract-reviews/rev-1/export");
    });
    expect(document.querySelector('a[href*="/contract-reviews/rev-1/export"]')).toBeNull();
  });

  it("渲染置信度/来源/回查徽标与建议改写块（复制走剪贴板）", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    renderCard();

    await screen.findByText("付款节点缺失验收标准");
    expect(screen.getByText("置信 60%")).toBeInTheDocument();
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByText("证据已回查")).toBeInTheDocument();
    expect(screen.getByText("法条已核")).toBeInTheDocument();
    expect(screen.getByText("规则")).toBeInTheDocument();
    expect(screen.getByText("建议改写")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("合同总价：人民币玖拾伍万元整（¥950,000.00）。");
    });
  });

  it("点击证据行自动展开条款面板并高亮摘录片段", async () => {
    renderCard();
    await screen.findByText("付款节点缺失验收标准");

    // 回归 review P2-1：面板是 <details>，定位必须自动展开，否则高亮不可见
    const panelBefore = screen.getByText(/条款原文（/).closest("details");
    expect(panelBefore).not.toHaveAttribute("open");

    fireEvent.click(screen.getByText(/证据：合同总价/));
    const panel = screen.getByText(/条款原文（/).closest("details");
    expect(panel).toHaveAttribute("open");
    // 条款面板展开并定位到第 1 条，摘录命中以 <mark> 高亮
    await waitFor(() => {
      const mark = document.querySelector("mark");
      expect(mark?.textContent).toBe("合同总价");
    });
    expect(screen.getByText(/定位：第 1 条 合同价款/)).toBeInTheDocument();
  });

  it("深审部分批次失败时显示黄条而非静默", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/projects/p1/contract-reviews") return LIST;
      if (path.endsWith("/review-templates")) return [];
      return {
        ...DETAIL,
        summary_json: {
          ...DETAIL.summary_json,
          llm_batches: { total: 3, failed: 1, errors: ["batch#2: LLMResponseError: 解析失败"] },
        },
      };
    });
    renderCard();

    await screen.findByText("付款节点缺失验收标准");
    expect(screen.getByText(/深度审查部分批次失败（1\/3）/)).toBeInTheDocument();
    expect(screen.getByText(/batch#2/)).toBeInTheDocument();
  });

  it("修订对照面板渲染新增/已解决/持续三组", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path === "/projects/p1/contract-reviews") return LIST;
      if (path.endsWith("/review-templates")) return [];
      return {
        ...DETAIL,
        summary_json: {
          ...DETAIL.summary_json,
          revision_diff: {
            added: [{ rule_id: "CONTRACT-PAY-001", kind: "risk", severity: "high", message: "新增：付款周期与招标文件不一致" }],
            resolved: [{ rule_id: "CONTRACT-MISS-002", kind: "missing", severity: "low", message: "未约定知识产权归属" }],
            carried: [],
          },
        },
      };
    });
    renderCard();

    await screen.findByText("付款节点缺失验收标准");
    expect(screen.getByText(/修订对照（相对上一轮：新增 1 \/ 已解决 1 \/ 持续 0）/)).toBeInTheDocument();
    expect(screen.getByText("新增：付款周期与招标文件不一致")).toBeInTheDocument();
    expect(screen.getByText("已解决")).toBeInTheDocument();
  });
});
