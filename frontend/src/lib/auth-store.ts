import { create } from "zustand";

import type { User } from "./types";

type AuthState = {
  accessToken: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  setAccessToken: (token: string) => void;
  setUser: (user: User) => void;
  clear: () => void;
};

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  user: null,
  setSession: (token, user) => set({ accessToken: token, user }),
  setAccessToken: (token) => set({ accessToken: token }),
  setUser: (user) => set({ user }),
  clear: () => set({ accessToken: null, user: null }),
}));

export function hasPermission(permissions: string[] | undefined, required: string): boolean {
  if (!permissions) return false;
  return permissions.some(
    (permission) =>
      permission === "*" ||
      permission === required ||
      (permission.endsWith(":*") && required.startsWith(permission.slice(0, -1))),
  );
}
