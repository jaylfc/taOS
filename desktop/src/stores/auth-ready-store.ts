import { create } from "zustand";

// Tracks whether LoginGate has confirmed an authenticated session and is
// rendering the real desktop (its "ready" phase), as opposed to the loading /
// login / onboarding screens.
//
// SystemShortcuts (which drives useSessionPersistence and the active-theme
// restore) mounts as a sibling of LoginGate, before LoginGate's own
// /auth/status check resolves. Firing per-user restore fetches on bare
// component mount therefore races the auth check: right after a logout, the
// next mount has no session cookie yet, so the restore requests 401 and are
// never retried once the user actually logs back in (#1601, #1603). Restore
// effects gate on `ready` instead of mount, and reset when it drops back to
// false so a subsequent login re-fetches that session's saved settings.
interface AuthReadyStore {
  ready: boolean;
  setReady: (ready: boolean) => void;
}

export const useAuthReadyStore = create<AuthReadyStore>((set) => ({
  ready: false,
  setReady: (ready) => set({ ready }),
}));
