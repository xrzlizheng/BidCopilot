"use client";

import { ArrowDown, ArrowUp, ImageIcon, List, Plus, Table2, Trash2 } from "lucide-react";

import { Button, Input } from "@/components/ui";
import { AuthedImage } from "@/components/authed-image";
import type { KnowledgeImageAsset } from "@/lib/types";

type Block = Record<string, any>;

export function SectionBlockEditor({
  blocks,
  onChange,
  images,
}: {
  blocks: Block[];
  onChange: (blocks: Block[]) => void;
  images: KnowledgeImageAsset[];
}) {
  function update(index: number, next: Block) {
    onChange(blocks.map((block, current) => (current === index ? next : block)));
  }

  function remove(index: number) {
    onChange(blocks.filter((_, current) => current !== index));
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= blocks.length) return;
    const next = [...blocks];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  function addParagraph() {
    onChange([...blocks, { type: "paragraph", text: "" }]);
  }

  function addBullets() {
    onChange([...blocks, { type: "bullets", items: [""] }]);
  }

  function addTable() {
    onChange([...blocks, { type: "table", headers: ["项目", "内容"], rows: [["", ""]] }]);
  }

  function addImage(imageId: string) {
    if (!imageId) return;
    onChange([...blocks, { type: "image", image_id: imageId, caption: "" }]);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="secondary" onClick={addParagraph}>
          <Plus size={14} /> 段落
        </Button>
        <Button type="button" variant="secondary" onClick={addBullets}>
          <List size={14} /> 列表
        </Button>
        <Button type="button" variant="secondary" onClick={addTable}>
          <Table2 size={14} /> 表格
        </Button>
        <select
          className="h-10 rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700"
          value=""
          onChange={(event) => addImage(event.target.value)}
          disabled={images.length === 0}
        >
          <option value="">{images.length === 0 ? "知识库暂无图片" : "插入知识库图片…"}</option>
          {images.map((image) => (
            <option key={image.id} value={image.id}>
              {image.title}
              {image.sensitive ? "（敏感）" : ""}
            </option>
          ))}
        </select>
      </div>

      {blocks.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-400">
          本章节为空。添加段落、列表、表格或图片。
        </p>
      ) : null}

      {blocks.map((block, index) => (
        <div key={index} className="rounded-xl border border-slate-200 bg-white/70 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="mono-label text-slate-400">
              {block.type === "paragraph"
                ? "段落"
                : block.type === "bullets"
                  ? "列表"
                  : block.type === "table"
                    ? "表格"
                    : "图片"}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
                onClick={() => move(index, -1)}
                disabled={index === 0}
                aria-label="上移"
              >
                <ArrowUp size={14} />
              </button>
              <button
                type="button"
                className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
                onClick={() => move(index, 1)}
                disabled={index === blocks.length - 1}
                aria-label="下移"
              >
                <ArrowDown size={14} />
              </button>
              <button
                type="button"
                className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                onClick={() => remove(index)}
                aria-label="删除块"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>

          {block.type === "paragraph" ? (
            <textarea
              className="mt-2 min-h-24 w-full rounded-lg border border-slate-300 p-3 text-sm outline-none focus:border-slate-500"
              value={block.text ?? ""}
              onChange={(event) => update(index, { ...block, text: event.target.value })}
              placeholder="段落内容"
            />
          ) : null}

          {block.type === "bullets" ? (
            <textarea
              className="mt-2 min-h-24 w-full rounded-lg border border-slate-300 p-3 text-sm outline-none focus:border-slate-500"
              value={(block.items ?? []).join("\n")}
              onChange={(event) =>
                update(index, {
                  ...block,
                  items: event.target.value.split("\n").filter((item) => item.trim()),
                })
              }
              placeholder="每行一条"
            />
          ) : null}

          {block.type === "table" ? (
            <div className="mt-2 space-y-2">
              <Input
                value={block.caption ?? ""}
                placeholder="表名，例如：资格、人员与业绩证明材料"
                onChange={(event) => update(index, { ...block, caption: event.target.value })}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() =>
                    update(index, {
                      ...block,
                      rows: [...(block.rows ?? []), (block.headers ?? []).map(() => "")],
                    })
                  }
                >
                  <Plus size={13} /> 行
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() =>
                    update(index, {
                      ...block,
                      headers: [...(block.headers ?? []), `列${(block.headers ?? []).length + 1}`],
                      rows: (block.rows ?? []).map((row: string[]) => [...row, ""]),
                    })
                  }
                >
                  <Plus size={13} /> 列
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() =>
                    update(index, {
                      ...block,
                      rows: (block.rows ?? []).slice(0, -1),
                    })
                  }
                  disabled={(block.rows ?? []).length === 0}
                >
                  删除末行
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() =>
                    update(index, {
                      ...block,
                      headers: (block.headers ?? []).slice(0, -1),
                      rows: (block.rows ?? []).map((row: string[]) => row.slice(0, -1)),
                    })
                  }
                  disabled={(block.headers ?? []).length <= 1}
                >
                  删除末列
                </Button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      {(block.headers ?? []).map((header: string, col: number) => (
                        <th key={col} className="p-1">
                          <Input
                            value={header}
                            onChange={(event) => {
                              const headers = [...(block.headers ?? [])];
                              headers[col] = event.target.value;
                              update(index, { ...block, headers });
                            }}
                          />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(block.rows ?? []).map((row: string[], rowIndex: number) => (
                      <tr key={rowIndex}>
                        {(block.headers ?? []).map((_: string, col: number) => (
                          <td key={col} className="p-1">
                            <Input
                              value={row[col] ?? ""}
                              onChange={(event) => {
                                const rows = (block.rows ?? []).map((item: string[]) => [...item]);
                                rows[rowIndex][col] = event.target.value;
                                update(index, { ...block, rows });
                              }}
                            />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {block.type === "image" ? (
            <div className="mt-2 space-y-2">
              {block.image_id ? (
                <AuthedImage
                  imageId={block.image_id}
                  alt={images.find((image) => image.id === block.image_id)?.title ?? "知识库图片"}
                  className="h-36 w-full rounded-lg border border-slate-100 bg-slate-50 object-contain"
                />
              ) : null}
              <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
                <ImageIcon size={15} className="text-aurora-600" />
                {images.find((image) => image.id === block.image_id)?.title ?? "未知图片"}
              </div>
              <select
                className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700"
                value={block.image_id ?? ""}
                onChange={(event) => update(index, { ...block, image_id: event.target.value })}
              >
                {images.map((image) => (
                  <option key={image.id} value={image.id}>
                    {image.title}
                    {image.sensitive ? "（敏感）" : ""}
                  </option>
                ))}
              </select>
              <Input
                value={block.caption ?? ""}
                placeholder="题注，例如：图 1-1 双活架构"
                onChange={(event) => update(index, { ...block, caption: event.target.value })}
              />
            </div>
          ) : null}
        </div>
      ))}

      <p className="text-[11px] text-slate-400">
        表格支持增删行列与单元格编辑；图片只能从企业知识库选择，保存后按格式模板导出。
      </p>
    </div>
  );
}
