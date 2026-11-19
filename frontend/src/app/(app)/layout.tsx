import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGuard } from "@/components/auth-guard";
import { ConfirmProvider } from "@/components/confirm-dialog";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <ConfirmProvider>
        <AppShell>{children}</AppShell>
      </ConfirmProvider>
    </AuthGuard>
  );
}
