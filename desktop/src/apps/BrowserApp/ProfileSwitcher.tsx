/**
 * Profile dropdown. Anchored to the profile chip in Chrome.tsx.
 * Lists user's profiles + check-mark on active + Manage footer.
 *
 * Manage footer calls the `onManage` callback supplied by the parent
 * (Chrome.tsx). PR 5 Task 5 will wire that to open the ProfileManager
 * modal; for Task 4 the parent provides a no-op placeholder.
 */
import { useEffect, useRef, useState } from "react";
import { Check, Plus, Settings } from "lucide-react";
import { useBrowserStore } from "@/stores/browser-store";
import { listProfiles, type Profile } from "@/lib/browser-profile-api";

interface ProfileSwitcherProps {
  windowId: string;
  onClose: () => void;
  onManage?: () => void;
}

export function ProfileSwitcher({
  windowId,
  onClose,
  onManage,
}: ProfileSwitcherProps) {
  const win = useBrowserStore((s) => s.windows[windowId]);
  const switchProfile = useBrowserStore((s) => s.switchProfile);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  // Load profiles on mount
  useEffect(() => {
    listProfiles().then(setProfiles);
  }, []);

  // Click-outside dismiss
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const id = setTimeout(() => window.addEventListener("mousedown", handler), 0);
    return () => {
      clearTimeout(id);
      window.removeEventListener("mousedown", handler);
    };
  }, [onClose]);

  if (!win) return null;

  async function handleCreate() {
    if (!newName.trim()) return;
    const { createProfile } = await import("@/lib/browser-profile-api");
    const created = await createProfile({ name: newName.trim() });
    if (created) {
      setProfiles((prev) => (prev ? [...prev, created] : [created]));
      switchProfile(windowId, created.profile_id);
      onClose();
    }
  }

  return (
    <div
      ref={ref}
      role="menu"
      aria-label="Switch profile"
      className="absolute right-0 z-[60] mt-1.5 w-[262px] rounded-xl border border-shell-border-strong bg-shell-bg-glass p-1.5 text-xs shadow-window backdrop-blur-xl"
    >
      <div className="px-2.5 pb-1.5 pt-1.5 text-[10px] font-bold uppercase tracking-[0.07em] text-shell-text-tertiary">
        Profiles
      </div>

      {profiles === null ? (
        <div className="px-2.5 py-1.5 italic text-shell-text-tertiary">Loading…</div>
      ) : profiles.length === 0 ? (
        <div className="px-2.5 py-1.5 italic text-shell-text-tertiary">No profiles</div>
      ) : (
        profiles.map((p) => {
          const isActive = p.profile_id === win.profileId;
          const initial = (p.name?.[0] ?? "?").toUpperCase();
          return (
            <button
              key={p.profile_id}
              type="button"
              role="menuitem"
              aria-current={isActive ? "true" : undefined}
              onClick={() => {
                if (!isActive) switchProfile(windowId, p.profile_id);
                onClose();
              }}
              className="flex w-full items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-left transition-colors hover:bg-white/[0.06]"
            >
              <span
                className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white"
                style={{ backgroundColor: p.color ?? "#8b92a3" }}
                aria-hidden="true"
              >
                {initial}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-semibold capitalize text-shell-text">
                  {p.name}
                </span>
                <span className="block truncate text-[11px] text-shell-text-secondary">
                  {isActive ? "Signed in" : "Isolated cookies and storage"}
                </span>
              </span>
              {isActive && (
                <Check size={15} className="shrink-0 text-accent-strong" aria-label="Active" />
              )}
            </button>
          );
        })
      )}

      <div className="my-1.5 mx-1 h-px bg-shell-border" />

      {creating ? (
        <div className="px-2 py-1">
          <input
            type="text"
            aria-label="New profile name"
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreate();
              if (e.key === "Escape") {
                setCreating(false);
                setNewName("");
              }
            }}
            placeholder="Profile name"
            className="w-full rounded-lg border border-shell-border bg-shell-bg-deep px-2 py-1.5 text-xs text-shell-text outline-none focus:border-accent/40"
          />
        </div>
      ) : (
        <button
          type="button"
          role="menuitem"
          onClick={() => setCreating(true)}
          className="flex w-full items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-left font-semibold text-shell-text-secondary transition-colors hover:bg-white/[0.06] hover:text-shell-text"
        >
          <Plus size={15} />
          New profile
        </button>
      )}

      {onManage && (
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            onManage();
            onClose();
          }}
          className="flex w-full items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-left font-semibold text-shell-text-secondary transition-colors hover:bg-white/[0.06] hover:text-shell-text"
        >
          <Settings size={15} />
          Manage profiles
        </button>
      )}
    </div>
  );
}
