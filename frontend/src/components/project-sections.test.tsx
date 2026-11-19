import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiDownload, apiFetch, apiFetchBlob } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { ProjectSections } from "./project-sections";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
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

const SECTION = {
  id: "s1",
  project_id: "p1",
  title: "技术方案响应",
  status: "approved",
  factcheck_status: "passed",
  generation_mode: "rules",
  traceability_rate: 1,
  unsourced_count: 0,
  content_text: "正文",
  content_json: [{ type: "paragraph", text: "正文" }],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectSections projectId="p1" />
    </QueryClientProvider>,
  );
}

describe("ProjectSections 导出动作", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetchBlob).mockReset();
    vi.mocked(apiDownload).mockReset();
    useAuthStore.getState().setSession("token", {
      id: "u1",
      tenant_id: "t1",
      email: "owner@example.com",
      display_name: "演示管理员",
      roles: ["tenant_admin"],
      permissions: ["project:write", "project:approve", "project:export", "knowledge:use"],
      tenant_name: "演示投标企业",
    });
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.endsWith("/sections")) return [SECTION];
      if (path.endsWith("/knowledge/images")) return [];
      return SECTION;
    });
    vi.mocked(apiFetchBlob).mockResolvedValue(new Blob(["docx"]));
    vi.mocked(apiDownload).mockResolvedValue({ blob: new Blob(["docx"]), filename: "bid.docx" });
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:mock"), writable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), writable: true });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    useAuthStore.getState().clear();
  });

  it("一键匿名化走 POST 下载通道，而不是 GET blob", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "一键匿名化" }));

    // 回归 D2：后端 /anonymize 只注册了 POST，GET 必然 405
    await waitFor(() => {
      expect(vi.mocked(apiDownload)).toHaveBeenCalledWith("/projects/p1/anonymize", {});
    });
    expect(vi.mocked(apiFetchBlob)).not.toHaveBeenCalled();
  });

  it("导出 docx 仍走 POST 下载并带闸门参数", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "导出 docx" }));

    await waitFor(() => {
      expect(vi.mocked(apiDownload)).toHaveBeenCalledWith("/projects/p1/export/docx", {
        dark_bid: false,
        acknowledge_unresolved: false,
      });
    });
  });
});
