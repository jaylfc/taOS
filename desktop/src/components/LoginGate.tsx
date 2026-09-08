import { useState, useEffect, useCallback } from "react";
import { Lock } from "lucide-react";
import { OnboardingScreen } from "./OnboardingScreen";
import { OffNetworkScreen } from "./OffNetworkScreen";
import { SESSION_EXPIRED_EVENT } from "@/lib/auth-guard";
import { useAuthReadyStore } from "@/stores/auth-ready-store";

interface Props {
  children: React.ReactNode;
}

type AuthStatus =
  | { phase: "loading" }
  | { phase: "onboarding" }
  | { phase: "invite"; username: string; inviteCode: string; multiUser: boolean }
  // No fields: the SPA no longer renders a sign-in form, so it has no use for
  // `legacy` / `multiUser`. The server login page reads both for itself.
  | { phase: "login" }
  | { phase: "unreachable" }
  | { phase: "ready" };

// One login surface. The SPA does NOT render its own password form: it hands
// off to the server-rendered /auth/login, which is where PIN sign-in and the
// on-screen keyboard live.
//
// This is the contract auth_middleware.py:285-292 always documented ("the SPA
// checks /auth/status on boot and redirects to /auth/login"); LoginGate never
// honoured it, and the drift had teeth. /desktop is in EXEMPT_PATHS, so the
// session gate never fires for the shell HTML: the kiosk booted straight to
// this component's own password-only form and never saw the PIN screen at all.
// On a keyboard-less touchscreen that is a hard lockout -- the exact failure
// tsk-2qaisb exists to remove. Proven on the real pitop kiosk, 2026-08-26.
const LOGIN_PATH = "/auth/login";
const REDIRECT_GUARD_KEY = "taos.login-redirected";

// sessionStorage throws outright in some privacy modes, so every access is
// guarded. A storage failure must not be able to stop the redirect.
function readGuard(): boolean {
  try {
    return sessionStorage.getItem(REDIRECT_GUARD_KEY) === "1";
  } catch {
    return false;
  }
}

// Both the automatic handoff and the manual fallback link must carry the same
// destination. The shell has no client-side routing today, so this is
// `/desktop/` in practice -- the point is that the two paths cannot drift if
// that ever changes. No fragment: nothing in the SPA reads location.hash.
function loginHref(): string {
  const next = window.location.pathname + window.location.search;
  return `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
}

function writeGuard(value: boolean): void {
  try {
    if (value) sessionStorage.setItem(REDIRECT_GUARD_KEY, "1");
    else sessionStorage.removeItem(REDIRECT_GUARD_KEY);
  } catch {
    /* storage unavailable — the redirect still happens, just unguarded */
  }
}

export function LoginGate({ children }: Props) {
  const [status, setStatus] = useState<AuthStatus>({ phase: "loading" });
  // Set when we have already bounced to /auth/login once and come back still
  // unauthenticated. Redirecting again would loop the browser between two URLs
  // forever, which on a kiosk is worse than any login form: the device is
  // unusable and nothing on screen explains why.
  const [redirectBlocked, setRedirectBlocked] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch("/auth/status", { credentials: "include" });
      if (!res.ok) {
        setStatus({ phase: "ready" });
        return;
      }
      const data = await res.json();
      if (!data.configured) {
        setStatus({ phase: "onboarding" });
      } else if (data.authenticated) {
        if (data.needs_onboarding && data.user?.username) {
          // Pending invited user — collect their profile and password
          setStatus({
            phase: "invite",
            username: data.user.username,
            inviteCode: "",   // invite code was accepted at login; the session holds it
            multiUser: !!data.multi_user,
          });
        } else {
          setStatus({ phase: "ready" });
        }
      } else {
        setStatus({ phase: "login" });
      }
    } catch {
      // A thrown fetch (network failure, not an HTTP error) means the host is
      // unreachable -- e.g. the PWA was opened off the host's network. Offer
      // taOSgo rather than load the shell into a broken, data-less state.
      setStatus({ phase: "unreachable" });
    }
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // Publish auth-readiness for session-scoped restore effects that live
  // outside LoginGate's children (e.g. useSessionPersistence, active-theme
  // restore) — they must not fetch per-user data until this flips true, and
  // must re-fetch the next time it does (see auth-ready-store.ts).
  useEffect(() => {
    useAuthReadyStore.getState().setReady(status.phase === "ready");
  }, [status.phase]);

  // Listen for session-expired events from the global auth guard. Any
  // /api/* call returning 401 fires this; we re-run /auth/status which
  // will flip phase to "login" and unmount the app shell back to the
  // sign-in form. Without this, a stale cookie (e.g. after a controller
  // reinstall) left every app rendering empty data with no signal to
  // re-authenticate.
  useEffect(() => {
    const onExpired = () => {
      // Only re-prompt if we currently think we're authenticated.
      // Avoids a refresh loop if the user is already on the login form.
      setStatus((cur) => (cur.phase === "ready" ? { phase: "loading" } : cur));
      void refreshStatus();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [refreshStatus]);

  // Hand off to the server login page as soon as we know we are signed out.
  // The invite flow is unaffected: POST /auth/login creates a session for a
  // pending user and returns them to /desktop, where /auth/status reports
  // needs_onboarding and the "invite" phase below renders the completion
  // screen. routes/auth.py already documents that handoff at its form path.
  useEffect(() => {
    if (status.phase !== "login") return;
    if (readGuard()) {
      setRedirectBlocked(true);
      return;
    }
    writeGuard(true);
    window.location.assign(loginHref());
  }, [status.phase]);

  // Clear the loop guard once a session actually exists, so a later expiry in
  // the same tab can redirect again rather than landing on the manual link.
  // "invite" counts: refreshStatus only reaches it on `authenticated: true`, so
  // the bounce that set the guard has already succeeded. Leaving it set there
  // would strand an invited user on the manual link if their session expired
  // part-way through completing their profile.
  useEffect(() => {
    if (status.phase === "ready" || status.phase === "invite") writeGuard(false);
  }, [status.phase]);

  if (status.phase === "loading") {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-shell-bg text-shell-text-tertiary text-sm">
        Loading...
      </div>
    );
  }

  if (status.phase === "unreachable") {
    return <OffNetworkScreen onRetry={refreshStatus} />;
  }

  if (status.phase === "onboarding") {
    return <OnboardingScreen onDone={refreshStatus} defaultAutoLogin={true} />;
  }

  if (status.phase === "invite") {
    return (
      <OnboardingScreen
        onDone={refreshStatus}
        invitedUsername={status.username}
        inviteCode={status.inviteCode}
        defaultAutoLogin={!status.multiUser}
      />
    );
  }

  if (status.phase === "login") {
    // The redirect fires from the effect above. This renders only for the
    // instant before navigation, or -- if the loop guard tripped -- as a
    // manual way out. It deliberately offers a LINK and never a password
    // field: a second sign-in form here is the split that caused this bug.
    return (
      <div
        className="h-screen w-screen flex items-center justify-center p-4"
        style={{ background: "var(--color-shell-bg)" }}
      >
        <div className="w-full max-w-sm p-6 rounded-2xl border border-white/10 flex flex-col items-center gap-3 text-center">
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
          >
            <Lock size={24} className="text-white" />
          </div>
          <h1 className="text-lg font-semibold text-shell-text">taOS</h1>
          {redirectBlocked ? (
            <>
              <p className="text-xs text-shell-text-secondary" role="alert">
                Could not reach the sign-in page automatically.
              </p>
              <a
                href={loginHref()}
                className="mt-1 px-4 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:brightness-110 transition-all"
              >
                Go to sign in
              </a>
            </>
          ) : (
            <p className="text-xs text-shell-text-secondary">Taking you to sign in...</p>
          )}
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
