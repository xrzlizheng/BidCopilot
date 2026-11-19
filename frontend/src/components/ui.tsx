"use client";

// Button 内部用 useState 做「异步点击防重复提交」，因此本模块必须是 Client Component。
// Server Component（如 help 页）仍可导入并渲染这里的组件，只是它们会作为客户端边界。
import { forwardRef, useState } from "react";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  MouseEvent,
  ReactNode,
  SelectHTMLAttributes,
} from "react";

function cn(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: "primary" | "secondary" | "ghost" | "danger";
  }
>(function Button({ variant = "primary", className, onClick, disabled, ...props }, ref) {
  const [running, setRunning] = useState(false);
  const styles = {
    primary:
      "bg-gradient-to-r from-electric-500 to-electric-600 text-white shadow-[0_12px_30px_-16px_rgba(99,102,241,0.9)] hover:from-electric-400 hover:to-electric-500",
    secondary:
      "border border-slate-200/80 bg-white/80 text-slate-700 shadow-sm backdrop-blur hover:border-slate-300 hover:bg-white",
    ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
    danger: "bg-rose-600 text-white hover:bg-rose-500",
  }[variant];

  /**
   * 异步 onClick 自动防重复提交。
   *
   * 很多按钮的 onClick 是 `async () => { await confirm(...); mutation.mutate() }`
   * 或直接 `async () => await apiFetch(...)`；这类点击在请求/确认期间再次点击会
   * 触发第二次提交（重复创建、重复删除）。这里统一在 Promise 未结束前禁用按钮，
   * 调用方无需逐个补 `disabled={x.isPending}`。
   *
   * 注意：`() => mutation.mutate()` 这类同步写法拿不到 Promise，仍需调用方
   * 显式传 `disabled={mutation.isPending}`。
   */
  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (!onClick) return;
    const result = onClick(event) as unknown;
    if (result instanceof Promise) {
      setRunning(true);
      void result.finally(() => setRunning(false));
    }
  };

  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        styles,
        className,
      )}
      {...props}
      onClick={onClick ? handleClick : undefined}
      disabled={disabled || running}
      aria-busy={running || undefined}
    />
  );
});

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-xl border border-slate-200/90 bg-white/85 px-3 text-sm text-slate-900 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-aurora-500/70 focus:ring-4 focus:ring-aurora-500/10",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-10 rounded-xl border border-slate-200/90 bg-white/85 px-3 text-sm text-slate-700 shadow-sm outline-none transition focus:border-aurora-500/70 focus:ring-4 focus:ring-aurora-500/10",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

export function Card({
  children,
  className,
  id,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  /** 可选锚点：供阻断提示等场景直接滚动定位到该卡片。 */
  id?: string;
} & HTMLAttributes<HTMLElement>) {
  return (
    <section
      id={id}
      {...rest}
      className={cn(
        "rounded-2xl border border-slate-200/70 bg-white/85 shadow-card backdrop-blur-sm",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function Badge({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "slate" | "green" | "amber" | "rose" | "blue" | "cyan";
}) {
  const styles = {
    slate: "bg-slate-100 text-slate-600",
    green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    amber: "bg-amber-50 text-amber-700 ring-amber-200",
    rose: "bg-rose-50 text-rose-700 ring-rose-200",
    blue: "bg-blue-50 text-blue-700 ring-blue-200",
    cyan: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  }[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ring-transparent",
        styles,
      )}
    >
      {children}
    </span>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  eyebrow,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  eyebrow?: string;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow ? <p className="mono-label mb-2 text-aurora-600">{eyebrow}</p> : null}
        <h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-[28px]">
          {title}
        </h1>
        {subtitle ? <p className="mt-1.5 max-w-2xl text-sm text-slate-500">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/**
 * 空状态。
 *
 * 只写「暂无数据」等于把问题丢回给用户——他不知道是没权限、没配置，还是该去哪建。
 * 因此约定：`text` 说明**为什么空**，`hint` 说明**下一步做什么**，`action` 给一个
 * 可直接点的入口。三者能写就写。
 */
export function EmptyState({
  text,
  hint,
  action,
}: {
  text: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="p-10 text-center text-sm text-slate-500">
      <div className="mx-auto mb-3 h-10 w-10 rounded-full border border-dashed border-slate-300 bg-slate-50" />
      <p className="text-slate-600">{text}</p>
      {hint ? <p className="mx-auto mt-1.5 max-w-md text-xs leading-5 text-slate-500">{hint}</p> : null}
      {action ? <div className="mt-3 flex justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function Loading() {
  return <div className="p-10 text-center text-sm text-slate-500">加载中…</div>;
}

/**
 * 请求失败态。
 *
 * 不显示错误等于让用户对着空白页猜：是没权限、服务没起，还是网络断了。
 * 这里把后端返回的 message 原样显示，并给一个「重试」入口。
 */
export function ErrorState({
  error,
  onRetry,
  hint,
}: {
  error: unknown;
  onRetry?: () => void;
  hint?: string;
}) {
  const message = error instanceof Error ? error.message : "请求失败，请稍后重试";
  return (
    <div className="p-8 text-center text-sm">
      <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full border border-rose-200 bg-rose-50 text-rose-500">
        !
      </div>
      <p className="font-medium text-rose-700">{message}</p>
      {hint ? <p className="mx-auto mt-1.5 max-w-md text-xs leading-5 text-slate-500">{hint}</p> : null}
      {onRetry ? (
        <Button className="mt-3" variant="secondary" onClick={onRetry}>
          重试
        </Button>
      ) : null}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = "aurora",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  icon?: ReactNode;
  tone?: "aurora" | "electric" | "amber" | "slate";
}) {
  const accent = {
    aurora: "text-aurora-600 bg-aurora-500/10",
    electric: "text-electric-600 bg-electric-500/10",
    amber: "text-amber-600 bg-amber-500/10",
    slate: "text-slate-600 bg-slate-500/10",
  }[tone];
  return (
    <div className="rounded-2xl border border-slate-200/70 bg-white/85 p-4 shadow-card backdrop-blur-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium text-slate-500">{label}</p>
        {icon ? (
          <span className={cn("grid h-8 w-8 place-items-center rounded-lg", accent)}>{icon}</span>
        ) : null}
      </div>
      <p className="tabular mt-3 text-2xl font-semibold tracking-tight text-slate-950">{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function ProgressBar({
  value,
  tone = "aurora",
}: {
  value: number;
  tone?: "aurora" | "electric" | "amber";
}) {
  const bar = {
    aurora: "from-aurora-500 to-aurora-400",
    electric: "from-electric-500 to-electric-400",
    amber: "from-amber-500 to-amber-400",
  }[tone];
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
      <div
        className={cn("h-full rounded-full bg-gradient-to-r transition-all", bar)}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}
