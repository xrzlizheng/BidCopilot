import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, apiFetchBlob } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { ContractLifecyclePanel } from "./contract-lifecycle-panel";

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

const DETAIL = {
  id: "r1",
  project_id: "p1",
  review_id: null,
  contract_no: "HT-2026-001",
  title: "某政务云运维合同",
  party_a: "某市大数据局",
  party_b: "投标人科技有限公司",
  signed_date: null,
  effective_date: "2026-11-01",
  expiry_date: "2027-10-31",
  amount: "1000000.00",
  currency: "CNY",
  status: "draft" as const,
  owner_id: null,
  created_by: "u1",
  draft_generated_at: "2026-10-28T10:00:00+00:00",
  draft_disclaimer: "机器生成草稿，需法务复核",
  expiry_reminded_at: null,
  created_at: "2026-10-28T09:00:00+00:00",
  draft: [
    {
      id: "c1",
      seq: 1,
      title: "合同价款（依据投标承诺）",
      text: "依据投标承诺，合同总价为人民币 1000000.00 元。",
      category: "price",
      source_json: { type: "bid_commitment", commitment_id: "cm1" },
    },
  ],
};

const LEDGER = {
  record_id: "r1",
  currency: "CNY",
  summary: {
    contract_amount: "1000000.00",
    invoice_total: "0.30",
    payment_total: "0.10",
    deposit_total: "0.00",
    retention_total: "0.00",
    outstanding: "0.20",
  },
  entries: [
    {
      id: "le1",
      record_id: "r1",
      entry_type: "invoice",
      amount: "0.10",
      occurred_on: "2026-11-02",
      note: "首期开票",
      voucher_ref: "FP-001",
      created_by: "u1",
      created_at: "2026-11-02T09:00:00+00:00",
    },
  ],
};

const CHANGES = {
  items: [
    {
      id: "ch1",
      record_id: "r1",
      change_type: "change",
      amount_before: "1000000.00",
      amount_after: "1200000.50",
      expiry_before: "2027-10-31",
      expiry_after: null,
      reason: "增加运维范围",
      approval_status: "pending",
      requested_by: "u1",
      approved_by: null,
      approved_at: null,
      created_at: "2026-10-29T09:00:00+00:00",
    },
  ],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContractLifecyclePanel recordId="r1" />
    </QueryClientProvider>,
  );
}

describe("ContractLifecyclePanel", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetchBlob).mockReset();
    useAuthStore.getState().setSession("token", {
      id: "u1",
      tenant_id: "t1",
      email: "owner@example.com",
      display_name: "演示管理员",
      roles: ["tenant_admin"],
      permissions: ["project:read", "project:write", "project:approve"],
      tenant_name: "演示投标企业",
    });
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.endsWith("/ledger")) return LEDGER;
      if (path.endsWith("/changes")) return CHANGES;
      return DETAIL;
    });
    vi.mocked(apiFetchBlob).mockResolvedValue(new Blob(["xlsx-bytes"]));
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:mock"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    useAuthStore.getState().clear();
  });

  it("renders master data, draft disclaimer, source tracing and ledger summary", async () => {
    renderPanel();

    await screen.findByText("某政务云运维合同");
    // 概览页签：主数据与草稿免责声明
    expect(screen.getByText("CNY 1000000.00")).toBeInTheDocument();
    expect(screen.getByText(/机器生成草稿，需法务复核/)).toBeInTheDocument();

    // 条款页签：草稿条款与来源追溯
    fireEvent.click(screen.getByRole("button", { name: /^条款/ }));
    expect(screen.getByText("合同价款（依据投标承诺）", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("来源：bid_commitment")).toBeInTheDocument();

    // 履约页签：台账汇总
    fireEvent.click(screen.getByRole("button", { name: /^履约/ }));
    expect(screen.getByText("CNY 0.30")).toBeInTheDocument();
    expect(screen.getByText("CNY 0.20")).toBeInTheDocument();

    // 变更页签：变更记录
    fireEvent.click(screen.getByRole("button", { name: /^变更/ }));
    expect(screen.getByText("增加运维范围")).toBeInTheDocument();
  });

  it("submits draft for approval through POST and sends only the comment field", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "提交审批" }));

    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(
          ([path, init]) => path === "/contract-records/r1/submit" && init?.method === "POST",
        );
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ comment: null });
    });
  });

  it("exports ledger through authenticated blob request", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "导出台账（xlsx）" }));

    await waitFor(() => {
      expect(vi.mocked(apiFetchBlob)).toHaveBeenCalledWith("/contract-records/r1/export");
    });
    expect(document.querySelector('a[href*="/contract-records/r1/export"]')).toBeNull();
  });
});
