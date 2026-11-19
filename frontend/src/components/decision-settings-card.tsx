"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge, Button, Card, ErrorState, Input } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";

type RuntimeSettings = {
  weights: {
    qualification: number;
    economics: number;
    cost: number;
    risk: number;
    resource: number;
    competition: number;
  };
  thresholds: { invest_min: number; pending_min: number };
  cost_model: {
    direct_rate: number | null;
    indirect_rate: number | null;
    tax_rate: number | null;
    person_day_rate: number | null;
  };
  reminders: { enabled: boolean; offsets: number[]; buffer_days: number };
  source: Record<string, string>;
};

const WEIGHT_LABELS: Record<string, string> = {
  qualification: "资格",
  economics: "经济性",
  cost: "成本底线",
  risk: "风险",
  resource: "资源",
  competition: "竞争情报",
};

export function DecisionSettingsCard() {
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canWrite = hasPermission(user?.permissions, "tenant:settings:manage");
  const [form, setForm] = useState<RuntimeSettings | null>(null);
  const [offsetsText, setOffsetsText] = useState("7,3,1");
  const [notice, setNotice] = useState<string | null>(null);

  const data = useQuery({
    queryKey: ["runtime-decision-settings"],
    queryFn: () => apiFetch<RuntimeSettings>("/system/decision-settings"),
  });

  useEffect(() => {
    if (!data.data) return;
    setForm(data.data);
    setOffsetsText((data.data.reminders.offsets || []).join(","));
  }, [data.data]);

  const save = useMutation({
    mutationFn: () => {
      if (!form) throw new Error("设置尚未加载");
      const offsets = offsetsText
        .split(",")
        .map((value) => Number(value.trim()))
        .filter((value) => Number.isInteger(value) && value > 0 && value <= 60);
      return apiFetch<RuntimeSettings>("/system/decision-settings", {
        method: "PUT",
        body: JSON.stringify({
          weights: form.weights,
          thresholds: form.thresholds,
          cost_model: form.cost_model,
          reminders: { ...form.reminders, offsets },
        }),
      });
    },
    onSuccess: (next) => {
      setForm(next);
      setNotice("决策与提醒参数已保存，下一次测算/提醒生效。");
      queryClient.invalidateQueries({ queryKey: ["runtime-decision-settings"] });
    },
    onError: (error) =>
      setNotice(error instanceof Error ? error.message : "保存失败"),
  });

  if (data.error) {
    return (
      <Card className="p-5">
        <ErrorState error={data.error} onRetry={() => data.refetch()} />
      </Card>
    );
  }
  if (!form) return null;

  const updateWeight = (key: string, value: string) =>
    setForm({ ...form, weights: { ...form.weights, [key]: Number(value) } });
  const updateNumber = (section: "thresholds" | "cost_model", key: string, value: string) =>
    setForm({
      ...form,
      [section]: { ...form[section], [key]: value === "" ? null : Number(value) },
    });

  return (
    <Card className="mt-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">决策与提醒参数</h2>
          <p className="mt-1 text-xs text-slate-500">
            权重/阈值默认 tool-default；成本率未校准前，经济性比率维度不计分。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone="slate">来源 {form.source.cost_model === "missing" ? "未校准" : form.source.cost_model}</Badge>
          {canWrite ? (
            <Button variant="secondary" onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? "保存中…" : "保存参数"}
            </Button>
          ) : null}
        </div>
      </div>
      {notice ? <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">{notice}</p> : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-5">
        {Object.entries(form.weights).map(([key, value]) => (
          <label key={key} className="text-xs text-slate-500">
            {WEIGHT_LABELS[key] ?? key} 权重
            <Input
              className="mt-1"
              type="number"
              value={value}
              onChange={(event) => updateWeight(key, event.target.value)}
            />
          </label>
        ))}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-500">
          Go 阈值
          <Input
            className="mt-1"
            type="number"
            value={form.thresholds.invest_min}
            onChange={(event) => updateNumber("thresholds", "invest_min", event.target.value)}
          />
        </label>
        <label className="text-xs text-slate-500">
          Conditional Go 阈值
          <Input
            className="mt-1"
            type="number"
            value={form.thresholds.pending_min}
            onChange={(event) => updateNumber("thresholds", "pending_min", event.target.value)}
          />
        </label>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        {(["direct_rate", "indirect_rate", "tax_rate"] as const).map((key) => (
          <label key={key} className="text-xs text-slate-500">
            {key === "direct_rate" ? "直接成本率" : key === "indirect_rate" ? "间接成本率" : "税率"}
            <Input
              className="mt-1"
              type="number"
              step="0.01"
              placeholder="留空表示未校准"
              value={form.cost_model[key] ?? ""}
              onChange={(event) => updateNumber("cost_model", key, event.target.value)}
            />
          </label>
        ))}
        <label className="text-xs text-slate-500">
          人天单价（元/人天）
          <Input
            className="mt-1"
            type="number"
            placeholder="留空表示未校准"
            value={form.cost_model.person_day_rate ?? ""}
            onChange={(event) => updateNumber("cost_model", "person_day_rate", event.target.value)}
          />
        </label>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-slate-500">
          提醒提前天数（逗号分隔）
          <Input
            className="mt-1"
            value={offsetsText}
            onChange={(event) => setOffsetsText(event.target.value)}
            placeholder="7,3,1"
          />
        </label>
        <label className="text-xs text-slate-500">
          内部截止缓冲天数
          <Input
            className="mt-1"
            type="number"
            value={form.reminders.buffer_days}
            onChange={(event) =>
              setForm({
                ...form,
                reminders: { ...form.reminders, buffer_days: Number(event.target.value) },
              })
            }
          />
        </label>
        <label className="mt-6 flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={form.reminders.enabled}
            onChange={(event) =>
              setForm({ ...form, reminders: { ...form.reminders, enabled: event.target.checked } })
            }
          />
          启用截止提醒
        </label>
      </div>
    </Card>
  );
}
