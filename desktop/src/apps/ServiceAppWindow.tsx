import { useProcessStore } from "@/stores/process-store";

interface Props {
  windowId: string;
}

/**
 * Renders an installed service's web UI inside a sandboxed iframe.
 *
 * The service URL and display name are passed as window props by the
 * caller that opens the window (ServiceIcon in the Launchpad). The
 * windowId is used to look up those props from the process store.
 */
export function ServiceAppWindow({ windowId }: Props) {
  const win = useProcessStore((s) => s.windows.find((w) => w.id === windowId));
  const url = (win?.props?.url as string | undefined) ?? "";
  const displayName = (win?.props?.displayName as string | undefined) ?? "Service";

  if (!url) {
    return (
      <div className="flex items-center justify-center h-full text-shell-text-secondary text-sm">
        No URL configured for this service.
      </div>
    );
  }

  return (
    <iframe
      src={url}
      title={displayName}
      className="w-full h-full border-0"
      sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-downloads"
      allow="clipboard-write; fullscreen"
    />
  );
}
