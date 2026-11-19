"use client";

import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Plus, Radar, Scale, Upload } from "lucide-react";

import { Badge, Button, Card, EmptyState, Input, Loading, PageHeader, Select } from "@/components/ui";
import { CompanyScalePanel } from "@/components/company-scale-panel";
import { EnterpriseIntelPanel } from "@/components/enterprise-intel-panel";
import { IntelWatchlistPanel } from "@/components/intel-watchlist-panel";
import { apiFetch } from "@/lib/api";
import type {
  AssetItem,
  Company,
  CompanyBid,
  CompanyBidSyncResponse,
  CompanyProfileResponse,
  ExpiringItem,
  ImportResult,
  OrgUnitNode,
} from "@/lib/types";

const KINDS = [
  { key: "qualifications", label: "资质" },
  { key: "personnel", label: "人员" },
  { key: "performances", label: "业绩" },
  { key: "credits", label: "信用" },
] as const;

type Kind = (typeof KINDS)[number]["key"];
type AssetsTab = "ledger" | "scale" | "intel";

function flattenOrg(nodes: OrgUnitNode[]): OrgUnitNode[] {
  return nodes.flatMap((node) => [node, ...flattenOrg(node.children ?? [])]);
}

const EMPTY_FORM: Record<string, string> = {
  name: "",
  level: "",
  cert_no: "",
  issuer: "",
  valid_from: "",
  valid_to: "",
  cert_type: "",
  major: "",
  project_name: "",
  amount: "",
  complete_date: "",
  buyer: "",
  category: "",
  check_item: "",
  status: "",
};

function detailText(item: AssetItem): string {
  if (item.kind === "qualifications") return [item.level, item.cert_no, item.issuer].filter(Boolean).join(" · ");
  if (item.kind === "personnel") return [item.cert_type, item.level, item.cert_no].filter(Boolean).join(" · ");
  if (item.kind === "performances") return [item.buyer, item.category].filter(Boolean).join(" · ");
  return item.status ?? "";
}

export default function AssetsPage() {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<Kind>("qualifications");
  const [activeTab, setActiveTab] = useState<AssetsTab>("ledger");
  const [showAddForm, setShowAddForm] = useState(false);
  const [companyId, setCompanyId] = useState("");
  const [orgUnitId, setOrgUnitId] = useState("");
  const [q, setQ] = useState("");
  const [form, setForm] = useState<Record<string, string>>(EMPTY_FORM);
  const [companyForm, setCompanyForm] = useState({ name: "", credit_code: "" });
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [profile, setProfile] = useState<CompanyProfileResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [syncingRole, setSyncingRole] = useState<string | null>(null);
  const [intelNotice, setIntelNotice] = useState<string | null>(null);

  const companies = useQuery({
    queryKey: ["companies"],
    queryFn: () => apiFetch<Company[]>("/companies"),
  });
  const orgTree = useQuery({
    queryKey: ["org-units"],
    queryFn: () => apiFetch<OrgUnitNode[]>("/org-units/tree"),
  });
  const orgUnits = flattenOrg(orgTree.data ?? []);
  const orgNames = new Map(orgUnits.map((unit) => [unit.id, unit.name]));
  const expiring = useQuery({
    queryKey: ["assets-expiring"],
    queryFn: () => apiFetch<{ items: ExpiringItem[]; total: number }>("/assets/expiring?days=90"),
  });
  const assets = useQuery({
    queryKey: ["assets", kind, companyId, q],
    queryFn: () =>
      apiFetch<{ items: AssetItem[]; total: number }>(
        `/assets/${kind}?company_id=${companyId}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
      ),
    enabled: Boolean(companyId),
  });
  const bids = useQuery({
    queryKey: ["company-bids", companyId],
    queryFn: () =>
      apiFetch<{ items: CompanyBid[]; total: number }>(
        `/companies/${companyId}/bids?page_size=10`,
      ),
    enabled: Boolean(companyId),
  });

  useEffect(() => {
    if (!companyId && companies.data && companies.data.length > 0) {
      setCompanyId(companies.data[0].id);
    }
  }, [companies.data, companyId]);

  useEffect(() => {
    setProfile(null);
    setIntelNotice(null);
  }, [companyId]);

  async function loadProfile() {
    if (!companyId) return;
    setProfileLoading(true);
    setMessage(null);
    try {
      setProfile(await apiFetch<CompanyProfileResponse>(`/companies/${companyId}/profile`));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "企业概况查询失败");
    } finally {
      setProfileLoading(false);
    }
  }

  async function syncBids(role: "winner" | "tenderer") {
    if (!companyId) return;
    setSyncingRole(role);
    setMessage(null);
    setIntelNotice(null);
    try {
      const result = await apiFetch<CompanyBidSyncResponse>(
        `/companies/${companyId}/bids/sync`,
        { method: "POST", body: JSON.stringify({ roles: [role] }) },
      );
      const outcome = result.results[0];
      const state = outcome?.status ?? "unknown";
      if (outcome?.error) {
        setMessage(`同步失败：${outcome.error.code} ${outcome.error.message}`);
      } else {
        setIntelNotice(
          `同步完成（${state}）：拉取 ${outcome?.fetched ?? 0} 条，写入 ${result.upserted} 条。`,
        );
      }
      await queryClient.invalidateQueries({ queryKey: ["company-bids", companyId] });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "企业历史同步失败");
    } finally {
      setSyncingRole(null);
    }
  }

  function updateForm(field: string, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function createCompany(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      const company = await apiFetch<Company>("/companies", {
        method: "POST",
        body: JSON.stringify({
          name: companyForm.name,
          credit_code: companyForm.credit_code,
        }),
      });
      setCompanyId(company.id);
      setCompanyForm({ name: "", credit_code: "" });
      await queryClient.invalidateQueries({ queryKey: ["companies"] });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建企业失败");
    }
  }

  async function createAsset(event: FormEvent) {
    event.preventDefault();
    if (!companyId) return;
    setMessage(null);
    try {
      const payload: Record<string, string> = { company_id: companyId };
      if (orgUnitId) payload.org_unit_id = orgUnitId;
      const fieldsByKind: Record<Kind, string[]> = {
        qualifications: ["name", "level", "cert_no", "issuer", "valid_from", "valid_to"],
        personnel: ["name", "cert_type", "level", "major", "cert_no", "valid_to"],
        performances: ["project_name", "amount", "complete_date", "buyer", "category"],
        credits: ["check_item", "status"],
      };
      for (const field of fieldsByKind[kind]) {
        if (form[field]) payload[field] = form[field];
      }
      await apiFetch(`/assets/${kind}`, { method: "POST", body: JSON.stringify(payload) });
      setForm(EMPTY_FORM);
      setShowAddForm(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["assets-expiring"] }),
      ]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建资产失败");
    }
  }

  const currentCompany = companies.data?.find((row) => row.id === companyId);

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setMessage(null);
    const data = new FormData();
    data.append("kind", kind);
    data.append("file", file);
    try {
      const result = await apiFetch<ImportResult>("/assets/import", { method: "POST", body: data });
      setImportResult(result);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["assets"] }),
        queryClient.invalidateQueries({ queryKey: ["assets-expiring"] }),
      ]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入失败");
    } finally {
      event.target.value = "";
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="ASSETS / EVIDENCE"
        title="企业资产"
        subtitle="资质、人员、业绩、信用四类台账；工商自检与投标历史单独可追溯。"
      />

      {message ? (
        <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {message}
        </div>
      ) : null}
      {importResult ? (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm">
          导入完成：成功 {importResult.imported} 行，失败 {importResult.failed} 行
          {importResult.errors.slice(0, 3).map((error) => (
            <div key={`${error.row}-${error.message}`} className="mt-1 text-rose-600">
              第 {error.row} 行：{error.message}
            </div>
          ))}
        </div>
      ) : null}

      <Card className="z-20 mb-4 p-4 shadow-card sm:p-5 lg:sticky lg:top-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Building2 size={16} className="text-electric-600" />
            <h2 className="text-sm font-semibold">企业资产</h2>
            <Badge tone="slate">{companies.data?.length ?? 0}</Badge>
            {currentCompany ? <Badge tone="cyan">{currentCompany.name}</Badge> : null}
            {expiring.data?.total ? <Badge tone="amber">90 天内到期 {expiring.data.total} 项</Badge> : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select className="min-w-56" value={companyId} onChange={(event) => setCompanyId(event.target.value)}>
              <option value="">选择企业</option>
              {companies.data?.map((company) => (
                <option key={company.id} value={company.id}>
                  {company.name}
                </option>
              ))}
            </Select>
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-sm text-slate-700 shadow-sm">
              <Upload size={14} /> 导入当前台账
              <input
                className="hidden"
                type="file"
                accept=".csv,.xlsx"
                onChange={importFile}
                title="导入当前类型资产"
              />
            </label>
            <Button
              className="h-9"
              disabled={!companyId}
              onClick={() => {
                setActiveTab("ledger");
                setShowAddForm(true);
                window.requestAnimationFrame(() => {
                  document.getElementById("assets-content")?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                  });
                });
              }}
            >
              <Plus size={14} /> 新增资产
            </Button>
          </div>
        </div>
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium text-slate-500">新建企业</summary>
          <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={createCompany}>
            <Input
              className="min-w-48 flex-1"
              placeholder="企业名称"
              value={companyForm.name}
              onChange={(event) => setCompanyForm((current) => ({ ...current, name: event.target.value }))}
              required
            />
            <Input
              className="min-w-48 flex-1"
              placeholder="统一社会信用代码（可选）"
              value={companyForm.credit_code}
              onChange={(event) =>
                setCompanyForm((current) => ({ ...current, credit_code: event.target.value }))
              }
            />
            <Button type="submit" variant="secondary">新建企业</Button>
          </form>
        </details>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {[
            { key: "ledger" as const, label: "台账", detail: "资质 / 人员 / 业绩 / 信用", icon: <Building2 size={14} /> },
            { key: "scale" as const, label: "划型", detail: "中小企业声明函依据", icon: <Scale size={14} /> },
            { key: "intel" as const, label: "工商自检", detail: "执照一致性 / 集团关联", icon: <Radar size={14} /> },
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

      <div id="assets-content" className="scroll-mt-24 lg:scroll-mt-[250px]">
        {activeTab === "ledger" ? (
          <Card className="mt-4 p-5">
            {!companyId ? (
              <EmptyState text="先选择或新建企业，再维护资质、人员、业绩与信用台账。" />
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
                    {KINDS.map((item) => (
                      <button
                        key={item.key}
                        className={`rounded-md px-3 py-1.5 text-sm ${
                          kind === item.key ? "bg-white font-medium shadow-sm" : "text-slate-500"
                        }`}
                        onClick={() => {
                          setKind(item.key);
                          setShowAddForm(false);
                        }}
                        type="button"
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      className="w-48"
                      placeholder="搜索当前台账"
                      value={q}
                      onChange={(event) => setQ(event.target.value)}
                    />
                    <Button
                      variant="secondary"
                      onClick={() => setShowAddForm((value) => !value)}
                    >
                      <Plus size={14} /> {showAddForm ? "收起新增" : `新增${kindsLabel(kind)}`}
                    </Button>
                  </div>
                </div>
                {showAddForm ? (
                  <form className="mt-4 grid gap-2 sm:grid-cols-2" onSubmit={createAsset}>
                    <label className="text-xs text-slate-500 sm:col-span-2">
                      归属
                      <Select
                        className="mt-1"
                        value={orgUnitId}
                        onChange={(event) => setOrgUnitId(event.target.value)}
                      >
                        <option value="">企业共享（不限定部门）</option>
                        {orgUnits.map((unit) => (
                          <option key={unit.id} value={unit.id}>
                            {unit.name}
                          </option>
                        ))}
                      </Select>
                    </label>
                    {kind === "qualifications" ? (
                      <>
                        <Input placeholder="资质名称 *" value={form.name} onChange={(e) => updateForm("name", e.target.value)} required />
                        <Input placeholder="等级" value={form.level} onChange={(e) => updateForm("level", e.target.value)} />
                        <Input placeholder="证书编号" value={form.cert_no} onChange={(e) => updateForm("cert_no", e.target.value)} />
                        <Input placeholder="发证机关" value={form.issuer} onChange={(e) => updateForm("issuer", e.target.value)} />
                        <Input type="date" value={form.valid_from} onChange={(e) => updateForm("valid_from", e.target.value)} />
                        <Input type="date" value={form.valid_to} onChange={(e) => updateForm("valid_to", e.target.value)} />
                      </>
                    ) : null}
                    {kind === "personnel" ? (
                      <>
                        <Input placeholder="姓名 *" value={form.name} onChange={(e) => updateForm("name", e.target.value)} required />
                        <Input placeholder="证书类型 *" value={form.cert_type} onChange={(e) => updateForm("cert_type", e.target.value)} required />
                        <Input placeholder="等级" value={form.level} onChange={(e) => updateForm("level", e.target.value)} />
                        <Input placeholder="专业" value={form.major} onChange={(e) => updateForm("major", e.target.value)} />
                        <Input placeholder="证书编号" value={form.cert_no} onChange={(e) => updateForm("cert_no", e.target.value)} />
                        <Input type="date" value={form.valid_to} onChange={(e) => updateForm("valid_to", e.target.value)} />
                      </>
                    ) : null}
                    {kind === "performances" ? (
                      <>
                        <Input placeholder="项目名称 *" value={form.project_name} onChange={(e) => updateForm("project_name", e.target.value)} required />
                        <Input placeholder="合同金额（元）" value={form.amount} onChange={(e) => updateForm("amount", e.target.value)} />
                        <Input type="date" value={form.complete_date} onChange={(e) => updateForm("complete_date", e.target.value)} />
                        <Input placeholder="采购人" value={form.buyer} onChange={(e) => updateForm("buyer", e.target.value)} />
                        <Input placeholder="类别" value={form.category} onChange={(e) => updateForm("category", e.target.value)} />
                      </>
                    ) : null}
                    {kind === "credits" ? (
                      <>
                        <Input placeholder="检查项 *" value={form.check_item} onChange={(e) => updateForm("check_item", e.target.value)} required />
                        <Input placeholder="状态 *" value={form.status} onChange={(e) => updateForm("status", e.target.value)} required />
                      </>
                    ) : null}
                    <div className="sm:col-span-2">
                      <Button type="submit">新增{kindsLabel(kind)}</Button>
                    </div>
                  </form>
                ) : (
                  <p className="mt-3 text-xs text-slate-500">
                    点击「新增{kindsLabel(kind)}」展开录入表单；也可以在顶部直接导入当前类型资产。
                  </p>
                )}
              </>
            )}
          </Card>
        ) : null}

      <div className="mt-4">
        {activeTab === "scale" ? (
          <CompanyScalePanel company={currentCompany} />
        ) : null}
      </div>

      {activeTab === "intel" ? (
      <Card className="mt-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">企业工商与投标记录</h2>
            <p className="mt-1 text-xs text-slate-500">
              KeepRisk E_007_1341 企业概况 + E_007_1271 结构化检索；查询与同步均为按次计费。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={loadProfile}
              disabled={!companyId || profileLoading}
            >
              {profileLoading ? "查询中…" : "查询企业概况"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => syncBids("winner")}
              disabled={!companyId || syncingRole !== null}
            >
              {syncingRole === "winner" ? "同步中…" : "同步中标历史"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => syncBids("tenderer")}
              disabled={!companyId || syncingRole !== null}
            >
              {syncingRole === "tenderer" ? "同步中…" : "同步招标历史"}
            </Button>
          </div>
        </div>
        {intelNotice ? (
          <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
            {intelNotice}
          </div>
        ) : null}
        {!companyId ? (
          <EmptyState text="先选择企业。" />
        ) : (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-slate-200 p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">标讯概况</h3>
                {profile ? (
                  <Badge tone={profile.status === "ok" ? "green" : "amber"}>
                    {profile.status}
                  </Badge>
                ) : null}
              </div>
              {!profile ? (
                <p className="mt-6 text-sm text-slate-500">点击“查询企业概况”读取实时数据。</p>
              ) : profile.status !== "ok" ? (
                <div className="mt-3 text-sm text-amber-700">
                  数据源返回 {profile.status}：{profile.error?.message ?? "无可用概况"}
                </div>
              ) : profile.entries.length === 0 ? (
                <EmptyState
                text="外部数据源没有该企业的工商概况。"
                hint="可能是企业名称与工商登记名不一致，或数据源暂未收录。可点上方「同步」重试；长期缺失不影响本地台账维护。"
              />
              ) : (
                profile.entries.map((entry, index) => (
                  <div key={`${entry.subject_name}-${entry.subject_status ?? ""}-${index}`} className="mt-3 space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{entry.subject_name}</span>
                      {entry.subject_status ? <Badge>{entry.subject_status}</Badge> : null}
                      {entry.region ? (
                        <span className="text-xs text-slate-500">{entry.region}</span>
                      ) : null}
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                      {[
                        ["招标", entry.tender_count],
                        ["中标", entry.winner_count],
                        ["投标", entry.bidder_count],
                        ["代理", entry.agent_count],
                      ].map(([label, value]) => (
                        <div key={label} className="rounded-lg bg-slate-50 px-3 py-2">
                          <div className="text-xs text-slate-500">{label}</div>
                          <div className="text-lg font-semibold">{value}</div>
                        </div>
                      ))}
                    </div>
                    {entry.latest_title ? (
                      <div className="text-sm text-slate-600">
                        最新：{entry.latest_title}
                        <span className="ml-2 text-xs text-slate-400">
                          {entry.latest_publish_at ?? ""}
                        </span>
                      </div>
                    ) : null}
                  </div>
                ))
              )}
            </div>
            <div className="rounded-xl border border-slate-200 p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">投标/中标历史</h3>
                {bids.data ? <Badge>{bids.data.total} 条</Badge> : null}
              </div>
              {bids.isLoading ? (
                <Loading />
              ) : bids.error ? (
                <div className="mt-3 text-sm text-rose-600">{bids.error.message}</div>
              ) : !bids.data || bids.data.items.length === 0 ? (
                <p className="mt-6 text-sm text-slate-500">暂无本地记录，可点上方按钮同步。</p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="py-2 pr-3">日期</th>
                        <th className="py-2 pr-3">标题</th>
                        <th className="py-2 pr-3">角色</th>
                        <th className="py-2">金额</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {bids.data.items.map((bid) => (
                        <tr key={bid.id}>
                          <td className="py-2 pr-3 text-slate-500">
                            {bid.publish_at ? bid.publish_at.slice(0, 10) : "—"}
                          </td>
                          <td className="max-w-[18rem] py-2 pr-3">
                            {bid.source_url ? (
                              <a
                                className="text-blue-600 hover:underline"
                                href={bid.source_url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {bid.title}
                              </a>
                            ) : (
                              bid.title
                            )}
                          </td>
                          <td className="py-2 pr-3">
                            <Badge tone={bid.role === "winner" ? "green" : "slate"}>
                              {roleLabel(bid.role)}
                            </Badge>
                          </td>
                          <td className="py-2 text-slate-600">
                            {formatAmount(bid.win_bid_amount ?? bid.budget_amount)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </Card>
      ) : null}

      {activeTab === "intel" ? (
        <>
          <EnterpriseIntelPanel
            companyName={currentCompany?.name ?? null}
            title="企业工商自检"
            description="核验本企业执照信息、信用与集团关联；按次计费，状态原样展示，缓存或空结果不计费。"
          />
          <IntelWatchlistPanel companyName={currentCompany?.name ?? null} />
        </>
      ) : null}

      {activeTab === "ledger" ? (
      <Card className="mt-4">
        {!companyId ? (
          <EmptyState text="先选择或新建企业。" />
        ) : assets.isLoading ? (
          <Loading />
        ) : assets.error ? (
          <div className="p-6 text-sm text-rose-600">{assets.error.message}</div>
        ) : !assets.data || assets.data.items.length === 0 ? (
          <EmptyState text="当前台账暂无数据，可新增或导入。" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3">明细</th>
                  <th className="px-4 py-3">归属</th>
                  <th className="px-4 py-3">时间</th>
                  <th className="px-4 py-3">来源</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {assets.data.items.map((item) => (
                  <tr key={item.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-medium">{item.name}</td>
                    <td className="px-4 py-3 text-slate-600">{detailText(item)}</td>
                    <td className="px-4 py-3">
                      <Badge tone={item.org_unit_id ? "amber" : "slate"}>
                        {item.org_unit_id
                          ? (orgNames.get(item.org_unit_id) ?? "部门")
                          : "企业共享"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {item.valid_to ?? item.complete_date ?? item.checked_at ?? "—"}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={item.source === "import" ? "blue" : "slate"}>{item.source}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      ) : null}
      </div>
    </>
  );
}

function kindsLabel(kind: Kind): string {
  return KINDS.find((item) => item.key === kind)?.label ?? "资产";
}

function roleLabel(role: string): string {
  if (role === "winner") return "中标";
  if (role === "tenderer") return "招标";
  if (role === "agency") return "代理";
  return role;
}

function formatAmount(value: string | number | null): string {
  if (value === null || value === "") return "—";
  const numeric = Number(value);
  if (Number.isNaN(numeric)) return String(value);
  return numeric.toLocaleString("zh-CN");
}
