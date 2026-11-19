"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card, ErrorState, Input, Select } from "@/components/ui";
import { ApiError, apiDownload, apiFetch } from "@/lib/api";
import type { PricingStrategy } from "./pricing-strategy-panel";

export type QuoteItem = {
  name: string;
  spec: string;
  quantity: string;
  unit: string;
  unit_price: string;
  amount: string;
};

export type PriceQuote = {
  id: string;
  project_id: string;
  strategy_id: string | null;
  quote_number: string;
  project_name: string;
  buyer_name: string | null;
  supplier_name: string | null;
  total_amount: string;
  currency: string;
  amount_in_words: string;
  items: QuoteItem[];
  tier: string | null;
  price_source: string;
  valid_until: string | null;
  notes: string | null;
  created_at: string | null;
};

const TIER_OPTIONS = [
  ["balanced", "均衡档"],
  ["conservative", "保守档"],
  ["aggressive", "进取档"],
] as const;

type EditableQuoteItem = {
  name: string;
  spec: string;
  quantity: string;
  unit: string;
  unit_price: string;
};

const EMPTY_ITEM: EditableQuoteItem = {
  name: "",
  spec: "",
  quantity: "",
  unit: "",
  unit_price: "",
};

function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isNaN(parsed)
    ? String(value)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(parsed);
}

function dateText(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}

export function QuoteSheetPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [tier, setTier] = useState<string>("balanced");
  const [override, setOverride] = useState("");
  const [validityDays, setValidityDays] = useState("90");
  const [notes, setNotes] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [items, setItems] = useState<EditableQuoteItem[]>([]);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const latest = useQuery({
    queryKey: ["project-pricing-quote", projectId],
    queryFn: () => apiFetch<PriceQuote>(`/projects/${projectId}/pricing/quote/latest`),
    retry: false,
  });

  // 与报价策略面板共用同一 queryKey：策略生成后这里能直接拿到最新档位建议价。
  // 404（尚未生成策略）视为 null，不重复渲染策略面板已有的错误态。
  const strategy = useQuery({
    queryKey: ["project-pricing-strategy", projectId],
    queryFn: () => apiFetch<PricingStrategy>(`/projects/${projectId}/pricing/strategy/latest`),
    retry: false,
  });

  const tierPrice = strategy.data?.tiers?.find((entry) => entry.tier === tier)?.price ?? null;
  const expectedText = override.trim() !== "" ? override.trim() : tierPrice;
  const expectedTotal =
    expectedText !== null && expectedText !== "" && Number.isFinite(Number(expectedText))
      ? Number(expectedText)
      : null;

  const rowsComplete = items.every((item) => {
    const quantity = Number(item.quantity);
    const unitPrice = Number(item.unit_price);
    return (
      item.name.trim() !== "" &&
      item.unit.trim() !== "" &&
      item.quantity.trim() !== "" &&
      Number.isFinite(quantity) &&
      quantity > 0 &&
      item.unit_price.trim() !== "" &&
      Number.isFinite(unitPrice) &&
      unitPrice >= 0
    );
  });
  const subtotal = items.reduce(
    (sum, item) => sum + Number(item.quantity) * Number(item.unit_price),
    0,
  );
  // 与后端 Decimal 校验同口径：按 2 位小数比较合计与预期总额。
  const mismatch =
    items.length > 0 &&
    rowsComplete &&
    expectedTotal !== null &&
    subtotal.toFixed(2) !== expectedTotal.toFixed(2);
  const itemsBlocked = items.length > 0 && (!rowsComplete || mismatch);

  const updateItem = (index: number, field: keyof EditableQuoteItem, value: string) =>
    setItems((current) =>
      current.map((item, rowIndex) => (rowIndex === index ? { ...item, [field]: value } : item)),
    );

  const generate = useMutation({
    mutationFn: () =>
      apiFetch<PriceQuote>(`/projects/${projectId}/pricing/quote`, {
        method: "POST",
        body: JSON.stringify({
          tier,
          quote_amount: override.trim() || null,
          items: items.map((item) => ({
            name: item.name.trim(),
            spec: item.spec.trim(),
            quantity: item.quantity.trim(),
            unit: item.unit.trim(),
            unit_price: item.unit_price.trim(),
          })),
          supplier_name: supplierName.trim() || null,
          validity_days: Number(validityDays) || 90,
          notes: notes.trim() || null,
        }),
      }),
    onSuccess: (quote) => {
      setNotice({ tone: "ok", text: `报价单 ${quote.quote_number} 已生成。` });
      queryClient.setQueryData(["project-pricing-quote", projectId], quote);
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "生成失败" }),
  });

  const exportDocx = useMutation({
    mutationFn: () => apiDownload(`/projects/${projectId}/pricing/quote/export/docx`, {}),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename || `quote_${projectId}.docx`;
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "导出失败" }),
  });

  const quote = latest.data;

  return (
    <Card className="mt-4 p-5" aria-label="报价单面板">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">报价单</h2>
            {quote ? <Badge tone="slate">{quote.quote_number}</Badge> : null}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            总价取自最新报价策略的档位建议价，或显式填写；每次生成保留不可变快照，导出为正式报价单 DOCX。
          </p>
        </div>
        {quote ? (
          <Button
            variant="secondary"
            onClick={() => exportDocx.mutate()}
            disabled={exportDocx.isPending}
            aria-label="导出报价单"
          >
            {exportDocx.isPending ? "导出中…" : "导出 DOCX"}
          </Button>
        ) : null}
      </div>

      {notice ? (
        <p className={`mt-3 rounded-lg px-3 py-2 text-sm ${notice.tone === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>
          {notice.text}
        </p>
      ) : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <label className="text-xs text-slate-500">
          定价档位
          <Select className="mt-1" value={tier} onChange={(event) => setTier(event.target.value)} aria-label="定价档位">
            {TIER_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-xs text-slate-500">
          自定义总价（元）
          <Input className="mt-1" value={override} onChange={(event) => setOverride(event.target.value)} placeholder="可选；填写后覆盖档位价" />
        </label>
        <label className="text-xs text-slate-500">
          报价有效期（天）
          <Input className="mt-1" value={validityDays} onChange={(event) => setValidityDays(event.target.value)} placeholder="默认 90" />
        </label>
        <label className="text-xs text-slate-500">
          备注
          <Input className="mt-1" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="如：含税总价" />
        </label>
        <label className="text-xs text-slate-500">
          供应商名称
          <Input
            className="mt-1"
            value={supplierName}
            onChange={(event) => setSupplierName(event.target.value)}
            placeholder="留空则取项目关联企业"
            aria-label="供应商名称"
          />
        </label>
        <div className="flex items-end">
          <Button
            onClick={() => generate.mutate()}
            disabled={generate.isPending || itemsBlocked}
            aria-label="生成报价单"
          >
            {generate.isPending ? "生成中…" : "生成报价单"}
          </Button>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-slate-200 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium">报价明细（可选）</h3>
          <Button
            variant="secondary"
            onClick={() => setItems((current) => [...current, { ...EMPTY_ITEM }])}
            aria-label="添加明细行"
          >
            添加明细行
          </Button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          不添加明细时按总额生成单行明细；添加后明细合计必须等于预期总额（自定义总价，或所选档位的策略建议价）。
        </p>

        {items.length > 0 ? (
          <div className="mt-3 space-y-2">
            {items.map((item, index) => (
              <div key={index} className="grid gap-2 sm:grid-cols-12">
                <Input
                  className="sm:col-span-3"
                  value={item.name}
                  onChange={(event) => updateItem(index, "name", event.target.value)}
                  placeholder="名称"
                  aria-label={`明细第${index + 1}行名称`}
                />
                <Input
                  className="sm:col-span-3"
                  value={item.spec}
                  onChange={(event) => updateItem(index, "spec", event.target.value)}
                  placeholder="规格说明（可选）"
                  aria-label={`明细第${index + 1}行规格`}
                />
                <Input
                  className="sm:col-span-2"
                  value={item.quantity}
                  onChange={(event) => updateItem(index, "quantity", event.target.value)}
                  placeholder="数量"
                  inputMode="decimal"
                  aria-label={`明细第${index + 1}行数量`}
                />
                <Input
                  className="sm:col-span-1"
                  value={item.unit}
                  onChange={(event) => updateItem(index, "unit", event.target.value)}
                  placeholder="单位"
                  aria-label={`明细第${index + 1}行单位`}
                />
                <Input
                  className="sm:col-span-2"
                  value={item.unit_price}
                  onChange={(event) => updateItem(index, "unit_price", event.target.value)}
                  placeholder="单价（元）"
                  inputMode="decimal"
                  aria-label={`明细第${index + 1}行单价`}
                />
                <div className="flex items-center sm:col-span-1">
                  <Button
                    variant="ghost"
                    onClick={() => setItems((current) => current.filter((_, rowIndex) => rowIndex !== index))}
                    aria-label={`删除明细第${index + 1}行`}
                  >
                    删除
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <p className="mt-3 text-xs text-slate-600">
          明细合计：<span className="font-medium">{items.length > 0 ? money(subtotal.toFixed(2)) : "—"}</span> 元
          <span className="mx-2 text-slate-300">|</span>
          预期总额：<span className="font-medium">{money(expectedText)}</span> 元
        </p>

        {items.length > 0 && !rowsComplete ? (
          <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
            明细行未填完整：名称与单位必填，数量需大于 0，单价不能为负。
          </p>
        ) : null}
        {mismatch ? (
          <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
            明细合计 {money(subtotal.toFixed(2))} 与预期总额 {money(expectedText)} 不一致，请调整明细或总额后再生成。
          </p>
        ) : null}
        {items.length > 0 && expectedTotal === null ? (
          <p className="mt-2 text-xs text-slate-500">
            预期总额未知：请填写自定义总价，或先生成报价策略并选择可用档位。
          </p>
        ) : null}
      </div>

      {latest.isError && !(latest.error instanceof ApiError && latest.error.status === 404) ? (
        <ErrorState error={latest.error} onRetry={() => latest.refetch()} />
      ) : null}
      {latest.isError && latest.error instanceof ApiError && latest.error.status === 404 ? (
        <p className="mt-3 text-sm text-slate-500">
          尚未生成报价单；请先生成报价策略，再选择档位或填写自定义总价生成。
        </p>
      ) : null}

      {quote ? (
        <>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <div className="rounded-lg bg-slate-50 p-3">
              <p className="text-xs text-slate-500">报价总额（{quote.currency}）</p>
              <p className="mt-1 text-lg font-semibold">{money(quote.total_amount)}</p>
              <p className="mt-1 text-xs text-slate-500">{quote.amount_in_words}</p>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <p className="text-xs text-slate-500">定价依据</p>
              <p className="mt-1 text-sm font-medium">
                {quote.price_source === "input" ? "显式报价" : `策略档位（${quote.tier ?? "—"}）`}
              </p>
              <p className="mt-1 text-xs text-slate-500">生成于 {dateText(quote.created_at)}</p>
            </div>
            <div className="rounded-lg bg-slate-50 p-3">
              <p className="text-xs text-slate-500">报价有效期</p>
              <p className="mt-1 text-sm font-medium">至 {dateText(quote.valid_until)}</p>
              <p className="mt-1 text-xs text-slate-500">供应商：{quote.supplier_name ?? "未填写"}</p>
            </div>
          </div>

          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-3">名称</th>
                  <th className="py-2 pr-3">规格说明</th>
                  <th className="py-2 pr-3">数量</th>
                  <th className="py-2 pr-3">单位</th>
                  <th className="py-2 pr-3">单价（元）</th>
                  <th className="py-2">金额（元）</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {quote.items.map((item, index) => (
                  <tr key={`${item.name}-${index}`}>
                    <td className="py-2 pr-3 font-medium">{item.name}</td>
                    <td className="py-2 pr-3 text-xs text-slate-600">{item.spec || "—"}</td>
                    <td className="py-2 pr-3">{item.quantity}</td>
                    <td className="py-2 pr-3">{item.unit}</td>
                    <td className="py-2 pr-3">{money(item.unit_price)}</td>
                    <td className="py-2">{money(item.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {quote.notes ? (
            <p className="mt-3 text-xs text-slate-600">备注：{quote.notes}</p>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}