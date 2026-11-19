import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Button } from "./ui";

describe("Button 异步点击自动防重复提交", () => {
  afterEach(() => {
    cleanup();
  });

  it("异步 onClick 执行期间按钮被禁用，结束后恢复", async () => {
    // 用对象持有 resolve：TS 不会把 Promise executor 里的赋值算进类型收窄。
    const deferred: { resolve?: () => void } = {};
    const onClick = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          deferred.resolve = resolve;
        }),
    );
    render(<Button onClick={onClick}>保存</Button>);

    const button = screen.getByRole("button", { name: "保存" });
    expect(button).toBeEnabled();

    fireEvent.click(button);
    // 请求未完成：按钮禁用，重复点击不会再触发
    await waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(button).toHaveAttribute("aria-busy", "true");

    deferred.resolve?.();
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).not.toHaveAttribute("aria-busy");
  });

  it("同步 onClick 不会被自动禁用（交回调用方控制 isPending）", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>同步</Button>);

    const button = screen.getByRole("button", { name: "同步" });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(onClick).toHaveBeenCalledTimes(2);
    expect(button).toBeEnabled();
  });

  it("外部传入的 disabled 仍然生效", () => {
    render(
      <Button disabled onClick={() => {}}>
        已禁用
      </Button>,
    );
    expect(screen.getByRole("button", { name: "已禁用" })).toBeDisabled();
  });
});
