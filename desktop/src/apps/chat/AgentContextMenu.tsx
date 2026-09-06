import React, { useEffect, useRef } from "react";
import { muteAgent, unmuteAgent, removeChannelMember } from "@/lib/channel-admin-api";

export type AgentContextMenuProps = {
  slug: string;
  channelId?: string;
  channelType?: string;
  isMuted?: boolean;
  x: number;
  y: number;
  onClose: () => void;
  onDm?: (slug: string) => void;
  onViewInfo?: (slug: string) => void;
  onJumpToSettings?: (slug: string) => void;
};

export function AgentContextMenu({
  slug, channelId, channelType, isMuted,
  x, y, onClose, onDm, onViewInfo, onJumpToSettings,
}: AgentContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const isDm = channelType === "dm";

  const doMute = async () => {
    if (!channelId) return;
    try {
      if (isMuted) await unmuteAgent(channelId, slug);
      else await muteAgent(channelId, slug);
    } finally { onClose(); }
  };
  const doRemove = async () => {
    if (!channelId) return;
    try { await removeChannelMember(channelId, slug); } finally { onClose(); }
  };

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Actions for @${slug}`}
      className="fixed z-50 min-w-[200px] bg-shell-surface border border-white/10 rounded-lg shadow-xl py-1 text-sm"
      style={{ top: y, left: x }}
    >
      <MenuItem onClick={() => { onDm?.(slug); onClose(); }}>DM @{slug}</MenuItem>
      {channelId && !isDm && (
        <MenuItem onClick={doMute}>
          {isMuted ? "Unmute" : "Mute"} in this channel
        </MenuItem>
      )}
      {channelId && !isDm && (
        <MenuItem onClick={doRemove}>Remove from channel</MenuItem>
      )}
      <div className="my-1 h-px bg-white/10" />
      <MenuItem onClick={() => { onViewInfo?.(slug); onClose(); }}>View agent info</MenuItem>
      <MenuItem onClick={() => { onJumpToSettings?.(slug); onClose(); }}>Jump to agent settings</MenuItem>
    </div>
  );
}

function MenuItem({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className="w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none"
    >
      {children}
    </button>
  );
}
