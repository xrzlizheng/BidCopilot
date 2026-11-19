"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, RefreshCw, Send, ShieldCheck, XCircle } from "lucide-react";

import { Badge, Button, Card, ErrorState, Input, Select } from "@/components/ui";
import { EnterpriseIntelPanel } from "@/components/enterprise-intel-panel";
import { ApiError, apiFetch, apiFetchBlob } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";

export type ContractStatus =
  | "draft"
  | "pending_approval"
  | "active"
  | "expired"
  | "terminated"
  | "archived";

export type ContractDraftClause = {
  id: string;
  seq: number;
  title: string;
  text: string;
  category: string;
  source_json: Record<string, unknown>;
};

export type ContractRecordDetail = {
  id: string;
  project_id: string;
  review_id: string | null;
  contract_no: string;
  title: string;
  party_a: string;
  party_b: string;
  signed_date: string | null;
  effective_date: string | null;
  expiry_date: string | null;
  amount: string | null;
  currency: string;
  status: ContractStatus;
  owner_id: string | null;
  created_by: string | null;
  draft_generated_at: string | null;
  draft_disclaimer: string | null;
  expiry_reminded_at: string | null;
  created_at: string;
  draft?: ContractDraftClause[];
};

export type ContractLedgerEntry = {
  id: string;
  record_id: string;
  entry_type: "invoice" | "payment" | "deposit" | "retention";
  amount: string;
  occurred_on: string;
  note: string | null;
  voucher_ref: string | null;
  created_by: string | null;
  created_at: string;
};

export type ContractLedger = {
  record_id: string;
  currency: string;
  summary: {
    contract_amount: string;
    invoice_total: string;
    payment_total: string;
    deposit_total: string;
    retention_total: string;
    outstanding: string;
  };
  entries: ContractLedgerEntry[];
};

export type ContractChange = {
  id: string;
  record_id: string;
  change_type: "change" | "renewal";
  amount_before: string | null;
  amount_after: string | null;
  expiry_before: string | null;
  expiry_after: string | null;
  reason: string;
  approval_status: "pending" | "approved" | "rejected";
  requested_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
};

export type ContractChangeList = {
  items: ContractChange[];
};

const STATUS_META: Record<ContractStatus, { label: string; tone: "slate" | "green" | "amber" | "rose" | "blue" }> = {
  draft: { label: "草稿", tone: "slate" },
  pending_approval: { label: "待审批", tone: "amber" },
  active: { label: "履约中", tone: "green" },
  expired: { label: "已到期", tone: "rose" },
  terminated: { label: "已终止", tone: "rose" },
  archived: { label: "已归档", tone: "slate" },
};

const LEDGER_LABELS: Record<string, string> = {
  invoice: "开票",
  payment: "收款",
  deposit: "保证金",
  retention: "质保金",
};

const CHANGE_STATUS_LABELS: Record<string, string> = {
  pending: "待审批",
  approved: "已通过",
  rejected: "已驳回",
};

const TABS = [
  { key: "overview", label: "概览" },
  { key: "clauses", label: "条款" },
  { key: "ledger", label: "履约" },
  { key: "changes", label: "变更" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export function ContractLifecyclePanel({ recordId }: { recordId: string }) {
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "project:write");
  const canApprove = hasPermission(user?.permissions, "project:approve");
  const queryClient = useQueryClient();

  const detail = useQuery({
    queryKey: ["contract-record", recordId],
    queryFn: () => apiFetch<ContractRecordDetail>(`/contract-records/${recordId}`),
    retry: false,
  });
  const ledger = useQuery({
    queryKey: ["contract-ledger", recordId],
    queryFn: () => apiFetch<ContractLedger>(`/contract-records/${recordId}/ledger`),
    retry: false,
  });
  const changes = useQuery({
    queryKey: ["contract-changes", recordId],
    queryFn: () => apiFetch<ContractChangeList>(`/contract-records/${recordId}/changes`),
    retry: false,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["contract-record", recordId] });
    queryClient.invalidateQueries({ queryKey: ["contract-ledger", recordId] });
    queryClient.invalidateQueries({ queryKey: ["contract-changes", recordId] });
  };

  const action = useMutation({
    mutationFn: (input: "draft" | "submit" | "approve" | "reject") =>
      apiFetch<ContractRecordDetail | { record: ContractRecordDetail }>(
        `/contract-records/${recordId}/${input}`,
        {
          method: "POST",
          body: JSON.stringify({ comment: null }),
        },
      ),
    onSuccess: refresh,
  });

  const exportLedger = useMutation({
    mutationFn: () => apiFetchBlob(`/contract-records/${recordId}/export`),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `合同履约台账_${recordId}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    },
  });

  const [tab, setTab] = useState<TabKey>("overview");
  const [showAllClauses, setShowAllClauses] = useState(false);
  const [ledgerForm, setLedgerForm] = useState({
    entry_type: "invoice",
    amount: "",
    occurred_on: "",
    note: "",
    voucher_ref: "",
  });
  const [ledgerError, setLedgerError] = useState<string | null>(null);

  const addLedger = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/contract-records/${recordId}/ledger`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setLedgerForm({ entry_type: "invoice", amount: "", occurred_on: "", note: "", voucher_ref: "" });
      setLedgerError(null);
      refresh();
    },
    onError: (error: unknown) =>
      setLedgerError(error instanceof ApiError ? error.message : "登记失败"),
  });

  const [changeForm, setChangeForm] = useState({
    change_type: "change",
    amount_after: "",
    expiry_after: "",
    reason: "",
  });
  const [changeError, setChangeError] = useState<string | null>(null);

  const addChange = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/contract-records/${recordId}/changes`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setChangeForm({ change_type: "change", amount_after: "", expiry_after: "", reason: "" });
      setChangeError(null);
      refresh();
    },
    onError: (error: unknown) =>
      setChangeError(error instanceof ApiError ? error.message : "提交变更失败"),
  });

  const decideChange = useMutation({
    mutationFn: (input: { changeId: string; action: "approve" | "reject" }) =>
      apiFetch(`/contract-records/${recordId}/changes/${input.changeId}/${input.action}`, {
        method: "POST",
        body: JSON.stringify({ comment: null }),
      }),
    onSuccess: refresh,
  });

  if (detail.isLoading) {
    return <Card className="p-6 text-sm text-slate-500">加载合同台账…</Card>;
  }
  if (detail.error || !detail.data) {
    return (
      <Card className="p-6 text-sm text-rose-600">
        {detail.error instanceof ApiError || detail.error instanceof Error
          ? detail.error.message
          : "合同台账加载失败"}
      </Card>
    );
  }

  const record = detail.data;
  const status = STATUS_META[record.status] ?? { label: record.status, tone: "slate" as const };
  const summary = ledger.data?.summary;

  const clauseCount = (record.draft ?? []).length;
  const ledgerCount = (ledger.data?.entries ?? []).length;
  const changeCount = (changes.data?.items ?? []).length;
  const tabCounts: Record<TabKey, number | null> = {
    overview: null,
    clauses: clauseCount,
    ledger: ledger.isError ? null : ledgerCount,
    changes: changes.isError ? null : changeCount,
  };
  const clauseSlice = showAllClauses ? record.draft ?? [] : (record.draft ?? []).slice(0, 4);

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <ShieldCheck size={16} className="text-electric-600" />
            <h2 className="text-base font-semibold text-slate-900">{record.title}</h2>
            <Badge tone={status.tone}>{status.label}</Badge>
            <Badge tone="slate">{record.contract_no}</Badge>
          </div>
          <p className="mt-1.5 text-xs text-slate-500">
            {record.party_a || "甲方待填"} → {record.party_b || "乙方待填"} · 到期日{" "}
            {record.expiry_date ?? "未设置"}
          </p>
        </div>
        <div className="text-right">
          <p className="mono-label text-slate-400">CONTRACT AMOUNT</p>
          <p className="tabular text-lg font-semibold text-slate-950">
            {record.currency} {record.amount ?? "—"}
          </p>
        </div>
      </div>

      {record.draft_disclaimer ? (
        <p className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {record.draft_disclaimer}：机器生成条款仅作初稿，签署前必须由法务逐条复核。
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        {canWrite && record.status === "draft" ? (
          <Button variant="secondary" disabled={action.isPending} onClick={() => action.mutate("draft")}>
            <RefreshCw size={14} /> 生成/刷新草稿
          </Button>
        ) : null}
        {canWrite && record.status === "draft" ? (
          <Button disabled={action.isPending} onClick={() => action.mutate("submit")}>
            <Send size={14} /> 提交审批
          </Button>
        ) : null}
        {canApprove && record.status === "pending_approval" ? (
          <>
            <Button disabled={action.isPending} onClick={() => action.mutate("approve")}>
              审批通过
            </Button>
            <Button variant="danger" disabled={action.isPending} onClick={() => action.mutate("reject")}>
              <XCircle size={14} /> 驳回
            </Button>
          </>
        ) : null}
        <Button variant="ghost" disabled={exportLedger.isPending} onClick={() => exportLedger.mutate()}>
          <FileSpreadsheet size={14} /> 导出台账（xlsx）
        </Button>
      </div>

      {action.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {action.error instanceof ApiError
            ? `${action.error.message}（${action.error.code}）`
            : "操作失败"}
        </p>
      ) : null}
      {exportLedger.error ? (
        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {exportLedger.error instanceof Error ? exportLedger.error.message : "导出失败"}
        </p>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center gap-1 border-b border-slate-200/70">
        {TABS.map((item) => {
          const count = tabCounts[item.key];
          const active = tab === item.key;
          return (
            <button
              key={item.key}
              type="button"
              aria-pressed={active}
              onClick={() => setTab(item.key)}
              className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition ${
                active
                  ? "border-aurora-500 text-aurora-700"
                  : "border-transparent text-slate-500 hover:border-slate-200 hover:text-slate-800"
              }`}
            >
              {item.label}
              {count !== null ? (
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                    active ? "bg-aurora-500/10 text-aurora-700" : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {tab === "overview" ? (
        <div className="mt-4 space-y-4">
          <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
            <div className="flex justify-between gap-3 border-b border-slate-100 py-1.5">
              <dt className="text-slate-500">甲方</dt>
              <dd className="text-right font-medium text-slate-800">{record.party_a || "待填"}</dd>
            </div>
            <div className="flex justify-between gap-3 border-b border-slate-100 py-1.5">
              <dt className="text-slate-500">乙方</dt>
              <dd className="text-right font-medium text-slate-800">{record.party_b || "待填"}</dd>
            </div>
            <div className="flex justify-between gap-3 border-b border-slate-100 py-1.5">
              <dt className="text-slate-500">签约日期</dt>
              <dd className="text-right text-slate-700">{record.signed_date ?? "未设置"}</dd>
            </div>
            <div className="flex justify-between gap-3 border-b border-slate-100 py-1.5">
              <dt className="text-slate-500">生效日期</dt>
              <dd className="text-right text-slate-700">{record.effective_date ?? "未设置"}</dd>
            </div>
            <div className="flex justify-between gap-3 border-b border-slate-100 py-1.5">
              <dt className="text-slate-500">到期日期</dt>
              <dd className="text-right text-slate-700">{record.expiry_date ?? "未设置"}</dd>
            </div>
            <div className="flex justify-between gap-3 border-b border-slate-100 py-1.5">
              <dt className="text-slate-500">到期提醒</dt>
              <dd className="text-right text-slate-700">
                {record.expiry_reminded_at ? "已提醒" : "未提醒"}
              </dd>
            </div>
          </dl>

          {summary ? (
            <div className="grid gap-2 sm:grid-cols-3">
              <div className="rounded-xl border border-slate-200/70 bg-white/70 p-3">
                <p className="text-[11px] text-slate-500">已开票</p>
                <p className="tabular mt-1 text-sm font-semibold text-slate-900">
                  {ledger.data?.currency} {summary.invoice_total}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200/70 bg-white/70 p-3">
                <p className="text-[11px] text-slate-500">已收款</p>
                <p className="tabular mt-1 text-sm font-semibold text-slate-900">
                  {ledger.data?.currency} {summary.payment_total}
                </p>
              </div>
              <div
                className={`rounded-xl border p-3 ${
                  Number(summary.outstanding) > 0
                    ? "border-amber-200/80 bg-amber-50/60"
                    : "border-slate-200/70 bg-white/70"
                }`}
              >
                <p
                  className={`text-[11px] ${
                    Number(summary.outstanding) > 0 ? "text-amber-700" : "text-slate-500"
                  }`}
                >
                  未收余额
                </p>
                <p
                  className={`tabular mt-1 text-sm font-semibold ${
                    Number(summary.outstanding) > 0 ? "text-amber-900" : "text-slate-900"
                  }`}
                >
                  {ledger.data?.currency} {summary.outstanding}
                </p>
              </div>
            </div>
          ) : null}

          <p className="text-[11px] leading-5 text-slate-400">
            条款 {clauseCount} 条 · 履约流水 {ledgerCount} 条 · 变更 {changeCount} 条。切换上方标签查看明细。
          </p>
        </div>
      ) : null}

      {tab === "clauses" ? (
        <div className="mt-4">
          <div className="space-y-2">
            {clauseCount === 0 ? (
              <p className="text-xs text-slate-400">尚未生成草稿。可在上方点击「生成/刷新草稿」。</p>
            ) : (
              clauseSlice.map((clause) => (
                <div key={clause.id} className="rounded-xl border border-slate-200/70 bg-white/60 p-3">
                  <p className="text-xs font-medium text-slate-800">
                    第 {clause.seq} 条 · {clause.title}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">{clause.text}</p>
                  <p className="mono-label mt-1 text-[10px] text-slate-400">
                    来源：{String(clause.source_json.type ?? "unknown")}
                  </p>
                </div>
              ))
            )}
          </div>
          {clauseCount > 4 ? (
            <button
              type="button"
              className="mt-2 text-xs text-aurora-600 hover:underline"
              onClick={() => setShowAllClauses((prev) => !prev)}
            >
              {showAllClauses ? "收起条款" : `展开全部 ${clauseCount} 条条款`}
            </button>
          ) : null}
        </div>
      ) : null}

      {tab === "ledger" ? (
        <div className="mt-4">
          {summary ? (
            <div className="grid gap-2 sm:grid-cols-4">
              <div className="rounded-xl border border-slate-200/70 bg-white/70 p-3">
                <p className="text-[11px] text-slate-500">已开票</p>
                <p className="tabular mt-1 text-sm font-semibold text-slate-900">
                  {ledger.data?.currency} {summary.invoice_total}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200/70 bg-white/70 p-3">
                <p className="text-[11px] text-slate-500">已收款</p>
                <p className="tabular mt-1 text-sm font-semibold text-slate-900">
                  {ledger.data?.currency} {summary.payment_total}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200/70 bg-white/70 p-3">
                <p className="text-[11px] text-slate-500">保证金 / 质保金</p>
                <p className="tabular mt-1 text-sm font-semibold text-slate-900">
                  {summary.deposit_total} / {summary.retention_total}
                </p>
              </div>
              <div
                className={`rounded-xl border p-3 ${
                  Number(summary.outstanding) > 0
                    ? "border-amber-200/80 bg-amber-50/60"
                    : "border-slate-200/70 bg-white/70"
                }`}
              >
                <p
                  className={`text-[11px] ${
                    Number(summary.outstanding) > 0 ? "text-amber-700" : "text-slate-500"
                  }`}
                >
                  未收余额
                </p>
                <p
                  className={`tabular mt-1 text-sm font-semibold ${
                    Number(summary.outstanding) > 0 ? "text-amber-900" : "text-slate-900"
                  }`}
                >
                  {ledger.data?.currency} {summary.outstanding}
                </p>
              </div>
            </div>
          ) : null}

          {ledger.isError ? (
            <ErrorState error={ledger.error} onRetry={() => ledger.refetch()} />
          ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-slate-400">
                  <th className="py-2 pr-3 font-medium">类型</th>
                  <th className="py-2 pr-3 font-medium">金额</th>
                  <th className="py-2 pr-3 font-medium">发生日</th>
                  <th className="py-2 pr-3 font-medium">备注</th>
                  <th className="py-2 font-medium">凭证</th>
                </tr>
              </thead>
              <tbody>
                {(ledger.data?.entries ?? []).map((entry) => (
                  <tr key={entry.id} className="border-b border-slate-100 text-slate-600">
                    <td className="py-2 pr-3">{LEDGER_LABELS[entry.entry_type] ?? entry.entry_type}</td>
                    <td className="tabular py-2 pr-3">{entry.amount}</td>
                    <td className="py-2 pr-3">{entry.occurred_on}</td>
                    <td className="py-2 pr-3">{entry.note ?? "—"}</td>
                    <td className="py-2">{entry.voucher_ref ?? "—"}</td>
                  </tr>
                ))}
                {ledgerCount === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-3 text-slate-400">
                      暂无履约流水，请在下方登记开票或收款。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          )}

          {canWrite ? (
            <div className="mt-3 rounded-xl border border-slate-200/70 bg-white/60 p-3">
              <p className="mono-label text-slate-500">登记履约流水</p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                <Select
                  aria-label="台账类型"
                  value={ledgerForm.entry_type}
                  onChange={(event) => setLedgerForm((prev) => ({ ...prev, entry_type: event.target.value }))}
                >
                  <option value="invoice">开票</option>
                  <option value="payment">收款</option>
                  <option value="deposit">保证金</option>
                  <option value="retention">质保金</option>
                </Select>
                <Input
                  aria-label="金额"
                  type="number"
                  min="0"
                  step="0.01"
                  placeholder="金额（元）"
                  value={ledgerForm.amount}
                  onChange={(event) => setLedgerForm((prev) => ({ ...prev, amount: event.target.value }))}
                />
                <Input
                  aria-label="发生日"
                  type="date"
                  value={ledgerForm.occurred_on}
                  onChange={(event) => setLedgerForm((prev) => ({ ...prev, occurred_on: event.target.value }))}
                />
                <Input
                  aria-label="备注"
                  placeholder="备注"
                  value={ledgerForm.note}
                  onChange={(event) => setLedgerForm((prev) => ({ ...prev, note: event.target.value }))}
                />
                <Input
                  aria-label="凭证"
                  placeholder="凭证号"
                  value={ledgerForm.voucher_ref}
                  onChange={(event) => setLedgerForm((prev) => ({ ...prev, voucher_ref: event.target.value }))}
                />
              </div>
              {ledgerError ? <p className="mt-2 text-xs text-rose-600">{ledgerError}</p> : null}
              <div className="mt-2 flex items-center gap-2">
                <Button
                  variant="secondary"
                  disabled={addLedger.isPending}
                  onClick={() => {
                    if (!ledgerForm.amount || !ledgerForm.occurred_on) {
                      setLedgerError("金额与发生日为必填");
                      return;
                    }
                    addLedger.mutate({
                      entry_type: ledgerForm.entry_type,
                      amount: ledgerForm.amount,
                      occurred_on: ledgerForm.occurred_on,
                      note: ledgerForm.note || null,
                      voucher_ref: ledgerForm.voucher_ref || null,
                    });
                  }}
                >
                  登记
                </Button>
                <span className="text-[11px] text-slate-400">金额与发生日必填</span>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {tab === "changes" ? (
        <div className="mt-4">
          {changes.isError ? (
            <ErrorState error={changes.error} onRetry={() => changes.refetch()} />
          ) : null}
          <div className="space-y-2">
            {!changes.isError && changeCount === 0 ? (
              <p className="text-xs text-slate-400">暂无变更记录。</p>
            ) : (
              (changes.data?.items ?? []).map((change) => (
                <div key={change.id} className="rounded-xl border border-slate-200/70 bg-white/60 p-3">
                  <div className="flex items-center gap-2">
                    <Badge tone={change.change_type === "renewal" ? "blue" : "amber"}>
                      {change.change_type === "renewal" ? "续签" : "变更"}
                    </Badge>
                    <Badge tone="slate">
                      {CHANGE_STATUS_LABELS[change.approval_status] ?? change.approval_status}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-600">{change.reason}</p>
                  <p className="mono-label mt-1 text-[10px] text-slate-400">
                    {change.amount_before ?? "—"} → {change.amount_after ?? "—"} ·{" "}
                    {change.expiry_before ?? "—"} → {change.expiry_after ?? "—"}
                  </p>
                  {canApprove && change.approval_status === "pending" ? (
                    <div className="mt-2 flex gap-2">
                      <Button
                        disabled={decideChange.isPending}
                        onClick={() => decideChange.mutate({ changeId: change.id, action: "approve" })}
                      >
                        通过
                      </Button>
                      <Button
                        variant="danger"
                        disabled={decideChange.isPending}
                        onClick={() => decideChange.mutate({ changeId: change.id, action: "reject" })}
                      >
                        驳回
                      </Button>
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </div>

          {canWrite ? (
            <div className="mt-3 rounded-xl border border-slate-200/70 bg-white/60 p-3">
              <p className="mono-label text-slate-500">发起变更 / 续签</p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <Select
                  aria-label="变更类型"
                  value={changeForm.change_type}
                  onChange={(event) => setChangeForm((prev) => ({ ...prev, change_type: event.target.value }))}
                >
                  <option value="change">金额变更</option>
                  <option value="renewal">续签</option>
                </Select>
                {changeForm.change_type === "change" ? (
                  <Input
                    aria-label="变更后金额"
                    type="number"
                    min="0"
                    step="0.01"
                    placeholder="变更后金额"
                    value={changeForm.amount_after}
                    onChange={(event) => setChangeForm((prev) => ({ ...prev, amount_after: event.target.value }))}
                  />
                ) : (
                  <Input
                    aria-label="续签后到期日"
                    type="date"
                    value={changeForm.expiry_after}
                    onChange={(event) => setChangeForm((prev) => ({ ...prev, expiry_after: event.target.value }))}
                  />
                )}
                <Input
                  aria-label="变更原因"
                  className="sm:col-span-2"
                  placeholder="变更原因（必填，将永久留痕）"
                  value={changeForm.reason}
                  onChange={(event) => setChangeForm((prev) => ({ ...prev, reason: event.target.value }))}
                />
              </div>
              {changeError ? <p className="mt-2 text-xs text-rose-600">{changeError}</p> : null}
              <div className="mt-2 flex items-center gap-2">
                <Button
                  variant="secondary"
                  disabled={addChange.isPending}
                  onClick={() => {
                    if (!changeForm.reason.trim()) {
                      setChangeError("变更原因不能为空");
                      return;
                    }
                    const payload: Record<string, unknown> = {
                      change_type: changeForm.change_type,
                      reason: changeForm.reason.trim(),
                    };
                    if (changeForm.change_type === "change") {
                      payload.amount_before = record.amount;
                      payload.amount_after = changeForm.amount_after || null;
                    } else {
                      payload.expiry_before = record.expiry_date;
                      payload.expiry_after = changeForm.expiry_after || null;
                    }
                    addChange.mutate(payload);
                  }}
                >
                  提交变更
                </Button>
                <span className="text-[11px] text-slate-400">提交后需审批通过才生效</span>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <EnterpriseIntelPanel
        companyName={record.party_a || record.party_b}
        title="合同相对方尽调"
        description="默认核验甲方；若相对方为乙方，可用「主体匹配」切换。"
      />
    </Card>
  );
}
