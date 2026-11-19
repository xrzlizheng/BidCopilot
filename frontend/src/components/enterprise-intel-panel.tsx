"use client";

import { useEffect, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { apiFetch } from "@/lib/api";

export type IntelStatus =
  | "ok"
  | "empty"
  | "unavailable"
  | "rate_limited"
  | "unauthorized"
  | "contract_error";

export interface IntelErrorPayload {
  code: string;
  message: string;
  field?: string | null;
  raw_payload_ref?: string | null;
}

export interface IntelResponse<T = unknown> {
  status: IntelStatus;
  data: T | null;
  source: string;
  source_url?: string | null;
  fetched_at: string;
  order_no: string | null;
  charged: boolean;
  api_calls: number;
  billing_units: number;
  warnings: IntelErrorPayload[];
  error: IntelErrorPayload | null;
}

type IntelKind = "resolve" | "profile" | "credit" | "relations" | "due";

const KIND_LABEL: Record<IntelKind, string> = {
  resolve: "主体匹配",
  profile: "工商照面",
  credit: "信用评分",
  relations: "关联关系",
  due: "尽调摘要",
};

const DUE_SECTIONS = "basic,shareholders,beneficial_owner,equity_pledge";

const PROFILE_FIELDS: Array<[string, string]> = [
  ["entName", "企业名称"],
  ["creditCode", "统一社会信用代码"],
  ["legalName", "法定代表人"],
  ["regStatus", "登记状态"],
  ["regCap", "注册资本"],
  ["regCapCur", "币种"],
  ["regLocation", "注册地址"],
  ["estiblishTime", "成立日期"],
  ["industry", "行业"],
];

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function statusTone(status: IntelStatus): "green" | "slate" | "amber" | "rose" {
  if (status === "ok") return "green";
  if (status === "empty") return "slate";
  if (status === "rate_limited" || status === "unauthorized") return "amber";
  return "rose";
}

export function EnterpriseIntelPanel({
  companyName,
  title = "企业工商自检",
  description = "核验本企业工商信息、信用与关联关系；按次计费，状态原样展示，缓存或空结果不计费。",
}: {
  companyName?: string | null;
  title?: string;
  description?: string;
}) {
  const [kind, setKind] = useState<IntelKind>("profile");
  const [result, setResult] = useState<IntelResponse | null>(null);
  const [loading, setLoading] = useState<IntelKind | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(companyName ?? null);

  useEffect(() => {
    setTarget(companyName ?? null);
    setResult(null);
    setMessage(null);
  }, [companyName]);

  async function load(next: IntelKind, override?: string) {
    const name = override ?? target ?? companyName;
    if (!name) return;
    setKind(next);
    setLoading(next);
    setMessage(null);
    setResult(null);
    try {
      const body =
        next === "resolve"
          ? await apiFetch<IntelResponse>(
              `/company-intel/resolve?keyword=${encodeURIComponent(name)}`,
            )
          : next === "due"
            ? await apiFetch<IntelResponse>("/company-intel/due-diligence", {
                method: "POST",
                body: JSON.stringify({
                  ent_name: name,
                  sections: DUE_SECTIONS,
                  include_judicial: false,
                  digest_only: true,
                }),
              })
            : await apiFetch<IntelResponse>(
                `/company-intel/${next}?ent_name=${encodeURIComponent(name)}`,
              );
      setResult(body);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "查询失败");
    } finally {
      setLoading(null);
    }
  }

  const data = asRecord(result?.data);
  const candidates = asArray(data?.candidates).map(asRecord).filter(Boolean) as Array<
    Record<string, unknown>
  >;
  const profileRows = PROFILE_FIELDS.map(([key, label]) => [label, text(data?.[key])] as const);
  const sections = asArray(data?.sections).map(asRecord).filter(Boolean) as Array<Record<string, unknown>>;
  const riskSummary = asRecord(data?.risk_summary);

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">{title}（工商 / 信用 / 关联 / 尽调）</h2>
          <p className="mt-1 text-xs text-slate-500">{description}</p>
          {target ? <p className="mt-1 text-xs text-slate-400">当前主体：{target}</p> : null}
        </div>
        <Badge tone="blue">MCP</Badge>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {(["resolve", "profile", "credit", "relations", "due"] as IntelKind[]).map((item) => (
          <Button
            key={item}
            variant="secondary"
            onClick={() => load(item)}
            disabled={!target || loading !== null}
          >
            {loading === item ? "查询中…" : KIND_LABEL[item]}
          </Button>
        ))}
      </div>

      {!target ? <p className="mt-4 text-xs text-slate-500">先选择企业后再查询。</p> : null}
      {message ? <p className="mt-4 text-xs text-rose-600">{message}</p> : null}

      {result ? (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <Badge tone={statusTone(result.status)}>{result.status}</Badge>
            <span>{KIND_LABEL[kind]}</span>
            <span>· 计费 {result.charged ? "是" : "否"}</span>
            <span>· 接口调用 {result.api_calls} 次</span>
            <span>· 计费单位 {result.billing_units}</span>
            {result.order_no ? <span>· 单号 {result.order_no}</span> : null}
          </div>

          {result.error ? (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
              {result.error.code}：{result.error.message}
            </p>
          ) : null}
          {result.warnings.length > 0 ? (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
              {result.warnings.length} 个分节未成功：{result.warnings.map((item) => item.code).join("、")}
            </p>
          ) : null}

          {result.status !== "ok" && result.status !== "empty" ? null : kind === "resolve" ? (
            <ul className="space-y-2 text-xs">
              {candidates.map((row, index) => {
                const name = String(row.ent_name ?? "");
                return (
                  <li
                    key={`${name}-${index}`}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-100 px-3 py-2"
                  >
                    <span>
                      <span className="font-medium text-slate-800">{name || "—"}</span>
                      <span className="ml-2 text-slate-500">
                        {text(row.credit_code)} · {text(row.legal_person)} · {text(row.province)}
                        {text(row.city)}
                      </span>
                    </span>
                    <Button
                      variant="secondary"
                      disabled={!name}
                      onClick={() => {
                        setTarget(name);
                        void load("profile", name);
                      }}
                    >
                      使用此主体
                    </Button>
                  </li>
                );
              })}
            </ul>
          ) : kind === "profile" ? (
            <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
              {profileRows.map(([label, value]) => (
                <div key={label} className="flex gap-2">
                  <dt className="w-32 shrink-0 text-slate-500">{label}</dt>
                  <dd className="min-w-0 break-words text-slate-800">{value}</dd>
                </div>
              ))}
            </dl>
          ) : kind === "due" && riskSummary ? (
            <div className="space-y-2">
              <p className="text-xs text-slate-600">
                风险等级 {text(riskSummary.overall_level)} · 红 {text(riskSummary.red_count)} · 黄{" "}
                {text(riskSummary.yellow_count)}
              </p>
              <ul className="space-y-1 text-xs text-slate-700">
                {sections.map((item, index) => (
                  <li key={`${text(item.key)}-${index}`} className="flex gap-2">
                    <Badge tone={item.status === "ok" ? "green" : "amber"}>
                      {text(item.status)}
                    </Badge>
                    <span className="font-medium">{text(item.title)}</span>
                    <span className="text-slate-500">{text(asRecord(item.digest)?.summary)}</span>
                    {item.cached ? <span className="text-slate-400">缓存</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : kind === "relations" ? (
            <ul className="space-y-1 text-xs text-slate-700">
              {sections.map((item, index) => (
                <li key={`${text(item.key)}-${index}`} className="flex gap-2">
                  <span className="w-40 shrink-0 font-medium">{text(item.title)}</span>
                  <span className="text-slate-500">{text(asRecord(item.digest)?.summary)}</span>
                </li>
              ))}
            </ul>
          ) : null}

          <details className="text-xs">
            <summary className="cursor-pointer text-slate-500">原始响应</summary>
            <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-slate-50 p-3 text-[11px] leading-5 text-slate-600">
              {JSON.stringify(result.data, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </Card>
  );
}
