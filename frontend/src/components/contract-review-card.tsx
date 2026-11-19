"use client";

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Scale } from "lucide-react";

import { Badge, Button, Card } from "@/components/ui";
import { ApiError, apiFetch, apiFetchBlob } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { locateExcerpt, splitByRange } from "@/lib/quote-anchor";
import type {
  ContractFinding,
  ContractReview,
  ContractReviewTemplate,
} from "@/lib/types";

const KIND_LABELS: Record<string, string> = {
  deviation: "实质性偏差",
  risk: "风险",
  missing: "缺口",
  unknown: "无法判断",
};

const SEVERITY_TONES: Record<string, "rose" | "amber" | "slate"> = {
  high: "rose",
  medium: "amber",
  low: "slate",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待确认",
  confirmed: "已确认",
  dismissed: "已驳回",
};

const DETECTION_LABELS: Record<string, string> = {
  rule: "规则",
  heuristic: "启发式",
  llm: "AI",
};

type LlmBatches = {
  total?: number;
  failed?: number;
  errors?: string[];
  truncated?: boolean;
  truncated_count?: number;
};
type RevisionDiffRow = {
  rule_id: string | null;
  kind: string;
  severity: string;
  message: string;
  status?: string;
};
type RevisionDiff = {
  added?: RevisionDiffRow[];
  resolved?: RevisionDiffRow[];
  carried?: RevisionDiffRow[];
};

function badgeToneForConfidence(value: number | null): "green" | "amber" | "slate" {
  if (value == null) return "slate";
  if (value >= 0.9) return "green";
  if (value >= 0.5) return "amber";
  return "slate";
}

export function ContractReviewCard({ projectId }: { projectId: string }) {
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "project:write");
  const canExport = hasPermission(user?.permissions, "compliance:export");
  const queryClient = useQueryClient();

  // 高级选项（审查点模板/补充关注点/核心目的）与条款定位的本地状态
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [extraPoints, setExtraPoints] = useState("");
  const [corePurpose, setCorePurpose] = useState("");
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  // 条款原文面板受控展开：点击证据行定位时必须自动展开，否则高亮不可见（review P2-1）
  const [clausePanelOpen, setClausePanelOpen] = useState(false);
  const clausePanelRef = useRef<HTMLDivElement | null>(null);
  const reReviewInputRef = useRef<HTMLInputElement | null>(null);

  const query = useQuery({
    queryKey: ["contract-reviews", projectId],
    queryFn: () => apiFetch<ContractReview[]>(`/projects/${projectId}/contract-reviews`),
    retry: false,
  });

  // 列表端点按 API 契约只返回审查概要（无 findings/disclaimer）；
  // 刷新页面后必须再按 id 取一次详情，否则确认/驳回/导出入口会全部消失。
  const latestId = query.data?.[0]?.id;
  const detailQuery = useQuery({
    queryKey: ["contract-review-detail", projectId, latestId],
    queryFn: () => apiFetch<ContractReview>(`/projects/${projectId}/contract-reviews/${latestId}`),
    enabled: Boolean(latestId),
    retry: false,
  });

  const templatesQuery = useQuery({
    queryKey: ["contract-review-templates", projectId],
    queryFn: () =>
      apiFetch<ContractReviewTemplate[]>(
        `/projects/${projectId}/contract-reviews/review-templates`,
      ),
    enabled: canWrite,
    retry: false,
  });

  const buildUploadForm = (file: File) => {
    const data = new FormData();
    data.append("file", file);
    if (selectedTemplateId) data.append("template_ids", selectedTemplateId);
    if (extraPoints.trim()) data.append("review_points", extraPoints);
    if (corePurpose.trim()) data.append("core_purpose", corePurpose);
    return data;
  };

  const upload = useMutation({
    mutationFn: async (file: File) =>
      apiFetch<ContractReview & { findings: ContractFinding[]; disclaimer: string }>(
        `/projects/${projectId}/contract-reviews`,
        { method: "POST", body: buildUploadForm(file) },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contract-reviews", projectId] });
      queryClient.invalidateQueries({ queryKey: ["contract-review-detail", projectId] });
    },
  });

  const decide = useMutation({
    mutationFn: (input: { findingId: string; status: "confirmed" | "dismissed" }) =>
      apiFetch<ContractReview>(
        `/projects/${projectId}/contract-reviews/findings/${input.findingId}`,
        { method: "PATCH", body: JSON.stringify({ status: input.status }) },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contract-reviews", projectId] });
      queryClient.invalidateQueries({ queryKey: ["contract-review-detail", projectId] });
    },
  });

  const exportReview = useMutation({
    mutationFn: () => apiFetchBlob(`/projects/${projectId}/contract-reviews/${latestId}/export`),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `合同审查清单_${projectId}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    },
  });

  const deepReview = useMutation({
    mutationFn: () =>
      apiFetch<ContractReview & { findings: ContractFinding[]; disclaimer: string }>(
        `/projects/${projectId}/contract-reviews/${latestId}/deep-review`,
        { method: "POST" },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contract-reviews", projectId] });
      queryClient.invalidateQueries({ queryKey: ["contract-review-detail", projectId] });
    },
  });

  const reReview = useMutation({
    mutationFn: async (file: File) => {
      const data = new FormData();
      data.append("file", file);
      return apiFetch<ContractReview & { findings: ContractFinding[]; disclaimer: string }>(
        `/projects/${projectId}/contract-reviews/${latestId}/re-review`,
        { method: "POST", body: data },
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contract-reviews", projectId] });
      queryClient.invalidateQueries({ queryKey: ["contract-review-detail", projectId] });
    },
  });

  const latest = query.data?.[0];
  const detail = upload.data ?? reReview.data ?? detailQuery.data ?? null;
  const findings: ContractFinding[] =
    upload.data?.findings ?? reReview.data?.findings ?? detail?.findings ?? [];
  const clauses = useMemo(() => detail?.clauses ?? [], [detail]);
  const summary = (detail?.summary_json ?? latest?.summary_json) as
    | {
        clauses?: number;
        findings?: { high?: number; medium?: number; low?: number };
        llm_batches?: LlmBatches;
        revision_diff?: RevisionDiff;
        review_points?: number;
      }
    | undefined;
  const high = summary?.findings?.high ?? 0;
  const medium = summary?.findings?.medium ?? 0;
  const low = summary?.findings?.low ?? 0;
  const llmBatches = summary?.llm_batches;
  const revisionDiff = detail?.summary_json?.revision_diff as RevisionDiff | undefined;
  const templateMatch = detail?.template_matches as
    | { matched?: { name: string; basis: string; score?: number | null }[]; selected?: string[] }
    | null
    | undefined;

  const clausesById = useMemo(
    () => new Map(clauses.map((clause) => [clause.id, clause])),
    [clauses],
  );
  const selectedFinding = findings.find((f) => f.id === selectedFindingId) ?? null;
  const selectedClause = selectedFinding?.clause_id
    ? clausesById.get(selectedFinding.clause_id) ?? null
    : null;
  const highlight = useMemo(() => {
    if (!selectedClause || !selectedFinding) return null;
    const excerpt = String(selectedFinding.evidence_span?.excerpt ?? "");
    return locateExcerpt(excerpt, selectedClause.text);
  }, [selectedClause, selectedFinding]);

  const jumpToClause = (findingId: string) => {
    setSelectedFindingId(findingId);
    setClausePanelOpen(true);
    // 等条款面板渲染后滚动定位
    requestAnimationFrame(() => clausePanelRef.current?.scrollIntoView({ block: "nearest" }));
  };

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Scale size={16} className="text-electric-600" />
        <h2 className="text-sm font-semibold">合同审查</h2>
        {summary ? (
          <>
            <Badge tone="slate">条款 {summary.clauses ?? 0}</Badge>
            {high > 0 ? <Badge tone="rose">高 {high}</Badge> : null}
            {medium > 0 ? <Badge tone="amber">中 {medium}</Badge> : null}
            {low > 0 ? <Badge tone="slate">低 {low}</Badge> : null}
            {summary.review_points ? (
              <Badge tone="slate">审查点 {summary.review_points}</Badge>
            ) : null}
          </>
        ) : null}
        {canWrite ? (
          <label className="ml-auto">
            <input
              type="file"
              accept=".docx,.pdf,.txt,.md,.png,.jpg,.jpeg,.webp"
              className="hidden"
              disabled={upload.isPending}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload.mutate(file);
                event.target.value = "";
              }}
            />
            <Button variant="secondary" disabled={upload.isPending}>
              {upload.isPending ? "审查中…" : "上传合同并审查"}
            </Button>
          </label>
        ) : null}
      </div>

      <p className="mt-2 text-xs leading-5 text-slate-500">
        {detail?.disclaimer ??
          "规则与启发式审查结果，非法律意见，请结合法务复核。三方一致性对照基于招标约束集与合同条款的启发式匹配。"}
      </p>

      {canWrite ? (
        <details className="mt-2 rounded-xl border border-slate-100 bg-slate-50/40 px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-slate-600">
            高级选项：审查点模板与关注点
          </summary>
          <div className="mt-2 space-y-2">
            <label className="block text-xs text-slate-600">
              审查点模板（不选则按合同内容自动匹配）
              <select
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs"
                value={selectedTemplateId}
                onChange={(event) => setSelectedTemplateId(event.target.value)}
              >
                <option value="">自动匹配</option>
                {(templatesQuery.data ?? []).map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}（{template.review_points.length} 条审查点
                    {template.is_system ? "" : "，租户"}）
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-slate-600">
              补充关注点（每行一条，始终保留在最前）
              <textarea
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs"
                rows={2}
                value={extraPoints}
                onChange={(event) => setExtraPoints(event.target.value)}
                placeholder={"例如：付款节点与里程碑对应关系"}
              />
            </label>
            <label className="block text-xs text-slate-600">
              核心审查目的（可选，覆盖模板默认）
              <input
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs"
                value={corePurpose}
                onChange={(event) => setCorePurpose(event.target.value)}
                placeholder="例如：重点核查付款与违约责任对等性"
              />
            </label>
          </div>
        </details>
      ) : null}

      {upload.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {upload.error instanceof ApiError || upload.error instanceof Error
            ? upload.error.message
            : "审查失败"}
        </p>
      ) : null}
      {deepReview.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {deepReview.error instanceof Error ? deepReview.error.message : "深度审查失败"}
        </p>
      ) : null}
      {reReview.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {reReview.error instanceof Error ? reReview.error.message : "修订对照失败"}
        </p>
      ) : null}
      {decide.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {decide.error instanceof Error ? decide.error.message : "操作失败"}
        </p>
      ) : null}
      {exportReview.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {exportReview.error instanceof Error ? exportReview.error.message : "导出失败"}
        </p>
      ) : null}

      {/* 深审部分批次失败/截断：显式黄条，不静默 */}
      {llmBatches && (llmBatches.failed || llmBatches.truncated) ? (
        <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {llmBatches.failed
            ? `深度审查部分批次失败（${llmBatches.failed}/${llmBatches.total ?? "?"}），结果可能不完整，可重试。`
            : ""}
          {llmBatches.truncated ? (
            <span className="mt-1 block">
              结论总量超出上限，已截断 {llmBatches.truncated_count ?? "?"} 条未入库（仅保留前
              50 条）。
            </span>
          ) : null}
          {llmBatches.errors?.length ? (
            <span className="mt-1 block text-xs text-amber-700">
              {llmBatches.errors.slice(0, 2).join("；")}
            </span>
          ) : null}
        </p>
      ) : null}

      {!detail ? (
        query.error ? (
          <p className="mt-3 text-sm text-rose-600">
            {query.error instanceof Error ? query.error.message : "加载失败"}
          </p>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            尚未上传合同。上传中标后的合同草案，将自动进行条款切分、风险规则与三方一致性对照。
          </p>
        )
      ) : null}

      {templateMatch?.matched?.length ? (
        <p className="mt-2 text-xs text-slate-500">
          审查点模板：
          {templateMatch.matched
            .map(
              (item) =>
                `${item.name}${
                  item.basis === "manual"
                    ? "（手动指定）"
                    : item.basis === "fallback_general"
                      ? "（兜底）"
                      : `（命中 ${item.score ?? 0}）`
                }`,
            )
            .join("、")}
        </p>
      ) : null}

      {findings.length > 0 ? (
        <div className="mt-3 space-y-2">
          {findings.map((finding) => {
            const confidenceValue =
              finding.confidence == null ? null : Number(finding.confidence);
            const span = finding.evidence_span ?? {};
            const quoteVerified = span.quote_verified;
            const lawVerified = span.legal_basis_verified;
            return (
              <div
                key={finding.id}
                className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge tone={KIND_LABELS[finding.kind] ? (SEVERITY_TONES[finding.severity] ?? "slate") : "slate"}>
                    {KIND_LABELS[finding.kind] ?? finding.kind}
                  </Badge>
                  <span className="text-xs text-slate-400">
                    {finding.rule_id ?? finding.source}
                  </span>
                  {finding.detection_method ? (
                    <Badge tone="slate">{DETECTION_LABELS[finding.detection_method] ?? finding.detection_method}</Badge>
                  ) : null}
                  {confidenceValue != null ? (
                    <Badge tone={badgeToneForConfidence(confidenceValue)}>
                      置信 {Math.round(confidenceValue * 100)}%
                    </Badge>
                  ) : null}
                  {quoteVerified === true ? <Badge tone="green">证据已回查</Badge> : null}
                  {quoteVerified === false ? <Badge tone="amber">证据未回查</Badge> : null}
                  {lawVerified === true ? <Badge tone="green">法条已核</Badge> : null}
                  {lawVerified === false ? <Badge tone="amber">法条未核</Badge> : null}
                  <Badge tone={finding.status === "pending" ? "amber" : finding.status === "confirmed" ? "green" : "slate"}>
                    {STATUS_LABELS[finding.status] ?? finding.status}
                  </Badge>
                  {canWrite && finding.status === "pending" ? (
                    <span className="ml-auto flex gap-1.5">
                      <button
                        type="button"
                        className="text-xs font-medium text-green-700 underline"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ findingId: finding.id, status: "confirmed" })}
                      >
                        确认
                      </button>
                      <button
                        type="button"
                        className="text-xs font-medium text-slate-500 underline"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ findingId: finding.id, status: "dismissed" })}
                      >
                        驳回
                      </button>
                    </span>
                  ) : null}
                </div>
                <p className="mt-1.5 text-sm text-slate-800">{finding.message}</p>
                {finding.evidence_span?.excerpt ? (
                  <button
                    type="button"
                    className="mt-1 block text-left text-xs text-slate-500 underline decoration-dotted"
                    onClick={() => jumpToClause(finding.id)}
                  >
                    证据：{String(finding.evidence_span.excerpt).slice(0, 120)}
                    {clauses.length > 0 ? "（点击定位条款原文）" : ""}
                  </button>
                ) : null}
                {finding.redline ? (
                  <div className="mt-1.5 rounded-lg border border-green-100 bg-green-50 px-2.5 py-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-green-800">建议改写</span>
                      <button
                        type="button"
                        className="text-xs text-green-700 underline"
                        onClick={() => {
                          void navigator.clipboard?.writeText(finding.redline ?? "");
                        }}
                      >
                        复制
                      </button>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-green-900">{finding.redline}</p>
                  </div>
                ) : null}
                {finding.legal_basis ? (
                  <p className="mt-1 text-xs text-slate-400">依据：{finding.legal_basis}</p>
                ) : null}
              </div>
            );
          })}
          {latest ? (
            <div className="flex flex-wrap items-center gap-3">
              {canExport ? (
                <button
                  type="button"
                  className="text-xs font-medium text-electric-700 underline disabled:opacity-50"
                  disabled={exportReview.isPending}
                  onClick={() => exportReview.mutate()}
                >
                  {exportReview.isPending ? "导出中…" : "导出审查清单（xlsx）"}
                </button>
              ) : null}
              {canWrite ? (
                <button
                  type="button"
                  className="text-xs font-medium text-electric-700 underline disabled:opacity-50"
                  disabled={deepReview.isPending}
                  onClick={() => deepReview.mutate()}
                >
                  {deepReview.isPending ? "深度审查中…" : "AI 深度审查（按次计费）"}
                </button>
              ) : null}
              {canWrite ? (
                <>
                  <input
                    ref={reReviewInputRef}
                    type="file"
                    accept=".docx,.pdf,.txt,.md,.png,.jpg,.jpeg,.webp"
                    className="hidden"
                    disabled={reReview.isPending}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) reReview.mutate(file);
                      event.target.value = "";
                    }}
                  />
                  <button
                    type="button"
                    className="text-xs font-medium text-electric-700 underline disabled:opacity-50"
                    disabled={reReview.isPending}
                    onClick={() => reReviewInputRef.current?.click()}
                  >
                    {reReview.isPending ? "修订对照中…" : "上传修订版并对照"}
                  </button>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : detail && findings.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">未发现命中项。</p>
      ) : null}

      {revisionDiff ? (
        <div className="mt-3 rounded-xl border border-slate-100 bg-white px-3 py-2.5">
          <h3 className="text-xs font-semibold text-slate-700">
            修订对照（相对上一轮：新增 {revisionDiff.added?.length ?? 0} / 已解决{" "}
            {revisionDiff.resolved?.length ?? 0} / 持续 {revisionDiff.carried?.length ?? 0}）
          </h3>
          {(["added", "resolved", "carried"] as const).map((bucket) =>
            (revisionDiff[bucket] ?? []).length ? (
              <div key={bucket} className="mt-1.5">
                <p className="text-xs font-medium text-slate-500">
                  {bucket === "added" ? "新增" : bucket === "resolved" ? "已解决" : "持续存在"}
                </p>
                <ul className="mt-0.5 space-y-0.5">
                  {revisionDiff[bucket]?.slice(0, 8).map((item, index) => (
                    <li key={`${bucket}-${index}`} className="text-xs text-slate-600">
                      <Badge tone={SEVERITY_TONES[item.severity] ?? "slate"}>
                        {KIND_LABELS[item.kind] ?? item.kind}
                      </Badge>{" "}
                      {item.status === "dismissed" ? (
                        <Badge tone="slate">已驳回</Badge>
                      ) : null}
                      {item.message.slice(0, 90)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}
        </div>
      ) : null}

      {/* 条款原文视图：点击 finding 的证据行定位 + 高亮 */}
      {clauses.length > 0 ? (
        <div ref={clausePanelRef} className="mt-3">
          <details
            open={clausePanelOpen}
            onToggle={(event) => setClausePanelOpen(event.currentTarget.open)}
            className="rounded-xl border border-slate-100 bg-white px-3 py-2.5"
          >
            <summary className="cursor-pointer text-xs font-semibold text-slate-700">
              条款原文（{clauses.length} 条）
              {selectedClause ? ` · 定位：第 ${selectedClause.seq} 条 ${selectedClause.title}` : ""}
            </summary>
            {selectedClause ? (
              <div className="mt-2 rounded-lg bg-slate-50 px-3 py-2">
                <p className="text-xs font-medium text-slate-500">
                  第 {selectedClause.seq} 条 {selectedClause.title}
                  {selectedClause.page != null ? `（第 ${selectedClause.page} 页）` : ""}
                </p>
                <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-800">
                  {(() => {
                    const parts = splitByRange(selectedClause.text, highlight);
                    if (!parts.hit) return selectedClause.text;
                    return (
                      <>
                        {parts.before}
                        <mark className="rounded bg-amber-200 px-0.5">{parts.hit}</mark>
                        {parts.after}
                      </>
                    );
                  })()}
                </p>
              </div>
            ) : (
              <p className="mt-2 text-xs text-slate-500">
                点击任一结论的「证据」行，可定位到对应条款原文并高亮摘录片段。
              </p>
            )}
          </details>
        </div>
      ) : null}
    </Card>
  );
}
