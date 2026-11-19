import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui";
import { ConfirmProvider, useConfirm } from "./confirm-dialog";

function Trigger({
  onResult,
  options,
}: {
  onResult: (ok: boolean) => void;
  options?: { tone?: "danger" | "primary"; confirmText?: string };
}) {
  const confirm = useConfirm();
  return (
    <Button
      onClick={async () => {
        const ok = await confirm({
          title: "删除附件「回执.png」？",
          description: "这是交标回执的合规留痕，删除后无法恢复。",
          confirmText: options?.confirmText,
          tone: options?.tone,
        });
        onResult(ok);
      }}
    >
      删除
    </Button>
  );
}

function setup(onResult: (ok: boolean) => void, options?: { confirmText?: string }) {
  return render(
    <ConfirmProvider>
      <Trigger onResult={onResult} options={options} />
    </ConfirmProvider>,
  );
}

describe("ConfirmDialog 二次确认", () => {
  afterEach(() => {
    cleanup();
  });

  it("点击后弹出对话框，并把后果写清楚", async () => {
    setup(vi.fn());
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("删除附件「回执.png」？")).toBeInTheDocument();
    expect(screen.getByText(/合规留痕/)).toBeInTheDocument();
  });

  it("确认返回 true，取消返回 false", async () => {
    const onResult = vi.fn();
    setup(onResult, { confirmText: "删除附件" });

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(await screen.findByRole("button", { name: "删除附件" }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
    // 确认后对话框关闭
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    onResult.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it("Esc 等同于取消", async () => {
    const onResult = vi.fn();
    setup(onResult);

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await screen.findByRole("dialog");
    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });
});
