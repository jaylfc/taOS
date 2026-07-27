import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AppStandalone } from "./AppStandalone";
import { AppShell } from "./components/AppShell";
import { installAuthGuard } from "./lib/auth-guard";
import { restoreActiveTheme, installWebkitRepaintGuards } from "./stores/theme-store";
import { getApp } from "./registry/app-registry";
import "./theme/tokens.css";

installAuthGuard();

// Apply the user's persisted theme on boot, same as chat-main.tsx.
void restoreActiveTheme();
// WebKit blanks backdrop-filter surfaces when the tab is backgrounded then
// shown again; re-composite on return (same fix the desktop shell installs).
installWebkitRepaintGuards();

const params = new URLSearchParams(location.search);
const appId = params.get("app") ?? "";
const manifest = appId ? getApp(appId) : undefined;

if (!manifest?.pwa) {
  // Unknown or non-PWA app: show a minimal not-installable message.
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
        This app is not available as a standalone PWA.
      </div>
    </StrictMode>,
  );
} else {
  // Inject the dynamic manifest link so the browser picks up the correct
  // name, icons, and start_url for this specific app.
  const link = document.createElement("link");
  link.rel = "manifest";
  link.href = `/manifest?app=${encodeURIComponent(appId)}`;
  document.head.appendChild(link);

  document.title = manifest.name;

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <AppShell>
        <AppStandalone appId={appId} />
      </AppShell>
    </StrictMode>,
  );
}
