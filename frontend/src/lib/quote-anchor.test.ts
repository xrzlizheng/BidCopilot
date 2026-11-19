import { describe, expect, it } from "vitest";

import { locateExcerpt, splitByRange } from "./quote-anchor";

describe("locateExcerpt", () => {
  const clause = "合同总价：人民币 95 万元整。付款方式：验收后 30 日内支付。";

  it("精确摘录直接命中", () => {
    const range = locateExcerpt("付款方式：验收后 30 日内支付。", clause);
    expect(range).not.toBeNull();
    expect(clause.slice(range!.start, range!.end)).toBe("付款方式：验收后 30 日内支付。");
  });

  it("去空白归一化后命中并映射回原偏移", () => {
    // 摘录把原文里的空格去掉了
    const range = locateExcerpt("人民币95万元整", clause);
    expect(range).not.toBeNull();
    expect(clause.slice(range!.start, range!.end)).toBe("人民币 95 万元整");
  });

  it("超长摘录用尾段候选命中", () => {
    // 摘录 = 20 字噪声 + 长条款原文（去头 10 字，共 85 字）：首段候选含噪声不命中，
    // 尾段（末 80 字）是条款原文的连续子串 → 经尾段候选命中（候选瀑布的真实形态）
    const longClause = "甲".repeat(90) + "合同总价：人民币 95 万元整。";
    const target = longClause.slice(10);
    const long = `${"垃圾前缀".repeat(5)}${target}`;
    expect(long.length).toBeGreaterThan(80);
    const range = locateExcerpt(long, longClause);
    expect(range).not.toBeNull();
    expect(longClause.slice(range!.start, range!.end)).toBe(long.slice(-80));
  });

  it("纯噪声超长摘录返回 null", () => {
    expect(locateExcerpt("无关文本".repeat(40), clause)).toBeNull();
    expect(locateExcerpt("无关文本".repeat(40), "甲".repeat(120))).toBeNull();
  });

  it("完全无关的摘录返回 null", () => {
    expect(locateExcerpt("这里什么都没有", clause)).toBeNull();
    expect(locateExcerpt("", clause)).toBeNull();
    expect(locateExcerpt("合同总价", "")).toBeNull();
  });

  it("splitByRange 三段切分与整段回退", () => {
    const range = { start: 5, end: 12 };
    const parts = splitByRange(clause, range);
    expect(parts.before + parts.hit + parts.after).toBe(clause);
    expect(parts.hit).toBe(clause.slice(5, 12));
    const fallback = splitByRange(clause, null);
    expect(fallback.hit).toBe("");
    expect(fallback.before).toBe(clause);
  });
});
