"use client";

import { useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type SemanticLayer = { raw: number; weight: number; contribution: number };

type SemanticEvidence = {
  shared_terms: string[];
  shared_concepts: string[];
  concept_pairs: { concept: string; left: string[]; right: string[] }[];
  shared_excerpts: string[];
  contributions: { concept: SemanticLayer; ngram: SemanticLayer; term: SemanticLayer };
  formula: string;
  explanation: string;
  boundary: string;
  note?: string;
};

type SimilarityPair = {
  left: string;
  right: string;
  score: number;
  level: "high" | "medium" | "low";
  common_excerpt: string | null;
  semantic_score: number;
  semantic_level: "high" | "medium" | "low";
  semantic_evidence: SemanticEvidence;
  combined_score: number;
};

type SimilarityResult = {
  status: string;
  files: string[];
  summary: {
    files: number;
    pairs: number;
    high: number;
    medium: number;
    semantic_high: number;
    semantic_medium: number;
    chars: number;
  };
  pairs: SimilarityPair[];
  disclaimer: string;
  boundary?: string;
};

const LEVEL_LABEL: Record<string, string> = { high: "高", medium: "中", low: "低" };

function LevelBadge({ level }: { level: "high" | "medium" | "low" }) {
  return (
    <Badge tone={level === "high" ? "rose" : level === "medium" ? "amber" : "slate"}>
      {LEVEL_LABEL[level] ?? level}
    </Badge>
  );
}

/**
 * 跨投标人雷同自查（交标前合规终检）。
 *
 * 归位说明：这是投标项目的交标前检查，与就绪清单、自查清单回答同一个问题
 * 「这份标现在能不能交」，因此嵌入项目工作区的「响应与自查」阶段，而不是
 * 独立占一级菜单。买方侧「招标文件体检」不在此组件内（它属于另一类业务）。
 */
export function BidderSimilarityPanel() {
  const [files, setFiles] = useState<File[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [similarity, setSimilarity] = useState<SimilarityResult | null>(null);

  async function runSimilarity() {
    if (files.length < 2) {
      setMessage("至少选择 2 份文件");
      return;
    }
    setLoading(true);
    setMessage(null);
    setSimilarity(null);
    try {
      const data = new FormData();
      for (const item of files) data.append("files", item);
      const result = await apiFetch<SimilarityResult>("/file-check/similarity", {
        method: "POST",
        body: data,
      });
      setSimilarity(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "查重失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="mt-4 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">跨投标人雷同自查</h2>
        <Badge tone="slate">交标前合规终检</Badge>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        字面相似度（4-gram Jaccard）+ 语义相似度（概念词典归一化 + 字符 n-gram + 术语重合，
        纯确定性、不调用大模型）；只用于合规自查，不对“串标”做定性，也不提供规避检测建议。
      </p>

      <input
        className="mt-3 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        type="file"
        multiple
        accept=".docx,.pdf,.txt,.md"
        onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
      />
      <p className="mt-2 text-xs text-slate-500">已选择 {files.length} 份</p>
      <Button className="mt-3" onClick={runSimilarity} disabled={files.length < 2 || loading}>
        {loading ? "比对中…" : "开始比对"}
      </Button>
      {message ? <p className="mt-2 text-xs text-rose-600">{message}</p> : null}

      {similarity ? (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <Badge tone={similarity.status === "warning" ? "amber" : "green"}>
              {similarity.status}
            </Badge>
            <span>{similarity.summary.files} 份文件</span>
            <span>· 字面高相似 {similarity.summary.high} 对</span>
            <span>· 字面中相似 {similarity.summary.medium} 对</span>
            <span>· 语义高相似 {similarity.summary.semantic_high} 对</span>
            <span>· 语义中相似 {similarity.summary.semantic_medium} 对</span>
          </div>
          <ul className="mt-3 space-y-2">
            {similarity.pairs.map((pair) => (
              <li
                key={`${pair.left}-${pair.right}`}
                className="rounded-xl border border-slate-100 px-3 py-2 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-400">字面</span>
                  <LevelBadge level={pair.level} />
                  <span className="text-slate-400">语义</span>
                  <LevelBadge level={pair.semantic_level} />
                  <span className="font-medium text-slate-800">
                    {pair.left} ↔ {pair.right}
                  </span>
                  <span className="text-slate-500">
                    字面 {(pair.score * 100).toFixed(1)}% · 语义{" "}
                    {(pair.semantic_score * 100).toFixed(1)}% · 综合{" "}
                    {(pair.combined_score * 100).toFixed(1)}%
                  </span>
                </div>

                {pair.semantic_evidence.shared_concepts.length > 0 ? (
                  <p className="mt-1 text-slate-600">
                    语义命中概念：
                    {pair.semantic_evidence.concept_pairs.map((item) => (
                      <span key={item.concept}>
                        <span className="font-medium text-slate-700">{item.concept}</span>
                        <span className="text-slate-400">
                          （{item.left.join("/")} ↔ {item.right.join("/")}）
                        </span>
                      </span>
                    ))}
                  </p>
                ) : null}

                {pair.semantic_evidence.shared_terms.length > 0 ? (
                  <p className="mt-1 text-slate-600">
                    术语重合：{pair.semantic_evidence.shared_terms.join("、")}
                  </p>
                ) : null}

                {pair.common_excerpt ? (
                  <p className="mt-1 text-slate-600">字面共同片段：{pair.common_excerpt}</p>
                ) : null}

                {pair.semantic_evidence.shared_excerpts.length > 0 ? (
                  <ul className="mt-1 space-y-1 text-slate-600">
                    {pair.semantic_evidence.shared_excerpts.map((excerpt, index) => (
                      <li key={`${pair.left}-${pair.right}-excerpt-${index}`}>
                        共同片段：{excerpt}
                      </li>
                    ))}
                  </ul>
                ) : null}

                <details className="mt-1 text-slate-500">
                  <summary className="cursor-pointer select-none">
                    分值来源（三层贡献，合计 {(pair.semantic_score * 100).toFixed(1)}%）
                  </summary>
                  <p className="mt-1">
                    概念层 {pair.semantic_evidence.contributions.concept.raw.toFixed(4)} ×{" "}
                    {pair.semantic_evidence.contributions.concept.weight} ={" "}
                    {pair.semantic_evidence.contributions.concept.contribution.toFixed(4)}
                    {"；"}字符 n-gram 层{" "}
                    {pair.semantic_evidence.contributions.ngram.raw.toFixed(4)} ×{" "}
                    {pair.semantic_evidence.contributions.ngram.weight} ={" "}
                    {pair.semantic_evidence.contributions.ngram.contribution.toFixed(4)}
                    {"；"}术语层 {pair.semantic_evidence.contributions.term.raw.toFixed(4)} ×{" "}
                    {pair.semantic_evidence.contributions.term.weight} ={" "}
                    {pair.semantic_evidence.contributions.term.contribution.toFixed(4)}
                  </p>
                  {pair.semantic_evidence.note ? (
                    <p className="mt-1 text-amber-700">{pair.semantic_evidence.note}</p>
                  ) : null}
                </details>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[11px] font-medium text-slate-600">
            仅供合规自查，不构成“串标”认定，也不提供任何规避检测建议；命中项须人工复核原文。
          </p>
          <p className="mt-1 text-[11px] text-slate-500">{similarity.disclaimer}</p>
          {similarity.boundary ? (
            <p className="mt-1 text-[11px] text-slate-500">{similarity.boundary}</p>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
