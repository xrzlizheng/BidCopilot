const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
const isProd = process.env.NODE_ENV === "production";

// ── 为何 script-src 仍保留 'unsafe-inline'（已实测验证）──────────────────────
// Next.js 14 App Router 会向内联 <script> 注入 RSC flight 数据，并从 'self' 加载 chunk 脚本。
// 本应用页面绝大多数是静态预渲染（构建期生成 HTML，路由表标记为 ○）。实测两种路由：
//   · 动态路由（/opportunities/[id]）：per-request nonce 方案能让 Next 自动给全部脚本打上
//     nonce（22/22 命中），`script-src 'self' 'nonce-..' 'strict-dynamic'` 可移除 unsafe-inline；
//   · 静态页面（/login）：内联 flight 脚本与 chunk 脚本在构建期生成、无法携带 per-request
//     nonce；且 'strict-dynamic' 会忽略 'self'，这些脚本会被 CSP 阻止 → hydration 失败。
// 因此，移除 script-src 的 'unsafe-inline' 会破坏静态页运行；除非把每个页面改成强制动态渲染
// （全站 SSR 回归，代价过大），故当前保留 'unsafe-inline'。
// 后续 nonce 方案：见 src/middleware.ts（已验证可用，默认关闭）。当页面改为动态渲染、或对静态
// 内联脚本改用 hash 型 CSP 后，设置 CSP_NONCE=1 即可切换到 nonce 化 CSP。
// style-src 保留 'unsafe-inline'：Next 会注入少量内联样式，且 CSS 注入风险远低于脚本注入。
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  ...(isProd
    ? [
        {
          key: "Content-Security-Policy",
          value: [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "frame-ancestors 'none'",
          ].join("; "),
        },
      ]
    : []),
];

const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async redirects() {
    return [
      {
        source: "/research",
        destination: "/opportunities?source=web",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
