/**
 * Shared boot-shell wrapper used by both PWAs (desktop SPA + chat PWA).
 *
 * Composes:
 *   - BackendStatusProvider (polls /api/health)
 *   - AppErrorBoundary      (catches BackendUnavailableError + ChunkLoadError)
 *   - BackendBanner         (top-of-viewport "restarting" bar)
 *   - UpdateAvailableToast  (version-mismatch prompt)
 *
 * Registers the service worker on mount.
 *
 * The build-time __TAOS_VERSION__ constant is injected by Vite (see
 * vite.config.ts). When unset (e.g. during unit tests), defaults to
 * "dev" so the update-available toast stays silent.
 */
import { useEffect, type ReactNode } from "react";
import { BackendStatusProvider } from "@/contexts/BackendStatusContext";
import { BackendBanner } from "./BackendBanner";
import { UpdateAvailableToast } from "./UpdateAvailableToast";
import { SpaUpdateToast } from "./SpaUpdateToast";
import { AppErrorBoundary } from "./AppErrorBoundary";
import { registerServiceWorker } from "@/lib/sw-register";

declare const __TAOS_VERSION__: string | undefined;
const BUILD_VERSION = typeof __TAOS_VERSION__ === "string" ? __TAOS_VERSION__ : "dev";

export function AppShell({ children }: { children: ReactNode }) {
  useEffect(() => {
    void registerServiceWorker();
  }, []);

  return (
    <BackendStatusProvider>
      <BackendBanner />
      <UpdateAvailableToast buildVersion={BUILD_VERSION} />
      <SpaUpdateToast />
      <AppErrorBoundary>{children}</AppErrorBoundary>
    </BackendStatusProvider>
  );
}
