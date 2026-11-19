"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Globe, Search } from "lucide-react";

import { Badge, Button, Card, EmptyState, Input, Select } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import type { WebSearchResponse } from "@/lib/types";

type AiStatus = {
  web_search: { configured: boolean; provider: string; host: string | null };
};

export function WebIntelPanel() {
  const [query, setQuery] = useState("");
  const [count, setCount] = useState("5");

  const status = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => apiFetch<AiStatus>("/system/ai-status"),
    retry: false,
    staleTime: 60_000,
  });

  const search = useMutation({
    mutationFn: () =>
      apiFetch<WebSearchResponse>("/web-search", {
        method: "POST",
        body: JSON.stringify({ query: query.trim(), count: Number(count) }),
      }),
  });

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Globe size={16} className="text-electric-600" />
          <h2 className="text-sm font-semibold">联网检索</h2>
          {status.data ? (
            <Badge tone={status.data.web_search.configured ? "green" : "amber"}>
              {status.data.web_search.provider}
              {status.data.web_search.configured ? " 已配置" : " 未配置"}
            </Badge>
          ) : null}
          <span className="mono-label ml-auto text-slate-400">仅作线索 · 需人工核实</span>
        </div>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
          检索公开网络信息，补充项目背景、竞对与价格线索。结果仅作为本次检索线索，不写入
          KeepRisk 商机库，也不作为标书事实来源。
        </p>
        <form
          className="mt-3 flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (query.trim()) search.mutate();
          }}
        >
          <Input
            className="w-full sm:w-96"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如：某市 政务云 运维 中标 厂商"
          />
          <Select className="w-24" value={count} onChange={(event) => setCount(event.target.value)}>
            <option value="3">3 条</option>
            <option value="5">5 条</option>
            <option value="10">10 条</option>
          </Select>
          <Button type="submit" disabled={search.isPending || !query.trim()}>
            <Search size={14} /> {search.isPending ? "检索中…" : "联网检索"}
          </Button>
        </form>
      </Card>

      {search.error ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {search.error.message}
        </div>
      ) : null}

      {search.data ? (
        <Card>
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-4">
            <h2 className="text-sm font-semibold">{search.data.query}</h2>
            <Badge tone="blue">{search.data.provider}</Badge>
            <Badge tone={search.data.status === "ok" ? "green" : "amber"}>{search.data.status}</Badge>
            <span className="ml-auto text-xs text-slate-400">
              {search.data.results.length} 条 · 已计费 {search.data.charged ? "是" : "否"}
            </span>
          </div>
          {search.data.results.length === 0 ? (
            <EmptyState
                text="这次检索没有可用结果。"
                hint="换更具体的关键词（项目名 + 采购人）重试；若持续为空，检查「账号设置 → AI 模型」里的检索服务是否已配置。"
              />
          ) : (
            <ul className="divide-y divide-slate-100">
              {search.data.results.map((item) => (
                <li key={item.url} className="px-5 py-4">
                  <a
                    className="text-sm font-medium text-electric-600 hover:underline"
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {item.title}
                  </a>
                  <p className="mt-1 text-xs text-slate-400">{item.url}</p>
                  {item.snippet ? (
                    <p className="mt-1 text-sm leading-6 text-slate-600">{item.snippet}</p>
                  ) : null}
                  {item.published_at ? (
                    <p className="mt-1 text-[11px] text-slate-400">发布线索：{item.published_at}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}
    </div>
  );
}
