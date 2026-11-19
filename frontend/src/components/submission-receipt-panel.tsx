"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Card, EmptyState, Input, Loading, Select } from "@/components/ui";
import { useConfirm } from "@/components/confirm-dialog";
import { apiFetch, apiFetchBlob } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";

type ReceiptAttachment = {
  id: string;
  receipt_id: string;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  uploaded_by: string | null;
  created_at: string;
};

type Receipt = {
  id: string;
  project_id: string;
  submitted_at: string;
  platform: string;
  receipt_no: string | null;
  notes: string | null;
  operator_id: string | null;
  checklist_summary: {
    total?: number;
    done?: number;
    pending?: number;
    blocked?: number;
    blocking_open?: number;
    completion_rate?: number;
  };
  created_at: string;
  attachments?: ReceiptAttachment[];
};

const ATTACHMENT_KINDS = [
  { value: "receipt", label: "平台回执" },
  { value: "signature", label: "CA 签章凭证" },
  { value: "encryption", label: "加密上传成功页" },
  { value: "other", label: "其他证据" },
] as const;

function kindLabel(kind: string): string {
  return ATTACHMENT_KINDS.find((item) => item.value === kind)?.label ?? kind;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(2)} MB`;
}

function localNow(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16);
}

export function SubmissionReceiptPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "project:write");
  const [form, setForm] = useState({
    submitted_at: localNow(),
    platform: "",
    receipt_no: "",
    notes: "",
  });
  const [kindDrafts, setKindDrafts] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);

  const receipts = useQuery({
    queryKey: ["submission-receipts", projectId],
    queryFn: () => apiFetch<Receipt[]>(`/projects/${projectId}/submission-receipts`),
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ["submission-receipts", projectId] });

  const create = useMutation({
    mutationFn: () =>
      apiFetch<Receipt>(`/projects/${projectId}/submission-receipts`, {
        method: "POST",
        body: JSON.stringify({
          submitted_at: new Date(form.submitted_at).toISOString(),
          platform: form.platform.trim(),
          receipt_no: form.receipt_no.trim() || null,
          notes: form.notes.trim() || null,
        }),
      }),
    onSuccess: () => {
      setForm((prev) => ({ ...prev, platform: "", receipt_no: "", notes: "" }));
      setMessage("回执已记录，交标自查快照已冻结");
      refresh();
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "记录回执失败"),
  });

  const uploadAttachment = useMutation({
    mutationFn: (input: { receiptId: string; file: File; kind: string }) => {
      const data = new FormData();
      data.append("file", input.file);
      data.append("kind", input.kind);
      return apiFetch<ReceiptAttachment>(
        `/projects/${projectId}/submission-receipts/${input.receiptId}/attachments`,
        { method: "POST", body: data },
      );
    },
    onSuccess: (attachment) => {
      setMessage(`附件「${attachment.filename}」已留痕，SHA-256 ${attachment.sha256.slice(0, 12)}…`);
      refresh();
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "上传附件失败"),
  });

  const deleteAttachment = useMutation({
    mutationFn: (input: { receiptId: string; attachmentId: string }) =>
      apiFetch<void>(
        `/projects/${projectId}/submission-receipts/${input.receiptId}/attachments/${input.attachmentId}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      setMessage("附件已删除，对象存储内容同时清除");
      refresh();
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "删除附件失败"),
  });

  const downloadAttachment = useMutation({
    // 裸 <a href> 不会带 Bearer，下载必须走 apiFetchBlob。
    mutationFn: async (attachment: ReceiptAttachment) => {
      const blob = await apiFetchBlob(
        `/projects/${projectId}/submission-receipts/${attachment.receipt_id}/attachments/${attachment.id}`,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = attachment.filename;
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : "下载附件失败"),
  });

  return (
    <Card className="mt-4 p-5" id="receipt-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">交标回执</h2>
            <Badge tone="amber">人工确认</Badge>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            人工记录递交时间、平台与回执号，并冻结当时的交标自查快照。系统不会自动向交易平台确认“已递交”。
          </p>
          <p className="mt-1 text-xs text-slate-500">
            附件为人工留痕（平台回执截图、CA 签章凭证、加密上传成功页等），仅支持 PNG / JPEG / PDF / WebP，单个不超过 10MB；
            系统不代为上传至交易平台。
          </p>
        </div>
      </div>

      <form
        className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (form.submitted_at && form.platform.trim()) create.mutate();
        }}
      >
        <Input
          type="datetime-local"
          value={form.submitted_at}
          onChange={(event) => setForm((prev) => ({ ...prev, submitted_at: event.target.value }))}
          required
        />
        <Input
          placeholder="递交平台 *"
          value={form.platform}
          onChange={(event) => setForm((prev) => ({ ...prev, platform: event.target.value }))}
          required
        />
        <Input
          placeholder="回执号（可选）"
          value={form.receipt_no}
          onChange={(event) => setForm((prev) => ({ ...prev, receipt_no: event.target.value }))}
        />
        <Input
          placeholder="备注（可选）"
          value={form.notes}
          onChange={(event) => setForm((prev) => ({ ...prev, notes: event.target.value }))}
        />
        <div className="sm:col-span-2 lg:col-span-4">
          <Button type="submit" disabled={!form.platform.trim() || create.isPending}>
            {create.isPending ? "记录中…" : "记录交标回执"}
          </Button>
        </div>
      </form>

      {message ? <p className="mt-3 text-xs text-slate-600">{message}</p> : null}
      {receipts.isLoading ? <Loading /> : null}
      {receipts.error ? (
        <p className="mt-3 text-xs text-rose-600">{receipts.error.message}</p>
      ) : null}
      {receipts.data && receipts.data.length === 0 ? (
        <EmptyState
                text="还没有交标回执记录。"
                hint="在上方登记递交时间、平台与回执编号，并上传平台回执截图作为留痕；这是「递交动作 ≠ 已递交」的凭证。"
              />
      ) : null}

      <ul className="mt-3 space-y-2">
        {(receipts.data ?? []).map((receipt) => {
          const attachments = receipt.attachments ?? [];
          const kind = kindDrafts[receipt.id] ?? "receipt";
          return (
            <li key={receipt.id} className="rounded-xl border border-slate-100 px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-slate-800">
                  {receipt.platform}
                  {receipt.receipt_no ? ` · ${receipt.receipt_no}` : ""}
                </span>
                <span className="text-slate-500">
                  {receipt.submitted_at.slice(0, 16).replace("T", " ")}
                </span>
              </div>
              <p className="mt-1 text-slate-500">
                快照：共 {receipt.checklist_summary.total ?? 0} 项 · 完成{" "}
                {receipt.checklist_summary.done ?? 0} · 阻断未闭环{" "}
                {receipt.checklist_summary.blocking_open ?? 0} · 完成率{" "}
                {Math.round((receipt.checklist_summary.completion_rate ?? 0) * 100)}%
              </p>
              {receipt.notes ? <p className="mt-1 text-slate-600">{receipt.notes}</p> : null}

              <div className="mt-2 rounded-lg bg-slate-50 px-2 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium text-slate-700">
                    附件留痕（{attachments.length}）
                    <span className="ml-2 font-normal text-slate-500">
                      人工上传，系统不代为上传至交易平台
                    </span>
                  </p>
                  {canWrite ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Select
                        value={kind}
                        onChange={(event) =>
                          setKindDrafts((prev) => ({ ...prev, [receipt.id]: event.target.value }))
                        }
                      >
                        {ATTACHMENT_KINDS.map((item) => (
                          <option key={item.value} value={item.value}>
                            {item.label}
                          </option>
                        ))}
                      </Select>
                      <label>
                        <input
                          type="file"
                          accept="image/png,image/jpeg,application/pdf,image/webp"
                          className="hidden"
                          disabled={uploadAttachment.isPending}
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            if (file) uploadAttachment.mutate({ receiptId: receipt.id, file, kind });
                            event.target.value = "";
                          }}
                        />
                        <Button variant="secondary" disabled={uploadAttachment.isPending}>
                          {uploadAttachment.isPending ? "上传中…" : "上传附件"}
                        </Button>
                      </label>
                    </div>
                  ) : null}
                </div>

                {attachments.length === 0 ? (
                  <p className="mt-1 text-slate-500">暂无附件。</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {attachments.map((attachment) => (
                      <li
                        key={attachment.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-200 bg-white px-2 py-1"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-slate-800">
                            {attachment.filename}
                            <span className="ml-2 text-slate-500">{kindLabel(attachment.kind)}</span>
                          </p>
                          <p className="text-slate-500">
                            {formatBytes(attachment.size_bytes)} · SHA-256{" "}
                            <span className="font-mono">{attachment.sha256.slice(0, 12)}</span>
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button
                            variant="ghost"
                            disabled={downloadAttachment.isPending}
                            onClick={() => downloadAttachment.mutate(attachment)}
                          >
                            下载
                          </Button>
                          {canWrite ? (
                            <Button
                              variant="ghost"
                              disabled={deleteAttachment.isPending}
                              onClick={async () => {
                                // 回执附件是交标合规留痕，删除后对象存储内容一并清除且不可恢复，
                                // 必须让用户看到后果再确认。
                                const ok = await confirm({
                                  title: `删除附件「${attachment.filename}」？`,
                                  description:
                                    "这是交标回执的合规留痕。删除后附件与对象存储内容会同时清除，且无法恢复；如只是重复上传，请保留原件。",
                                  confirmText: "删除附件",
                                  tone: "danger",
                                });
                                if (!ok) return;
                                deleteAttachment.mutate({
                                  receiptId: receipt.id,
                                  attachmentId: attachment.id,
                                });
                              }}
                            >
                              删除
                            </Button>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
