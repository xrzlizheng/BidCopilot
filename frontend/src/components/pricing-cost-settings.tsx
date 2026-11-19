"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge, Button, Card, ErrorState, Input } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";

// 契约冻结：GET/PUT /api/v1/system/pricing-settings。
// cost_model 三率为 Decimal 字符串或 null（未校准）；labor_rates 为 岗位→日单价 字符串映射；
// target_margins 三档均为字符串。PUT 两块可选（省略=不变），这里保存时全量提交两块。
export type PricingSettings = {
  cost_model: {
    direct_rate: string | null;
    indirect_rate: string | null;
    tax_rate: string | null;
    labor_rates: Record<string, string>;
  };
  target_margins: {
    conservative: string;
    balanced: string;
    aggressive: string;
  };
  source: Record<string, string>;
};

type LaborRow = { role: string; rate: string };

const RATE_FIELDS = [
  ["direct_rate", "直接成本率"],
  ["indirect_rate", "间接费率"],
  ["tax_rate", "税率"],
] as const;

const MARGIN_FIELDS = [
  ["conservative", "保守档目标净利率"],
  ["balanced", "均衡档目标净利率"],
  ["aggressive", "进取档目标净利率"],
] as const;

export function PricingCostSettings() {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "tenant:settings:manage");
  const [open, setOpen] = useState(false);
  const [rates, setRates] = useState<Record<string, string>>({
    direct_rate: "",
    indirect_rate: "",
    tax_rate: "",
  });
  const [laborRows, setLaborRows] = useState<LaborRow[]>([]);
  const [margins, setMargins] = useState<Record<string, string>>({
    conservative: "",
    balanced: "",
    aggressive: "",
  });
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const settings = useQuery({
    queryKey: ["pricing-settings"],
    queryFn: () => apiFetch<PricingSettings>("/system/pricing-settings"),
  });

  useEffect(() => {
    const data = settings.data;
    if (!data) return;
    setRates({
      direct_rate: data.cost_model.direct_rate ?? "",
      indirect_rate: data.cost_model.indirect_rate ?? "",
      tax_rate: data.cost_model.tax_rate ?? "",
    });
    setLaborRows(
      Object.entries(data.cost_model.labor_rates ?? {}).map(([role, rate]) => ({ role, rate })),
    );
    setMargins({ ...data.target_margins });
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () => {
      const rateChecks: [string, string][] = RATE_FIELDS.map(([key, label]) => [
        label,
        rates[key] ?? "",
      ]);
      for (const [label, value] of rateChecks) {
        if (value.trim() === "") continue;
        const parsed = Number(value);
        if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
          throw new Error(`${label}需为 0–1 之间的小数；留空表示未校准。`);
        }
      }
      const labor_rates: Record<string, string> = {};
      for (const row of laborRows) {
        const role = row.role.trim();
        const rate = row.rate.trim();
        if (!role && !rate) continue;
        if (!role || !rate) {
          throw new Error("人力单价行需同时填写岗位与日单价，请补全或删除空行。");
        }
        labor_rates[role] = rate;
      }
      const target_margins = {
        conservative: (margins.conservative ?? "").trim(),
        balanced: (margins.balanced ?? "").trim(),
        aggressive: (margins.aggressive ?? "").trim(),
      };
      if (!target_margins.conservative || !target_margins.balanced || !target_margins.aggressive) {
        throw new Error("三档目标净利率均需填写。");
      }
      return apiFetch<PricingSettings>("/system/pricing-settings", {
        method: "PUT",
        body: JSON.stringify({
          cost_model: {
            direct_rate: rates.direct_rate.trim() || null,
            indirect_rate: rates.indirect_rate.trim() || null,
            tax_rate: rates.tax_rate.trim() || null,
            labor_rates,
          },
          target_margins,
        }),
      });
    },
    onSuccess: (next) => {
      setNotice({ tone: "ok", text: "报价成本参数已保存，下一次报价策略测算生效。" });
      queryClient.setQueryData(["pricing-settings"], next);
    },
    onError: (error) =>
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "保存失败" }),
  });

  const data = settings.data;

  return (
    <Card className="mt-4 p-5" aria-label="报价成本参数">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="pricing-cost-settings-body"
      >
        <span>
          <span className="text-sm font-semibold text-slate-800">报价成本参数</span>
          <span className="ml-2 text-xs text-slate-400">
            三率 / 人力单价 / 三档目标净利率；未校准时档位价可能不可用
          </span>
        </span>
        <span className="text-xs font-medium text-electric-600">{open ? "收起" : "展开"}</span>
      </button>

      {open ? (
        <div id="pricing-cost-settings-body" className="mt-3">
          {settings.isLoading ? (
            <p className="text-sm text-slate-500">加载中…</p>
          ) : settings.isError ? (
            <ErrorState error={settings.error} onRetry={() => settings.refetch()} />
          ) : !data ? null : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="slate">
                  来源{" "}
                  {Object.entries(data.source ?? {})
                    .map(([key, value]) => `${key}=${value}`)
                    .join("，") || "未提供"}
                </Badge>
                {canWrite ? null : <Badge tone="amber">只读</Badge>}
                {canWrite ? (
                  <Button
                    variant="secondary"
                    onClick={() => save.mutate()}
                    disabled={save.isPending}
                    aria-label="保存成本参数"
                  >
                    {save.isPending ? "保存中…" : "保存参数"}
                  </Button>
                ) : (
                  <span className="text-xs text-slate-500">
                    修改需要 tenant:settings:manage 权限
                  </span>
                )}
              </div>

              {notice ? (
                <p
                  className={`mt-3 rounded-lg px-3 py-2 text-sm ${
                    notice.tone === "ok"
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-amber-50 text-amber-800"
                  }`}
                >
                  {notice.text}
                </p>
              ) : null}

              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                {RATE_FIELDS.map(([key, label]) => (
                  <label key={key} className="text-xs text-slate-500">
                    {label}（0–1 小数）
                    <Input
                      className="mt-1"
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      placeholder="留空表示未校准"
                      value={rates[key] ?? ""}
                      disabled={!canWrite}
                      aria-label={label}
                      onChange={(event) =>
                        setRates((current) => ({ ...current, [key]: event.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>

              <div className="mt-4 rounded-lg border border-slate-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">人力单价（元/人天）</h3>
                  {canWrite ? (
                    <Button
                      variant="secondary"
                      onClick={() => setLaborRows((current) => [...current, { role: "", rate: "" }])}
                      aria-label="添加人力单价行"
                    >
                      添加岗位
                    </Button>
                  ) : null}
                </div>
                {laborRows.length === 0 ? (
                  <p className="mt-2 text-xs text-slate-500">
                    尚未配置人力单价；报价策略的人力成本测算需要岗位与日单价，岗位名须与策略输入一致。
                  </p>
                ) : (
                  <div className="mt-2 space-y-2">
                    {laborRows.map((row, index) => (
                      <div key={index} className="grid gap-2 sm:grid-cols-12">
                        <Input
                          className="sm:col-span-5"
                          value={row.role}
                          disabled={!canWrite}
                          placeholder="岗位，如 工程师"
                          aria-label={`人力岗位第${index + 1}行`}
                          onChange={(event) =>
                            setLaborRows((current) =>
                              current.map((item, rowIndex) =>
                                rowIndex === index ? { ...item, role: event.target.value } : item,
                              ),
                            )
                          }
                        />
                        <Input
                          className="sm:col-span-5"
                          value={row.rate}
                          disabled={!canWrite}
                          placeholder="日单价（元），如 1200"
                          inputMode="decimal"
                          aria-label={`人力日单价第${index + 1}行`}
                          onChange={(event) =>
                            setLaborRows((current) =>
                              current.map((item, rowIndex) =>
                                rowIndex === index ? { ...item, rate: event.target.value } : item,
                              ),
                            )
                          }
                        />
                        <div className="flex items-center sm:col-span-2">
                          {canWrite ? (
                            <Button
                              variant="ghost"
                              onClick={() =>
                                setLaborRows((current) =>
                                  current.filter((_, rowIndex) => rowIndex !== index),
                                )
                              }
                              aria-label={`删除人力单价第${index + 1}行`}
                            >
                              删除
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                {MARGIN_FIELDS.map(([key, label]) => (
                  <label key={key} className="text-xs text-slate-500">
                    {label}
                    <Input
                      className="mt-1"
                      type="number"
                      step="0.01"
                      placeholder="如 0.08"
                      value={margins[key] ?? ""}
                      disabled={!canWrite}
                      aria-label={label}
                      onChange={(event) =>
                        setMargins((current) => ({ ...current, [key]: event.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
      ) : null}
    </Card>
  );
}