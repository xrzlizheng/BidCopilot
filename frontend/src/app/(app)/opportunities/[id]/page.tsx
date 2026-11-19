"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Card, Loading, PageHeader } from "@/components/ui";
import { BuyerIntelCard } from "@/components/buyer-intel-card";
import { EnterpriseIntelPanel } from "@/components/enterprise-intel-panel";
import { OpportunityMatchPanel } from "@/components/opportunity-match-panel";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { apiFetch } from "@/lib/api";
import type { OpportunityDetail, Project } from "@/lib/types";

const INFO_TYPES: Record<string, string> = {
  tender: "招标",
  planned: "拟建",
  award: "中标",
  other: "其他",
};

function formatDate(value: string | null): string {
  return value ? value.slice(0, 16).replace("T", " ") : "—";
}

function formatMoney(value: string | number | null): string {
  if (value === null || value === "") return "—";
  const amount = Number(value);
  return Number.isNaN(amount)
    ? String(value)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(amount);
}

export default function OpportunityDetailPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "opportunity:write");
  const query = useQuery({
    queryKey: ["opportunity", params.id],
    queryFn: () => apiFetch<OpportunityDetail>(`/opportunities/${params.id}`),
  });

  const favorite = useMutation({
    mutationFn: (next: boolean) =>
      apiFetch(`/opportunities/${params.id}/favorite`, { method: next ? "POST" : "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunity", params.id] }),
  });

  const createProject = useMutation({
    mutationFn: (name: string) =>
      apiFetch<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ opportunity_id: params.id, name }),
      }),
    onSuccess: (project) => router.push(`/projects/${project.id}`),
  });

  if (query.isLoading) return <Loading />;
  if (query.error) return <div className="text-sm text-rose-600">{query.error.message}</div>;
  if (!query.data) return null;
  const item = query.data;

  // 防御：即使后端历史数据里仍有对象存储路径，也不当正文渲染
  const contentRaw = (item.content_html ?? "").trim();
  const looksLikeStorageRef =
    contentRaw.length > 0 &&
    !/[\s<>]/.test(contentRaw) &&
    /\/(files)\/|\.(pdf|docx?|xlsx?|zip|ofd)$/i.test(contentRaw);
  const hasRenderableContent = contentRaw.length > 0 && !looksLikeStorageRef;

  return (
    <>
      <PageHeader
        title={item.title}
        subtitle={`${item.source} · 抓取于 ${formatDate(item.fetched_at)}`}
        actions={
          <div className="flex gap-2">
            <Link className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm" href="/opportunities">
              返回
            </Link>
            <Button
              variant="secondary"
              onClick={() => createProject.mutate(item.title)}
              disabled={createProject.isPending}
            >
              {createProject.isPending ? "创建中…" : "转为投标项目"}
            </Button>
            <Button onClick={() => favorite.mutate(!item.is_favorite)} disabled={favorite.isPending}>
              {item.is_favorite ? "取消收藏" : "收藏"}
            </Button>
          </div>
        }
      />

      {item.detail_error ? (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          详情拉取失败：{item.detail_error.code} · {item.detail_error.message}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Badge
              tone={
                item.detail_status === "full"
                  ? "green"
                  : item.detail_status === "attachment"
                    ? "blue"
                    : "amber"
              }
            >
              {item.detail_status === "full"
                ? "全文"
                : item.detail_status === "attachment"
                  ? "附件原文"
                  : "仅列表口径"}
            </Badge>
            <Badge tone="blue">{INFO_TYPES[item.info_type] ?? item.info_type}</Badge>
            {item.industry.map((industry) => (
              <Badge key={industry}>{industry}</Badge>
            ))}
          </div>
          {hasRenderableContent ? (
            <article
              className="text-sm leading-7 text-slate-700 [&_h1]:mb-3 [&_h1]:text-xl [&_h2]:mb-2 [&_h2]:text-lg [&_p]:mb-3 [&_table]:w-full [&_td]:border [&_td]:border-slate-200 [&_td]:p-2 [&_th]:border [&_th]:border-slate-200 [&_th]:p-2"
              dangerouslySetInnerHTML={{ __html: item.content_html as string }}
            />
          ) : item.detail_status === "attachment" ? (
            <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-6 text-center">
              <p className="text-sm font-medium text-slate-700">该公告正文为附件文件</p>
              <p className="mx-auto mt-1 max-w-md text-xs leading-5 text-slate-500">
                上游返回的是 PDF / Word 附件，系统未解析附件正文，因此这里不展示以免把存储路径当正文。
                请打开原文查看完整内容。
              </p>
              {item.source_url ? (
                <a
                  className="mt-3 inline-flex items-center gap-1 rounded-xl bg-white px-3 py-2 text-xs font-medium text-blue-600 ring-1 ring-slate-200 hover:bg-blue-50"
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开原文 ↗
                </a>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-slate-500">
              当前只有列表字段。详情正文未返回或拉取失败，系统不会用猜测内容填充。
            </p>
          )}
        </Card>

        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">基本信息</h2>
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-slate-500">采购人</dt>
              <dd>{item.buyer ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">代理机构</dt>
              <dd>{item.agency ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">地区</dt>
              <dd>{item.region ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">预算</dt>
              <dd>{formatMoney(item.budget_amount)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">项目编号</dt>
              <dd>{item.project_code ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">发布时间</dt>
              <dd>{formatDate(item.publish_at)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">投标截止</dt>
              <dd>{item.bid_deadline ? formatDate(item.bid_deadline) : "源未提供"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">原文</dt>
              <dd>
                {item.source_url ? (
                  <a className="text-blue-600 hover:underline" href={item.source_url} target="_blank" rel="noreferrer">
                    打开来源
                  </a>
                ) : (
                  "—"
                )}
              </dd>
            </div>
          </dl>
        </Card>
      </div>

      {item.awards.length > 0 ? (
        <Card className="mt-4 p-5">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">中标记录</h2>
          <table className="min-w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="pb-2">中标人</th>
                <th className="pb-2">金额</th>
                <th className="pb-2">时间</th>
                <th className="pb-2">来源</th>
              </tr>
            </thead>
            <tbody>
              {item.awards.map((award, index) => (
                <tr key={`${award.winner}-${index}`} className="border-t border-slate-100">
                  <td className="py-2">{award.winner}</td>
                  <td className="py-2">{formatMoney(award.win_amount)}</td>
                  <td className="py-2">{formatDate(award.award_at)}</td>
                  <td className="py-2">
                    {award.source_url ? (
                      <a className="text-blue-600 hover:underline" href={award.source_url} target="_blank" rel="noreferrer">
                        原文
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}

      <BuyerIntelCard opportunityId={params.id} buyer={item.buyer} />
      <EnterpriseIntelPanel
        companyName={item.buyer}
        title="采购人企业情报"
        description="对采购人做工商、信用与关联关系核验；公告名称不规范时先用「主体匹配」锚定企业。"
      />

      <OpportunityMatchPanel opportunityId={params.id} canWrite={canWrite} />
    </>
  );
}
