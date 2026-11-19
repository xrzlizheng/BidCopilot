import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";
import type { Project, WorkspaceInfo } from "@/lib/types";
import { ProjectStageNav } from "./workspace-stages";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
}));

const PROJECT = {
  id: "p1",
  name: "2026年度合肥高新区政务云服务项目",
  status: "active",
  bid_deadline: null,
  internal_deadline: null,
  decision_status: null,
} as unknown as Project;

function workspace(blockers: string[], stageKey = "parse"): WorkspaceInfo {
  return {
    sections_total: 0,
    sections_approved: 0,
    stages: [
      {
        key: stageKey,
        label: "解析与决策",
        status: "in_progress",
        detail: "等待上传招标文件",
        blockers,
      },
      {
        key: "compose",
        label: "模板与大纲",
        status: "pending",
        detail: "0 个章节",
        blockers: ["尚未生成大纲"],
      },
      {
        key: "produce",
        label: "生成与核查",
        status: "pending",
        detail: "已审批 0/0",
        blockers: ["尚未生成章节"],
      },
      {
        key: "deliver",
        label: "合规与交付",
        status: "pending",
        detail: "等待审批与预检",
        blockers: ["尚未运行合规预检"],
      },
    ],
  } as unknown as WorkspaceInfo;
}

/** 渲染阶段导航，并挂一个真实存在的锚点卡片用于验证跳转与高亮。 */
function renderNav(
  info: WorkspaceInfo,
  options: {
    onTabChange?: (tab: "parse" | "produce" | "response" | "deliver") => void;
    anchorId?: string;
  } = {},
) {
  const { onTabChange = () => {}, anchorId = "tender-upload" } = options;
  vi.mocked(apiFetch).mockImplementation(async (path: string) => {
    if (path.endsWith("/workspace")) return info;
    if (path.endsWith("/materials")) return [];
    throw new Error(`unexpected path: ${path}`);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectStageNav
        projectId="p1"
        project={PROJECT}
        activeTab="parse"
        onTabChange={onTabChange}
      />
      <section id={anchorId} data-testid="anchor" />
    </QueryClientProvider>,
  );
}

describe("ProjectStageNav 阻断提示必须给出明确下一步", () => {
  let scrollSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    // jsdom 未实现 scrollIntoView
    scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView;
  });

  afterEach(() => {
    cleanup();
  });

  it("主按钮直接写成「下一步：上传招标文件」，而不是只说有阻断", async () => {
    renderNav(workspace(["尚未解析招标文件"]));

    // 回归：原先只显示「继续：解析与决策」，用户不知道下一步该做什么。
    expect(
      await screen.findByRole("button", { name: /下一步：上传招标文件/ }),
    ).toBeInTheDocument();
  });

  it("阻断条同时给出可点击的动作按钮，点击后切到能解除阻断的阶段", async () => {
    const onTabChange = vi.fn();
    renderNav(workspace(["尚未解析招标文件"]), { onTabChange });

    const action = await screen.findByRole("button", { name: /^上传招标文件/ });
    fireEvent.click(action);

    await waitFor(() => {
      expect(onTabChange).toHaveBeenCalledWith("parse");
    });
  });

  it("点击后滚动到目标卡片并给它加高亮脉冲", async () => {
    renderNav(workspace(["尚未解析招标文件"]));

    fireEvent.click(await screen.findByRole("button", { name: /^上传招标文件/ }));

    const anchor = screen.getByTestId("anchor");
    await waitFor(() => {
      // 目标卡片被滚进视口
      expect(scrollSpy).toHaveBeenCalled();
      // 并带上高亮类（CSS 里定义了 jump-highlight 脉冲）
      expect(anchor.classList.contains("jump-highlight")).toBe(true);
    });
  });

  it("「导出前需审批章节」要跳到标书生产阶段，而不是停留在合规与交付", async () => {
    const onTabChange = vi.fn();
    const info = workspace(["导出前需审批章节", "尚未运行合规预检"], "deliver");
    info.stages[0].status = "done";
    renderNav(info, { onTabChange, anchorId: "outline-panel" });

    // 首个未完成阶段是 compose（模板与大纲），其阻断为「尚未生成大纲」
    expect(
      await screen.findByRole("button", { name: /下一步：去选模板生成大纲/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^去选模板生成大纲/ }));

    await waitFor(() => {
      expect(onTabChange).toHaveBeenCalledWith("produce");
    });
  });
});
