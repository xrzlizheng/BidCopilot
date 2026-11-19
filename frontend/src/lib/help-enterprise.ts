import type { HelpSection } from "@/lib/help-content";

export type EnterpriseHelpEntry = { group: string; section: HelpSection };

// 社区版：无额外帮助章节（企业版 overlay 覆盖本文件注入「平台租户」章节）。
export const ENTERPRISE_SECTIONS: EnterpriseHelpEntry[] = [];