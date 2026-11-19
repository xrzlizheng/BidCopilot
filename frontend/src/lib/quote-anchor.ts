/**
 * findings 摘录在条款原文中的定位（候选瀑布，思路移植自开源合同审查项目的
 * 锚点契约；与 OnlyOffice 解耦，纯文本渲染即可用）。
 *
 * 候选顺序：精确匹配 → 去空白归一化匹配（偏移映射回原文）→ 首/尾 80 字截断
 * 再走前两步。全部失败返回 null（前端不渲染高亮，退化为整段展示）。
 */

export type ExcerptRange = { start: number; end: number };

const TAIL_HEAD_CAP = 80;

function normalize(text: string): { compact: string; positions: number[] } {
  const positions: number[] = [];
  let compact = "";
  for (let index = 0; index < text.length; index += 1) {
    if (!/\s/.test(text[index])) {
      positions.push(index);
      compact += text[index];
    }
  }
  return { compact, positions };
}

function locateNormalized(needle: string, haystack: string): ExcerptRange | null {
  if (!needle) return null;
  const exact = haystack.indexOf(needle);
  if (exact >= 0) return { start: exact, end: exact + needle.length };
  const { compact, positions } = normalize(haystack);
  const compactNeedle = needle.replace(/\s+/g, "");
  if (!compactNeedle) return null;
  const found = compact.indexOf(compactNeedle);
  if (found < 0) return null;
  return {
    start: positions[found],
    end: positions[found + compactNeedle.length - 1] + 1,
  };
}

export function locateExcerpt(excerpt: string, clauseText: string): ExcerptRange | null {
  if (!excerpt || !clauseText) return null;
  const direct = locateNormalized(excerpt, clauseText);
  if (direct) return direct;
  if (excerpt.length > TAIL_HEAD_CAP) {
    const head = excerpt.slice(0, TAIL_HEAD_CAP);
    const tail = excerpt.slice(-TAIL_HEAD_CAP);
    return locateNormalized(head, clauseText) ?? locateNormalized(tail, clauseText);
  }
  return null;
}

/** 按定位结果把条款文本切成三段（前/命中/后），命中段由调用方包高亮。 */
export function splitByRange(
  text: string,
  range: ExcerptRange | null,
): { before: string; hit: string; after: string } {
  if (!range || range.start < 0 || range.end > text.length || range.start >= range.end) {
    return { before: text, hit: "", after: "" };
  }
  return {
    before: text.slice(0, range.start),
    hit: text.slice(range.start, range.end),
    after: text.slice(range.end),
  };
}
