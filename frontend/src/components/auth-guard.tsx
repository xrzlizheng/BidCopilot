"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { ensureSession } from "@/lib/api";

export function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    ensureSession().then((user) => {
      if (!active) return;
      if (!user) {
        router.replace("/login");
        return;
      }
      setReady(true);
    });
    return () => {
      active = false;
    };
  }, [router]);

  if (!ready) {
    return <div className="grid min-h-screen place-items-center text-sm text-slate-500">加载中…</div>;
  }
  return <>{children}</>;
}
