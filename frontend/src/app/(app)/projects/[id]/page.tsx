"use client";

import Link from "next/link";
import { useEffect, useState, type ChangeEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";

import { CompliancePanel } from "@/components/compliance-panel";
import { CommitmentsPanel } from "@/components/commitments-panel";
import { ContractReviewCard } from "@/components/contract-review-card";
import { PolicyQaPanel } from "@/components/policy-qa-panel";
import { PricingCostSettings } from "@/components/pricing-cost-settings";
import { PricingStrategyPanel } from "@/components/pricing-strategy-panel";
import { QuoteSheetPanel } from "@/components/quote-sheet-panel";
import { ComplianceResponses } from "@/components/compliance-responses";
import { DecisionCardPanel } from "@/components/decision-card";
import { DefenseOutlinePanel } from "@/components/defense-outline";
import { DocumentDiffPanel } from "@/components/document-diff-panel";
import { BidRiskPanel } from "@/components/bid-risk-panel";
import { BidderSimilarityPanel } from "@/components/bidder-similarity-panel";
import { BidderVerificationPanel } from "@/components/bidder-verification-panel";
import { ProjectStatusActions } from "@/components/project-status-actions";
import { ProjectSections } from "@/components/project-sections";
import { ReadinessPanel } from "@/components/readiness-panel";
import { SubmissionReceiptPanel } from "@/components/submission-receipt-panel";
import { SelfCheckPanel } from "@/components/self-check";
import {
  ProjectStageNav,
  type ProjectWorkspaceTab,
} from "@/components/workspace-stages";
import { Badge, Button, Card, ErrorState, Input, Loading, PageHeader, Select } from "@/components/ui";
import { ApiError, apiFetch } from "@/lib/api";
import type {
  AiUsage,
  BidResult,
  Company,
  Decision,
  Evaluation,
  MatrixRow,
  ProjectDetail,
  QualificationItem,
} from "@/lib/types";

const COVERAGE_TONES: Record<string, "green" | "amber" | "rose"> = {
  covered: "green",
  partial: "amber",
  missing: "rose",
};
const QUALIFICATION_TONES: Record<string, "green" | "amber" | "rose"> = {
  PASS: "green",
  UNKNOWN: "amber",
  FAIL: "rose",
};
const DECISION_TONES: Record<string, "green" | "amber" | "rose"> = {
  Go: "green",
  "Conditional Go": "amber",
  "No-Go": "rose",
};
const BAND_LABELS: Record<string, string> = {
  strong: "强",
  competitive: "有竞争力",
  weak: "偏弱",
};
const BAND_TONES: Record<string, "green" | "amber" | "rose"> = {
  strong: "green",
  competitive: "amber",
  weak: "rose",
};
const OUTCOME_LABELS: Record<string, string> = {
  won: "中标",
  lost: "未中标",
  cancelled: "流标/终止",
};
const OUTCOME_TONES: Record<string, "green" | "rose" | "slate"> = {
  won: "green",
  lost: "rose",
  cancelled: "slate",
};
const LOSS_REASONS = [
  ["price", "报价"],
  ["technical", "技术方案"],
  ["qualification", "资格/资质"],
  ["compliance", "合规/格式"],
  ["relationship", "客户关系"],
  ["budget", "预算/流标"],
  ["other", "其他"],
] as const;

const EMPTY_RESULT = {
  outcome: "won",
  win_amount: "",
  loss_reason: "price",
  competitor: "",
  notes: "",
};

function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isNaN(amount)
    ? String(value)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(amount);
}

export default function ProjectPage({ params }: { params: { id: string } }) {
  const queryClient = useQueryClient();
  const [companyId, setCompanyId] = useState("");
  const [packageCode, setPackageCode] = useState("");
  const [documentKind, setDocumentKind] = useState("tender");
  const [message, setMessage] = useState<string | null>(null);
  const [qualification, setQualification] = useState<{
    items: QualificationItem[];
    summary: Record<string, number>;
  } | null>(null);
  const [resultForm, setResultForm] = useState({ ...EMPTY_RESULT });
  const [workspaceTab, setWorkspaceTab] = useState<ProjectWorkspaceTab>("parse");

  const detail = useQuery({
    queryKey: ["project", params.id],
    queryFn: () => apiFetch<ProjectDetail>(`/projects/${params.id}`),
  });
  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: () => apiFetch<Company[]>("/companies"),
  });
  const matrix = useQuery({
    queryKey: ["project-matrix", params.id],
    queryFn: () => apiFetch<MatrixRow[]>(`/projects/${params.id}/matrix`),
  });
  const decision = useQuery({
    queryKey: ["project-decision", params.id],
    queryFn: () => apiFetch<Decision>(`/projects/${params.id}/decision`),
    retry: false,
  });
  const bidResult = useQuery({
    queryKey: ["project-result", params.id],
    queryFn: () => apiFetch<BidResult>(`/projects/${params.id}/result`),
    retry: false,
  });
  const evaluation = useQuery({
    queryKey: ["project-evaluation", params.id],
    queryFn: () => apiFetch<Evaluation>(`/projects/${params.id}/evaluations/latest`),
    retry: false,
  });
  // AI 用量来自 ai_usage_ledger 聚合：只有真实 llm 生成/评标才计数，
  // rules 模式与大纲生成不计（避免同一批章节被重复计入套餐）。
  const aiUsage = useQuery({
    queryKey: ["project-ai-usage", params.id],
    queryFn: () => apiFetch<AiUsage>(`/projects/${params.id}/ai-usage`),
    retry: false,
  });

  useEffect(() => {
    if (detail.data?.project.company_id) setCompanyId(detail.data.project.company_id);
  }, [detail.data?.project.company_id]);

  useEffect(() => {
    if (bidResult.data) {
      setResultForm({
        outcome: bidResult.data.outcome,
        win_amount:
          bidResult.data.win_amount === null ? "" : String(bidResult.data.win_amount),
        loss_reason: bidResult.data.loss_reason ?? "price",
        competitor: bidResult.data.competitor ?? "",
        notes: bidResult.data.notes ?? "",
      });
    }
  }, [bidResult.data]);

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 6000);
    return () => clearTimeout(timer);
  }, [message]);

  const savePackage = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${params.id}`, {
        method: "PATCH",
        body: JSON.stringify({ package_code: packageCode }),
      }),
    onSuccess: () => {
      setMessage("投标标包已保存。");
      queryClient.invalidateQueries({ queryKey: ["project", params.id] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "保存失败"),
  });

  const saveCompany = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${params.id}`, {
        method: "PATCH",
        body: JSON.stringify({ company_id: companyId }),
      }),
    onSuccess: () => {
      setMessage("投标企业已保存。");
      queryClient.invalidateQueries({ queryKey: ["project", params.id] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "保存失败"),
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const data = new FormData();
      data.append("file", file);
      data.append("document_kind", documentKind);
      return apiFetch<{
        document: {
          parse_status: string;
          parse_error: string | null;
          ocr_used: boolean;
          document_kind: string;
        };
        constraint: { parse_meta: Record<string, unknown> } | null;
      }>(
        `/projects/${params.id}/documents`,
        { method: "POST", body: data },
      );
    },
    onSuccess: (result) => {
      const ocrProvider = result.constraint?.parse_meta?.ocr_provider;
      const ocrLabel =
        ocrProvider === "mineru"
          ? "MinerU 本地识别"
          : ocrProvider === "minimax"
            ? "MiniMax 图片理解"
            : "OCR";
      setMessage(
        result.document.parse_status === "parsed"
          ? result.document.ocr_used
            ? `OCR 解析完成（${ocrLabel}）。`
            : "招标文件解析完成。"
          : `解析失败：${result.document.parse_error ?? result.document.parse_status}`,
      );
      queryClient.invalidateQueries({ queryKey: ["project", params.id] });
      queryClient.invalidateQueries({ queryKey: ["project-matrix", params.id] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "上传失败"),
  });

  const buildMatrix = useMutation({
    mutationFn: () => apiFetch<MatrixRow[]>(`/projects/${params.id}/matrix`, { method: "POST" }),
    onSuccess: () => {
      setMessage("评分矩阵已生成。");
      queryClient.invalidateQueries({ queryKey: ["project-matrix", params.id] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "矩阵生成失败"),
  });

  const runQualification = useMutation({
    mutationFn: () =>
      apiFetch<{ items: QualificationItem[]; summary: Record<string, number> }>(
        `/projects/${params.id}/qualification-check`,
        { method: "POST" },
      ),
    onSuccess: (result) => setQualification(result),
    onError: (error) => setMessage(error instanceof Error ? error.message : "资格预审失败"),
  });

  const runDecision = useMutation({
    mutationFn: () =>
      apiFetch<{ decision: Decision }>(`/projects/${params.id}/decision`, { method: "POST" }),
    onSuccess: (result) => {
      setMessage(`Bid-No-Bid：${result.decision.status}`);
      queryClient.invalidateQueries({ queryKey: ["project-decision", params.id] });
      queryClient.invalidateQueries({ queryKey: ["project", params.id] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "决策生成失败"),
  });

  const saveResult = useMutation({
    mutationFn: () =>
      apiFetch<BidResult>(`/projects/${params.id}/result`, {
        method: "PUT",
        body: JSON.stringify({
          outcome: resultForm.outcome,
          win_amount:
            resultForm.outcome === "won" && resultForm.win_amount
              ? resultForm.win_amount
              : null,
          loss_reason: resultForm.outcome === "lost" ? resultForm.loss_reason : null,
          competitor: resultForm.competitor || null,
          notes: resultForm.notes || null,
        }),
      }),
    onSuccess: () => {
      setMessage("投标结果复盘已保存。");
      queryClient.invalidateQueries({ queryKey: ["project", params.id] });
      queryClient.invalidateQueries({ queryKey: ["project-result", params.id] });
      queryClient.invalidateQueries({ queryKey: ["bid-analytics"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "复盘保存失败"),
  });

  const runEvaluation = useMutation({
    mutationFn: () =>
      apiFetch<Evaluation>(`/projects/${params.id}/evaluations`, { method: "POST" }),
    onSuccess: () => {
      setMessage("AI 评标完成。");
      queryClient.invalidateQueries({ queryKey: ["project-evaluation", params.id] });
      queryClient.invalidateQueries({ queryKey: ["project-ai-usage", params.id] });
    },
    onError: (error) =>
      setMessage(
        error instanceof ApiError
          ? `${error.message}（${error.code}）`
          : error instanceof Error
            ? error.message
            : "AI 评标失败",
      ),
  });

  function updateResult(field: keyof typeof EMPTY_RESULT, value: string) {
    setResultForm((current) => ({ ...current, [field]: value }));
  }

  function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    event.target.value = "";
  }

  if (detail.isLoading) return <Loading />;
  if (detail.error) return <div className="text-sm text-rose-600">{detail.error.message}</div>;
  if (!detail.data) return null;

  const evaluationSummary = evaluation.data?.result_json.summary;
  const evaluationItems = evaluation.data?.result_json.items ?? [];
  const evaluationWarnings = evaluation.data?.result_json.warnings ?? [];
  const evaluationCoverage = evaluationSummary?.coverage;
  // 覆盖不全时得分率只统计已评项，界面上必须说清楚，否则空白的项会被读成零分。
  const evaluationPartial =
    evaluationCoverage !== undefined && evaluationCoverage.ratio < 1;
  const evaluationProvisional = evaluationSummary?.band_status === "provisional";
  const evaluationConfidence = evaluationSummary?.confidence_summary;
  // 三路分布比"需复核 N 项"更能说明问题：是模型不确定，还是输出本身就不完整。
  const evaluationReviewHint =
    [
      evaluationConfidence
        ? `建议复核 ${evaluationConfidence.suggested} 项 · 强制 ${evaluationConfidence.required} 项`
        : null,
      evaluationSummary?.unevaluated_count
        ? `另有 ${evaluationSummary.unevaluated_count} 项未评`
        : null,
      evaluationSummary?.matrix_conflict_count
        ? `矩阵矛盾 ${evaluationSummary.matrix_conflict_count} 项`
        : null,
    ]
      .filter(Boolean)
      .join("；") || undefined;

  const project = detail.data.project;
  const rawConstraint = detail.data.latest_constraint?.payload;
  const constraint = rawConstraint
    ? {
        ...rawConstraint,
        scoring_items: rawConstraint.scoring_items ?? [],
        reject_clauses: rawConstraint.reject_clauses ?? [],
        qualification_requirements: rawConstraint.qualification_requirements ?? [],
        timeline: rawConstraint.timeline ?? [],
        submission_checklist: rawConstraint.submission_checklist ?? [],
        format_rules: rawConstraint.format_rules ?? [],
        budget: rawConstraint.budget ?? { amount: null, currency: "CNY" },
        dark_bid: rawConstraint.dark_bid ?? { required: false, rules: [] },
        bid_risks: rawConstraint.bid_risks,
      }
    : undefined;
  const packageInfo = (
    constraint as
      | { packages?: { declared_count?: number | null; items?: { code: string; name?: string }[] } }
      | null
      | undefined
  )?.packages;
  const packageItems = Array.isArray(packageInfo?.items) ? packageInfo!.items! : [];
  const showPackages = (packageInfo?.declared_count ?? 0) > 1 || packageItems.length >= 2;
  const currentDecision = decision.data;

  return (
    <>
      <PageHeader
        eyebrow="PROJECT / WORKSPACE"
        title={project.name}
        subtitle={[project.buyer, project.code].filter(Boolean).join(" · ") || "投标项目工作台"}
        actions={
          <div className="flex items-center gap-2">
            {project.decision_status ? (
              <Badge tone={DECISION_TONES[project.decision_status] ?? "slate"}>
                {project.decision_status}
              </Badge>
            ) : null}
            <Link className="rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm shadow-sm" href="/projects">
              返回项目
            </Link>
          </div>
        }
      />
      <ProjectStageNav
        projectId={params.id}
        project={project}
        activeTab={workspaceTab}
        onTabChange={setWorkspaceTab}
      />

      {message ? (
        <div className="fixed right-6 top-20 z-50 max-w-sm rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-lg">
          <div className="flex items-start justify-between gap-3">
            <span>{message}</span>
            <button
              type="button"
              onClick={() => setMessage(null)}
              className="text-slate-400 transition hover:text-slate-600"
              aria-label="关闭提示"
            >
              ×
            </button>
          </div>
        </div>
      ) : null}

      <div id="project-stage-content" className="scroll-mt-24 lg:scroll-mt-[280px]">
        {workspaceTab === "parse" ? (
          <>
      <div className="mb-4 flex justify-end">
        <Link
          href="/file-check"
          className="text-xs text-blue-600 hover:underline"
          title="检查招标文件是否指定品牌、限制地域、设置不合理门槛"
        >
          招标文件体检（买方侧工具）
        </Link>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5">
          <h2 className="text-sm font-semibold">投标企业</h2>
          <p className="mt-1 text-xs text-slate-500">用于评分证据匹配与资格预审。</p>
          <div className="mt-3 flex gap-2">
            <Select className="w-full" value={companyId} onChange={(event) => setCompanyId(event.target.value)}>
              <option value="">选择企业</option>
              {companies.data?.map((company) => (
                <option key={company.id} value={company.id}>
                  {company.name}
                </option>
              ))}
            </Select>
            <Button variant="secondary" onClick={() => saveCompany.mutate()} disabled={!companyId || saveCompany.isPending}>
              保存
            </Button>
          </div>
          {companies.data && companies.data.length === 0 ? (
            <Link className="mt-2 inline-block text-xs text-blue-600 hover:underline" href="/assets">
              先到企业资产库创建企业
            </Link>
          ) : null}
        </Card>

        {showPackages ? (
          <Card className="p-5">
            <h2 className="text-sm font-semibold">投标标包</h2>
            <p className="mt-1 text-xs text-slate-500">
              招标文件划分为多个标包，请指定本次投标的包件。
            </p>
            <div className="mt-3 flex gap-2">
              <Select value={packageCode} onChange={(event) => setPackageCode(event.target.value)}>
                <option value="">未指定</option>
                {packageItems.map((item) => (
                  <option key={item.code} value={item.code}>
                    标包{item.code} {item.name ?? ""}
                  </option>
                ))}
              </Select>
              <Button variant="secondary" onClick={() => savePackage.mutate()} disabled={savePackage.isPending}>
                保存
              </Button>
            </div>
            <p className="mt-2 text-xs text-slate-500">当前：{packageCode || "未指定"}</p>
          </Card>
        ) : null}

        <Card className="p-5 lg:col-span-2" id="tender-upload">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">招标文件</h2>
              <p className="mt-1 text-xs text-slate-500">
                支持 docx / pdf（文本层或 OCR）/ txt / md / 图片 / pptx / xlsx / epub / html / ofd 等 MinerU 全集类型。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm"
                value={documentKind}
                onChange={(event) => setDocumentKind(event.target.value)}
              >
                <option value="tender">招标文件</option>
                <option value="clarification">澄清/答疑</option>
                <option value="amendment">补遗/更正</option>
                <option value="final">最终标书（装配体检）</option>
              </select>
              <input
                type="file"
                accept=".docx,.pdf,.txt,.md,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif,.doc,.ppt,.pptx,.xls,.xlsx,.csv,.rtf,.odt,.ods,.odp,.epub,.html,.mhtml,.ofd"
                onChange={handleUpload}
                disabled={upload.isPending}
              />
            </div>
          </div>
          <div className="mt-3 space-y-2">
            {detail.data.documents.length === 0 ? (
              <p className="text-sm text-slate-500">尚未上传招标文件。</p>
            ) : (
              detail.data.documents.map((document) => (
                <div key={document.id} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{document.filename}</span>
                    <div className="flex items-center gap-2">
                      <Badge tone="slate">
                        {document.document_kind === "amendment"
                          ? "补遗/更正"
                          : document.document_kind === "clarification"
                            ? "澄清/答疑"
                            : document.document_kind === "final"
                              ? "最终标书"
                              : "招标文件"}
                      </Badge>
                      {document.ocr_used ? <Badge tone="blue">OCR</Badge> : null}
                      <Badge tone={document.parse_status === "parsed" ? "green" : "rose"}>
                        {document.parse_status}
                      </Badge>
                    </div>
                  </div>
                  {document.parse_error ? (
                    <p className="mt-1 text-xs text-rose-600">{document.parse_error}</p>
                  ) : null}
                  {document.document_kind === "final" && document.assembly_meta?.analyzed ? (
                    <p className="mt-1 text-xs text-slate-500">
                      装配体检：封面 {document.assembly_meta.has_cover_name ? "✓" : "✗"} · 目录{" "}
                      {document.assembly_meta.has_toc ? "✓" : "✗"} · 页码{" "}
                      {document.assembly_meta.has_page_numbers === null
                        ? "PDF 需人工核对"
                        : document.assembly_meta.has_page_numbers
                          ? "✓"
                          : "✗"}
                      {document.assembly_meta.page_count
                        ? ` · ${document.assembly_meta.page_count} 页`
                        : ""}
                    </p>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </Card>
      </div>

      <BidderVerificationPanel
        ownCompanyName={companies.data?.find((company) => company.id === companyId)?.name ?? null}
        competitor={bidResult.data?.competitor ?? null}
      />

      <Card className="mt-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">约束集</h2>
            <p className="mt-1 text-xs text-slate-500">
              {detail.data.latest_constraint
                ? `版本 v${detail.data.latest_constraint.version} · 解析器 ${String(constraint?.parse_meta?.parser_version ?? "")}`
                : "上传并解析后生成"}
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => buildMatrix.mutate()}
              disabled={!detail.data.latest_constraint || buildMatrix.isPending}
            >
              {buildMatrix.isPending ? "生成中…" : "生成评分矩阵"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => runQualification.mutate()}
              disabled={!detail.data.latest_constraint || runQualification.isPending}
            >
              {runQualification.isPending ? "预审中…" : "运行资格预审"}
            </Button>
          </div>
        </div>

        {!constraint ? (
          <p className="mt-4 text-sm text-slate-500">暂无可展示的约束集。</p>
        ) : (
          <div className="mt-4 space-y-5">
            <div className="grid gap-3 sm:grid-cols-4">
              <Metric label="评分项" value={String(constraint.scoring_items.length)} />
              <Metric label="废标条款" value={String(constraint.reject_clauses.length)} />
              <Metric label="资格要求" value={String(constraint.qualification_requirements.length)} />
              <Metric label="预算" value={money(constraint.budget.amount)} />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold">评分项</h3>
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="min-w-full text-sm">
                    <thead className="bg-slate-50 text-left text-xs text-slate-500">
                      <tr>
                        <th className="px-3 py-2">评分项</th>
                        <th className="px-3 py-2">分值</th>
                        <th className="px-3 py-2">类别</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {constraint.scoring_items.map((item, index) => (
                        <tr key={`${item.item}-${index}`}>
                          <td className="px-3 py-2">{item.item}</td>
                          <td className="px-3 py-2">{item.score ?? "—"}</td>
                          <td className="px-3 py-2">{item.category ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold">资格要求</h3>
                <div className="space-y-2">
                  {constraint.qualification_requirements.map((item, index) => (
                    <div key={`${item.text}-${index}`} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                      <div className="flex items-center gap-2">
                        <Badge>{item.category ?? "OTHER"}</Badge>
                        {item.level ? <span className="text-xs text-slate-500">{item.level}</span> : null}
                        {item.quantity ? <span className="text-xs text-slate-500">数量 ≥ {item.quantity}</span> : null}
                      </div>
                      <p className="mt-1 text-slate-700">{item.text}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <Panel title="时间节点" items={constraint.timeline.map((item) => `${item.event}：${item.at}`)} />
              <Panel title="废标条款" items={constraint.reject_clauses.map((item) => item.text)} />
              <Panel
                title="暗标"
                items={[
                  constraint.dark_bid.required ? "需要暗标处理" : "未识别到暗标要求",
                  ...constraint.dark_bid.rules.map((item) => item.text),
                ]}
              />
            </div>
          </div>
        )}
      </Card>

      <Card className="mt-4 p-5">
        <h2 className="text-sm font-semibold">评分响应矩阵</h2>
        {matrix.isLoading ? (
          <p className="mt-2 text-sm text-slate-500">加载中…</p>
        ) : matrix.isError ? (
          <ErrorState error={matrix.error} onRetry={() => matrix.refetch()} />
        ) : !matrix.data || matrix.data.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">尚未生成。点击“生成评分矩阵”。</p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-3 py-2">评分项</th>
                  <th className="px-3 py-2">分值</th>
                  <th className="px-3 py-2">覆盖</th>
                  <th className="px-3 py-2">匹配资产</th>
                  <th className="px-3 py-2">缺失证据</th>
                  <th className="px-3 py-2">章节</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {matrix.data.map((row) => (
                  <tr key={row.scoring_item_id}>
                    <td className="px-3 py-2">{row.item}</td>
                    <td className="px-3 py-2">{row.score ?? "—"}</td>
                    <td className="px-3 py-2">
                      <Badge tone={COVERAGE_TONES[row.coverage_status] ?? "slate"}>{row.coverage_status}</Badge>
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      {row.matched_assets.length ? row.matched_assets.map((asset) => asset.name).join("、") : "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      {row.missing_evidence.length ? row.missing_evidence.join("、") : "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-600">{row.section_mapping ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="rounded-xl border border-slate-200 bg-white/70 px-4 py-2">
        <ProjectStatusActions project={project} />
      </div>
          </>
        ) : null}

        {workspaceTab === "parse" ? (
          <>
            <DocumentDiffPanel projectId={params.id} />
            <MoreTools title="更多决策工具">
              <BidRiskPanel risks={constraint?.bid_risks} />
              <DecisionCardPanel projectId={params.id} />
              <PricingStrategyPanel projectId={params.id} />
              <PricingCostSettings />
              <QuoteSheetPanel projectId={params.id} />
            </MoreTools>
          </>
        ) : null}

        {workspaceTab === "produce" ? (
          <>
            <Card className="mt-4 p-5" id="outline-panel">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold">模板与大纲</h2>
                  <p className="mt-1 text-xs text-slate-500">
                    结构模板决定章节骨架，格式模板决定导出版式；应用模板会重建未审批章节。
                  </p>
                  <p className="mt-2 text-xs text-slate-600">
                    当前：{project.template_code ?? "未选结构模板"} / {project.format_code ?? "未选格式模板"}
                  </p>
                </div>
                <Link
                  href="/templates"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm text-slate-700 shadow-sm"
                >
                  去模板与技能 <ArrowRight size={14} />
                </Link>
              </div>
            </Card>
            <ProjectSections projectId={params.id} />
            <Card className="mt-4 p-5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold">AI 用量</h2>
                  <p className="mt-1 text-xs text-slate-500">
                    按实际 AI 产出计量：章节生成与 AI 评标计入，规则模式与大纲展开不计。
                  </p>
                </div>
                <Button
                  variant="secondary"
                  onClick={() => aiUsage.refetch()}
                  disabled={aiUsage.isFetching}
                >
                  {aiUsage.isFetching ? "刷新中…" : "刷新用量"}
                </Button>
              </div>
              {aiUsage.data ? (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <Metric label="AI 篇数" value={`${aiUsage.data.pieces} 篇`} />
                  <Metric label="AI 字数" value={`${aiUsage.data.words} 字`} />
                </div>
              ) : (
                <p className="mt-3 text-sm text-slate-500">
                  {aiUsage.error ? "暂无法读取 AI 用量。" : "加载中…"}
                </p>
              )}
            </Card>
            <MoreTools title="更多生产工具">
              <DefenseOutlinePanel projectId={params.id} />
              <PolicyQaPanel projectId={params.id} />
            </MoreTools>
          </>
        ) : null}

        {workspaceTab === "response" ? (
          <>
            <div className="mt-4 flex justify-end">
              <Link
                href={`/projects/${params.id}/delivery`}
                className="text-xs text-blue-600 hover:underline"
              >
                客户交付摘要
              </Link>
            </div>
            <ReadinessPanel projectId={params.id} />
            <SubmissionReceiptPanel projectId={params.id} />
            <SelfCheckPanel projectId={params.id} />
            <BidderSimilarityPanel />
            <ComplianceResponses projectId={params.id} />
          </>
        ) : null}

        {workspaceTab === "deliver" ? (
          <>
            <CompliancePanel projectId={params.id} />
            <ContractReviewCard projectId={params.id} />
            <CommitmentsPanel projectId={params.id} />
          </>
        ) : null}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {workspaceTab === "parse" ? (
          <>
        <Card className="p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">资格预审</h2>
            {qualification ? (
              <div className="flex gap-1">
                <Badge tone="green">PASS {qualification.summary.PASS ?? 0}</Badge>
                <Badge tone="rose">FAIL {qualification.summary.FAIL ?? 0}</Badge>
                <Badge tone="amber">UNKNOWN {qualification.summary.UNKNOWN ?? 0}</Badge>
              </div>
            ) : null}
          </div>
          {!qualification ? (
            <p className="mt-2 text-sm text-slate-500">点击上方“运行资格预审”。</p>
          ) : (
            <div className="mt-3 space-y-2">
              {qualification.items.map((item) => (
                <div key={`${item.requirement_index}-${item.text}`} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{item.category}</span>
                    <Badge tone={QUALIFICATION_TONES[item.status] ?? "slate"}>{item.status}</Badge>
                  </div>
                  <p className="mt-1 text-slate-700">{item.text}</p>
                  <p className="mt-1 text-xs text-slate-500">{item.reason}</p>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card className="p-5" id="decision-card">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Bid-No-Bid</h2>
            <Button variant="secondary" onClick={() => runDecision.mutate()} disabled={!constraint || runDecision.isPending}>
              {runDecision.isPending ? "生成中…" : "生成决策"}
            </Button>
          </div>
          {!currentDecision ? (
            <p className="mt-2 text-sm text-slate-500">尚未生成决策报告。</p>
          ) : (
            <div className="mt-3">
              <Badge tone={DECISION_TONES[currentDecision.status] ?? "slate"}>{currentDecision.status}</Badge>
              <p className="mt-2 text-sm text-slate-700">{currentDecision.summary}</p>
              <dl className="mt-3 space-y-2 text-sm">
                {currentDecision.factors.map((factor) => (
                  <div key={factor.name} className="flex justify-between gap-3">
                    <dt className="text-slate-500">{factor.name}</dt>
                    <dd className="text-right">{factor.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </Card>
          </>
        ) : null}

        {workspaceTab === "deliver" ? (
          <>
        <Card className="p-5 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">AI 评标模拟</h2>
              <p className="mt-1 text-xs text-slate-500">
                按评分项逐条评分、引用章节证据、标记缺口与需复核项；需配置 LLM 网关。
              </p>
            </div>
            <Button
              variant="secondary"
              onClick={() => runEvaluation.mutate()}
              disabled={runEvaluation.isPending}
            >
              {runEvaluation.isPending ? "评估中…" : "运行 AI 评标"}
            </Button>
          </div>
          {!evaluation.data ? (
            <p className="mt-3 text-sm text-slate-500">尚未运行 AI 评标。</p>
          ) : evaluation.data.status !== "completed" ? (
            <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              评估失败：
              {evaluation.data.error_message ?? evaluation.data.error_code ?? "unknown"}
            </div>
          ) : !evaluationSummary ? (
            <p className="mt-3 text-sm text-slate-500">评估结果缺少摘要。</p>
          ) : (
            <div className="mt-3 space-y-4">
              <div className="grid gap-3 sm:grid-cols-4">
                <Metric
                  label="总得分"
                  value={`${evaluationSummary.total_score} / ${evaluationSummary.max_score}`}
                  hint={
                    evaluationPartial && evaluationCoverage
                      ? `已评 ${evaluationCoverage.evaluated_items}/${evaluationCoverage.expected_items} 项，应评满分 ${evaluationSummary.expected_max_score ?? "—"}`
                      : undefined
                  }
                />
                <Metric
                  label="得分率"
                  value={`${evaluationSummary.percentage}%`}
                  hint={evaluationPartial ? "仅按已评项计算" : undefined}
                />
                <Metric
                  label="需复核"
                  value={`${evaluationSummary.needs_review_count} 项`}
                  hint={evaluationReviewHint}
                />
                <div className="rounded-lg bg-slate-50 px-3 py-2">
                  <p className="text-xs text-slate-500">评级</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <Badge tone={BAND_TONES[evaluationSummary.band] ?? "slate"}>
                      {BAND_LABELS[evaluationSummary.band] ?? evaluationSummary.band}
                    </Badge>
                    {evaluationProvisional ? <Badge tone="slate">暂定</Badge> : null}
                  </div>
                </div>
              </div>
              {evaluationSummary.overall_summary ? (
                <p className="text-sm text-slate-700">{evaluationSummary.overall_summary}</p>
              ) : null}
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="py-2 pr-3">评分项</th>
                      <th className="py-2 pr-3">得分</th>
                      <th className="py-2 pr-3">理由 / 证据</th>
                      <th className="py-2">缺口</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {evaluationItems.map((item) => (
                      <tr key={item.scoring_item_id}>
                        <td className="py-2 pr-3 align-top">
                          <div className="font-medium">{item.item}</div>
                          <div className="text-xs text-slate-500">{item.category}</div>
                        </td>
                        <td className="py-2 pr-3 align-top">
                          {item.score === null ? (
                            <span className="text-slate-400">未评 / {item.max_score}</span>
                          ) : (
                            <>
                              <span className="font-semibold">{item.score}</span> /{" "}
                              {item.max_score}
                            </>
                          )}
                          <div className="mt-1 flex flex-wrap items-center gap-1">
                            {item.review_level === "required" ? (
                              <Badge tone="rose">强制复核</Badge>
                            ) : item.review_level === "suggested" ? (
                              <Badge tone="amber">建议复核</Badge>
                            ) : item.needs_review ? (
                              <Badge tone="amber">需复核</Badge>
                            ) : null}
                            {item.matrix_conflict ? (
                              <span title={item.matrix_conflict}>
                                <Badge tone="rose">矩阵矛盾</Badge>
                              </span>
                            ) : null}
                            {item.confidence_score != null ? (
                              <span
                                className="text-xs text-slate-500"
                                title={item.review_reason ?? undefined}
                              >
                                置信 {item.confidence_score.toFixed(2)}
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="max-w-md py-2 pr-3 align-top">
                          <p className="text-slate-700">{item.rationale}</p>
                          {item.evidence.slice(0, 2).map((evidence) => (
                            <p key={evidence} className="mt-1 text-xs text-slate-500">
                              证据：{evidence}
                            </p>
                          ))}
                        </td>
                        <td className="max-w-xs py-2 align-top text-slate-600">
                          {item.gaps.length === 0 ? (
                            "—"
                          ) : (
                            <ul className="list-disc space-y-1 pl-4">
                              {item.gaps.slice(0, 3).map((gap) => (
                                <li key={gap}>{gap}</li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <h3 className="text-sm font-semibold">主要风险</h3>
                  {evaluationSummary.risks.length === 0 ? (
                    <p className="mt-1 text-sm text-slate-500">无</p>
                  ) : (
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-slate-600">
                      {evaluationSummary.risks.map((risk) => (
                        <li key={risk}>{risk}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h3 className="text-sm font-semibold">整改建议</h3>
                  {evaluationSummary.improvements.length === 0 ? (
                    <p className="mt-1 text-sm text-slate-500">无</p>
                  ) : (
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-slate-600">
                      {evaluationSummary.improvements.map((improvement) => (
                        <li key={improvement}>{improvement}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
              {evaluationWarnings.length > 0 ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  {evaluationWarnings.slice(0, 3).join("；")}
                </div>
              ) : null}
            </div>
          )}
        </Card>

        <Card className="p-5 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">结果复盘</h2>
              <p className="mt-1 text-xs text-slate-500">
                记录中标/失标与原因，反哺赢率与失标分析。
              </p>
            </div>
            {bidResult.data ? (
              <Badge tone={OUTCOME_TONES[bidResult.data.outcome] ?? "slate"}>
                {OUTCOME_LABELS[bidResult.data.outcome] ?? bidResult.data.outcome}
              </Badge>
            ) : null}
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <Select
              className="w-full"
              value={resultForm.outcome}
              onChange={(event) => updateResult("outcome", event.target.value)}
            >
              <option value="won">中标</option>
              <option value="lost">未中标</option>
              <option value="cancelled">流标 / 终止</option>
            </Select>
            {resultForm.outcome === "won" ? (
              <Input
                type="number"
                min="0"
                step="0.01"
                placeholder="中标金额（元）"
                value={resultForm.win_amount}
                onChange={(event) => updateResult("win_amount", event.target.value)}
              />
            ) : null}
            {resultForm.outcome === "lost" ? (
              <Select
                className="w-full"
                value={resultForm.loss_reason}
                onChange={(event) => updateResult("loss_reason", event.target.value)}
              >
                {LOSS_REASONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    失标原因：{label}
                  </option>
                ))}
              </Select>
            ) : null}
            <Input
              placeholder="竞争对手（可选）"
              value={resultForm.competitor}
              onChange={(event) => updateResult("competitor", event.target.value)}
            />
            <textarea
              className="min-h-20 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-slate-500 sm:col-span-2"
              placeholder="复盘备注：关键差距、对手策略、下次改进（可选）"
              value={resultForm.notes}
              onChange={(event) => updateResult("notes", event.target.value)}
            />
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <span className="text-xs text-slate-500">
              {bidResult.data
                ? `最近记录：${bidResult.data.recorded_at.slice(0, 16).replace("T", " ")}`
                : "尚未记录复盘结果。"}
            </span>
            <Button onClick={() => saveResult.mutate()} disabled={saveResult.isPending}>
              {saveResult.isPending ? "保存中…" : "保存复盘"}
            </Button>
          </div>
        </Card>
          </>
        ) : null}
      </div>
      </div>
    </>
  );
}

function MoreTools({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-4 rounded-2xl border border-slate-200/70 bg-white/70 p-4">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span>
          <span className="text-sm font-semibold text-slate-800">{title}</span>
          <span className="ml-2 text-xs text-slate-400">按需展开，避免干扰主流程</span>
        </span>
        <span className="text-xs font-medium text-electric-600">{open ? "收起" : "展开"}</span>
      </button>
      {open ? <div className="mt-2">{children}</div> : null}
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

function Panel({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      {items.length === 0 ? (
        <p className="text-sm text-slate-500">未识别到相关内容。</p>
      ) : (
        <ul className="space-y-1 text-sm text-slate-600">
          {items.slice(0, 5).map((item, index) => (
            <li key={`${title}-${index}`} className="rounded bg-slate-50 px-2 py-1">
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
