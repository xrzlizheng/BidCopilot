import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-lg px-6 py-24 text-center">
      <p className="mono-label text-slate-400">404</p>
      <h1 className="mt-2 text-lg font-semibold text-slate-800">页面不存在</h1>
      <p className="mt-2 text-sm text-slate-500">链接可能已失效，或资源不属于当前租户。</p>
      <Link
        href="/dashboard"
        className="mt-4 inline-flex rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white"
      >
        返回指挥台
      </Link>
    </div>
  );
}
