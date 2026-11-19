"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui";

/**
 * 统一二次确认对话框。
 *
 * 为什么不用 `window.confirm`：
 * 1. 浏览器原生弹窗无法说明「后果」，而删除回执附件、吊销 API 令牌这类操作的
 *    后果必须写清楚（令牌吊销后集成方会立刻 401）；
 * 2. 原生弹窗阻塞主线程、样式不可控、在部分内嵌浏览器被禁用；
 * 3. 无法测试（jsdom 里要打桩 window.confirm）。
 *
 * 用法：
 *   const confirm = useConfirm();
 *   if (!(await confirm({ title: "删除附件？", description: "...", tone: "danger" }))) return;
 */
export type ConfirmOptions = {
  title: string;
  description?: string;
  /** 确认按钮文案，默认「确定」。危险操作建议写成动词，如「删除附件」。 */
  confirmText?: string;
  cancelText?: string;
  tone?: "danger" | "primary";
};

type ConfirmState = (ConfirmOptions & { resolve: (ok: boolean) => void }) | null;

const ConfirmContext = createContext<((options: ConfirmOptions) => Promise<boolean>) | null>(null);

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm 必须在 <ConfirmProvider> 内使用");
  }
  return ctx;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfirmState>(null);
  const confirmButtonRef = useRef<HTMLButtonElement | null>(null);

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setState({ ...options, resolve });
      }),
    [],
  );

  const settle = useCallback((ok: boolean) => {
    setState((current) => {
      current?.resolve(ok);
      return null;
    });
  }, []);

  // 打开时聚焦确认按钮：键盘用户回车即确认，Esc 取消。
  useEffect(() => {
    if (!state) return;
    confirmButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        settle(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [settle, state]);

  const value = useMemo(() => confirm, [confirm]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {state ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-sm"
          role="presentation"
          onClick={() => settle(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-dialog-title"
            className="w-full max-w-md rounded-2xl border border-slate-200/80 bg-white p-5 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start gap-3">
              <span
                className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl ${
                  state.tone === "danger" ? "bg-rose-50 text-rose-600" : "bg-electric-50 text-electric-600"
                }`}
              >
                <AlertTriangle size={18} />
              </span>
              <div className="min-w-0">
                <h2 id="confirm-dialog-title" className="text-sm font-semibold text-slate-900">
                  {state.title}
                </h2>
                {state.description ? (
                  <p className="mt-1.5 whitespace-pre-line text-xs leading-5 text-slate-600">
                    {state.description}
                  </p>
                ) : null}
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => settle(false)}>
                {state.cancelText ?? "取消"}
              </Button>
              <Button
                ref={confirmButtonRef}
                variant={state.tone === "danger" ? "danger" : "primary"}
                onClick={() => settle(true)}
              >
                {state.confirmText ?? "确定"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </ConfirmContext.Provider>
  );
}
