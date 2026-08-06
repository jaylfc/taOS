import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AppShell } from "./components/AppShell";
import { installAuthGuard } from "./lib/auth-guard";
import { installGlobalErrorReporting } from "./lib/client-log";
import "./theme/tokens.css";
import { initDisplayScale } from "./stores/display-store";

// Wrap window.fetch so any 401 from /api/* triggers a session-expired
// event that LoginGate picks up and shows the login screen. Previously
// a stale cookie (e.g. after a controller reinstall) left the SPA
// rendering empty data instead of prompting for re-login.
// Apply the saved per-device display scale before first paint, so the initial
// layout is already at the right scale instead of reflowing after mount.
initDisplayScale();

installAuthGuard();

// Ship uncaught errors + rejections to the controller so a PWA crash is
// diagnosable server-side (a PWA has no console the user can open).
installGlobalErrorReporting();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppShell>
      <App />
    </AppShell>
  </StrictMode>,
);
