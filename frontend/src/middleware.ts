import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// 生产 CSP nonce 化中间件（默认关闭；启用方式与前提见末尾说明）。
//
// 背景：Next.js 14 App Router 会向内联 <script> 注入 RSC flight 数据，并从 'self' 加载 chunk。
// 已实测两种路由：
//   · 动态渲染页面：本中间件生成 per-request nonce 后，Next 会自动给全部脚本打上 nonce
//     （动态路由 /opportunities/[id] 22/22 脚本命中），
//     `script-src 'self' 'nonce-<n>' 'strict-dynamic'` 可正常移除 'unsafe-inline'；
//   · 静态预渲染页面（/login 等，构建期生成 HTML）：内联 flight 脚本与 chunk 脚本无法携带
//     per-request nonce，且 'strict-dynamic' 会忽略 'self'，这些脚本会被 CSP 阻止 → 破坏 hydration。
//
// 结论：只有当页面均为动态渲染（或对静态内联脚本改用 hash 型 CSP）后，才设置环境变量
// CSP_NONCE=1 启用本中间件；否则保持关闭，由 next.config.mjs 下发带 'unsafe-inline' 的静态 CSP。
function buildCsp(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function middleware(request: NextRequest) {
  if (process.env.CSP_NONCE !== "1") {
    // 默认关闭：不改动响应头，沿用 next.config.mjs 的静态 CSP（含 'unsafe-inline'）。
    return NextResponse.next();
  }

  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = buildCsp(nonce);

  // 同时写入请求头与响应头：
  // - 请求头（x-nonce / CSP）让 Next.js 渲染器取到 nonce，从而给自身脚本打上 nonce；
  // - 响应头让浏览器按该 CSP 执行。
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    // 覆盖页面渲染路径，跳过静态资源与 /api 代理（这些不需要文档级 CSP）。
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
    },
  ],
};
