"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Circle,
  Layers3,
  PackageCheck,
} from "lucide-react";

import { Badge, Button } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { flashTarget, locateAnchor } from "@/lib/scroll-highlight";
import type { MaterialItem, Project, WorkspaceInfo } from "@/lib/types";

export type ProjectWorkspaceTab = "parse" | "produce" | "response" | "deliver";

const TABS: {
  key: ProjectWorkspaceTab;
  label: string;
  hint: string;
  icon: typeof CheckCircle2;
}[] = [
  { key: "parse", label: "解析与决策", hint: "文件、约束、矩阵与决策", icon: Circle },
  { key: "produce", label: "标书生产", hint: "模板、大纲、章节与核查", icon: Layers3 },
  { key: "response", label: "响应与自查", hint: "逐条响应与交标清单", icon: CheckCircle2 },
  { key: "deliver", label: "合规与交付", hint: "预检、合同与结果复盘", icon: AlertTriangle },
];

const TAB_BY_STAGE: Record<string, ProjectWorkspaceTab> = {
  parse: "parse",
  compose: "produce",
  produce: "produce",
  deliver: "deliver",
};

const STATUS_META: Record<string, { label: string; tone: "green" | "amber" | "slate" }> = {
  done: { label: "已完成", tone: "green" },
  in_progress: { label: "进行中", tone: "amber" },
  pending: { label: "待开始", tone: "slate" },
};

/**
 * 把后端返回的阻断文案翻译成「下一步该点哪里」。
 *
 * 背景：原先阻断条只有一句「XX 仍有阻断：尚未解析招标文件」，用户看完仍然不知道
 * 该做什么、该去哪。这里给每个已知阻断配一个明确的动词 + 目标阶段 + 目标锚点，
 * 点击即切到对应阶段并滚动到真正要操作的表单控件。
 *
 * 后端阻断文案（`projects/service.py`）与映射必须一一对应，改文案时同步改这里：
 * - parse:   尚未解析招标文件
 * - compose: 尚未生成大纲
 * - produce: 尚未生成章节 / 尚无审批通过的章节
 * - deliver: 导出前需审批章节 / 尚未运行合规预检
 */
type BlockerAction = { label: string; tab?: ProjectWorkspaceTab; anchorId?: string };

const BLOCKER_ACTIONS: { match: RegExp; action: BlockerAction }[] = [
  {
    match: /尚未(上传|解析)|未上传|未解析|招标文件/,
    action: { label: "上传招标文件", tab: "parse", anchorId: "tender-upload" },
  },
  {
    match: /决策|投不投/,
    action: { label: "去生成决策", tab: "parse", anchorId: "decision-card" },
  },
  {
    match: /大纲|模板/,
    action: { label: "去选模板生成大纲", tab: "produce", anchorId: "outline-panel" },
  },
  {
    // 「导出前需审批章节」出现在 deliver 阶段，但审批动作在 produce 阶段的章节面板。
    match: /章节|审批/,
    action: { label: "去生成/审批章节", tab: "produce", anchorId: "sections-panel" },
  },
  {
    match: /响应|自检|就绪/,
    action: { label: "去逐条确认响应", tab: "response", anchorId: "readiness-panel" },
  },
  {
    match: /合规|预检/,
    action: { label: "去跑合规预检", tab: "deliver", anchorId: "compliance-panel" },
  },
  {
    match: /回执|交标|递交/,
    action: { label: "去登记交标回执", tab: "response", anchorId: "receipt-panel" },
  },
];

function blockerAction(blockers: string[]): BlockerAction {
  const text = blockers.join("；");
  for (const rule of BLOCKER_ACTIONS) {
    if (rule.match.test(text)) return rule.action;
  }
  return { label: "前往该阶段处理" };
}


const PROJECT_STATUS: Record<string, string> = {
  active: "在投",
  won: "已中标",
  lost: "未中标",
};

const DECISION_TONES: Record<string, "green" | "amber" | "rose" | "slate"> = {
  Go: "green",
  "Conditional Go": "amber",
  "No-Go": "rose",
};

function combineStatus(first?: string, second?: string): string {
  if (first === "done" && second === "done") return "done";
  if (first === "in_progress" || second === "in_progress") return "in_progress";
  if (first === "done" || second === "done") return "in_progress";
  return "pending";
}

function recommendedTab(info: WorkspaceInfo): ProjectWorkspaceTab {
  const firstOpen = info.stages.find((stage) => stage.status !== "done");
  return firstOpen ? TAB_BY_STAGE[firstOpen.key] ?? "parse" : "deliver";
}

function dateLabel(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : "未设置";
}

export function ProjectStageNav({
  projectId,
  project,
  activeTab,
  onTabChange,
}: {
  projectId: string;
  project: Project;
  activeTab: ProjectWorkspaceTab;
  onTabChange: (tab: ProjectWorkspaceTab) => void;
}) {
  const userSelected = useRef(false);
  const workspace = useQuery({
    queryKey: ["project-workspace", projectId],
    queryFn: () => apiFetch<WorkspaceInfo>(`/projects/${projectId}/workspace`),
    retry: false,
  });
  const materials = useQuery({
    queryKey: ["project-materials", projectId],
    queryFn: () => apiFetch<MaterialItem[]>(`/projects/${projectId}/materials`),
    retry: false,
  });

  useEffect(() => {
    if (!workspace.data || userSelected.current) return;
    onTabChange(recommendedTab(workspace.data));
  }, [onTabChange, workspace.data]);

  const counts = (materials.data ?? []).reduce<Record<string, number>>((acc, item) => {
    acc[item.status] = (acc[item.status] ?? 0) + 1;
    return acc;
  }, {});
  const info = workspace.data;
  const firstOpen = info?.stages.find((stage) => stage.status !== "done");
  const recommend = info ? recommendedTab(info) : "parse";
  const statusByTab: Record<ProjectWorkspaceTab, string> = {
    parse: info?.stages.find((stage) => stage.key === "parse")?.status ?? "pending",
    produce: combineStatus(
      info?.stages.find((stage) => stage.key === "compose")?.status,
      info?.stages.find((stage) => stage.key === "produce")?.status,
    ),
    response:
      info?.stages.find((stage) => stage.key === "deliver")?.status === "done"
        ? "done"
        : (info?.sections_approved ?? 0) > 0
          ? "in_progress"
          : "pending",
    deliver: info?.stages.find((stage) => stage.key === "deliver")?.status ?? "pending",
  };

  function select(tab: ProjectWorkspaceTab, options?: { scroll?: boolean }) {
    userSelected.current = true;
    onTabChange(tab);
    if (options?.scroll === false) return;
    window.requestAnimationFrame(() => {
      document.getElementById("project-stage-content")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }

  /**
   * 阻断条/主按钮：切到该阶段 → 滚到目标卡片 → 高亮脉冲 → 聚焦首个可操作控件。
   *
   * 说明：切换页签后目标内容在下一帧才渲染（数据面板可能更晚），所以用有上限的
   * rAF 轮询等待锚点出现，避免「点了没反应」。
   */
  function goToBlocker(stageKey: string, action: BlockerAction) {
    const tab = action.tab ?? TAB_BY_STAGE[stageKey] ?? "parse";
    if (!action.anchorId) {
      select(tab);
      return;
    }
    const anchorId = action.anchorId;
    // 只做一次平滑滚动：交给锚点定位，避免与「滚到阶段顶部」互相打断。
    select(tab, { scroll: false });
    locateAnchor(anchorId, (target) => {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      flashTarget(target);
      // 文件选择框无法程序化唤起，但可以聚焦其所在卡片内的第一个可聚焦控件，
      // 让键盘用户直接落到「选择文件」。
      const focusable = target.querySelector<HTMLElement>(
        'input[type="file"], input, select, textarea, button',
      );
      focusable?.focus({ preventScroll: true });
    });
  }

  return (
    <div className="z-20 mb-4 rounded-2xl border border-slate-200/80 bg-white/92 p-3 shadow-card backdrop-blur-xl sm:p-4 lg:sticky lg:top-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Layers3 size={16} className="text-aurora-600" />
          <span className="text-sm font-semibold text-slate-900">项目工作区</span>
          <Badge tone={project.status === "won" ? "green" : project.status === "lost" ? "rose" : "amber"}>
            {PROJECT_STATUS[project.status] ?? project.status}
          </Badge>
          {project.decision_status ? (
            <Badge tone={DECISION_TONES[project.decision_status] ?? "slate"}>
              {project.decision_status}
            </Badge>
          ) : null}
          <span className="text-xs text-slate-500">
            投标截止 {dateLabel(project.bid_deadline)}
            {project.internal_deadline ? ` · 内部截止 ${dateLabel(project.internal_deadline)}` : ""}
          </span>
        </div>
        <Button
          className="h-9"
          onClick={() => {
            // 有阻断时，主按钮直接跳到「能解除阻断的那个控件」，而不只是切页签。
            if (firstOpen?.blockers.length) {
              goToBlocker(firstOpen.key, blockerAction(firstOpen.blockers));
            } else {
              select(recommend);
            }
          }}
          disabled={!info}
        >
          {info
            ? firstOpen?.blockers.length
              ? `下一步：${blockerAction(firstOpen.blockers).label}`
              : `继续：${TABS.find((tab) => tab.key === recommend)?.label}`
            : "加载阶段…"}
          <ArrowRight size={14} />
        </Button>
      </div>

      {workspace.isError ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-rose-200/80 bg-rose-50/70 px-3 py-2 text-xs text-rose-700">
          <AlertTriangle size={14} className="shrink-0" />
          <span className="min-w-0 flex-1">
            阶段状态没取到：
            {workspace.error instanceof Error ? workspace.error.message : "请求失败"}
            。下方阶段卡片与「下一步」可能不准确。
          </span>
          <Button
            className="h-7 shrink-0 px-2.5 text-xs"
            variant="secondary"
            onClick={() => workspace.refetch()}
          >
            重试
          </Button>
        </div>
      ) : null}

      <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        {TABS.map((tab) => {
          const active = activeTab === tab.key;
          const status = STATUS_META[statusByTab[tab.key]] ?? STATUS_META.pending;
          const detail =
            tab.key === "parse"
              ? info?.stages.find((stage) => stage.key === "parse")?.detail ?? "上传招标文件开始"
              : tab.key === "produce"
                ? info
                  ? `${info.sections_total} 个章节 · ${info.sections_approved} 个已审批`
                  : "模板、大纲与章节"
                : tab.key === "response"
                  ? "逐条确认；未处理会拦导出"
                  : info?.stages.find((stage) => stage.key === "deliver")?.detail ?? "预检、合同与复盘";
          return (
            <button
              key={tab.key}
              type="button"
              aria-pressed={active}
              onClick={() => select(tab.key)}
              className={`rounded-xl border px-3 py-2.5 text-left transition ${
                active
                  ? "border-aurora-500/60 bg-aurora-500/[0.06] shadow-glow"
                  : "border-slate-200/80 bg-white/60 hover:border-slate-300"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className={`inline-flex items-center gap-1.5 text-xs font-semibold ${active ? "text-slate-900" : "text-slate-700"}`}>
                  <tab.icon size={13} className={active ? "text-aurora-600" : "text-slate-400"} />
                  {tab.label}
                </span>
                <Badge tone={status.tone}>{status.label}</Badge>
              </div>
              <p className="mt-1 truncate text-[11px] text-slate-500">{detail}</p>
            </button>
          );
        })}
      </div>

      {firstOpen?.blockers.length ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-amber-200/80 bg-amber-50/70 px-3 py-2 text-xs text-amber-800">
          <AlertTriangle size={14} className="shrink-0" />
          <span className="min-w-0 flex-1">
            <span className="font-medium">下一步：</span>
            {firstOpen.blockers[0]}
            {firstOpen.blockers.length > 1 ? `（另有 ${firstOpen.blockers.length - 1} 项）` : ""}
          </span>
          <Button
            className="h-7 shrink-0 px-2.5 text-xs"
            onClick={() => goToBlocker(firstOpen.key, blockerAction(firstOpen.blockers))}
          >
            {blockerAction(firstOpen.blockers).label}
            <ArrowRight size={12} />
          </Button>
        </div>
      ) : null}

      {materials.data?.length ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2.5 text-[11px] text-slate-500">
          <span className="inline-flex items-center gap-1">
            <PackageCheck size={13} className="text-aurora-600" /> 材料
          </span>
          <Badge tone="green">已匹配 {counts.available ?? 0}</Badge>
          <Badge tone="amber">待确认 {counts.unknown ?? 0}</Badge>
          <Badge tone="rose">缺失 {counts.missing ?? 0}</Badge>
          <Badge tone="slate">需编写 {counts.to_write ?? 0}</Badge>
          <Link href="/knowledge" className="ml-auto text-xs font-medium text-electric-600">
            查看知识库 <ArrowRight size={12} />
          </Link>
        </div>
      ) : null}
    </div>
  );
}
