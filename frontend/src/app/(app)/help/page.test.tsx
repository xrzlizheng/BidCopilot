import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import HelpPage from "./page";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

describe("HelpPage", () => {
  it("renders the usage guide and keeps every table-of-contents anchor valid", () => {
    render(<HelpPage />);

    expect(screen.getByRole("heading", { level: 1, name: "使用帮助" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "一条主线，从商机到交付" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "快速开始" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "机会中心" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "投标项目" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "安全与边界" })).toBeInTheDocument();
    expect(screen.getByText(/编辑已审批章节会重新打开审核流程/)).toBeInTheDocument();

    const anchors = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'));
    expect(anchors.length).toBeGreaterThan(10);
    for (const anchor of anchors) {
      expect(document.getElementById(anchor.getAttribute("href")!.slice(1))).not.toBeNull();
    }
  });
});
