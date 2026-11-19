"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-lg px-6 py-24 text-center">
      <h1 className="text-lg font-semibold text-slate-800">页面渲染失败</h1>
      <p className="mt-2 text-sm text-slate-500">
        {error.message || "请重试；若持续失败，请返回上一页或重新登录。"}
      </p>
      <Button className="mt-4" onClick={reset}>
        重试
      </Button>
    </div>
  );
}
