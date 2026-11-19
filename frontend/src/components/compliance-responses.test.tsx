import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import { ComplianceResponses } from "./compliance-responses";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
}));

const ITEMS = [
  {
    id: "r1",
    requirement_type: "qualification",
    requirement_text: "投标人须具备集成资质二级及以上",
    category: "QUALIFICATION",
    response_status: "pending",
    response_note: null,
    evidence_refs: [],
    section_ref: null,
    confirmed_by: null,
    confirmed_at: null,
  },
  {
    id: "r2",
    requirement_type: "substantive",
    requirement_text: "系统全年可用性不低于 99.95%",
    category: "实质性要求",
    response_status: "pending",
    response_note: null,
    evidence_refs: [],
    section_ref: null,
    confirmed_by: null,
    confirmed_at: null,
  },
  {
    id: "r3",
    requirement_type: "scoring",
    requirement_text: "技术方案",
    category: "技术",
    response_status: "responded",
    response_note: "已响应",
    evidence_refs: ["产品白皮书"],
    section_ref: "技术方案响应",
    confirmed_by: "u1",
    confirmed_at: "2026-10-23T10:00:00+00:00",
  },
];

function list() {
  return {
    items: ITEMS,
    summary: { pending: 2, responded: 1, deviated: 0, not_responded: 0 },
    total: ITEMS.length,
    unresolved: 2,
  };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ComplianceResponses projectId="p1" />
    </QueryClientProvider>,
  );
}

describe("ComplianceResponses", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path: string, init: RequestInit = {}) => {
      if (path.endsWith("/sections")) return [{ id: "s1", title: "技术方案响应" }];
      if (path.endsWith("/responses/batch")) {
        return { updated: 2, summary: list().summary, unresolved: 0 };
      }
      if (init.method === "PATCH") return ITEMS[0];
      return list();
    });
  });

  afterEach(() => cleanup());

  it("narrows the list with the status filter", async () => {
    renderCard();
    await screen.findByText("投标人须具备集成资质二级及以上");
    expect(screen.queryByText("技术方案")).not.toBeNull();

    fireEvent.change(screen.getByRole("combobox", { name: "按响应状态筛选" }), {
      target: { value: "unresolved" },
    });

    await screen.findByText("筛选 2 / 3 条");
    expect(screen.queryByText("技术方案")).toBeNull();
  });

  it("batches only the selected ids and never sends a status filter leak", async () => {
    renderCard();
    await screen.findByText("投标人须具备集成资质二级及以上");

    fireEvent.change(screen.getByRole("combobox", { name: "按响应状态筛选" }), {
      target: { value: "unresolved" },
    });
    fireEvent.click(await screen.findByText("选中筛选结果"));
    fireEvent.click(await screen.findByText("批量标记为已响应"));

    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(([path, init]) => path.endsWith("/responses/batch") && init?.method === "POST");
      expect(call).toBeDefined();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.response_ids).toEqual(["r1", "r2"]);
      expect(body.response_status).toBe("responded");
    });
  });

  it("links a section without touching the response status", async () => {
    renderCard();
    await screen.findByText("投标人须具备集成资质二级及以上");

    fireEvent.change(screen.getByRole("combobox", { name: "关联章节 投标人须具备集成资质二级及以上" }), {
      target: { value: "技术方案响应" },
    });

    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(([path, init]) => path.includes("/responses/r1") && init?.method === "PATCH");
      expect(call).toBeDefined();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.section_ref).toBe("技术方案响应");
      expect("response_status" in body).toBe(false);
    });
  });

  it("splits evidence references on CJK separators when the field loses focus", async () => {
    renderCard();
    await screen.findByText("投标人须具备集成资质二级及以上");

    const field = screen.getByRole("textbox", { name: "证据引用 投标人须具备集成资质二级及以上" });
    fireEvent.change(field, { target: { value: "ISO9001 证书，验收报告、保修承诺" } });
    fireEvent.focusOut(field);

    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(([path, init]) => path.includes("/responses/r1") && init?.method === "PATCH");
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body)).evidence_refs).toEqual([
        "ISO9001 证书",
        "验收报告",
        "保修承诺",
      ]);
    });
  });
});
