import "./polyfills";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AppStandalone } from "./AppStandalone";
import { AppShell } from "./components/AppShell";
import { NotificationToasts } from "./components/NotificationToast";
import { installAuthGuard } from "./lib/auth-guard";
import { primeDeviceClass } from "./lib/mobile-shell";
import { installShellLinks } from "./lib/shell-links";
import { restoreActiveTheme, installWebkitRepaintGuards } from "./stores/theme-store";
import { getApp } from "./registry/app-registry";
import "./theme/tokens.css";

// Wrap window.fetch so any 401 from /api/* triggers a session-expired
// event that LoginGate picks up and shows the login screen (same guard
// installed by main.tsx for the desktop shell PWA).
installAuthGuard();

async function boot() {
  // This window is a taOSmobile shell window when device_class says so: learn
  // that, then send links it opens in a new window to Firefox.
  const PRIME_TIMEOUT_MS = 2000;
  await Promise.race([
    primeDeviceClass(),
    new Promise<void>((resolve) => setTimeout(resolve, PRIME_TIMEOUT_MS)),
  ]); // render must not wait on a hung config fetch; a late prime still sets device_class for later link opens
  installShellLinks();

  // Apply the user's persisted theme on boot, same as chat-main.tsx.
  void restoreActiveTheme();
  // WebKit blanks backdrop-filter surfaces when the tab is backgrounded then
  // shown again; re-composite on return (same fix the desktop shell installs).
  installWebkitRepaintGuards();

  const params = new URLSearchParams(location.search);
  const appId = params.get("app") ?? "";
  const manifest = appId ? getApp(appId) : undefined;

  if (!manifest) {
    // Unknown app id: nothing in the registry to render.
    document.title = "Not installable";
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <div
          style={{
            height: "100%",
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "rgba(255,255,255,0.5)",
            fontSize: "0.9rem",
            fontFamily: "system-ui, sans-serif",
            background: "#141415",
          }}
        >
          This app is not available as a standalone app.
        </div>
      </StrictMode>,
    );
  } else {
    // Any registered app renders standalone (taOSmobile opens every app as its
    // own window at this URL). `pwa: true` controls ONLY the install surface:
    // the dynamic manifest link below and the install prompt in AppStandalone.
    if (manifest.pwa) {
      // Inject the dynamic manifest link so the browser picks up the correct
      // name, icons, and start_url for this specific app.
      const link = document.createElement("link");
      link.rel = "manifest";
      link.href = `/manifest?app=${encodeURIComponent(appId)}`;
      document.head.appendChild(link);
    }

    document.title = manifest.name;

    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <AppShell>
          <NotificationToasts />
          <AppStandalone appId={appId} />
        </AppShell>
      </StrictMode>,
    );
  }
}

export const booted = boot();