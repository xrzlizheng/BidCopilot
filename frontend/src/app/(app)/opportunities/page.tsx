"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Building2,
  CalendarDays,
  Database,
  Globe,
  MapPin,
  Radar,
  Search,
  SlidersHorizontal,
  Star,
} from "lucide-react";

import { Badge, Button, Card, EmptyState, Input, Loading, PageHeader, Select } from "@/components/ui";
import { OpportunityMatchBadges, type OpportunityMatchSummary } from "@/components/opportunity-match-panel";
import { OpportunitySubscriptions } from "@/components/opportunity-subscriptions";
import { WebIntelPanel } from "@/components/web-intel-panel";
import { apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import type { ListResponse, OpportunityListItem, SyncRun } from "@/lib/types";

function formatDate(value: string | null): string {
  return value ? value.slice(0, 10) : "—";
}

function formatMoney(value: string | number | null): string {
  if (value === null || value === "") return "—";
  const amount = Number(value);
  if (Number.isNaN(amount)) return String(value);
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(amount);
}

const INFO_TYPES: Record<string, string> = {
  tender: "招标",
  planned: "拟建",
  award: "中标",
  other: "其他",
};

const INFO_TONES: Record<string, "blue" | "amber" | "green" | "slate"> = {
  tender: "blue",
  planned: "amber",
  award: "green",
  other: "slate",
};

const INFO_TYPE_CODES: Record<string, string> = {
  tender: "1",
  planned: "2",
  award: "3",
};

export default function OpportunitiesPage() {
  return (
    <Suspense fallback={<Loading />}>
      <OpportunitiesView />
    </Suspense>
  );
}

function OpportunitiesView() {
  const searchParams = useSearchParams();
  const source = searchParams.get("source") === "web" ? "web" : "data";
  const newOnly = searchParams.get("new") === "1";
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canSync = hasPermission(user?.permissions, "opportunity:sync");
  const canWrite = hasPermission(user?.permissions, "opportunity:write");
  const [q, setQ] = useState("");
  const [infoType, setInfoType] = useState("");
  const [favorite, setFavorite] = useState(false);
  const [buyerWatch, setBuyerWatch] = useState(false);
  const [region, setRegion] = useState("");
  const [industry, setIndustry] = useState("");
  const [budgetMin, setBudgetMin] = useState("");
  const [budgetMax, setBudgetMax] = useState("");
  const [publishFrom, setPublishFrom] = useState("");
  const [publishTo, setPublishTo] = useState("");
  const [orderBy, setOrderBy] = useState("publish_at");
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("系统集成");
  const [showSubscriptions, setShowSubscriptions] = useState(false);
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  const [newWindowStart, setNewWindowStart] = useState(
    () => new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
  );

  useEffect(() => {
    if (newOnly) {
      setNewWindowStart(new Date(Date.now() - 24 * 3600 * 1000).toISOString());
      setPage(1);
    }
  }, [newOnly]);
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (infoType) params.set("info_type", infoType);
  if (favorite) params.set("favorite", "true");
  if (buyerWatch) params.set("buyer_watch", "true");
  if (newOnly) params.set("created_after", newWindowStart);
  if (region.trim()) params.set("region", region.trim());
  if (industry.trim()) params.set("industry", industry.trim());
  if (budgetMin.trim()) params.set("budget_min", budgetMin.trim());
  if (budgetMax.trim()) params.set("budget_max", budgetMax.trim());
  if (publishFrom) params.set("publish_from", publishFrom);
  if (publishTo) params.set("publish_to", publishTo);
  params.set("order_by", orderBy);
  params.set("page", String(page));
  params.set("page_size", "20");

  const query = useQuery({
    queryKey: ["opportunities", params.toString()],
    queryFn: () =>
      apiFetch<ListResponse<OpportunityListItem & { match?: OpportunityMatchSummary | null }>>(
        `/opportunities?${params.toString()}`,
      ),
  });

  const coverage = useQuery({
    queryKey: ["opportunity-coverage"],
    queryFn: () =>
      apiFetch<{
        total_opportunities: number;
        active_subscriptions: number;
        items: { name: string; matched: number; zero_hit: boolean }[];
      }>("/opportunities/coverage"),
  });

  const sync = useMutation({
    mutationFn: () =>
      apiFetch<SyncRun>("/opportunities/sync", {
        method: "POST",
        body: JSON.stringify({
          keywords: keyword.trim() ? [keyword.trim()] : [],
          info_types: ["1", "2", "3"],
          page_size: 20,
        }),
      }),
    onSuccess: (run) => {
      setSyncMessage(
        run.status === "succeeded"
          ? `同步完成：抓取 ${run.rows_fetched} 条，入库 ${run.rows_upserted} 条，新增 ${run.rows_new} 条。${
              run.warnings.length
                ? ` 告警 ${run.warnings.length} 条（${[...new Set(run.warnings.map((w) => w.code))].join("、")}）。`
                : ""
            }`
          : `同步失败：${run.error_code ?? run.status} ${run.error_message ?? ""}`,
      );
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
    onError: (error) => setSyncMessage(error instanceof Error ? error.message : "同步失败"),
  });

  const toggleFavorite = useMutation({
    mutationFn: ({ id, next }: { id: string; next: boolean }) =>
      apiFetch(`/opportunities/${id}/favorite`, { method: next ? "POST" : "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunities"] }),
  });

  const watchBuyer = useMutation({
    mutationFn: (buyer: string) =>
      apiFetch("/opportunities/buyer-watchlist", {
        method: "POST",
        body: JSON.stringify({ buyer_name: buyer }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunities"] }),
  });

  const totalPages = query.data ? Math.max(1, Math.ceil(query.data.total / query.data.page_size)) : 1;
  const items = query.data?.items ?? [];
  const advancedFilterCount = [
    region.trim(),
    industry.trim(),
    budgetMin.trim(),
    budgetMax.trim(),
    publishFrom,
    publishTo,
    orderBy !== "publish_at" ? orderBy : "",
  ].filter(Boolean).length;

  return (
    <>
      <PageHeader
        eyebrow="DISCOVER / INTELLIGENCE"
        title="机会中心"
        subtitle="统一情报入口：KeepRisk 数据接口商机与联网检索线索分别标注来源；检索结果需人工核实。"
        actions={
          source === "data" && canSync ? (
            <div className="flex items-center gap-2">
              <Input
                className="w-48"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="同步关键词"
              />
              <Button onClick={() => sync.mutate()} disabled={sync.isPending}>
                <Radar size={15} />
                {sync.isPending ? "同步中…" : "同步机会"}
              </Button>
            </div>
          ) : null
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-1.5 rounded-2xl border border-slate-200/70 bg-white/70 p-1.5 shadow-sm">
        <Link
          href="/opportunities"
          scroll={false}
          aria-current={source === "data" ? "page" : undefined}
          className={`inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition ${
            source === "data"
              ? "bg-slate-900 text-white shadow-sm"
              : "text-slate-600 hover:bg-slate-100"
          }`}
        >
          <Database size={14} />
          数据接口商机
          <span
            className={`tabular rounded-full px-1.5 py-0.5 text-[10px] ${
              source === "data" ? "bg-white/15 text-white" : "bg-slate-100 text-slate-500"
            }`}
          >
            {query.data?.total ?? "—"}
          </span>
        </Link>
        <Link
          href="/opportunities?source=web"
          scroll={false}
          aria-current={source === "web" ? "page" : undefined}
          className={`inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition ${
            source === "web"
              ? "bg-slate-900 text-white shadow-sm"
              : "text-slate-600 hover:bg-slate-100"
          }`}
        >
          <Globe size={14} />
          联网情报
        </Link>
        <span className="mono-label ml-auto hidden px-3 text-slate-400 sm:inline">
          统一入口 · 来源分开标注
        </span>
      </div>

      <div className={source === "data" ? "" : "hidden"}>
          {syncMessage ? (
          <div className="mb-4 rounded-xl border border-slate-200 bg-white/80 px-4 py-3 text-sm text-slate-600">
            {syncMessage}
          </div>
        ) : null}
  
        <Card className="mb-4 p-4">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              setPage(1);
              queryClient.invalidateQueries({ queryKey: ["opportunities"] });
            }}
          >
            <label className="min-w-56 flex-1 text-xs font-medium text-slate-500">
              关键词
              <Input
                className="mt-1.5"
                value={q}
                onChange={(event) => setQ(event.target.value)}
                placeholder="标题关键词"
              />
            </label>
            <label className="text-xs font-medium text-slate-500">
              类型
              <Select
                className="mt-1.5"
                value={infoType}
                onChange={(event) => {
                  setInfoType(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部</option>
                <option value="tender">招标</option>
                <option value="planned">拟建</option>
                <option value="award">中标</option>
              </Select>
            </label>
            <label className="flex h-10 items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={favorite}
                onChange={(event) => {
                  setFavorite(event.target.checked);
                  setPage(1);
                }}
              />
              仅看收藏
            </label>
            <label className="flex h-10 items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={buyerWatch}
                onChange={(event) => {
                  setBuyerWatch(event.target.checked);
                  setPage(1);
                }}
              />
              仅看关注采购人
            </label>
            <Link
              href={newOnly ? "/opportunities" : "/opportunities?new=1"}
              scroll={false}
              className={`inline-flex h-10 items-center rounded-xl border px-3 text-sm transition ${
                newOnly
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              只看新商机
            </Link>
            {canSync ? (
              <Button
                type="button"
                variant="secondary"
                onClick={() => setShowSubscriptions((value) => !value)}
              >
                <Radar size={14} /> {showSubscriptions ? "收起订阅" : "订阅管理"}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              aria-expanded={showAdvancedFilters}
              onClick={() => setShowAdvancedFilters((value) => !value)}
            >
              <SlidersHorizontal size={14} /> 更多筛选
              {advancedFilterCount ? `（${advancedFilterCount}）` : ""}
            </Button>
            {showAdvancedFilters ? (
            <div className="flex w-full flex-wrap items-end gap-3 border-t border-slate-100 pt-3">
              <label className="text-xs font-medium text-slate-500">
                地区
                <Input
                  className="mt-1.5 w-32"
                  value={region}
                  onChange={(event) => {
                    setRegion(event.target.value);
                    setPage(1);
                  }}
                  placeholder="例如：广东"
                />
              </label>
              <label className="text-xs font-medium text-slate-500">
                行业
                <Input
                  className="mt-1.5 w-32"
                  value={industry}
                  onChange={(event) => {
                    setIndustry(event.target.value);
                    setPage(1);
                  }}
                  placeholder="例如：IT 信息化"
                />
              </label>
              <label className="text-xs font-medium text-slate-500">
                预算下限
                <Input
                  className="mt-1.5 w-32"
                  type="number"
                  min={0}
                  value={budgetMin}
                  onChange={(event) => {
                    setBudgetMin(event.target.value);
                    setPage(1);
                  }}
                  placeholder="元"
                />
              </label>
              <label className="text-xs font-medium text-slate-500">
                预算上限
                <Input
                  className="mt-1.5 w-32"
                  type="number"
                  min={0}
                  value={budgetMax}
                  onChange={(event) => {
                    setBudgetMax(event.target.value);
                    setPage(1);
                  }}
                  placeholder="元"
                />
              </label>
              <label className="text-xs font-medium text-slate-500">
                发布起
                <Input
                  className="mt-1.5 w-40"
                  type="date"
                  value={publishFrom}
                  onChange={(event) => {
                    setPublishFrom(event.target.value);
                    setPage(1);
                  }}
                />
              </label>
              <label className="text-xs font-medium text-slate-500">
                发布止
                <Input
                  className="mt-1.5 w-40"
                  type="date"
                  value={publishTo}
                  onChange={(event) => {
                    setPublishTo(event.target.value);
                    setPage(1);
                  }}
                />
              </label>
              <label className="text-xs font-medium text-slate-500">
                排序
                <Select
                  className="mt-1.5"
                  value={orderBy}
                  onChange={(event) => {
                    setOrderBy(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="publish_at">发布时间（新→旧）</option>
                  <option value="budget">预算（高→低）</option>
                  <option value="match">订阅匹配度</option>
                </Select>
              </label>
              {orderBy === "match" ? (
                <span className="mono-label pb-2 text-slate-400">
                  加权分排序 · 徽标为命中条件种类
                </span>
              ) : null}
            </div>
            ) : advancedFilterCount ? (
              <p className="w-full text-xs text-slate-500">
                已启用 {advancedFilterCount} 个高级筛选，点击「更多筛选」查看或修改。
              </p>
            ) : null}
            <Button type="submit" variant="secondary">
              <Search size={14} /> 筛选
            </Button>
          </form>
        </Card>

        {showSubscriptions && canSync ? (
          <OpportunitySubscriptions
            defaults={{
              keyword: q,
              infoTypes: infoType ? [INFO_TYPE_CODES[infoType] ?? "1"] : ["1", "2", "3"],
              industry,
              budgetMin,
              budgetMax,
              publishFrom,
              publishTo,
            }}
          />
        ) : null}
  
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
          <span>
            共 <span className="tabular font-semibold text-slate-800">{query.data?.total ?? 0}</span> 条
            {q ? ` · 关键词「${q}」` : ""}
            {infoType ? ` · ${INFO_TYPES[infoType] ?? infoType}` : ""}
            {newOnly ? " · 仅最近 24 小时新入库" : ""}
          </span>
          <span>来源：KeepRisk · 本页 {items.length} 条</span>
          {coverage.data ? (
            <span>
              覆盖诊断：{coverage.data.active_subscriptions} 个启用订阅 · 零命中{" "}
              {coverage.data.items.filter((item) => item.zero_hit).length} 个 · 库内{" "}
              {coverage.data.total_opportunities} 条
            </span>
          ) : null}
        </div>
  
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <div className="text-sm text-rose-600">{query.error.message}</div>
        ) : items.length === 0 ? (
          <Card>
            <EmptyState text="暂无机会。管理员可先执行同步。" />
          </Card>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {items.map((item) => (
              <Card
                key={item.id}
                className="group flex flex-col p-4 transition hover:border-aurora-500/35 hover:shadow-glow"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    <Badge tone={INFO_TONES[item.info_type] ?? "slate"}>
                      {INFO_TYPES[item.info_type] ?? item.info_type}
                    </Badge>
                    {typeof item.match_hits === "number" ? (
                      <Badge tone={item.match_hits > 0 ? "green" : "slate"}>
                        命中 {item.match_hits} 项
                      </Badge>
                    ) : null}
                    <OpportunityMatchBadges match={item.match} />
                  </div>
                  <Badge tone={item.detail_status === "full" ? "green" : "amber"}>
                    {item.detail_status === "full" ? "正文已拉取" : "列表口径"}
                  </Badge>
                </div>
                <Link
                  href={`/opportunities/${item.id}`}
                  className="mt-3 line-clamp-2 text-[15px] font-semibold leading-6 text-slate-800 transition group-hover:text-electric-600"
                >
                  {item.title}
                </Link>
                <div className="mt-3 space-y-1.5 text-xs text-slate-500">
                  <p className="flex items-center gap-1.5">
                    <Building2 size={13} /> {item.buyer ?? "采购人未披露"}
                    {item.buyer && !item.explanation?.buyer_watched && canWrite ? (
                      <button
                        type="button"
                        className="ml-1 rounded-md border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500 hover:border-amber-300 hover:text-amber-700"
                        onClick={() => watchBuyer.mutate(item.buyer as string)}
                      >
                        关注采购人
                      </button>
                    ) : item.explanation?.buyer_watched ? (
                      <span className="ml-1 rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
                        已关注
                      </span>
                    ) : null}
                  </p>
                  <p className="flex items-center gap-1.5">
                    <MapPin size={13} /> {item.region ?? "地区未披露"}
                  </p>
                  <p className="flex items-center gap-1.5">
                    <CalendarDays size={13} /> {formatDate(item.publish_at)}
                  </p>
                  {item.explanation?.reasons?.length ? (
                    <p className="text-emerald-700">
                      {item.explanation.reasons.slice(0, 2).join(" · ")}
                    </p>
                  ) : null}
                  {item.explanation?.risks?.length ? (
                    <p className="text-amber-700">{item.explanation.risks.slice(0, 2).join(" · ")}</p>
                  ) : null}
                </div>
                <div className="mt-4 flex items-end justify-between border-t border-slate-100 pt-3">
                  <div>
                    <p className="mono-label text-slate-400">BUDGET</p>
                    <p className="tabular mt-1 text-lg font-semibold text-slate-900">
                      {formatMoney(item.budget_amount)}
                    </p>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      aria-label="收藏"
                      className="rounded-lg p-2 text-slate-400 transition hover:bg-amber-50 hover:text-amber-600"
                      onClick={() => toggleFavorite.mutate({ id: item.id, next: true })}
                    >
                      <Star size={15} />
                    </button>
                    <Link
                      href={`/opportunities/${item.id}`}
                      className="inline-flex items-center gap-1 rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-slate-700"
                    >
                      查看 <ArrowRight size={12} />
                    </Link>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
  
        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
          <span>第 {page} / {totalPages} 页</span>
          <div className="flex items-center gap-2">
            <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
              上一页
            </Button>
            <Button
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </div>

      <div className={source === "web" ? "" : "hidden"}>
        <WebIntelPanel />
      </div>
    </>
  );
}
