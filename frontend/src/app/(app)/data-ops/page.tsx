"use client";

import { useQuery } from "@tanstack/react-query";

import { Badge, Card, EmptyState, Loading, PageHeader } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import type { DataOps } from "@/lib/types";

function statusTone(status: string): "green" | "amber" | "rose" | "slate" {
  if (status === "ok" || status === "succeeded" || status === "empty") return "green";
  if (status === "rate_limited" || status === "unauthorized") return "amber";
  if (status === "failed" || status === "contract_error" || status === "unavailable") return "rose";
  return "slate";
}

export default function DataOpsPage() {
  const query = useQuery({
    queryKey: ["data-ops"],
    queryFn: () => apiFetch<DataOps>("/analytics/data-ops?days=30"),
  });

  return (
    <>
      <PageHeader
        title="数据运营"
        subtitle="KeepRisk 调用对账与同步健康；按租户统计，不展示密钥或正文"
      />
      {query.isLoading ? (
        <Loading />
      ) : query.error ? (
        <div className="text-sm text-rose-600">{query.error.message}</div>
      ) : !query.data ? (
        <EmptyState
                text="还没有外部数据调用记录。"
                hint="同步商机、查询采购人情报、联网检索等调用会在这里按次留痕并计入用量。先去「机会中心」执行一次同步即可看到。"
              />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-4">
            <Metric label="调用次数（30 天）" value={String(query.data.costs.total_calls)} />
            <Metric label="计费调用" value={String(query.data.costs.charged_calls)} />
            <Metric label="失败调用" value={String(query.data.costs.failed_calls)} />
            <Metric label="拉取行数" value={String(query.data.costs.rows)} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="text-sm font-semibold">按 pcode 汇总</h2>
              {query.data.costs.by_pcode.length === 0 ? (
                <p className="mt-3 text-sm text-slate-500">暂无外部调用记录。</p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="py-2 pr-3">pcode</th>
                        <th className="py-2 pr-3">调用</th>
                        <th className="py-2 pr-3">计费</th>
                        <th className="py-2 pr-3">失败</th>
                        <th className="py-2">行数</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {query.data.costs.by_pcode.map((row) => (
                        <tr key={row.pcode}>
                          <td className="py-2 pr-3 font-medium">{row.pcode}</td>
                          <td className="py-2 pr-3">{row.calls}</td>
                          <td className="py-2 pr-3">{row.charged_calls}</td>
                          <td className="py-2 pr-3">{row.failed_calls}</td>
                          <td className="py-2">{row.rows}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card className="p-5">
              <h2 className="text-sm font-semibold">同步健康</h2>
              {query.data.sources.length === 0 ? (
                <p className="mt-3 text-sm text-slate-500">暂无同步运行记录。</p>
              ) : (
                <div className="mt-3 space-y-2">
                  {query.data.sources.map((source) => (
                    <div
                      key={`${source.source}-${source.pcode}`}
                      className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium">{source.source}</span>
                        <Badge tone={statusTone(source.last_status ?? "unknown")}>
                          {source.last_status ?? "unknown"}
                        </Badge>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                        <span>运行 {source.runs}</span>
                        <span>成功 {source.succeeded}</span>
                        <span>失败 {source.failed}</span>
                        <span>成功率 {source.success_rate}%</span>
                      </div>
                      {source.last_failed_error ? (
                        <p className="mt-1 text-xs text-rose-600">
                          最近失败：{source.last_failed_error}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          <Card className="p-5">
            <h2 className="text-sm font-semibold">最近调用</h2>
            {query.data.costs.recent.length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">暂无调用记录。</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="py-2 pr-3">时间</th>
                      <th className="py-2 pr-3">pcode</th>
                      <th className="py-2 pr-3">操作</th>
                      <th className="py-2 pr-3">状态</th>
                      <th className="py-2 pr-3">orderNo</th>
                      <th className="py-2 pr-3">计费</th>
                      <th className="py-2">行数</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {query.data.costs.recent.map((row) => (
                      <tr key={`${row.created_at}-${row.operation}-${row.order_no ?? ""}`}>
                        <td className="py-2 pr-3 text-slate-500">
                          {row.created_at.slice(0, 16).replace("T", " ")}
                        </td>
                        <td className="py-2 pr-3 font-medium">{row.pcode}</td>
                        <td className="py-2 pr-3">{row.operation}</td>
                        <td className="py-2 pr-3">
                          <Badge tone={statusTone(row.status)}>{row.status}</Badge>
                        </td>
                        <td className="py-2 pr-3 text-xs text-slate-500">
                          {row.order_no ?? "—"}
                        </td>
                        <td className="py-2 pr-3">{row.charged ? "是" : "否"}</td>
                        <td className="py-2">{row.rows}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}
