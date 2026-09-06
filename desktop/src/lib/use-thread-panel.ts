import { useState } from "react";

export function useThreadPanel() {
  const [openThread, setOpen] = useState<{ channelId: string; parentId: string } | null>(null);

  return {
    openThread,
    openThreadFor: (channelId: string, parentId: string) => setOpen({ channelId, parentId }),
    closeThread: () => setOpen(null),
  };
}
