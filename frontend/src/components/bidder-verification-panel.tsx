"use client";

import { useEffect, useState } from "react";

import { Badge, Button, Card, Input } from "@/components/ui";
import { EnterpriseIntelPanel, type IntelResponse } from "@/components/enterprise-intel-panel";
import { apiFetch } from "@/lib/api";

type CompanyRow = Record<string, unknown>;
type BatchResponse = IntelResponse<CompanyRow[]>;
type CompareResponse = IntelResponse<{
  ranking?: CompanyRow[];
  companies?: CompanyRow[];
  count?: number;
}>;

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "string" ? value : String(value);
}

function score(value: unknown): string {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : null;
  if (!record) return "—";
  return `${text(record.score)}${record.level ? `（${text(record.level)}）` : ""}`;
}

export function BidderVerificationPanel({
  ownCompanyName,
  competitor,
}: {
  ownCompanyName?: string | null;
  competitor?: string | null;
}) {
  const [names, setNames] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [result, setResult] = useState<BatchResponse | null>(null);
  const [compareResult, setCompareResult] = useState<CompareResponse | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const seed = [ownCompanyName, competitor].filter(Boolean) as string[];
    setNames(Array.from(new Set(seed)));
    setResult(null);
    setSelected(null);
    setMessage(null);
  }, [ownCompanyName, competitor]);

  function addName() {
    const value = input.trim();
    if (!value) return;
    if (names.length >= 20) {
      setMessage("一次最多核验 20 家；更多请分批。");
      return;
    }
    setNames(Array.from(new Set([...names, value])));
    setInput("");
    setCompareResult(null);
  }

  async function verify() {
    if (names.length === 0) return;
    setLoading(true);
    setMessage(null);
    setResult(null);
    try {
      const body = await apiFetch<BatchResponse>("/company-intel/batch", {
        method: "POST",
        body: JSON.stringify({ ent_names: names }),
      });
      setResult(body);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "批量核验失败");
    } finally {
      setLoading(false);
    }
  }

  async function compare() {
    if (names.length < 2 || names.length > 5) {
      setMessage("企业对比需要 2–5 家；更多请分批评分。");
      return;
    }
    setCompareLoading(true);
    setMessage(null);
    setCompareResult(null);
    try {
      const body = await apiFetch<CompareResponse>("/company-intel/compare", {
        method: "POST",
        body: JSON.stringify({ ent_names: names }),
      });
      setCompareResult(body);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "企业对比失败");
    } finally {
      setCompareLoading(false);
    }
  }

  const rows = result?.data ?? [];
  const ranking = (compareResult?.data?.ranking ?? []) as CompanyRow[];
  const compared = (compareResult?.data?.companies ?? []) as CompanyRow[];
  const matched = rows.map((row) => String(row.entName ?? ""));
  const unmatched = names.filter((name) => !matched.includes(name));

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">投标主体核验</h2>
          <p className="mt-1 text-xs text-slate-500">
            核验本企业、联合体、分包与竞对的工商主体；按批计费（单次网关调用），未返回的名称单独列出。
          </p>
        </div>
        <Badge tone="blue">MCP</Badge>
      </div>

      <form
        className="mt-3 flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          addName();
        }}
      >
        <Input
          className="min-w-64 flex-1"
          placeholder="企业全称（联合体成员 / 分包 / 竞对）"
          value={input}
          onChange={(event) => setInput(event.target.value)}
        />
        <Button type="submit" variant="secondary" disabled={!input.trim()}>
          加入名单
        </Button>
        <Button type="button" onClick={verify} disabled={names.length === 0 || loading}>
          {loading ? "核验中…" : "批量核验"}
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={compare}
          disabled={names.length < 2 || names.length > 5 || compareLoading}
        >
          {compareLoading ? "对比中…" : "企业对比（2–5 家）"}
        </Button>
      </form>

      {names.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-2 text-xs">
          {names.map((name) => (
            <li key={name} className="flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1">
              <span className="text-slate-700">{name}</span>
              <button
                type="button"
                className="text-slate-400 hover:text-rose-600"
                onClick={() => {
                  setNames(names.filter((item) => item !== name));
                  setCompareResult(null);
                }}
                aria-label={`移除 ${name}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-slate-500">先加入企业名称；本企业会自动带入。</p>
      )}

      {message ? <p className="mt-3 text-xs text-rose-600">{message}</p> : null}

      {result ? (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <Badge tone={result.status === "ok" ? "green" : result.status === "empty" ? "slate" : "rose"}>
              {result.status}
            </Badge>
            <span>计费 {result.charged ? "是" : "否"}</span>
            <span>· 接口调用 {result.api_calls} 次</span>
            <span>· 计费单位 {result.billing_units}</span>
            {result.order_no ? <span>· 单号 {result.order_no}</span> : null}
          </div>
          {result.error ? (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
              {result.error.code}：{result.error.message}
            </p>
          ) : null}
          {rows.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead className="border-b border-slate-200 text-left text-slate-500">
                  <tr>
                    <th className="py-2 pr-3">企业名称</th>
                    <th className="py-2 pr-3">登记状态</th>
                    <th className="py-2 pr-3">信用代码</th>
                    <th className="py-2 pr-3">法定代表人</th>
                    <th className="py-2 pr-3">规模</th>
                    <th className="py-2">地区</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const name = String(row.entName ?? "");
                    return (
                      <tr
                        key={name}
                        className={`cursor-pointer border-b border-slate-100 hover:bg-slate-50 ${
                          selected === name ? "bg-aurora-500/5" : ""
                        }`}
                        onClick={() => name && setSelected(name)}
                      >
                        <td className="py-2 pr-3 font-medium text-slate-800">{text(row.entName)}</td>
                        <td className="py-2 pr-3 text-slate-600">{text(row.regStatus)}</td>
                        <td className="py-2 pr-3 text-slate-600">{text(row.creditCode)}</td>
                        <td className="py-2 pr-3 text-slate-600">{text(row.legalPersonName)}</td>
                        <td className="py-2 pr-3 text-slate-600">{text(row.entScale)}</td>
                        <td className="py-2 text-slate-600">
                          {text(row.province)}
                          {row.city ? ` · ${text(row.city)}` : ""}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-slate-500">未返回匹配企业。</p>
          )}
          {unmatched.length > 0 ? (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
              未返回（名称需核对）：{unmatched.join("、")}
            </p>
          ) : null}
          {selected ? (
            <EnterpriseIntelPanel
              companyName={selected}
              title="投标主体情报"
              description="对选中的投标主体做工商、信用与关联关系核验。"
            />
          ) : (
            <p className="text-xs text-slate-500">点击表格中的企业，查看信用与关联关系。</p>
          )}
        </div>
      ) : null}

      {compareResult ? (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <Badge
              tone={
                compareResult.status === "ok"
                  ? "green"
                  : compareResult.status === "empty"
                    ? "slate"
                    : "rose"
              }
            >
              {compareResult.status}
            </Badge>
            <span>企业对比</span>
            <span>· 计费 {compareResult.charged ? "是" : "否"}</span>
            <span>· 接口调用 {compareResult.api_calls} 次</span>
            <span>· 计费单位 {compareResult.billing_units}</span>
          </div>
          {compareResult.error ? (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
              {compareResult.error.code}：{compareResult.error.message}
            </p>
          ) : null}
          {ranking.length > 0 ? (
            <table className="min-w-full text-xs">
              <thead className="border-b border-slate-200 text-left text-slate-500">
                <tr>
                  <th className="py-2 pr-3">排名</th>
                  <th className="py-2 pr-3">企业</th>
                  <th className="py-2 pr-3">综合评分</th>
                  <th className="py-2">风险等级</th>
                </tr>
              </thead>
              <tbody>
                {ranking.map((row) => (
                  <tr key={String(row.entity)} className="border-b border-slate-100">
                    <td className="py-2 pr-3">{text(row.rank)}</td>
                    <td className="py-2 pr-3 font-medium text-slate-800">{text(row.entity)}</td>
                    <td className="py-2 pr-3 text-slate-700">{text(row.score)}</td>
                    <td className="py-2">
                      <Badge
                        tone={
                          row.risk_level === "green"
                            ? "green"
                            : row.risk_level === "red"
                              ? "rose"
                              : "amber"
                        }
                      >
                        {text(row.risk_level)}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {compared.length > 0 ? (
            <table className="min-w-full text-xs">
              <thead className="border-b border-slate-200 text-left text-slate-500">
                <tr>
                  <th className="py-2 pr-3">企业</th>
                  <th className="py-2 pr-3">综合</th>
                  <th className="py-2 pr-3">经营</th>
                  <th className="py-2 pr-3">科创</th>
                  <th className="py-2 pr-3">财务</th>
                  <th className="py-2">风险摘要</th>
                </tr>
              </thead>
              <tbody>
                {compared.map((company) => {
                  const credit = (company.credit_score ?? {}) as CompanyRow;
                  const scores = (credit.scores ?? {}) as CompanyRow;
                  const risk = ((company.risk_digest ?? {}) as CompanyRow).risk_summary as
                    | CompanyRow
                    | undefined;
                  return (
                    <tr key={String(company.entity)} className="border-b border-slate-100">
                      <td className="py-2 pr-3 font-medium text-slate-800">{text(company.entity)}</td>
                      <td className="py-2 pr-3 text-slate-700">{score(scores.composite)}</td>
                      <td className="py-2 pr-3 text-slate-600">{score(scores.operational_score)}</td>
                      <td className="py-2 pr-3 text-slate-600">{score(scores.innovation_score)}</td>
                      <td className="py-2 pr-3 text-slate-600">{score(scores.financial_score)}</td>
                      <td className="py-2 text-slate-600">
                        {text(risk?.overall_level)}（红 {text(risk?.red_count)} / 黄{" "}
                        {text(risk?.yellow_count)}）
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : null}
          {ranking.some((row) => row.risk_level === "unknown") ? (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
              风险等级为 unknown：数据角标未取到，不能据此判定通过；请对单家执行「尽调摘要」。
            </p>
          ) : null}
          {ranking.some((row) => row.risk_level === "red" || row.risk_level === "yellow") ? (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
              上游风险等级为 red/yellow：请对单家执行「尽调摘要」并人工复核，不能仅凭评分判断通过。
            </p>
          ) : null}
          {compared.map((company) => {
            const reco = ((company.credit_score ?? {}) as CompanyRow).recommendation;
            return reco ? (
              <p key={`reco-${String(company.entity)}`} className="text-xs text-slate-600">
                {text(company.entity)}：{text(reco)}
              </p>
            ) : null;
          })}
        </div>
      ) : null}
    </Card>
  );
}
