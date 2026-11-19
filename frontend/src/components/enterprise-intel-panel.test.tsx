import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import { EnterpriseIntelPanel, type IntelResponse } from "./enterprise-intel-panel";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

const OK: IntelResponse = {
  status: "ok",
  data: { entName: "示例科技有限公司", creditCode: "91000000TEST" },
  source: "keeprisk-mcp",
  fetched_at: "2026-10-31T00:00:00Z",
  order_no: "order-1",
  charged: true,
  api_calls: 1,
  billing_units: 1,
  warnings: [],
  error: null,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EnterpriseIntelPanel", () => {
  it("loads the company profile", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(OK);
    render(<EnterpriseIntelPanel companyName="示例科技有限公司" />);

    fireEvent.click(screen.getByRole("button", { name: "工商照面" }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    expect(String(vi.mocked(apiFetch).mock.calls[0][0])).toContain(
      "/company-intel/profile?ent_name=",
    );
    expect(await screen.findByText("示例科技有限公司")).toBeTruthy();
    expect(screen.getByText(/计费 是/)).toBeTruthy();
  });

  it("resolves a candidate and switches the target", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({
        ...OK,
        data: {
          candidates: [
            { ent_name: "示例科技有限公司", credit_code: "91000000TEST", legal_person: "张三" },
          ],
        },
      })
      .mockResolvedValueOnce({ ...OK, data: { entName: "示例科技有限公司" } });
    render(<EnterpriseIntelPanel companyName="示例科技" />);

    fireEvent.click(screen.getByRole("button", { name: "主体匹配" }));
    expect(await screen.findByText("示例科技有限公司")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "使用此主体" }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
    expect(String(vi.mocked(apiFetch).mock.calls[0][0])).toContain(
      "/company-intel/resolve?keyword=",
    );
    expect(String(vi.mocked(apiFetch).mock.calls[1][0])).toContain(
      "/company-intel/profile?ent_name=",
    );
    expect(await screen.findByText(/当前主体：示例科技有限公司/)).toBeTruthy();
  });

  it("passes through non-ok status", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      ...OK,
      status: "unavailable",
      data: null,
      charged: false,
      api_calls: 0,
      billing_units: 0,
      error: { code: "mcp_not_configured", message: "KEEPRISK_MCP_TOKEN is not configured" },
    });
    render(<EnterpriseIntelPanel companyName="示例科技有限公司" />);

    fireEvent.click(screen.getByRole("button", { name: "信用评分" }));

    expect(await screen.findByText(/mcp_not_configured/)).toBeTruthy();
    expect(screen.getByText("unavailable")).toBeTruthy();
  });

  it("requests due diligence with digest sections", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      ...OK,
      data: { risk_summary: { overall_level: "green", red_count: 0, yellow_count: 0 }, sections: [] },
    });
    render(<EnterpriseIntelPanel companyName="示例科技有限公司" />);

    fireEvent.click(screen.getByRole("button", { name: "尽调摘要" }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    const [url, init] = vi.mocked(apiFetch).mock.calls[0];
    expect(url).toBe("/company-intel/due-diligence");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body.ent_name).toBe("示例科技有限公司");
    expect(body.sections).toBe("basic,shareholders,beneficial_owner,equity_pledge");
    expect(await screen.findByText(/风险等级 green/)).toBeTruthy();
  });
});
