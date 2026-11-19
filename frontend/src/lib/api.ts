import { useAuthStore } from "./auth-store";
import type { User } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

// 并发去重：页面同时发多个请求时，若 token 已过期会各自触发一次 refresh。
// 不合并的话既浪费请求，又可能让先返回的旧 token 覆盖后返回的新 token；
// 后端 refresh 是一次性 jti + 重放检测，并发刷新会被判重放，必须单飞。
let refreshInFlight: Promise<boolean> | null = null;

function sessionExpired(): void {
  useAuthStore.getState().clear();
  if (typeof window !== "undefined" && window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

async function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const response = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) return false;
      const data = (await response.json()) as { access_token: string };
      useAuthStore.getState().setAccessToken(data.access_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const token = useAuthStore.getState().accessToken;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (response.status === 401) {
    if (retry && (await refreshAccessToken())) {
      return apiFetch<T>(path, init, false);
    }
    sessionExpired();
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      code?: string;
      message?: string;
    };
    throw new ApiError(response.status, body.code ?? "unknown", body.message ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function ensureSession(): Promise<User | null> {
  if (!useAuthStore.getState().accessToken) {
    const refreshed = await refreshAccessToken();
    if (!refreshed) return null;
  }
  try {
    const user = await apiFetch<User>("/auth/me");
    useAuthStore.getState().setUser(user);
    return user;
  } catch (error) {
    // 只有明确的 401/403 才清会话；网络抖动/500 不能把正在填写的表单踢掉。
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      useAuthStore.getState().clear();
      return null;
    }
    return useAuthStore.getState().user;
  }
}

async function downloadRequest(
  path: string,
  body: unknown,
  retry = true,
): Promise<{ blob: Blob; filename: string }> {
  const token = useAuthStore.getState().accessToken;
  const headers = new Headers({ "Content-Type": "application/json" });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (response.status === 401) {
    if (retry && (await refreshAccessToken())) {
      return downloadRequest(path, body, false);
    }
    sessionExpired();
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      code?: string;
      message?: string;
    };
    throw new ApiError(response.status, payload.code ?? "unknown", payload.message ?? response.statusText);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const matched = disposition.match(/filename="([^"]+)"/);
  return { blob: await response.blob(), filename: matched?.[1] ?? "export.docx" };
}

export async function apiDownload(
  path: string,
  body: unknown,
): Promise<{ blob: Blob; filename: string }> {
  return downloadRequest(path, body);
}

export async function logout(): Promise<void> {
  const response = await fetch("/api/v1/auth/logout", {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    // 服务端没清掉 HttpOnly refresh cookie 时不能假装已退出：
    // 否则下次打开页面会被 ensureSession 静默恢复会话。
    throw new ApiError(response.status, "logout_failed", "退出失败，请关闭浏览器后重试");
  }
  useAuthStore.getState().clear();
}

export async function apiFetchBlob(path: string, retry = true): Promise<Blob> {
  const token = useAuthStore.getState().accessToken;
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api/v1${path}`, { headers, credentials: "include" });
  if (response.status === 401) {
    if (retry && (await refreshAccessToken())) {
      return apiFetchBlob(path, false);
    }
    sessionExpired();
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      code?: string;
      message?: string;
    };
    throw new ApiError(response.status, payload.code ?? "unknown", payload.message ?? response.statusText);
  }
  return response.blob();
}
