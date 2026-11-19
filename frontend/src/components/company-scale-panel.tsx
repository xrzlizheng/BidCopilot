"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Card, EmptyState, Input, Select } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import type { Company, CompanyScale, ScaleHistoryEntry, ScaleImpact } from "@/lib/types";

// 与后端 enterprise_scale.INDUSTRIES 的 key 对齐。放在前端是为了让用户看到
// 「系统按哪套口径算」——划型标准不同行业差别极大，不展示行业就没法核对。
const INDUSTRIES: { key: string; label: string }[] = [
  { key: "software", label: "软件和信息技术服务业" },
  { key: "telecom", label: "信息传输业" },
  { key: "industry", label: "工业（制造/生产/加工）" },
  { key: "construction", label: "建筑业" },
  { key: "wholesale", label: "批发业" },
  { key: "retail", label: "零售业" },
  { key: "transport", label: "交通运输业" },
  { key: "warehouse", label: "仓储业" },
  { key: "post", label: "邮政业" },
  { key: "hotel", label: "住宿业" },
  { key: "catering", label: "餐饮业" },
  { key: "real_estate", label: "房地产开发经营" },
  { key: "property", label: "物业管理" },
  { key: "leasing", label: "租赁和商务服务业" },
  { key: "agriculture", label: "农、林、牧、渔业" },
  { key: "other", label: "其他未列明行业" },
];

// 这三类行业按「资产总额」划型，其余按「营业收入」
const ASSET_BASED = new Set(["construction", "real_estate", "leasing"]);

const SCALE_TONES: Record<string, "green" | "amber" | "rose" | "slate"> = {
  大型: "rose",
  中型: "amber",
  小型: "green",
  微型: "green",
};

export function CompanyScalePanel({ company }: { company?: Company }) {
  const queryClient = useQueryClient();
  const [industry, setIndustry] = useState("");
  const [employees, setEmployees] = useState("");
  const [revenue, setRevenue] = useState("");
  const [assets, setAssets] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [impact, setImpact] = useState<ScaleImpact | null>(null);
  const [impactError, setImpactError] = useState<string | null>(null);

  useEffect(() => {
    if (!company) return;
    setIndustry(company.enterprise_industry ?? "");
    setEmployees(company.employee_count != null ? String(company.employee_count) : "");
    setRevenue(company.annual_revenue != null ? String(company.annual_revenue) : "");
    setAssets(company.total_assets != null ? String(company.total_assets) : "");
  }, [company]);

  const scale = useQuery({
    queryKey: ["company-scale", company?.id],
    queryFn: () => apiFetch<CompanyScale>(`/companies/${company?.id}/scale`),
    enabled: Boolean(company?.id),
    retry: false,
  });

  const history = useQuery({
    queryKey: ["company-scale-history", company?.id],
    queryFn: () => apiFetch<ScaleHistoryEntry[]>(`/companies/${company?.id}/scale-history`),
    enabled: Boolean(company?.id),
    retry: false,
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!company) throw new Error("未选择企业");
      // 默认不传 enterprise_scale：已人工确认的结论保留，未确认的会自动重算。
      // 只有用户明确点「按指标重算」时才显式传 null 覆盖。
      const payload: Record<string, unknown> = {
        enterprise_industry: industry || null,
        employee_count: employees.trim() === "" ? null : Number(employees),
        annual_revenue: revenue.trim() === "" ? null : Number(revenue),
        total_assets: assets.trim() === "" ? null : Number(assets),
      };
      // 旧划型必须在发起请求前捕获：onSuccess 里读 props 时，父组件可能已经
      // 用新数据刷新过，拿到的会是新值，导致「变化前后」对比失效。
      const previous = company.enterprise_scale ?? null;
      const updated = await apiFetch<Company>(`/companies/${company.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      return { updated, previous };
    },
    onSuccess: async ({ previous }) => {
      setMessage("已保存，划型已按国标重新计算");
      await queryClient.invalidateQueries({ queryKey: ["company-scale", company?.id] });
      await queryClient.invalidateQueries({ queryKey: ["companies"] });
      await queryClient.invalidateQueries({ queryKey: ["company-scale-history", company?.id] });
      // 划型变动会波及在投项目：中小企业声明函错填属于提供虚假材料，
      // 所以保存后立刻评估影响，而不是等用户自己想起来。
      try {
        const result = await apiFetch<ScaleImpact>(
          `/companies/${company?.id}/scale-impact?previous_scale=${encodeURIComponent(previous ?? "")}`,
        );
        setImpact(result.needs_review || result.crossed_boundary ? result : null);
        setImpactError(null);
      } catch (error) {
        // 划型变动波及在投项目，评估失败不能静默：给显性告警让用户手动复查。
        setImpact(null);
        setImpactError(
          error instanceof Error
            ? `划型影响评估失败：${error.message}`
            : "划型影响评估失败，请手动复查在投项目",
        );
      }
    },
    onError: (error: Error) => setMessage(error.message),
  });

  const confirm = useMutation({
    mutationFn: async () => {
      if (!company || !scale.data?.scale) throw new Error("暂无可确认的划型");
      // 人工确认：把当前计算结果固化为 manual，后续重算不再覆盖
      return apiFetch<Company>(`/companies/${company.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enterprise_scale: scale.data.scale }),
      });
    },
    onSuccess: async () => {
      setMessage("已人工确认，后续指标变更不会自动覆盖该结论");
      await queryClient.invalidateQueries({ queryKey: ["company-scale", company?.id] });
      await queryClient.invalidateQueries({ queryKey: ["companies"] });
    },
    onError: (error: Error) => setMessage(error.message),
  });

  const recompute = useMutation({
    mutationFn: async () => {
      if (!company) throw new Error("未选择企业");
      // 显式传 null 表示放弃人工结论，恢复按国标自动计算
      return apiFetch<Company>(`/companies/${company.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enterprise_scale: null }),
      });
    },
    onSuccess: async () => {
      setMessage("已放弃人工确认，按当前指标重新计算");
      await queryClient.invalidateQueries({ queryKey: ["company-scale", company?.id] });
      await queryClient.invalidateQueries({ queryKey: ["companies"] });
    },
    onError: (error: Error) => setMessage(error.message),
  });

  if (!company) {
    return (
      <Card className="p-5">
        <h2 className="mb-2 text-sm font-semibold">企业划型</h2>
        <EmptyState text="先选择企业后维护划型指标。" />
      </Card>
    );
  }

  const result = scale.data;
  const needsAssets = ASSET_BASED.has(industry);
  const missing = result?.missing ?? [];

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">企业划型（中小企业声明函依据）</h2>
          <p className="mt-1 text-xs text-slate-500">
            按《中小企业划型标准规定》（工信部联企业〔2011〕300 号）计算。声明函填错属于提供虚假材料，
            可能直接废标，所以指标必须真实可查。
          </p>
        </div>
        {result?.scale ? (
          <Badge tone={SCALE_TONES[result.scale] ?? "slate"}>
            {result.scale}
            {result.is_sme === true ? "（中小企业）" : result.is_sme === false ? "（非中小企业）" : ""}
          </Badge>
        ) : null}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-600">
          划型行业
          <Select
            className="mt-1"
            value={industry}
            onChange={(event) => setIndustry(event.target.value)}
          >
            <option value="">请选择（按营业执照主营范围判断）</option>
            {INDUSTRIES.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-xs text-slate-600">
          从业人员（人）
          <Input
            className="mt-1"
            inputMode="numeric"
            placeholder="如 120"
            value={employees}
            onChange={(event) => setEmployees(event.target.value)}
          />
        </label>
        <label className="text-xs text-slate-600">
          营业收入（万元）
          <Input
            className="mt-1"
            inputMode="decimal"
            placeholder="如 2000"
            value={revenue}
            onChange={(event) => setRevenue(event.target.value)}
          />
        </label>
        <label className="text-xs text-slate-600">
          资产总额（万元）
          {needsAssets ? (
            <span className="ml-1 text-rose-600">该行业必填</span>
          ) : (
            <span className="ml-1 text-slate-400">该行业不参与判定</span>
          )}
          <Input
            className="mt-1"
            inputMode="decimal"
            placeholder="如 5000"
            value={assets}
            onChange={(event) => setAssets(event.target.value)}
          />
        </label>
      </div>

      {result ? (
        <div className="mt-3 rounded-lg bg-slate-50 p-3">
          <p className="text-xs text-slate-700">
            <span className="font-medium">判定依据：</span>
            {result.basis}
          </p>
          {missing.length > 0 ? (
            <p className="mt-1 text-xs text-amber-700">
              缺少 {missing.join("、")}，无法判定——请补齐后再用于中小企业项目投标。
            </p>
          ) : null}
          {result.source === "manual" ? (
            <p className="mt-1 text-xs text-emerald-700">该结论已人工确认，指标变更不会自动覆盖。</p>
          ) : null}
        </div>
      ) : null}

      {impactError ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {impactError}
        </div>
      ) : null}

      {impact ? (
        <div
          className={
            impact.crossed_boundary
              ? "mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3"
              : "mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3"
          }
        >
          <p
            className={
              impact.crossed_boundary
                ? "text-xs font-medium text-rose-700"
                : "text-xs font-medium text-amber-700"
            }
          >
            {impact.message}
          </p>
          {impact.affected_projects.length > 0 ? (
            <ul className="mt-1.5 space-y-1">
              {impact.affected_projects.map((project) => (
                <li key={project.project_id} className="text-xs leading-5 text-slate-700">
                  · {project.name}
                  {project.bid_deadline ? (
                    <span className="text-slate-500">
                      （截止 {new Date(project.bid_deadline).toLocaleDateString("zh-CN")}）
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {message ? <p className="mt-2 text-xs text-slate-500">{message}</p> : null}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "保存中…" : "保存并计算"}
        </Button>
        <Button
          variant="secondary"
          onClick={() => confirm.mutate()}
          disabled={!result?.scale || confirm.isPending || result?.confirmed}
        >
          {result?.confirmed ? "已确认" : "人工确认此结论"}
        </Button>
        {result?.confirmed ? (
          <Button variant="ghost" onClick={() => recompute.mutate()} disabled={recompute.isPending}>
            {recompute.isPending ? "重算中…" : "放弃人工结论，按指标重算"}
          </Button>
        ) : null}
      </div>

      {(history.data?.length ?? 0) > 0 ? (
        <div className="mt-3 rounded-lg border border-slate-200 p-3">
          <p className="mb-1.5 text-xs font-medium text-slate-700">划型变更历史</p>
          <ul className="space-y-1">
            {history.data!.slice(0, 6).map((entry) => (
              <li key={entry.at} className="text-xs leading-5 text-slate-600">
                <span className="text-slate-400">
                  {new Date(entry.at).toLocaleString("zh-CN", { hour12: false })}
                </span>
                <span className="mx-1">
                  {entry.from ?? "未判定"} → {entry.to}
                </span>
                {entry.crossed_boundary ? (
                  <Badge tone="rose">跨越中小企业分界线</Badge>
                ) : null}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-slate-400">
            记录存于审计链（带哈希），可作为声明函纠纷时的举证材料。
          </p>
        </div>
      ) : null}

      <p className="mt-3 text-xs leading-5 text-slate-400">
        单位统一为万元，与国标口径一致。建筑业、房地产开发、租赁和商务服务业按资产总额划型，
        其余行业按营业收入划型；缺口径时系统不给结论，避免误判影响投标。
      </p>
    </Card>
  );
}
