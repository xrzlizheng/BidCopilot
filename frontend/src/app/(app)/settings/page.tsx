"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Cpu, KeyRound, SlidersHorizontal, UserRound } from "lucide-react";

import { DecisionSettingsCard } from "@/components/decision-settings-card";
import { useConfirm } from "@/components/confirm-dialog";
import { Badge, Button, Card, ErrorState, Input, PageHeader, Select } from "@/components/ui";
import { ApiError, apiFetch, logout } from "@/lib/api";
import { hasPermission, useAuthStore } from "@/lib/auth-store";
import { jumpToAnchor } from "@/lib/scroll-highlight";
import type {
  ApiToken,
  ModelConfig,
  NotificationChannel,
  WebhookDelivery,
  WebhookEndpoint,
} from "@/lib/types";

/** 设置页各卡片的提示作用域：每个卡片只渲染自己的提示。 */
type MessageScope =
  | "password"
  | "modelPolicy"
  | "personalModel"
  | "tenantModel"
  | "channels"
  | "openApi";
type SettingsTab = "account" | "models" | "decision" | "notify" | "openapi";

function ScopedMessage({ text }: { text: string | null }) {
  if (!text) return null;
  return <div className="text-sm text-rose-600">{text}</div>;
}

export default function SettingsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const user = useAuthStore((state) => state.user);
  const canManageModels = hasPermission(user?.permissions, "model:manage");
  const canUseModels = hasPermission(user?.permissions, "model:use");
  const canManageModelPolicy = hasPermission(user?.permissions, "model:policy:manage");
  const canViewDecision = hasPermission(user?.permissions, "tenant:read");
  const canManageChannels = hasPermission(user?.permissions, "tenant:settings:manage");
  const [activeTab, setActiveTab] = useState<SettingsTab>("account");
  const [allowPersonalEnterprise, setAllowPersonalEnterprise] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  // 提示按作用域分开：否则模型配置的报错会渲染进「修改密码」卡片（见 test-report-3 D7）
  const [messages, setMessages] = useState<Record<MessageScope, string | null>>({
    password: null,
    modelPolicy: null,
    personalModel: null,
    tenantModel: null,
    channels: null,
    openApi: null,
  });
  const setMessage = (scope: MessageScope, value: string | null) =>
    setMessages((current) => ({ ...current, [scope]: value }));
  const [pending, setPending] = useState(false);
  const [modelForms, setModelForms] = useState<
    Record<string, { base_url: string; model: string; enabled: boolean }>
  >({});
  const [personalForms, setPersonalForms] = useState<
    Record<string, { base_url: string; model: string; enabled: boolean }>
  >({});
  const [personalKeys, setPersonalKeys] = useState<Record<string, string>>({});
  const [channelForm, setChannelForm] = useState({ kind: "wechat", target: "", label: "" });
  const [tokenForm, setTokenForm] = useState({
    name: "",
    scopes: [] as string[],
    expires_in_days: 90,
  });
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [webhookForm, setWebhookForm] = useState({
    url: "",
    events: ["project.status.changed"],
    secret: "",
  });
  const [issuedSecret, setIssuedSecret] = useState<string | null>(null);
  const [openDeliveries, setOpenDeliveries] = useState<string | null>(null);
  const models = useQuery({
    queryKey: ["model-configs"],
    queryFn: () => apiFetch<ModelConfig[]>("/system/model-configs"),
    enabled: canManageModels,
  });
  const personalModels = useQuery({
    queryKey: ["personal-model-configs"],
    queryFn: () => apiFetch<ModelConfig[]>("/system/model-configs/personal"),
    enabled: canUseModels,
  });
  const modelPolicy = useQuery({
    queryKey: ["model-policy"],
    queryFn: () =>
      apiFetch<{
        byok_enabled: boolean;
        allow_personal_key_for_enterprise_data: boolean;
      }>("/system/model-policy"),
    enabled: canManageModelPolicy,
  });
  const saveModel = useMutation({
    mutationFn: (payload: {
      purpose: string;
      base_url: string;
      model: string;
      enabled: boolean;
    }) =>
      apiFetch<ModelConfig>(`/system/model-configs/${payload.purpose}`, {
        method: "PATCH",
        body: JSON.stringify({
          base_url: payload.base_url || null,
          model: payload.model || null,
          enabled: payload.enabled,
        }),
      }),
    onSuccess: (row) => {
      setMessage("tenantModel", `${row.purpose === "main" ? "主模型" : "判别模型"}配置已保存。`);
      queryClient.invalidateQueries({ queryKey: ["model-configs"] });
      queryClient.invalidateQueries({ queryKey: ["ai-status"] });
    },
    onError: (error) =>
      setMessage(
        "tenantModel",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "模型配置保存失败",
      ),
  });

  const savePersonalModel = useMutation({
    mutationFn: (payload: {
      purpose: string;
      base_url: string;
      model: string;
      api_key: string;
      enabled: boolean;
    }) =>
      apiFetch<ModelConfig>(`/system/model-configs/personal/${payload.purpose}`, {
        method: "PUT",
        body: JSON.stringify({
          base_url: payload.base_url || null,
          model: payload.model || null,
          api_key: payload.api_key || null,
          enabled: payload.enabled,
        }),
      }),
    onSuccess: (row) => {
      setMessage(
        "personalModel",
        `${row.purpose === "main" ? "个人主模型" : "个人判别模型"}已保存，Key 只保留 last4。`,
      );
      setPersonalKeys((current) => ({ ...current, [row.purpose]: "" }));
      queryClient.invalidateQueries({ queryKey: ["personal-model-configs"] });
      queryClient.invalidateQueries({ queryKey: ["ai-status"] });
    },
    onError: (error) =>
      setMessage(
        "personalModel",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "个人模型配置保存失败",
      ),
  });
  const revokePersonalModel = useMutation({
    mutationFn: (purpose: string) =>
      apiFetch(`/system/model-configs/personal/${purpose}`, { method: "DELETE" }),
    onSuccess: () => {
      setMessage("personalModel", "个人模型 Key 已撤销。");
      queryClient.invalidateQueries({ queryKey: ["personal-model-configs"] });
      queryClient.invalidateQueries({ queryKey: ["ai-status"] });
    },
    onError: (error) =>
      setMessage(
        "personalModel",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "撤销失败",
      ),
  });
  const saveModelPolicy = useMutation({
    mutationFn: (enabled: boolean) =>
      apiFetch("/system/model-policy", {
        method: "PATCH",
        body: JSON.stringify({ allow_personal_key_for_enterprise_data: enabled }),
      }),
    onSuccess: () => {
      setMessage("modelPolicy", "个人模型企业数据策略已保存。");
      queryClient.invalidateQueries({ queryKey: ["model-policy"] });
    },
    onError: (error) =>
      setMessage(
        "modelPolicy",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "策略保存失败",
      ),
  });

  const channels = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => apiFetch<NotificationChannel[]>("/notifications/channels"),
    enabled: canManageChannels,
  });
  const createChannel = useMutation({
    mutationFn: (payload: { kind: string; target: string; label: string }) =>
      apiFetch<NotificationChannel>("/notifications/channels", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      setMessage("channels", "通知渠道已添加。");
      setChannelForm({ kind: "wechat", target: "", label: "" });
      queryClient.invalidateQueries({ queryKey: ["notification-channels"] });
    },
    onError: (error) =>
      setMessage(
        "channels",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "添加通知渠道失败",
      ),
  });
  const deleteChannel = useMutation({
    mutationFn: (channelId: string) =>
      apiFetch(`/notifications/channels/${channelId}`, { method: "DELETE" }),
    onSuccess: () => {
      setMessage("channels", "通知渠道已删除。");
      queryClient.invalidateQueries({ queryKey: ["notification-channels"] });
    },
    onError: (error) =>
      setMessage(
        "channels",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "删除通知渠道失败",
      ),
  });

  const apiTokens = useQuery({
    queryKey: ["api-tokens"],
    queryFn: () => apiFetch<ApiToken[]>("/api-tokens"),
    enabled: canManageChannels,
  });
  const createApiToken = useMutation({
    mutationFn: (payload: { name: string; scopes: string[]; expires_in_days: number }) =>
      apiFetch<ApiToken & { token: string }>("/api-tokens", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: (row) => {
      setIssuedToken(row.token);
      setTokenForm({ name: "", scopes: [], expires_in_days: 90 });
      queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
    },
    onError: (error) =>
      setMessage(
        "openApi",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "创建令牌失败",
      ),
  });
  const revokeApiToken = useMutation({
    mutationFn: (tokenId: string) => apiFetch(`/api-tokens/${tokenId}`, { method: "DELETE" }),
    onSuccess: () => {
      setMessage("openApi", "令牌已吊销。");
      queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
    },
    onError: (error) =>
      setMessage(
        "openApi",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "吊销失败",
      ),
  });

  const webhookEndpoints = useQuery({
    queryKey: ["webhooks"],
    queryFn: () => apiFetch<WebhookEndpoint[]>("/webhooks"),
    enabled: canManageChannels,
  });
  const createWebhook = useMutation({
    mutationFn: (payload: { url: string; events: string[]; secret?: string }) =>
      apiFetch<WebhookEndpoint & { secret: string }>("/webhooks", {
        method: "POST",
        body: JSON.stringify({
          url: payload.url,
          events: payload.events,
          ...(payload.secret ? { secret: payload.secret } : {}),
        }),
      }),
    onSuccess: (row) => {
      setIssuedSecret(row.secret);
      setWebhookForm({ url: "", events: ["project.status.changed"], secret: "" });
      queryClient.invalidateQueries({ queryKey: ["webhooks"] });
    },
    onError: (error) =>
      setMessage(
        "openApi",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "创建 Webhook 失败",
      ),
  });
  const deleteWebhook = useMutation({
    mutationFn: (endpointId: string) => apiFetch(`/webhooks/${endpointId}`, { method: "DELETE" }),
    onSuccess: () => {
      setMessage("openApi", "Webhook 已删除。");
      setOpenDeliveries(null);
      queryClient.invalidateQueries({ queryKey: ["webhooks"] });
    },
    onError: (error) =>
      setMessage(
        "openApi",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "删除 Webhook 失败",
      ),
  });
  const webhookDeliveries = useQuery({
    queryKey: ["webhook-deliveries", openDeliveries],
    queryFn: () => apiFetch<WebhookDelivery[]>(`/webhooks/${openDeliveries}/deliveries`),
    enabled: Boolean(openDeliveries),
  });

  useEffect(() => {
    if (!models.data) return;
    setModelForms(
      Object.fromEntries(
        models.data.map((row) => [
          row.purpose,
          {
            base_url: row.base_url ?? "",
            model: row.model ?? "",
            enabled: row.enabled,
          },
        ]),
      ),
    );
  }, [models.data]);

  useEffect(() => {
    if (!personalModels.data) return;
    setPersonalForms(
      Object.fromEntries(
        personalModels.data.map((row) => [
          row.purpose,
          {
            base_url: row.base_url ?? "",
            model: row.model ?? "",
            enabled: row.enabled,
          },
        ]),
      ),
    );
  }, [personalModels.data]);

  useEffect(() => {
    if (modelPolicy.data) {
      setAllowPersonalEnterprise(
        modelPolicy.data.allow_personal_key_for_enterprise_data,
      );
    }
  }, [modelPolicy.data]);

  // 一次性密钥（令牌明文 / Webhook 签名密钥）只在创建后显示一次：
  // 若用户此刻正看着表单底部，明文可能渲染在视野之外，直接滚过去并高亮，
  // 避免「创建成功但没看到、关掉页面就永久丢失」。
  useEffect(() => {
    if (issuedToken) jumpToAnchor("issued-token");
  }, [issuedToken]);

  useEffect(() => {
    if (issuedSecret) jumpToAnchor("issued-secret");
  }, [issuedSecret]);

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    setMessage("password", null);
    if (newPassword !== confirmPassword) {
      setMessage("password", "两次输入的新密码不一致。");
      return;
    }
    if (newPassword.length < 8) {
      setMessage("password", "新密码至少 8 位。");
      return;
    }
    setPending(true);
    try {
      await apiFetch("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      await logout();
      router.replace("/login");
    } catch (error) {
      setMessage(
        "password",
        error instanceof ApiError ? `${error.message}（${error.code}）` : "修改密码失败",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <PageHeader title="账号设置" subtitle="修改登录密码；成功后当前会话立即失效并需重新登录" />

      <Card className="z-20 mb-4 p-4 shadow-card sm:p-5 lg:sticky lg:top-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <UserRound size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">账号设置</h2>
            <Badge tone="slate">{user?.tenant_name ?? "当前租户"}</Badge>
            <Badge tone="cyan">{user?.roles.join("、") || "无角色"}</Badge>
          </div>
          <p className="text-xs text-slate-500">密钥只写不读；模型与决策参数的可见范围取决于角色。</p>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {[
            { key: "account" as const, label: "账号安全", detail: "账号信息与改密", icon: <UserRound size={14} /> },
            ...(canUseModels || canManageModels
              ? [{ key: "models" as const, label: "AI 模型", detail: "租户与个人 Key", icon: <Cpu size={14} /> }]
              : []),
            ...(canViewDecision
              ? [{ key: "decision" as const, label: "决策与提醒", detail: "权重、阈值与提醒", icon: <SlidersHorizontal size={14} /> }]
              : []),
            ...(canManageChannels
              ? [{ key: "notify" as const, label: "通知渠道", detail: "企业微信 / 邮件推送", icon: <Bell size={14} /> }]
              : []),
            ...(canManageChannels
              ? [{ key: "openapi" as const, label: "开放 API", detail: "API 令牌 / Webhook", icon: <KeyRound size={14} /> }]
              : []),
          ].map((tab) => (
            <button
              key={tab.key}
              type="button"
              aria-pressed={activeTab === tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`rounded-xl border px-3 py-2 text-left transition ${
                activeTab === tab.key
                  ? "border-aurora-500/60 bg-aurora-500/[0.06] text-slate-900 shadow-glow"
                  : "border-slate-200/80 bg-white/60 text-slate-600 hover:border-slate-300"
              }`}
            >
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
                <span className={activeTab === tab.key ? "text-aurora-600" : "text-slate-400"}>{tab.icon}</span>
                {tab.label}
              </span>
              <span className="mt-1 block truncate text-[11px] text-slate-500">{tab.detail}</span>
            </button>
          ))}
        </div>
      </Card>

      {activeTab === "account" ? (
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="text-sm font-semibold">当前账号</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">姓名</dt>
              <dd>{user?.display_name ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">邮箱</dt>
              <dd>{user?.email ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">租户</dt>
              <dd>{user?.tenant_name ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">角色</dt>
              <dd>{user?.roles.join("、") || "—"}</dd>
            </div>
          </dl>
        </Card>

        <Card className="p-5">
          <h2 className="text-sm font-semibold">修改密码</h2>
          <form className="mt-3 space-y-2" onSubmit={changePassword}>
            <Input
              type="password"
              placeholder="当前密码 *"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              required
            />
            <Input
              type="password"
              placeholder="新密码（≥8 位）*"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              minLength={8}
              required
            />
            <Input
              type="password"
              placeholder="确认新密码 *"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              minLength={8}
              required
            />
            <ScopedMessage text={messages.password} />
            <Button type="submit" disabled={pending}>
              {pending ? "提交中…" : "修改密码"}
            </Button>
          </form>
        </Card>
      </div>
      ) : null}

      {activeTab === "models" ? (
      <>
      {canManageModelPolicy ? (
        <Card className="mt-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">个人模型企业数据策略</h2>
              <p className="mt-1 text-xs text-slate-500">
                默认禁止项目生成使用个人 Key。开启后，用户可在生成时显式选择个人模型，企业数据会发送到个人供应商。
              </p>
            </div>
            <Badge
              tone={
                modelPolicy.isError ? "rose" : modelPolicy.data?.byok_enabled ? "amber" : "slate"
              }
            >
              {modelPolicy.isError
                ? "策略加载失败"
                : modelPolicy.data?.byok_enabled
                  ? "BYOK 已启用"
                  : "BYOK 全局关闭"}
            </Badge>
          </div>
          {modelPolicy.isError ? (
            <ErrorState error={modelPolicy.error} onRetry={() => modelPolicy.refetch()} />
          ) : null}
          <label className="mt-3 flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={allowPersonalEnterprise}
              onChange={(event) => setAllowPersonalEnterprise(event.target.checked)}
            />
            允许个人 Key 处理企业数据
          </label>
          <Button
            className="mt-3"
            variant="secondary"
            disabled={saveModelPolicy.isPending || modelPolicy.isError}
            onClick={() => saveModelPolicy.mutate(allowPersonalEnterprise)}
          >
            保存策略
          </Button>
          <div className="mt-3">
            <ScopedMessage text={messages.modelPolicy} />
          </div>
        </Card>
      ) : null}

      {canUseModels ? (
        <Card className="mt-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">AI 模型配置 · 个人 Key</h2>
              <p className="mt-1 text-xs text-slate-500">
                Key 加密存储、只写不读，页面只显示 last4；个人 Key 仅本人可用，企业数据默认不走个人 Key。
              </p>
            </div>
            <Badge tone="amber">个人</Badge>
          </div>
          <div className="mt-3">
            <ScopedMessage text={messages.personalModel} />
          </div>
          {personalModels.isLoading ? (
            <div className="mt-3 text-sm text-slate-500">加载中…</div>
          ) : personalModels.isError ? (
            <ErrorState error={personalModels.error} onRetry={() => personalModels.refetch()} />
          ) : (
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {(personalModels.data ?? []).map((row) => {
                const form = personalForms[row.purpose] ?? {
                  base_url: "",
                  model: "",
                  enabled: false,
                };
                return (
                  <div
                    key={row.purpose}
                    className="rounded-xl border border-slate-200/70 bg-white/60 p-4"
                  >
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium">
                        {row.purpose === "main" ? "个人主模型（生成）" : "个人判别模型（评标）"}
                      </p>
                      <Badge tone={row.configured ? "green" : "amber"}>
                        {row.configured ? `Key ••••${row.key_last4 ?? ""}` : "未配置 Key"}
                      </Badge>
                    </div>
                    <div className="mt-3 space-y-2">
                      <Input
                        placeholder="网关地址，例如 https://api.example.com/v1"
                        value={form.base_url}
                        onChange={(event) =>
                          setPersonalForms((current) => ({
                            ...current,
                            [row.purpose]: { ...form, base_url: event.target.value },
                          }))
                        }
                      />
                      <Input
                        placeholder="模型名，例如 deepseek-flash"
                        value={form.model}
                        onChange={(event) =>
                          setPersonalForms((current) => ({
                            ...current,
                            [row.purpose]: { ...form, model: event.target.value },
                          }))
                        }
                      />
                      <Input
                        type="password"
                        placeholder={row.key_last4 ? "输入新 Key 以轮换（留空保持不变）" : "API Key（只写）"}
                        value={personalKeys[row.purpose] ?? ""}
                        onChange={(event) =>
                          setPersonalKeys((current) => ({
                            ...current,
                            [row.purpose]: event.target.value,
                          }))
                        }
                      />
                      <label className="flex items-center gap-2 text-xs text-slate-500">
                        <input
                          type="checkbox"
                          checked={form.enabled}
                          onChange={(event) =>
                            setPersonalForms((current) => ({
                              ...current,
                              [row.purpose]: { ...form, enabled: event.target.checked },
                            }))
                          }
                        />
                        启用个人模型（需已写入 Key）
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="secondary"
                          disabled={savePersonalModel.isPending}
                          onClick={() =>
                            savePersonalModel.mutate({
                              purpose: row.purpose,
                              base_url: form.base_url,
                              model: form.model,
                              api_key: personalKeys[row.purpose] ?? "",
                              enabled: form.enabled,
                            })
                          }
                        >
                          保存个人{row.purpose === "main" ? "主模型" : "判别模型"}
                        </Button>
                        {row.configured ? (
                          <Button
                            variant="ghost"
                            disabled={revokePersonalModel.isPending}
                            onClick={async () => {
                              const ok = await confirm({
                                title: `撤销${row.purpose === "main" ? "主" : "判别"}模型 Key？`,
                                description:
                                  "撤销后个人 Key 立即失效，调用会回落到租户 Key（若已配置）。已产生的用量记录不受影响。",
                                confirmText: "撤销 Key",
                                tone: "danger",
                              });
                              if (!ok) return;
                              revokePersonalModel.mutate(row.purpose);
                            }}
                          >
                            撤销 Key
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      ) : null}

      {canManageModels ? (
        <Card className="mt-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">AI 模型配置</h2>
              <p className="mt-1 text-xs text-slate-500">
                企业网关地址与模型名；API Key 可由环境变量注入，个人 Key 使用上方独立配置。
              </p>
            </div>
            <Badge tone="cyan">租户级</Badge>
          </div>
          <div className="mt-3">
            <ScopedMessage text={messages.tenantModel} />
          </div>
          {models.isLoading ? (
            <div className="mt-3 text-sm text-slate-500">加载中…</div>
          ) : models.isError ? (
            <ErrorState error={models.error} onRetry={() => models.refetch()} />
          ) : (
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {(models.data ?? []).map((row) => {
                const form = modelForms[row.purpose] ?? {
                  base_url: "",
                  model: "",
                  enabled: false,
                };
                return (
                  <div key={row.purpose} className="rounded-xl border border-slate-200/70 bg-white/60 p-4">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium">
                        {row.purpose === "main" ? "主模型（生成）" : "判别模型（评标）"}
                      </p>
                      <Badge tone={row.configured ? "green" : "amber"}>
                        {row.configured ? "已配置密钥" : "缺少密钥/配置"}
                      </Badge>
                    </div>
                    <div className="mt-3 space-y-2">
                      <Input
                        placeholder="网关地址，例如 http://host:3000/v1"
                        value={form.base_url}
                        onChange={(event) =>
                          setModelForms((current) => ({
                            ...current,
                            [row.purpose]: { ...form, base_url: event.target.value },
                          }))
                        }
                      />
                      <Input
                        placeholder="模型名，例如 deepseek-flash"
                        value={form.model}
                        onChange={(event) =>
                          setModelForms((current) => ({
                            ...current,
                            [row.purpose]: { ...form, model: event.target.value },
                          }))
                        }
                      />
                      <label className="flex items-center gap-2 text-xs text-slate-500">
                        <input
                          type="checkbox"
                          checked={form.enabled}
                          onChange={(event) =>
                            setModelForms((current) => ({
                              ...current,
                              [row.purpose]: { ...form, enabled: event.target.checked },
                            }))
                          }
                        />
                        启用租户覆盖（关闭则使用环境变量默认值）
                      </label>
                      <p className="text-[11px] text-slate-400">
                        当前生效：{row.effective_model ?? "未配置"} @ {row.effective_base_url ?? "—"}
                      </p>
                      <Button
                        variant="secondary"
                        disabled={saveModel.isPending}
                        onClick={() =>
                          saveModel.mutate({
                            purpose: row.purpose,
                            base_url: form.base_url,
                            model: form.model,
                            enabled: form.enabled,
                          })
                        }
                      >
                        保存{row.purpose === "main" ? "主模型" : "判别模型"}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      ) : null}
      </>
      ) : null}

      {activeTab === "decision" && canViewDecision ? <DecisionSettingsCard /> : null}

      {activeTab === "notify" && canManageChannels ? (
        <Card className="mt-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">通知渠道</h2>
              <p className="mt-1 text-xs text-slate-500">
                租户级推送地址：系统提醒（资质到期、监控风险变化、待办截止）会按渠道广播。企业微信用群机器人
                Webhook（仅 https），邮件为逗号分隔的收件人列表，SMTP 由部署环境变量提供。
              </p>
            </div>
            {channels.isError ? (
              <Badge tone="rose">渠道加载失败</Badge>
            ) : (
              <Badge tone="cyan">{(channels.data ?? []).length} 个渠道</Badge>
            )}
          </div>
          <div className="mt-3">
            <ScopedMessage text={messages.channels} />
          </div>
          {channels.isLoading ? (
            <div className="mt-3 text-sm text-slate-500">加载中…</div>
          ) : channels.isError ? (
            <ErrorState error={channels.error} onRetry={() => channels.refetch()} />
          ) : (channels.data ?? []).length === 0 ? (
            <p className="mt-3 text-sm text-slate-500">尚未配置通知渠道。</p>
          ) : (
            <ul className="mt-3 divide-y divide-slate-100">
              {(channels.data ?? []).map((row) => (
                <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge tone={row.kind === "wechat" ? "green" : "amber"}>
                        {row.kind === "wechat" ? "企业微信" : "邮件"}
                      </Badge>
                      <span className="text-sm font-medium">{row.label || "未命名渠道"}</span>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-slate-500">{row.target}</p>
                  </div>
                  <Button
                    variant="ghost"
                    disabled={deleteChannel.isPending}
                    onClick={async () => {
                      const ok = await confirm({
                        title: `删除通知渠道「${row.label || row.target}」？`,
                        description:
                          "删除后该渠道不再接收截止提醒、订阅新增与审批待办通知；站内消息不受影响。",
                        confirmText: "删除渠道",
                        tone: "danger",
                      });
                      if (!ok) return;
                      deleteChannel.mutate(row.id);
                    }}
                  >
                    删除
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form
            className="mt-4 grid gap-2 sm:grid-cols-[10rem_1fr_10rem_auto] sm:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              setMessage("channels", null);
              createChannel.mutate({
                kind: channelForm.kind,
                target: channelForm.target,
                label: channelForm.label,
              });
            }}
          >
            <label className="text-xs text-slate-500">
              类型
              <Select
                className="mt-1"
                value={channelForm.kind}
                onChange={(event) =>
                  setChannelForm((current) => ({ ...current, kind: event.target.value }))
                }
              >
                <option value="wechat">企业微信</option>
                <option value="email">邮件</option>
              </Select>
            </label>
            <label className="text-xs text-slate-500">
              {channelForm.kind === "wechat" ? "Webhook 地址（https）" : "收件人（逗号分隔）"}
              <Input
                className="mt-1"
                placeholder={
                  channelForm.kind === "wechat"
                    ? "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
                    : "a@example.com,b@example.com"
                }
                value={channelForm.target}
                onChange={(event) =>
                  setChannelForm((current) => ({ ...current, target: event.target.value }))
                }
                required
              />
            </label>
            <label className="text-xs text-slate-500">
              备注（可选）
              <Input
                className="mt-1"
                placeholder="例如：投标部群"
                value={channelForm.label}
                onChange={(event) =>
                  setChannelForm((current) => ({ ...current, label: event.target.value }))
                }
              />
            </label>
            <Button type="submit" disabled={createChannel.isPending}>
              添加渠道
            </Button>
          </form>
        </Card>
      ) : null}

      {activeTab === "openapi" && canManageChannels ? (
        <div className="mt-4 grid gap-4">
          <Card className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">API 令牌</h2>
                <p className="mt-1 text-xs text-slate-500">
                  供外部系统调用本租户 REST API。明文只在创建时显示一次，库内只存哈希；
                  令牌权限 = 勾选范围 ∩ 你当前的权限，你失去某能力时令牌立即失效。吊销即时生效。
                </p>
              </div>
              {apiTokens.isError ? (
                <Badge tone="rose">令牌加载失败</Badge>
              ) : (
                <Badge tone="cyan">{(apiTokens.data ?? []).length} 枚</Badge>
              )}
            </div>
            <div className="mt-3">
              <ScopedMessage text={messages.openApi} />
            </div>
            {issuedToken ? (
              <div
                id="issued-token"
                className="mt-3 rounded-xl border border-amber-300/70 bg-amber-50 p-3 text-sm"
              >
                <p className="font-medium text-amber-800">新令牌（仅此一次显示，请立即保存）</p>
                <code className="mt-1 block break-all font-mono text-xs text-amber-900">
                  {issuedToken}
                </code>
                <p className="mt-2 text-xs text-amber-700">
                  关闭或刷新后无法再次查看明文；若已丢失，请吊销该令牌后重新创建。
                </p>
                <Button
                  className="mt-2"
                  variant="ghost"
                  onClick={() => {
                    setIssuedToken(null);
                    setMessage("openApi", null);
                  }}
                >
                  我已保存，收起
                </Button>
              </div>
            ) : null}
            {apiTokens.isLoading ? (
              <div className="mt-3 text-sm text-slate-500">加载中…</div>
            ) : apiTokens.isError ? (
              <ErrorState error={apiTokens.error} onRetry={() => apiTokens.refetch()} />
            ) : (apiTokens.data ?? []).length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">尚未创建 API 令牌。</p>
            ) : (
              <ul className="mt-3 divide-y divide-slate-100">
                {(apiTokens.data ?? []).map((row) => (
                  <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{row.name}</span>
                        <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600">
                          {row.token_prefix}…
                        </code>
                        {row.expires_at ? (
                          <Badge tone="slate">
                            到期 {new Date(row.expires_at).toLocaleDateString("zh-CN")}
                          </Badge>
                        ) : (
                          <Badge tone="amber">长期</Badge>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs text-slate-500">
                        范围：{row.scopes.join("、") || "—"} · 最近使用{" "}
                        {row.last_used_at
                          ? new Date(row.last_used_at).toLocaleString("zh-CN")
                          : "从未"}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      disabled={revokeApiToken.isPending}
                      onClick={async () => {
                        const ok = await confirm({
                          title: `吊销 API 令牌「${row.name}」？`,
                          description:
                            "吊销后该令牌立即失效，使用它的集成方会开始收到 401，需要重新签发令牌并替换配置。此操作不可撤销。",
                          confirmText: "吊销令牌",
                          tone: "danger",
                        });
                        if (!ok) return;
                        revokeApiToken.mutate(row.id);
                      }}
                    >
                      吊销
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <form
              className="mt-4 grid gap-2 sm:grid-cols-[1fr_9rem_auto] sm:items-end"
              onSubmit={(event) => {
                event.preventDefault();
                setMessage("openApi", null);
                createApiToken.mutate({
                  name: tokenForm.name,
                  scopes: tokenForm.scopes,
                  expires_in_days: tokenForm.expires_in_days,
                });
              }}
            >
              <label className="text-xs text-slate-500">
                名称
                <Input
                  className="mt-1"
                  placeholder="例如：CRM 同步"
                  value={tokenForm.name}
                  onChange={(event) =>
                    setTokenForm((current) => ({ ...current, name: event.target.value }))
                  }
                  required
                />
              </label>
              <label className="text-xs text-slate-500">
                有效期
                <Select
                  className="mt-1"
                  value={String(tokenForm.expires_in_days)}
                  onChange={(event) =>
                    setTokenForm((current) => ({
                      ...current,
                      expires_in_days: Number(event.target.value),
                    }))
                  }
                >
                  <option value="30">30 天</option>
                  <option value="90">90 天</option>
                  <option value="365">365 天</option>
                  <option value="3650">10 年</option>
                </Select>
              </label>
              <Button type="submit" disabled={createApiToken.isPending || !tokenForm.scopes.length}>
                创建令牌
              </Button>
            </form>
            <div className="mt-3">
              <p className="text-xs text-slate-500">
                勾选令牌可调用的接口范围（只列出你当前拥有的权限）：
              </p>
              <div className="mt-2 max-h-44 overflow-y-auto rounded-xl border border-slate-200/70 bg-white/60 p-3">
                <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                  {(user?.permissions ?? [])
                    .filter((scope) => scope !== "*")
                    .map((scope) => (
                      <label key={scope} className="flex items-center gap-1.5 text-xs text-slate-600">
                        <input
                          type="checkbox"
                          checked={tokenForm.scopes.includes(scope)}
                          onChange={(event) =>
                            setTokenForm((current) => ({
                              ...current,
                              scopes: event.target.checked
                                ? [...current.scopes, scope]
                                : current.scopes.filter((item) => item !== scope),
                            }))
                          }
                        />
                        <span className="truncate font-mono">{scope}</span>
                      </label>
                    ))}
                </div>
              </div>
            </div>
          </Card>

          <Card className="p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">Webhook 推送</h2>
                <p className="mt-1 text-xs text-slate-500">
                  事件发生时向你的服务端 POST 签名报文（HMAC-SHA256，含时间戳防重放）。
                  仅支持 https，内网地址会被拒绝；签名密钥只在创建时显示一次。
                </p>
              </div>
              {webhookEndpoints.isError ? (
                <Badge tone="rose">Webhook 加载失败</Badge>
              ) : (
                <Badge tone="cyan">{(webhookEndpoints.data ?? []).length} 个</Badge>
              )}
            </div>
            {issuedSecret ? (
              <div
                id="issued-secret"
                className="mt-3 rounded-xl border border-amber-300/70 bg-amber-50 p-3 text-sm"
              >
                <p className="font-medium text-amber-800">签名密钥（仅此一次显示，请立即保存）</p>
                <code className="mt-1 block break-all font-mono text-xs text-amber-900">
                  {issuedSecret}
                </code>
                <p className="mt-2 text-xs text-amber-700">
                  接收方用它校验 HMAC-SHA256 签名。关闭或刷新后无法再次查看明文；若已丢失，请删除端点后重建。
                </p>
                <Button
                  className="mt-2"
                  variant="ghost"
                  onClick={() => {
                    setIssuedSecret(null);
                    setMessage("openApi", null);
                  }}
                >
                  我已保存，收起
                </Button>
              </div>
            ) : null}
            {webhookEndpoints.isLoading ? (
              <div className="mt-3 text-sm text-slate-500">加载中…</div>
            ) : webhookEndpoints.isError ? (
              <ErrorState
                error={webhookEndpoints.error}
                onRetry={() => webhookEndpoints.refetch()}
              />
            ) : (webhookEndpoints.data ?? []).length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">尚未配置 Webhook。</p>
            ) : (
              <ul className="mt-3 divide-y divide-slate-100">
                {(webhookEndpoints.data ?? []).map((row) => (
                  <li key={row.id} className="py-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{row.url}</p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          事件：
                          {row.events.map((event) => (
                            <Badge key={event} tone="slate" >
                              <span className="mx-1">{event}</span>
                            </Badge>
                          ))}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          variant="secondary"
                          onClick={() =>
                            setOpenDeliveries((current) => (current === row.id ? null : row.id))
                          }
                        >
                          投递记录
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={deleteWebhook.isPending}
                          onClick={async () => {
                            const ok = await confirm({
                              title: "删除这个 Webhook 端点？",
                              description: `删除后 ${row.url} 不再接收事件推送，已投递记录一并不可查。接收方若有重试或对账逻辑需同步调整。`,
                              confirmText: "删除端点",
                              tone: "danger",
                            });
                            if (!ok) return;
                            deleteWebhook.mutate(row.id);
                          }}
                        >
                          删除
                        </Button>
                      </div>
                    </div>
                    {openDeliveries === row.id ? (
                      <div className="mt-2 rounded-xl border border-slate-200/70 bg-white/60 p-3">
                        {webhookDeliveries.isLoading ? (
                          <p className="text-xs text-slate-500">加载中…</p>
                        ) : webhookDeliveries.isError ? (
                          <p className="text-xs text-rose-600">
                            {webhookDeliveries.error instanceof Error
                              ? webhookDeliveries.error.message
                              : "投递记录加载失败"}
                          </p>
                        ) : (webhookDeliveries.data ?? []).length === 0 ? (
                          <p className="text-xs text-slate-500">暂无投递记录。</p>
                        ) : (
                          <ul className="space-y-1.5">
                            {(webhookDeliveries.data ?? []).map((delivery) => (
                              <li
                                key={delivery.id}
                                className="flex flex-wrap items-center gap-2 text-xs text-slate-600"
                              >
                                <Badge tone={delivery.status === "success" ? "green" : "rose"}>
                                  {delivery.status === "success" ? "成功" : "失败"}
                                </Badge>
                                <span className="font-mono">{delivery.event}</span>
                                <span>
                                  {new Date(delivery.created_at).toLocaleString("zh-CN")} ·{" "}
                                  {delivery.attempts} 次尝试
                                  {delivery.response_code ? ` · HTTP ${delivery.response_code}` : ""}
                                </span>
                                {delivery.error ? (
                                  <span className="text-rose-600">{delivery.error}</span>
                                ) : null}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            <form
              className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto]"
              onSubmit={(event) => {
                event.preventDefault();
                setMessage("openApi", null);
                createWebhook.mutate({
                  url: webhookForm.url,
                  events: webhookForm.events,
                  secret: webhookForm.secret,
                });
              }}
            >
              <label className="text-xs text-slate-500">
                接收地址（https）
                <Input
                  className="mt-1"
                  placeholder="https://your-server.example.com/bidcopilot-webhook"
                  value={webhookForm.url}
                  onChange={(event) =>
                    setWebhookForm((current) => ({ ...current, url: event.target.value }))
                  }
                  required
                />
              </label>
              <Button type="submit" disabled={createWebhook.isPending || !webhookForm.events.length}>
                添加 Webhook
              </Button>
            </form>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <div>
                <p className="text-xs text-slate-500">订阅事件：</p>
                <div className="mt-1.5 space-y-1.5">
                  {[
                    { value: "project.status.changed", label: "项目状态变更" },
                    { value: "submission_receipt.created", label: "交标回执创建" },
                    { value: "compliance.hard_finding", label: "合规 HARD 问题" },
                    { value: "*", label: "全部事件" },
                  ].map((option) => (
                    <label
                      key={option.value}
                      className="flex items-center gap-1.5 text-xs text-slate-600"
                    >
                      <input
                        type="checkbox"
                        checked={webhookForm.events.includes(option.value)}
                        onChange={(event) =>
                          setWebhookForm((current) => ({
                            ...current,
                            events: event.target.checked
                              ? [...current.events, option.value]
                              : current.events.filter((item) => item !== option.value),
                          }))
                        }
                      />
                      {option.label}
                      <code className="font-mono text-[11px] text-slate-400">{option.value}</code>
                    </label>
                  ))}
                </div>
              </div>
              <label className="text-xs text-slate-500">
                签名密钥（可选，留空由系统生成；至少 16 位）
                <Input
                  className="mt-1"
                  placeholder="留空自动生成"
                  value={webhookForm.secret}
                  onChange={(event) =>
                    setWebhookForm((current) => ({ ...current, secret: event.target.value }))
                  }
                />
              </label>
            </div>
          </Card>
        </div>
      ) : null}
    </>
  );
}
