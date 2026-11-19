"use client";

import { useState } from "react";

import { Badge, Button, Card, Input, Loading, PageHeader } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type TenderFinding = {
  rule_id: string;
  level: "high" | "medium";
  title: string;
  suggestion: string;
  block_index: number;
  evidence: string;
};

type AnbiaoFinding = {
  rule_id: string;
  level: "high";
  title: string;
  suggestion: string;
  location: string;
  evidence: string;
};

type AnbiaoResult = {
  status: string;
  filename: string;
  summary: {
    hard: number;
    rules: number;
    chars: number;
    blocks: number;
    metadata_checked: boolean;
    header_footer_checked: boolean;
  };
  findings: AnbiaoFinding[];
  disclaimer: string;
  boundary: string;
};

type TenderResult = {
  status: string;
  filename: string;
  province: string | null;
  summary: { high: number; medium: number; total: number; chars: number; blocks: number };
  findings: TenderFinding[];
  disclaimer: string;
};

/**
 * 招标文件体检（买方侧独立工具）。
 *
 * 定位说明：本页检查的是「招标文件写得合不合规」（指定品牌、地域限制、注册资本门槛等），
 * 面向的是**采购人/招标代理**，与产品主线「企业投标部门做投标文件」不是同一条业务流。
 * 因此它不作为主菜单一级入口，保留为独立工具页（可用于试用/免费钩子）；
 * 投标人侧的「跨投标人雷同自查」已并入投标项目的「响应与自查」阶段。
 */
export default function FileCheckPage() {
  const [file, setFile] = useState<File | null>(null);
  const [province, setProvince] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [tender, setTender] = useState<TenderResult | null>(null);
  const [bidFile, setBidFile] = useState<File | null>(null);
  const [bidLoading, setBidLoading] = useState(false);
  const [anbiao, setAnbiao] = useState<AnbiaoResult | null>(null);

  async function runTender() {
    if (!file) return;
    setLoading(true);
    setMessage(null);
    setTender(null);
    try {
      const data = new FormData();
      data.append("file", file);
      if (province.trim()) data.append("province", province.trim());
      const result = await apiFetch<TenderResult>("/file-check/tender", {
        method: "POST",
        body: data,
      });
      setTender(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "体检失败");
    } finally {
      setLoading(false);
    }
  }

  async function runAnbiao() {
    if (!bidFile) return;
    setBidLoading(true);
    setMessage(null);
    setAnbiao(null);
    try {
      const data = new FormData();
      data.append("file", bidFile);
      const result = await apiFetch<AnbiaoResult>("/file-check/anbiao", {
        method: "POST",
        body: data,
      });
      setAnbiao(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "暗标自查失败");
    } finally {
      setBidLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="FILE CHECK"
        title="文件体检"
        subtitle="两个确定性工具：买方侧检查招标文件是否指定品牌、限制地域；投标人侧暗标自查身份泄露、自指表述与文档元数据。不替代法律意见。"
      />

      <Card className="mb-4 border-amber-200 bg-amber-50/60 p-4">
        <p className="text-xs leading-5 text-amber-800">
          本页含买方侧「招标文件体检」与投标人侧「暗标自查」两个独立工具，不在投标工作流内。
          跨投标人「雷同自查」请在
          <span className="font-medium"> 投标项目 → 响应与自查 </span>
          中使用。
        </p>
      </Card>

      {message ? <p className="mb-3 text-xs text-rose-600">{message}</p> : null}
      {loading ? <Loading /> : null}

      <div className="space-y-4">
        <Card className="p-5">
          <h2 className="text-sm font-semibold">上传招标文件</h2>
          <p className="mt-1 text-xs text-slate-500">
            支持 docx/pdf/txt/md，单文件 ≤ 20MB；扫描件请先 OCR 或上传可复制文本版本。
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="text-sm">
              文件
              <input
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                type="file"
                accept=".docx,.pdf,.txt,.md"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <label className="text-sm">
              省份（可选）
              <Input
                className="mt-1"
                value={province}
                onChange={(event) => setProvince(event.target.value)}
                placeholder="例如：浙江省"
              />
            </label>
            <Button onClick={runTender} disabled={!file || loading}>
              {loading ? "体检中…" : "开始体检"}
            </Button>
          </div>
        </Card>

        <Card className="border-rose-200 bg-rose-50/40 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">暗标自查（投标人侧）</h2>
              <p className="mt-1 text-xs text-slate-600">
                上传技术标，检查企业名称/简称、人员姓名、证书编号、联系方式、统一社会信用代码、
                「我公司/本单位」等自指表述，以及文档元数据与页眉页脚残留。命中项按 A 组暗标规则给出证据位置与整改建议。
              </p>
            </div>
            <Badge tone="rose">投标人侧</Badge>
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="text-sm">
              技术标文件
              <input
                className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                type="file"
                accept=".docx,.pdf,.txt,.md"
                onChange={(event) => setBidFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <Button onClick={runAnbiao} disabled={!bidFile || bidLoading}>
              {bidLoading ? "自查中…" : "开始暗标自查"}
            </Button>
          </div>
          {bidLoading ? <Loading /> : null}
          {anbiao ? (
            <div className="mt-4">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <Badge tone="slate">{anbiao.filename}</Badge>
                <Badge tone={anbiao.summary.hard ? "rose" : "green"}>
                  {anbiao.summary.hard ? `命中 ${anbiao.summary.hard} 项` : "未命中"}
                </Badge>
                <span>· {anbiao.summary.rules} 类规则</span>
                <span>· {anbiao.summary.chars} 字</span>
                <span>· 元数据{anbiao.summary.metadata_checked ? "已查" : "未查"}</span>
                <span>· 页眉页脚{anbiao.summary.header_footer_checked ? "已查" : "未查（仅 docx）"}</span>
              </div>
              {anbiao.findings.length === 0 ? (
                <p className="mt-3 text-sm text-emerald-700">
                  未命中现有确定性规则；提交前仍请按本项目招标文件的暗标条款逐条复核。
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {anbiao.findings.map((finding, index) => (
                    <li
                      key={`${finding.rule_id}-${index}`}
                      className="rounded-xl border border-rose-100 bg-white/70 px-3 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <Badge tone="rose">高</Badge>
                        <span className="font-medium text-slate-800">{finding.title}</span>
                        <span className="text-slate-400">{finding.rule_id}</span>
                        <span className="text-slate-500">{finding.location}</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-600">命中：{finding.evidence}</p>
                      <p className="mt-1 text-xs text-blue-700">建议：{finding.suggestion}</p>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-[11px] text-slate-500">{anbiao.disclaimer}</p>
              <p className="mt-1 text-[11px] text-slate-500">{anbiao.boundary}</p>
            </div>
          ) : null}
        </Card>

        {tender ? (
          <Card className="p-5">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <Badge tone="slate">{tender.filename}</Badge>
              {tender.province ? <Badge tone="blue">{tender.province}</Badge> : null}
              <span>高 {tender.summary.high}</span>
              <span>· 中 {tender.summary.medium}</span>
              <span>· 共 {tender.summary.total} 项</span>
              <span>· {tender.summary.chars} 字</span>
            </div>
            {tender.findings.length === 0 ? (
              <p className="mt-3 text-sm text-slate-600">
                未命中现有确定性规则；仍需人工核对法规与项目实际。
              </p>
            ) : (
              <ul className="mt-3 space-y-2">
                {tender.findings.map((finding, index) => (
                  <li
                    key={`${finding.rule_id}-${index}`}
                    className="rounded-xl border border-slate-100 px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge tone={finding.level === "high" ? "rose" : "amber"}>
                        {finding.level}
                      </Badge>
                      <span className="font-medium text-slate-800">{finding.title}</span>
                      <span className="text-slate-400">{finding.rule_id}</span>
                    </div>
                    <p className="mt-1 text-xs text-slate-600">原文：{finding.evidence}</p>
                    <p className="mt-1 text-xs text-blue-700">建议：{finding.suggestion}</p>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-[11px] text-slate-500">{tender.disclaimer}</p>
          </Card>
        ) : null}
      </div>
    </>
  );
}
