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
      className="absolute z-[60] min-w-[220px] rounded-md bg-shell-surface border border-shell-border shadow-lg py-1 text-xs"
    >
      <div className="px-2 py-1 text-shell-text-tertiary uppercase tracking-wide text-[10px]">
        Profiles
      </div>

      {profiles === null ? (
        <div className="px-2 py-1 opacity-60 italic">Loading…</div>
      ) : profiles.length === 0 ? (
        <div className="px-2 py-1 opacity-60 italic">No profiles</div>
      ) : (
        profiles.map((p) => {
          const isActive = p.profile_id === win.profileId;
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
              className="w-full text-left px-2 py-1 hover:bg-shell-hover flex items-center gap-2"
            >
              <span
                className="inline-block w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: p.color ?? "#8b92a3" }}
                aria-hidden="true"
              />
              <span className="capitalize flex-1">{p.name}</span>
              {isActive && (
                <Check size={12} className="opacity-70" aria-label="Active" />
              )}
            </button>
          );
        })
      )}

      <div className="border-t border-shell-border-subtle my-1" />

      {creating ? (
        <div className="px-2 py-1 flex gap-1">
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
            className="flex-1 bg-shell-bg-deep border border-shell-border-subtle rounded px-1.5 py-0.5 text-xs outline-none focus:border-accent"
          />
        </div>
      ) : (
        <button
          type="button"
          role="menuitem"
          onClick={() => setCreating(true)}
          className="w-full text-left px-2 py-1 hover:bg-shell-hover flex items-center gap-1.5"
        >
          <Plus size={12} />
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
          className="w-full text-left px-2 py-1 hover:bg-shell-hover flex items-center gap-1.5"
        >
          <Settings size={12} />
          Manage profiles…
        </button>
      )}
    </div>
  );
}
