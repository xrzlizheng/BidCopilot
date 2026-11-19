"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Command, Fingerprint, Network, ShieldCheck, Sparkles } from "lucide-react";

import { Button, Input } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import type { LoginResponse } from "@/lib/types";

const highlights = [
  { icon: <Network size={16} />, title: "真实标讯数据", text: "KeepRisk 商机、企业画像与投标历史" },
  { icon: <Sparkles size={16} />, title: "AI 标书生产", text: "模板、素材、生成、核查、合规与导出" },
  { icon: <ShieldCheck size={16} />, title: "证据可追溯", text: "主张级核查、确定性规则、审计哈希链" },
];

export default function LoginPage() {
  const router = useRouter();
  const setSession = useAuthStore((state) => state.setSession);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await apiFetch<LoginResponse>(
        "/auth/login",
        { method: "POST", body: JSON.stringify({ email, password }) },
        false,
      );
      setSession(data.access_token, data.user);
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="grid min-h-screen lg:grid-cols-[1.15fr_1fr]">
      <section className="aurora-panel relative hidden overflow-hidden px-12 py-12 text-white lg:flex lg:flex-col">
        <div className="aurora-grid pointer-events-none absolute inset-0 opacity-40" />
        <div className="relative flex items-center gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-2xl bg-gradient-to-br from-aurora-400 to-aurora-500 text-ink-950 shadow-glow">
            <Command size={22} strokeWidth={2.4} />
          </span>
          <div>
            <p className="text-lg font-semibold tracking-tight">BidCopilot</p>
            <p className="mono-label mt-0.5 text-slate-400">AI BIDDING OPERATING SYSTEM</p>
          </div>
        </div>

        <div className="relative mt-auto max-w-xl">
          <p className="mono-label text-aurora-400">DATA / EVIDENCE / DELIVERY</p>
          <h1 className="mt-4 text-4xl font-semibold leading-[1.15] tracking-tight">
            不是替你写一份标书，
            <br />
            而是让每一次投标
            <span className="bg-gradient-to-r from-aurora-400 to-electric-400 bg-clip-text text-transparent">
              有据可依
            </span>
            。
          </h1>
          <p className="mt-5 max-w-lg text-sm leading-6 text-slate-400">
            从真实商机、资格决策到模板化生产、AI 核查与合规导出，
            把投标团队的经验沉淀为可复用的工作系统。
          </p>
          <div className="mt-8 grid gap-3 sm:grid-cols-3">
            {highlights.map((item) => (
              <div key={item.title} className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3">
                <span className="text-aurora-400">{item.icon}</span>
                <p className="mt-2 text-xs font-medium text-slate-200">{item.title}</p>
                <p className="mt-1 text-[11px] leading-5 text-slate-500">{item.text}</p>
              </div>
            ))}
          </div>
        </div>
        <p className="relative mt-10 text-[11px] text-slate-600">
          PRIVATE DEPLOYMENT · HUMAN IN THE LOOP · NO FABRICATED EVIDENCE
        </p>
      </section>

      <section className="flex items-center justify-center bg-[#f6f8fb] px-5 py-12">
        <div className="w-full max-w-md">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-electric-500 to-electric-600 text-white">
                <Command size={20} />
              </span>
              <p className="text-lg font-semibold">BidCopilot</p>
            </div>
          </div>
          <div className="rounded-3xl border border-slate-200/80 bg-white/90 p-8 shadow-card backdrop-blur">
            <p className="mono-label text-aurora-600">SECURE ACCESS</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">登录工作台</h2>
            <p className="mt-1 text-sm text-slate-500">使用企业管理员分配给你的账号。</p>

            <form className="mt-7 space-y-4" onSubmit={handleSubmit}>
              <label className="block text-sm font-medium text-slate-700">
                邮箱
                <Input
                  className="mt-1.5"
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </label>
              <label className="block text-sm font-medium text-slate-700">
                密码
                <Input
                  className="mt-1.5"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </label>
              {error ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {error}
                </div>
              ) : null}
              <Button className="mt-2 h-11 w-full" type="submit" disabled={loading}>
                {loading ? "验证中…" : "进入指挥台"}
              </Button>
            </form>

            <div className="mt-6 flex items-center gap-2 border-t border-slate-100 pt-4 text-[11px] text-slate-400">
              <Fingerprint size={14} />
              会话令牌短期有效；改密、停用或角色变更会立即失效。
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
