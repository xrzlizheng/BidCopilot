import type { ReactNode } from "react";

export type PlatformNavItem = {
  href: string;
  label: string;
  permission?: string;
  icon: ReactNode;
};

// 社区版：无「平台」分组（企业版 overlay 覆盖本文件注入平台租户入口）。
export const PLATFORM_NAV: PlatformNavItem[] = [];