import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/confirm-dialog";
import { apiFetch } from "@/lib/api";
import { OpportunitySubscriptions } from "./opportunity-subscriptions";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
  logout: vi.fn(),
  ensureSession: vi.fn(),
}));

const SUBSCRIPTIONS = {
  items: [
    {
      id: "s1",
      name: "政务云运维",
      query_json: { keywords: ["政务云"], info_types: ["1"], page_size: 20 },
      interval_minutes: 60,
      enabled: true,
      last_run_at: "2026-10-23T09:00:00+00:00",
      last_sync_run_id: "r1",
      created_at: "2026-10-20T09:00:00+00:00",
    },
    {
      id: "s2",
      name: "信创终端",
      query_json: { keywords: [], info_types: ["1", "2", "3"] },
      interval_minutes: 120,
      enabled: false,
      last_run_at: null,
      last_sync_run_id: null,
      created_at: "2026-10-21T09:00:00+00:00",
    },
  ],
  total: 2,
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ConfirmProvider>
      <QueryClientProvider client={client}>
      <OpportunitySubscriptions
        defaults={{
          keyword: "系统集成",
          infoTypes: ["1"],
          industry: "IT 信息化",
          budgetMin: "1000000",
          budgetMax: "5000000",
          publishFrom: "2026-10-01",
          publishTo: "2026-10-31",
        }}
      />
      </QueryClientProvider>
    </ConfirmProvider>,
  );
}

describe("OpportunitySubscriptions", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path: string, init: RequestInit = {}) => {
      if (init.method === "POST" && path.endsWith("/run")) {
        return {
          id: "r9",
          status: "succeeded",
          rows_fetched: 3,
          rows_upserted: 3,
          rows_new: 2,
          warnings_count: 1,
          warnings: [{ code: "missing_uid", message: "uid is missing", field: "uid" }],
          error_code: null,
          error_message: null,
          snapshot_ref: null,
          gateway_order_no: "order-9",
          charged: true,
          created_at: "2026-10-23T10:00:00+00:00",
          finished_at: "2026-10-23T10:00:05+00:00",
        };
      }
      if (init.method === "POST") {
        return { ...SUBSCRIPTIONS.items[0], id: "s3", name: JSON.parse(String(init.body)).name };
      }
      if (init.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return { ...SUBSCRIPTIONS.items[0], enabled: body.enabled };
      }
      if (init.method === "DELETE") return { deleted: true };
      return SUBSCRIPTIONS;
    });
  });

  afterEach(() => cleanup());

  it("lists subscriptions with their query snapshot and schedule", async () => {
    renderPanel();
    await screen.findByText("政务云运维");
    expect(screen.getByText(/关键词 政务云/)).toBeInTheDocument();
    expect(screen.getByText(/每 60 分钟/)).toBeInTheDocument();
    expect(screen.getByText(/已停用/)).toBeInTheDocument();
    expect(screen.getByText(/从未运行/)).toBeInTheDocument();
  });

  it("creates a subscription from the current filter snapshot", async () => {
    renderPanel();
    fireEvent.change(await screen.findByPlaceholderText("例如：政务云运维"), {
      target: { value: "数据中台" },
    });
    fireEvent.click(screen.getByRole("button", { name: "新建订阅" }));

    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(([path, init]) => path === "/opportunity-subscriptions" && init?.method === "POST");
      expect(call).toBeDefined();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.name).toBe("数据中台");
      expect(body.interval_minutes).toBe(60);
      expect(body.query.keywords).toEqual(["系统集成"]);
      expect(body.query.info_types).toEqual(["1"]);
      expect(body.query.industry_list).toEqual(["IT 信息化"]);
      expect(body.query.budget_min).toBe("1000000");
      expect(body.query.budget_max).toBe("5000000");
      expect(body.query.publish_from).toBe("2026-10-01");
      expect(body.query.publish_to).toBe("2026-10-31");
      // 免费文本地区不伪造 KeepRisk 结构化 region_list 字段。
      expect(body.query.region_list).toBeUndefined();
    });
  });

  it("toggles and runs a subscription against the API", async () => {
    renderPanel();
    await screen.findByText("政务云运维");

    fireEvent.click(screen.getAllByRole("button", { name: "停用" })[0]);
    await waitFor(() => {
      const call = vi
        .mocked(apiFetch)
        .mock.calls.find(([path, init]) => path === "/opportunity-subscriptions/s1" && init?.method === "PATCH");
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ enabled: false });
    });

    fireEvent.click(screen.getAllByRole("button", { name: /立即同步/ })[0]);
    await screen.findByText(/立即同步完成：抓取 3 条，新增 2 条/);
    await screen.findByText(/missing_uid/);
  });
});
